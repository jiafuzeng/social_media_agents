"""M4 创作支：接收 Intel 素材卡，按 post_count 并发生成原创推文草稿。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.host.models import MAX_COMPOSE_POSTS, MIN_COMPOSE_POSTS
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot, TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_DRAFT_CONCURRENCY = 3

_DRAFT_ANGLE_HINTS = (
    "直入主题，信息密度高",
    "轻度提问或互动口吻",
    "故事化或场景化开头",
    "对比或清单式表达",
    "引用素材中的具体事实点",
    "简短有力的行动号召",
    "温和科普口吻",
    "情绪共鸣但不夸张",
    "突出差异化卖点",
    "收尾留一句开放式互动",
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compose_context(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    user_instruction = str(
        payload.get("user_instruction")
        or data.get_state("user_instruction")
        or request.get("text")
        or ""
    ).strip()
    material_list = [
        item
        for item in _as_list(data.get_state("material_list") or payload.get("material_list"))
        if isinstance(item, dict)
    ]
    return {
        "user_instruction": user_instruction,
        "intent": str(payload.get("intent") or data.get_state("intent") or "compose"),
        "material_list": material_list,
        "intel_result": str(data.get_state("intel_result") or payload.get("intel_result") or ""),
        "plan_summary": str(data.get_state("plan_summary") or payload.get("plan_summary") or ""),
        "task_id": str(payload.get("task_id") or data.get_state("task_id") or request.get("task_id") or ""),
    }


def _resolve_post_count(request: dict[str, Any], snapshot: Snapshot) -> int:
    platform_cap = max(
        MIN_COMPOSE_POSTS,
        min(int(snapshot.platform.max_posts), MAX_COMPOSE_POSTS),
    )
    raw = request.get("post_count")
    if raw is None:
        return MIN_COMPOSE_POSTS
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return MIN_COMPOSE_POSTS
    return max(MIN_COMPOSE_POSTS, min(count, platform_cap))


def _draft_angle_hint(index: int) -> str:
    return _DRAFT_ANGLE_HINTS[(index - 1) % len(_DRAFT_ANGLE_HINTS)]


async def original_tweet_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收 compose 支上游（Intel）写回的素材与意图，准备创作上下文。"""
    ctx = _compose_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))

    await data.async_set_state("user_instruction", ctx["user_instruction"], emit=False)
    await data.async_set_state("material_list", ctx["material_list"], emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    account = snapshot.account
    return {
        **ctx,
        "limitations": limitations,
        "platform_key": snapshot.platform.platform_key,
        "max_chars": snapshot.platform.max_chars,
        "voice_summary": account.voice_summary if account else "",
        "content_pillars": list(account.content_pillars) if account else [],
    }


async def plan_compose_drafts(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """按 post_count 拆解为可并行的写稿子任务。"""
    ctx = _compose_context(data)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    post_count = _resolve_post_count(request, snapshot)
    work_items = [
        {
            "draft_key": f"d{index}",
            "draft_index": index,
            "total_count": post_count,
            "angle_hint": _draft_angle_hint(index),
        }
        for index in range(1, post_count + 1)
    ]
    await data.async_set_state("post_count", post_count, emit=False)
    await data.async_set_state("compose_draft_plan", work_items, emit=False)
    return work_items


async def original_tweet_reason(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """基于素材卡与人设，并发生成单条原创推文草稿。"""
    work = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    draft_key = str(work.get("draft_key") or "d1")
    draft_index = int(work.get("draft_index") or 1)
    total_count = int(work.get("total_count") or 1)
    angle_hint = str(work.get("angle_hint") or _draft_angle_hint(draft_index))

    ctx = _compose_context(data)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    account = snapshot.account
    material_list = list(ctx["material_list"])
    user_instruction = str(ctx["user_instruction"])

    info: dict[str, Any] = {
        "intent": "compose",
        "work_item": work,
        "material_cards": material_list,
        "intel_result": ctx["intel_result"],
        "plan_summary": ctx["plan_summary"],
        "platform": snapshot.platform.model_dump(mode="json"),
        "max_chars": snapshot.platform.max_chars,
        "draft_index": draft_index,
        "total_count": total_count,
        "angle_hint": angle_hint,
    }
    if account is not None:
        info["account"] = account.model_dump(mode="json")

    draft_text = ""
    rationale = ""
    error = ""
    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-original-draft")
            .input(user_instruction)
            .info(info)
            .instruct(
                [
                    f"本次共需生成 {total_count} 条推文，你负责第 {draft_index} 条（draft_key={draft_key}）。",
                    f"写法角度：{angle_hint}。与其他条目的开头、结构、落脚点要有明显区分，禁止复读同一句。",
                    "根据用户意图与 info.material_cards 写一条原创推文。",
                    "只借鉴素材的结构与事实点，不要整段抄袭；不要写长文分析。",
                    "遵守 info.account 的 voice、pillars、must_do、must_not。",
                    f"正文不超过 info.max_chars 字。",
                    "不要输出 hashtags 堆砌；不要编造素材卡中没有的事实。",
                    "素材为空时仍可基于用户意图与人设创作，但语气要保守。",
                ]
            )
            .output(
                {
                    "draft_text": (str, "推文正文", "not_null"),
                    "rationale": (str, "写法说明", "not_null"),
                },
                format="json",
            )
            .async_start()
        )
        if isinstance(raw, dict):
            draft_text = str(raw.get("draft_text") or "").strip()
            rationale = str(raw.get("rationale") or "").strip()
    except Exception as exc:
        error = f"original_tweet_error:{draft_key}:{type(exc).__name__}"

    return {
        "draft_key": draft_key,
        "draft_index": draft_index,
        "draft_text": draft_text,
        "rationale": rationale,
        "ok": bool(draft_text),
        "error": error,
    }


def _normalize_draft(
    *,
    draft_key: str,
    draft_text: str,
    rationale: str,
    platform_key: str,
) -> dict[str, Any]:
    """归一化单条推文草稿为 M7 package.drafts[] 契约。"""
    has_text = bool(draft_text)
    return {
        "draft_key": draft_key,
        "kind": "compose_post",
        "platform_key": platform_key,
        "degrade_op": "pass",
        "text": draft_text,
        "rationale": rationale,
        "decision": "publishable" if has_text else "skip",
        "evidence_ids": [],
        "kb_ids": [],
        "risk_flags": [],
        "status": "ready" if has_text else "skipped",
        "issues": [] if has_text else ["empty_draft"],
    }


def _normalize_package(
    *,
    drafts: list[dict[str, Any]],
    material_cards: list[dict[str, Any]],
    limitations: list[str],
    post_count: int,
) -> dict[str, Any]:
    ready_count = sum(1 for item in drafts if str(item.get("text") or "").strip())
    if ready_count == 0:
        summary = "未生成推文草稿"
        status = "partial"
    elif ready_count < post_count:
        summary = f"已生成 {ready_count}/{post_count} 条原创推文草稿"
        status = "partial"
    else:
        summary = f"已生成 {ready_count} 条原创推文草稿"
        status = "completed"
    return {
        "status": status,
        "intent": "compose",
        "task_type": "compose_post",
        "summary": summary,
        "material_cards": material_cards,
        "drafts": drafts,
        "limitations": limitations,
    }


async def normalized_output_tweet(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """汇总 for_each 写稿结果，归一化为 package / drafts 结构。"""
    ctx = _compose_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    material_list = list(ctx["material_list"])
    request = cast(dict[str, Any], data.get_state("request") or {})
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    post_count = _resolve_post_count(request, snapshot)
    platform_key = snapshot.platform.platform_key or TWITTER_PLATFORM_KEY

    task_results = [
        item for item in _as_list(data.input) if isinstance(item, dict)
    ]
    task_results.sort(key=lambda item: int(item.get("draft_index") or 0))

    drafts: list[dict[str, Any]] = []
    for result in task_results:
        error = str(result.get("error") or "").strip()
        if error and error not in limitations:
            limitations.append(error)
        if not result.get("ok", True) and not error:
            code = f"original_tweet_failed:{result.get('draft_key') or 'unknown'}"
            if code not in limitations:
                limitations.append(code)

        draft_text = str(result.get("draft_text") or "").strip()
        draft = _normalize_draft(
            draft_key=str(result.get("draft_key") or f"d{len(drafts) + 1}"),
            draft_text=draft_text,
            rationale=str(result.get("rationale") or "").strip(),
            platform_key=platform_key,
        )
        drafts.append(draft)

    package = _normalize_package(
        drafts=drafts,
        material_cards=material_list,
        limitations=limitations,
        post_count=post_count,
    )

    await data.async_set_state("drafts", drafts, emit=False)
    await data.async_set_state("package", package, emit=False)
    await data.async_set_state("material_list", material_list, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.original_tweet",
        status="completed" if drafts else "failed",
        subject_id=ctx["task_id"],
        facts={
            "material_cards": len(material_list),
            "post_count": post_count,
            "draft_count": len(drafts),
            "limitations": limitations,
        },
    )
    return {
        "package": package,
        "drafts": drafts,
        "limitations": limitations,
        "material_cards": material_list,
    }


def build_original_tweet_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-original-tweet-v1")
    (
        flow.to(original_tweet_prelude)
        .to(plan_compose_drafts)
        .for_each(concurrency=MAX_DRAFT_CONCURRENCY)
        .to(original_tweet_reason)
        .end_for_each()
        .to(normalized_output_tweet)
    )
    return flow


ORIGINAL_TWEET_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "request": "runtime_data.request",
        "intent": "runtime_data.intent",
        "source_kind": "runtime_data.source_kind",
        "source_anchor": "runtime_data.source_anchor",
        "user_instruction": "runtime_data.user_instruction",
        "limitations": "runtime_data.limitations",
        "material_list": "runtime_data.material_list",
        "intel_result": "runtime_data.intel_result",
        "plan_summary": "runtime_data.plan_summary",
        "material_plan": "runtime_data.material_plan",
        "tweet_cards": "runtime_data.tweet_cards",
        "trend_cards": "runtime_data.trend_cards",
        "tool_logs": "runtime_data.tool_logs",
    },
    "resources": {
        "trace": "resources.trace",
        "session_id": "resources.session_id",
        "snapshot": "resources.snapshot",
    },
}

ORIGINAL_TWEET_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "package": "result.package",
        "drafts": "result.drafts",
        "limitations": "result.limitations",
        "material_list": "result.material_list",
    },
}


__all__ = [
    "ORIGINAL_TWEET_SUBFLOW_CAPTURE",
    "ORIGINAL_TWEET_SUBFLOW_WRITE_BACK",
    "build_original_tweet_subflow",
    "normalized_output_tweet",
    "original_tweet_prelude",
    "original_tweet_reason",
    "plan_compose_drafts",
]
