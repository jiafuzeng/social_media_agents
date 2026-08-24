"""M6：写帖包级 review（compose / rewrite 共用）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.draft_media import resolve_draft_cta, resolve_draft_refs
from integrated_agent.runtimes.matrix.host.constraints import (
    AhoCorasickMatcher,
    KB_CITE_RE,
    apply_constraint_gate,
)
from integrated_agent.runtimes.matrix.host.drafting import apply_review, rollup_status
from integrated_agent.runtimes.matrix.host.models import GatedDraft, ReviewOut
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


def _coerce_gated_draft(item: dict[str, Any]) -> GatedDraft:
    allowed = set(GatedDraft.model_fields.keys())
    return GatedDraft.model_validate(
        {key: value for key, value in item.items() if key in allowed}
    )


def _slim_brief_for_draft(
    data: TriggerFlowRuntimeData,
    draft: GatedDraft,
) -> dict[str, Any] | None:
    """单条 Review 只带相关 work_item，避免 10 条计划撑爆 prompt。"""
    brief_raw = data.get_state("brief")
    if not isinstance(brief_raw, dict):
        return None
    work_items = [
        item
        for item in (brief_raw.get("work_items") or [])
        if isinstance(item, dict)
    ]
    draft_key = draft.draft_key
    draft_index = draft_key.removeprefix("d")
    matched = [
        item
        for item in work_items
        if str(item.get("work_item_id") or "") == draft_index
        or str(item.get("work_item_id") or "").endswith(draft_index)
        or f"d{item.get('work_item_id')}" == draft_key
    ]
    picked = matched[:1] if matched else work_items[:1]
    return {
        "normalized_brief": brief_raw.get("normalized_brief"),
        "requirements": list(brief_raw.get("requirements") or [])[:3],
        "work_items": picked,
    }


def _compose_source_text(data: TriggerFlowRuntimeData) -> str:
    source_text = str(data.get_state("source_text") or "").strip()
    if source_text:
        return source_text
    source_post = data.get_state("source_post")
    if isinstance(source_post, dict):
        return str(source_post.get("text") or "").strip()
    return ""


def _make_compose_re_gate(
    *,
    snapshot: Snapshot,
    intent: str,
    source_text: str,
    offered_cta_urls: list[str],
) -> Callable[[GatedDraft, str], Awaitable[GatedDraft]]:
    matcher = AhoCorasickMatcher(snapshot.policy.terms)

    async def re_gate(current: GatedDraft, revised_text: str) -> GatedDraft:
        media_keys = [item.media_key for item in current.media if item.media_key]
        gated = await apply_constraint_gate(
            work_item_id=current.draft_key.removeprefix("d-"),
            kind="compose_post",
            platform_key=current.platform_key,
            source_comment_key=None,
            text=revised_text,
            rationale=current.rationale,
            evidence_ids=current.evidence_ids,
            risk_flags=current.risk_flags,
            claim_types=["format"],
            reply_decision=None,
            proposed_degrade=None,
            max_chars=snapshot.platform.max_chars,
            matcher=matcher,
            offered_refs=list(current.evidence_ids),
            offered_kbs=list(current.kb_ids),
            retrieval_state="hits" if current.evidence_ids else "empty",
            templates=[item.model_dump(mode="json") for item in snapshot.templates],
            offered_cta_urls=offered_cta_urls,
            offered_media_keys=media_keys,
            source_text=source_text if intent == "rewrite" else "",
        )
        cited = [
            token
            for token in KB_CITE_RE.findall(gated.text or revised_text)
            if token in current.kb_ids
        ]
        display_text = resolve_draft_refs(gated.text or revised_text)
        display_text = resolve_draft_cta(
            display_text,
            offered_cta_urls=offered_cta_urls,
        )
        return gated.model_copy(
            update={
                "draft_key": current.draft_key,
                "text": display_text,
                "media": current.media,
                "kb_ids": cited,
            }
        )

    return re_gate


async def _run_compose_review_agent(
    data: TriggerFlowRuntimeData,
    *,
    drafts: list[GatedDraft],
    limitations: list[str],
    intent: str,
    snapshot: Snapshot,
    brief_override: dict[str, Any] | None = None,
) -> ReviewOut:
    review_input: dict[str, Any] = {
        "brief": brief_override if brief_override is not None else data.get_state("brief"),
        "rewrite_plan_card": data.get_state("rewrite_plan_card"),
        "drafts": [item.model_dump(mode="json") for item in drafts],
        "limitations": limitations,
    }
    if intent == "rewrite":
        review_input["source_post"] = data.get_state("source_post")
        review_input["source_media"] = data.get_state("source_media") or []
        review_input["author_card"] = data.get_state("author_card")

    # 不挂工作台 session：for_each 并发 Review 会抢同一 session，易空响应/刷 No target data。
    result = await (
        Agently.create_agent(name="matrix-compose-review")
        .input({"package": review_input})
        .info({"snapshot": {"snapshot_id": snapshot.snapshot_id}, "intent": intent})
        .instruct(
            [
                "只输出 item_verdicts、package_summary、limitations，不要额外字段。",
                "对齐 brief / 人设口径；只能评审已有 draft_key。",
                "verdict 只能是 accept、revise 或 reject。",
                "不得把 skip 或 template_fallback 改回可发长文。",
                "改写支：文案须相对 source_post 明显改写，不得近重复整段原文。",
            ]
        )
        .output(
            {
                "item_verdicts": [
                    {
                        "draft_key": (str, "not_null"),
                        "verdict": (str, "not_null"),
                        "revised_text": str,
                        "notes": str,
                    }
                ],
                "package_summary": (str, "not_null"),
                "limitations": [str],
            },
            format="json",
        )
        .async_start(max_retries=1)
    )
    return ReviewOut.model_validate(result)


async def review_compose_drafts(
    data: TriggerFlowRuntimeData,
    drafts: list[GatedDraft],
    *,
    limitations: list[str] | None = None,
    brief_override: dict[str, Any] | None = None,
) -> tuple[list[GatedDraft], list[str], str]:
    """对一批 Gate 草稿跑 Review + re_gate，返回 (reviewed, extra_limitations, summary)。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    intent = str(data.get_state("intent") or "compose")
    merged_limits = list(limitations or [])
    offered_cta_urls = (
        list(snapshot.account.offered_cta_urls or []) if snapshot.account else []
    )
    review = await _run_compose_review_agent(
        data,
        drafts=drafts,
        limitations=merged_limits,
        intent=intent,
        snapshot=snapshot,
        brief_override=brief_override,
    )
    re_gate = _make_compose_re_gate(
        snapshot=snapshot,
        intent=intent,
        source_text=_compose_source_text(data),
        offered_cta_urls=offered_cta_urls,
    )
    reviewed, extra = await apply_review(drafts, review, re_gate=re_gate)
    return reviewed, extra, review.package_summary


async def review_compose_draft_item(
    data: TriggerFlowRuntimeData,
    draft: GatedDraft,
    *,
    limitations: list[str] | None = None,
) -> tuple[GatedDraft, list[str]]:
    """单条草稿 Review（创作 for_each 内使用）。"""
    reviewed, extra, _ = await review_compose_drafts(
        data,
        [draft],
        limitations=limitations,
        brief_override=_slim_brief_for_draft(data, draft),
    )
    return reviewed[0], extra


async def compose_review(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    task_id = str(request.get("task_id") or "")
    intent = str(data.get_state("intent") or "compose")
    limitations = list(cast(list[str], data.get_state("limitations") or []))

    task_results = [
        item for item in cast(list[Any], data.input or []) if isinstance(item, dict)
    ]
    drafts = [_coerce_gated_draft(item) for item in task_results]
    drafts.sort(key=lambda item: item.draft_key)
    events = data.get_resource("events")
    if events is not None and task_id:
        await events.publish(task_id, "stage.started", {"stage": "review"})

    try:
        reviewed, extra, summary = await review_compose_drafts(
            data,
            drafts,
            limitations=limitations,
        )
    except Exception as exc:
        trace.log(
            layer="business",
            event_type="business.matrix.reviewed",
            status="failed",
            subject_id=task_id,
            error=exc,
        )
        raise

    if extra:
        for note in extra:
            if note not in limitations:
                limitations.append(note)

    await data.async_set_state(
        "drafts", [item.model_dump(mode="json") for item in reviewed], emit=False
    )
    await data.async_set_state("limitations", limitations, emit=False)

    trace.log(
        layer="business",
        event_type="business.matrix.reviewed",
        status="completed",
        subject_id=task_id,
        output={"draft_count": len(reviewed), "intent": intent},
    )
    if events is not None and task_id:
        await events.publish(
            task_id,
            "stage.completed",
            {"stage": "review", "draft_count": len(reviewed), "intent": intent},
        )
    return {
        "drafts": [item.model_dump(mode="json") for item in reviewed],
        "limitations": limitations,
        "review_summary": summary,
        "rollup_status": rollup_status(reviewed),
    }


__all__ = [
    "compose_review",
    "review_compose_draft_item",
    "review_compose_drafts",
    "_coerce_gated_draft",
]
