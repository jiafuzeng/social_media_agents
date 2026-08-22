"""M4 改写支：接收 Source 原文包，按 post_count 并发生成改写推文草稿。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.compose.branch_hold import (
    _collect_upstream,
    _normalize_branch_context,
)
from integrated_agent.runtimes.matrix.compose.originaltweet import (
    MAX_DRAFT_CONCURRENCY,
    _draft_angle_hint,
    _normalize_draft,
    _resolve_post_count,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot, TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rewrite_context(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = cast(dict[str, Any], data.get_state("request") or {})
    user_instruction = str(data.get_state("user_instruction") or request.get("text") or "").strip()
    return {
        "user_instruction": user_instruction,
        "intent": "rewrite",
        "source_kind": str(data.get_state("source_kind") or ""),
        "source_anchor": str(data.get_state("source_anchor") or "").strip(),
        "task_id": str(request.get("task_id") or data.get_state("task_id") or ""),
    }


def _extract_source_text(
    *,
    rewrite_ctx: dict[str, Any],
    user_instruction: str,
    request_text: str,
) -> str:
    source_post = rewrite_ctx.get("source_post")
    if isinstance(source_post, dict):
        text = str(source_post.get("text") or "").strip()
        if text:
            return text

    for item in _as_list(rewrite_ctx.get("tool_result_cleaned")):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("title") or "").strip()
        if text:
            return text

    source_result = str(rewrite_ctx.get("source_result") or "").strip()
    if source_result:
        return source_result

    for candidate in (user_instruction, request_text):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _normalize_rewrite_package(
    *,
    drafts: list[dict[str, Any]],
    material_cards: list[dict[str, Any]],
    evidence_cards: list[dict[str, Any]],
    branch_context: dict[str, Any],
    limitations: list[str],
    post_count: int,
) -> dict[str, Any]:
    ready_count = sum(1 for item in drafts if str(item.get("text") or "").strip())
    if ready_count == 0:
        summary = "未生成改写推文草稿"
        status = "partial"
    elif ready_count < post_count:
        summary = f"已生成 {ready_count}/{post_count} 条改写推文草稿"
        status = "partial"
    else:
        summary = f"已生成 {ready_count} 条改写推文草稿"
        status = "completed"
    return {
        "status": status,
        "intent": "rewrite",
        "task_type": "compose_post",
        "summary": summary,
        "material_cards": material_cards,
        "evidence_cards": evidence_cards,
        "branch_context": branch_context,
        "drafts": drafts,
        "limitations": limitations,
    }


async def rewrite_tweet_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """归一化 Source 原文包，准备改写上下文。"""
    ctx = _rewrite_context(data)
    request = cast(dict[str, Any], data.get_state("request") or {})
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))

    upstream = _collect_upstream(data)
    branch_context, evidence_cards = _normalize_branch_context(
        intent="rewrite",
        upstream=upstream,
        user_instruction=ctx["user_instruction"],
    )
    rewrite_ctx = _as_dict(branch_context.get("rewrite"))
    source_text = _extract_source_text(
        rewrite_ctx=rewrite_ctx,
        user_instruction=ctx["user_instruction"],
        request_text=str(request.get("text") or ""),
    )
    if not source_text and "rewrite_missing_source" not in limitations:
        limitations.append("rewrite_missing_source")

    await data.async_set_state("branch_context", branch_context, emit=False)
    await data.async_set_state("evidence_cards", evidence_cards, emit=False)
    await data.async_set_state("material_list", list(evidence_cards), emit=False)
    await data.async_set_state("source_text", source_text, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    account = snapshot.account
    return {
        **ctx,
        "limitations": limitations,
        "source_text": source_text,
        "branch_context": branch_context,
        "evidence_cards": evidence_cards,
        "rewrite_ctx": rewrite_ctx,
        "platform_key": snapshot.platform.platform_key,
        "max_chars": snapshot.platform.max_chars,
        "voice_summary": account.voice_summary if account else "",
        "content_pillars": list(account.content_pillars) if account else [],
    }


async def plan_rewrite_drafts(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """按 post_count 拆解为可并行的改写写稿子任务。"""
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
    await data.async_set_state("rewrite_draft_plan", work_items, emit=False)
    return work_items


async def rewrite_tweet_reason(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """基于原文包与人设，并发生成单条改写推文草稿。"""
    work = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    draft_key = str(work.get("draft_key") or "d1")
    draft_index = int(work.get("draft_index") or 1)
    total_count = int(work.get("total_count") or 1)
    angle_hint = str(work.get("angle_hint") or _draft_angle_hint(draft_index))

    ctx = _rewrite_context(data)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    account = snapshot.account
    branch_context = _as_dict(data.get_state("branch_context"))
    rewrite_ctx = _as_dict(branch_context.get("rewrite"))
    source_text = str(data.get_state("source_text") or "").strip()
    user_instruction = str(ctx["user_instruction"])

    source_post = rewrite_ctx.get("source_post")
    source_media = [
        item for item in _as_list(rewrite_ctx.get("source_media")) if isinstance(item, dict)
    ]
    author_card = rewrite_ctx.get("author_card")
    related_tweet_cards = [
        item
        for item in _as_list(rewrite_ctx.get("related_tweet_cards"))
        if isinstance(item, dict)
    ]

    info: dict[str, Any] = {
        "intent": "rewrite",
        "work_item": work,
        "user_instruction": user_instruction,
        "source_text": source_text,
        "source_post": source_post,
        "source_media": source_media,
        "author_card": author_card,
        "related_tweet_cards": related_tweet_cards,
        "source_result": str(rewrite_ctx.get("source_result") or ""),
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
    if not source_text:
        error = "rewrite_missing_source"
    else:
        try:
            raw = await (
                Agently.create_agent(name="matrix-compose-rewrite-draft")
                .input(user_instruction or source_text)
                .info(info)
                .instruct(
                    [
                        f"本次共需生成 {total_count} 条改写推文，你负责第 {draft_index} 条（draft_key={draft_key}）。",
                        f"写法角度：{angle_hint}。与其他条目的开头、结构、落脚点要有明显区分，禁止复读同一句。",
                        "根据 info.source_text 与用户指令，写一条本号口吻的推文。",
                        "必须原创表述，禁止整段照抄原文；可保留事实点，但句式与结构要改写。",
                        "遵守 info.account 的 voice、pillars、must_do、must_not。",
                        f"正文不超过 info.max_chars 字。",
                        "不要把 preview_url、jpg、mp4 链接写进正文；配图由包内 media 字段处理。",
                        "不要输出 hashtags 堆砌；不要编造原文中没有的事实。",
                        "info.related_tweet_cards 只作结构参考，不要写成第二篇原文。",
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
            error = f"rewrite_tweet_error:{draft_key}:{type(exc).__name__}"

    return {
        "draft_key": draft_key,
        "draft_index": draft_index,
        "draft_text": draft_text,
        "rationale": rationale,
        "ok": bool(draft_text),
        "error": error,
    }


async def normalized_output_rewrite(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """汇总 for_each 改写写稿结果，归一化为 package / drafts 结构。"""
    ctx = _rewrite_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    evidence_cards = [
        item for item in _as_list(data.get_state("evidence_cards")) if isinstance(item, dict)
    ]
    branch_context = _as_dict(data.get_state("branch_context"))
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
            code = f"rewrite_tweet_failed:{result.get('draft_key') or 'unknown'}"
            if code not in limitations:
                limitations.append(code)

        draft = _normalize_draft(
            draft_key=str(result.get("draft_key") or f"d{len(drafts) + 1}"),
            draft_text=str(result.get("draft_text") or "").strip(),
            rationale=str(result.get("rationale") or "").strip(),
            platform_key=platform_key,
        )
        drafts.append(draft)

    package = _normalize_rewrite_package(
        drafts=drafts,
        material_cards=evidence_cards,
        evidence_cards=evidence_cards,
        branch_context=branch_context,
        limitations=limitations,
        post_count=post_count,
    )

    await data.async_set_state("drafts", drafts, emit=False)
    await data.async_set_state("package", package, emit=False)
    await data.async_set_state("material_list", evidence_cards, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.rewrite_tweet",
        status="completed" if any(d["text"] for d in drafts) else "failed",
        subject_id=ctx["task_id"],
        facts={
            "evidence_cards": len(evidence_cards),
            "post_count": post_count,
            "draft_count": len([d for d in drafts if d["text"]]),
            "limitations": limitations,
        },
    )
    return {
        "package": package,
        "drafts": drafts,
        "limitations": limitations,
        "material_list": evidence_cards,
        "evidence_cards": evidence_cards,
        "branch_context": branch_context,
    }


def build_rewrite_tweet_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-rewrite-tweet-v1")
    (
        flow.to(rewrite_tweet_prelude)
        .to(plan_rewrite_drafts)
        .for_each(concurrency=MAX_DRAFT_CONCURRENCY)
        .to(rewrite_tweet_reason)
        .end_for_each()
        .to(normalized_output_rewrite)
    )
    return flow


REWRITE_TWEET_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "request": "runtime_data.request",
        "intent": "runtime_data.intent",
        "source_kind": "runtime_data.source_kind",
        "source_anchor": "runtime_data.source_anchor",
        "user_instruction": "runtime_data.user_instruction",
        "limitations": "runtime_data.limitations",
        "tool_logs": "runtime_data.tool_logs",
        "tool_result_cleaned": "runtime_data.tool_result_cleaned",
        "source_result": "runtime_data.source_result",
        "source_post": "runtime_data.source_post",
        "source_media": "runtime_data.source_media",
        "author_card": "runtime_data.author_card",
        "related_tweet_cards": "runtime_data.related_tweet_cards",
    },
    "resources": {
        "trace": "resources.trace",
        "session_id": "resources.session_id",
        "snapshot": "resources.snapshot",
    },
}

REWRITE_TWEET_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "package": "result.package",
        "drafts": "result.drafts",
        "limitations": "result.limitations",
        "material_list": "result.material_list",
        "evidence_cards": "result.evidence_cards",
        "branch_context": "result.branch_context",
    },
}


__all__ = [
    "REWRITE_TWEET_SUBFLOW_CAPTURE",
    "REWRITE_TWEET_SUBFLOW_WRITE_BACK",
    "build_rewrite_tweet_subflow",
    "normalized_output_rewrite",
    "plan_rewrite_drafts",
    "rewrite_tweet_prelude",
    "rewrite_tweet_reason",
]
