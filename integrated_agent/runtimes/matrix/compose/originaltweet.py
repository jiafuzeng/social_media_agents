"""M4 创作支：接收 Intel 素材卡，按 post_count 并发生成原创推文草稿。"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.compose.brief import (
    collect_compose_work_items,
    compose_brief,
)
from integrated_agent.runtimes.matrix.compose.draft_gate import gate_compose_draft
from integrated_agent.runtimes.matrix.compose.review import (
    _coerce_gated_draft,
    review_compose_draft_item,
)
from integrated_agent.runtimes.matrix.compose.material import (
    _align_material_cards,
    _collect_material_cards,
    _compose_media_bundle,
    _draft_angle_hint,
    _focus_hint_for_card,
    _resolve_post_count,
)
from integrated_agent.runtimes.matrix.host.drafting import rollup_status
from integrated_agent.runtimes.matrix.host.models import GatedDraft
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot, TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_DRAFT_CONCURRENCY = 3
MAX_DRAFT_REGEN_ATTEMPTS = 2


def _compose_draft_instruct(
    *,
    total_count: int,
    draft_index: int,
    draft_key: str,
    focus_hint: str,
) -> list[str]:
    return [
        f"本次共需生成 {total_count} 条推文，你负责第 {draft_index} 条（draft_key={draft_key}）。",
        f"写法角度：{focus_hint}。与其他条目的开头、结构、落脚点要有明显区分，禁止复读同一句。",
        "优先遵循 work_item.goal 与 talking_points；它们是包级计划，不要偏离。",
        "只根据 info.material_card 这一张素材卡写一条原创推文，不要混用其他素材。",
        "只借鉴素材的结构与事实点，不要整段抄袭；不要写长文分析。",
        "遵守 info.account 的 voice、pillars、must_do、must_not。",
        "正文不超过 info.max_chars 字。",
        "结尾只给一个增长 CTA：关注系列/点置顶/去官方渠道；禁止评论区互动话术。",
        "结尾只用文字 CTA；不要写 [[cta:0]] 或任意 https。",
        "仅当 info.offered_media 非空且配图与本条素材相关时，draft_text 用 [[media:m1]] 占位；offered_media 为空则不要写媒体占位，也不要把图片/视频链接写进正文。",
        "证据只能引用 info.offered_refs 的 ref_id，填 evidence_ids；正文不要写 [[ref:]] 或 [[kb:]]。offered_refs 为空时 evidence_ids 必须是 []。",
        "不要输出 hashtags 堆砌；不要编造素材卡中没有的事实。",
        "素材为空时仍可基于用户意图与人设创作，但语气要保守。",
    ]


def _is_publishable_compose_draft(gated: GatedDraft) -> bool:
    if not gated.text.strip():
        return False
    if gated.status in {"skipped", "failed"}:
        return False
    if gated.degrade_op == "skip":
        return False
    if "review_reject" in gated.issues:
        return False
    if any(str(item).startswith("revise_rejected:") for item in gated.issues):
        return False
    return gated.status in {"ready", "degraded"}


def _draft_payload_from_gated(
    gated: GatedDraft,
    *,
    work: dict[str, Any],
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = gated.model_dump(mode="json")
    payload.update(
        {
            "draft_index": int(
                (base or {}).get("draft_index") or work.get("draft_index") or 1
            ),
            "ok": bool(gated.text.strip()),
            "error": gated.issues[0] if gated.issues else "",
            "material_card": work.get("material_card") or {},
        }
    )
    return payload


def _regen_repair(
    *,
    gated: GatedDraft,
    attempt: int,
    reason: str,
    review_notes: str = "",
) -> dict[str, Any]:
    return {
        "issues": list(gated.issues) or [reason],
        "previous_text": gated.text,
        "review_notes": review_notes,
        "regen_reason": reason,
        "regen_attempt": attempt,
    }


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


async def original_tweet_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收 compose 支上游（Intel）写回的素材卡，准备创作上下文。"""
    ctx = _compose_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    material_cards = _collect_material_cards(data)

    await data.async_set_state("user_instruction", ctx["user_instruction"], emit=False)
    await data.async_set_state("material_list", material_cards, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    events = data.get_resource("events")
    if events is not None and ctx["task_id"]:
        await events.publish(ctx["task_id"], "stage.started", {"stage": "original_tweet"})

    account = snapshot.account
    return {
        **ctx,
        "material_list": material_cards,
        "limitations": limitations,
        "platform_key": snapshot.platform.platform_key,
        "max_chars": snapshot.platform.max_chars,
        "voice_summary": account.voice_summary if account else "",
        "content_pillars": list(account.content_pillars) if account else [],
    }


async def plan_compose_drafts(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """按 post_count 将素材卡一对一（或多退少补）分发为写稿任务。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    post_count = _resolve_post_count(request, snapshot)
    source_cards = _collect_material_cards(data)
    aligned_cards, allocation_mode = _align_material_cards(source_cards, post_count)

    work_items: list[dict[str, Any]] = []
    for index in range(1, post_count + 1):
        material_card = aligned_cards[index - 1]
        offered_media, media_catalog = _compose_media_bundle(material_card)
        angle_hint = _draft_angle_hint(index)
        work_items.append(
            {
                "draft_key": f"d{index}",
                "draft_index": index,
                "total_count": post_count,
                "angle_hint": angle_hint,
                "focus_hint": _focus_hint_for_card(material_card, angle_hint),
                "material_card": material_card,
                "card_allocation": allocation_mode,
                "offered_media": offered_media,
                "media_catalog": media_catalog,
            }
        )

    await data.async_set_state("post_count", post_count, emit=False)
    await data.async_set_state("material_list", aligned_cards, emit=False)
    await data.async_set_state("material_allocation", allocation_mode, emit=False)
    await data.async_set_state("compose_draft_plan", work_items, emit=False)
    return work_items


async def original_tweet_reason(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """检索 + 写稿 + Gate，生成单条原创推文草稿。"""
    return await original_tweet_draft_with_review(data)


async def original_tweet_draft_with_review(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """写稿 + Gate + Review；不合格则带 repair 再生成（至多 MAX_DRAFT_REGEN_ATTEMPTS 次）。"""
    work = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    draft_index = int(work.get("draft_index") or 1)
    total_count = int(work.get("total_count") or 1)
    draft_key = str(work.get("draft_key") or "d1")
    talking_points = [
        str(item).strip()
        for item in _as_list(work.get("talking_points") or [])
        if str(item).strip()
    ]
    angle_hint = str(work.get("angle_hint") or _draft_angle_hint(draft_index))
    focus_hint = str(work.get("focus_hint") or angle_hint)
    if talking_points:
        focus_hint = "；".join(talking_points[:3])

    instruct = _compose_draft_instruct(
        total_count=total_count,
        draft_index=draft_index,
        draft_key=draft_key,
        focus_hint=focus_hint,
    )
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    repair: dict[str, Any] | None = None
    last_payload: dict[str, Any] = {}

    for attempt in range(1, MAX_DRAFT_REGEN_ATTEMPTS + 1):
        attempt_instruct = list(instruct)
        if repair:
            attempt_instruct.append(
                "上一稿未通过 Gate/Review，请按 repair.issues、review_notes 重写，"
                "与 previous_text 明显区分，不要复读。"
            )
        last_payload = await gate_compose_draft(
            data,
            work=work,
            draft_agent_name="matrix-compose-draft",
            instruct=attempt_instruct,
            draft_repair=repair,
        )
        gated = _coerce_gated_draft(last_payload)

        if not gated.text.strip() or gated.degrade_op == "skip":
            if attempt < MAX_DRAFT_REGEN_ATTEMPTS:
                note = f"compose_draft_regen:{draft_key}:gate:{attempt}"
                if note not in limitations:
                    limitations.append(note)
                repair = _regen_repair(
                    gated=gated,
                    attempt=attempt,
                    reason="gate_skip_or_empty",
                )
                continue
            break

        try:
            reviewed, review_notes = await review_compose_draft_item(
                data,
                gated,
                limitations=limitations,
            )
        except Exception as exc:
            note = f"compose_review_error:{draft_key}:{type(exc).__name__}"
            if note not in limitations:
                limitations.append(note)
            if _is_publishable_compose_draft(gated):
                last_payload = _draft_payload_from_gated(
                    gated,
                    work=work,
                    base=last_payload,
                )
                break
            if attempt < MAX_DRAFT_REGEN_ATTEMPTS:
                repair = _regen_repair(
                    gated=gated,
                    attempt=attempt,
                    reason="review_agent_error",
                    review_notes=str(exc)[:200],
                )
                continue
            break
        for note in review_notes:
            if note not in limitations:
                limitations.append(note)

        last_payload = _draft_payload_from_gated(reviewed, work=work, base=last_payload)
        if _is_publishable_compose_draft(reviewed):
            break

        if attempt < MAX_DRAFT_REGEN_ATTEMPTS:
            note = f"compose_draft_regen:{draft_key}:review:{attempt}"
            if note not in limitations:
                limitations.append(note)
            repair = _regen_repair(
                gated=reviewed,
                attempt=attempt,
                reason="review_not_publishable",
                review_notes="；".join(
                    part
                    for part in (
                        reviewed.issues[0] if reviewed.issues else "",
                        str(repair.get("review_notes") or "") if repair else "",
                    )
                    if part
                ),
            )
            continue
        break

    if limitations:
        await data.async_set_state("limitations", limitations, emit=False)
    return last_payload


def _normalize_draft(
    *,
    draft_key: str,
    draft_text: str,
    rationale: str,
    platform_key: str,
    media: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """归一化单条推文草稿为 M7 package.drafts[] 契约。"""
    has_text = bool(draft_text)
    draft: dict[str, Any] = {
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
    if media:
        draft["media"] = media
    return draft


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
    """汇总 review 后的 Gate 草稿，归一化为 package / drafts 结构。"""
    ctx = _compose_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    material_list = list(ctx["material_list"])
    request = cast(dict[str, Any], data.get_state("request") or {})
    post_count = _resolve_post_count(request, cast(Snapshot, data.require_resource("snapshot")))

    review_payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    raw_input = data.input
    if isinstance(raw_input, list):
        drafts_raw = [item for item in raw_input if isinstance(item, dict)]
    else:
        drafts_raw = list(
            cast(
                list[Any],
                data.get_state("drafts") or review_payload.get("drafts") or [],
            )
        )
    if not drafts_raw:
        drafts_raw = list(cast(list[Any], data.get_state("drafts") or []))
    summary = str(
        review_payload.get("review_summary")
        or data.get_state("review_summary")
        or ""
    ).strip()

    drafts: list[dict[str, Any]] = []
    for index, item in enumerate(drafts_raw, start=1):
        if not isinstance(item, dict):
            continue
        gated = _coerce_gated_draft(item)
        draft = gated.model_dump(mode="json")
        if not draft.get("draft_key"):
            draft["draft_key"] = f"d{index}"
        drafts.append(draft)

    status = str(
        review_payload.get("rollup_status")
        or data.get_state("rollup_status")
        or rollup_status(
            [_coerce_gated_draft(item) for item in drafts if isinstance(item, dict)]
        )
    )
    if not summary:
        ready = sum(1 for item in drafts if str(item.get("text") or "").strip())
        if ready == 0:
            summary = "未生成推文草稿"
        elif ready < post_count:
            summary = f"已生成 {ready}/{post_count} 条原创推文草稿"
        else:
            summary = f"已生成 {ready} 条原创推文草稿"

    package = {
        "status": status,
        "intent": "compose",
        "task_type": "compose_post",
        "summary": summary,
        "material_cards": material_list,
        "drafts": drafts,
        "limitations": limitations,
    }

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
            "material_allocation": str(data.get_state("material_allocation") or ""),
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
        "brief": data.get_state("brief"),
        "work_items": data.get_state("work_items") or [],
    }


def build_original_tweet_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-original-tweet-v1")
    (
        flow.to(original_tweet_prelude)
        .to(compose_brief)
        .to(collect_compose_work_items)
        .for_each(concurrency=MAX_DRAFT_CONCURRENCY)
        .to(original_tweet_draft_with_review)
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
        "brief": "runtime_data.brief",
        "work_items": "runtime_data.work_items",
        "tweet_cards": "runtime_data.tweet_cards",
        "trend_cards": "runtime_data.trend_cards",
        "tool_logs": "runtime_data.tool_logs",
    },
    "resources": {
        "trace": "resources.trace",
        "snapshot": "resources.snapshot",
        "data_root": "resources.data_root",
        "knowledge": "resources.knowledge",
        "kb_user_id": "resources.kb_user_id",
        "events": "resources.events",
    },
}

ORIGINAL_TWEET_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "package": "result.package",
        "drafts": "result.drafts",
        "limitations": "result.limitations",
        "material_list": "result.material_list",
        "brief": "result.brief",
        "work_items": "result.work_items",
    },
}


__all__ = [
    "ORIGINAL_TWEET_SUBFLOW_CAPTURE",
    "ORIGINAL_TWEET_SUBFLOW_WRITE_BACK",
    "_align_material_cards",
    "_collect_material_cards",
    "build_original_tweet_subflow",
    "collect_compose_work_items",
    "compose_brief",
    "normalized_output_tweet",
    "original_tweet_draft_with_review",
    "original_tweet_prelude",
    "original_tweet_reason",
    "plan_compose_drafts",
]
