from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.models import (
    GatedDraft,
    MatrixTaskRequest,
    ReviewOut,
    WorkItem,
)

from ....constraints import AhoCorasickMatcher, apply_constraint_gate
from ....drafting import retrieve_and_gate_draft
from ....host import (
    BriefValidationError,
    apply_review,
    rollup_status,
    sanitize_brief,
    validate_brief,
)
from ....snapshots import Snapshot
from ....trace_log import TraceLog


async def compose_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    payload = cast(dict[str, Any], data.input)
    request = MatrixTaskRequest.model_validate(payload["request"])
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    limitations: list[str] = list(payload.get("limitations") or [])
    if request.need_trends:
        limitations.append("trends_unavailable")
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("drafts", [], emit=False)
    await data.async_set_state("evidence_cards", [], emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.snapshot_bound",
        status="completed",
        subject_id=request.task_id,
        facts={"snapshot_id": snapshot.snapshot_id, "need_trends": request.need_trends},
    )
    return payload


async def compose_brief(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    request = MatrixTaskRequest.model_validate(data.get_state("request"))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    model = data.require_resource("model")
    trace = cast(TraceLog, data.require_resource("trace"))
    info = {
        "platforms": [item.model_dump(mode="json") for item in snapshot.platforms],
        "brand": snapshot.brand.model_dump(mode="json"),
        "account": snapshot.account.model_dump(mode="json"),
        "trend_cards": [item.model_dump(mode="json") for item in snapshot.trend_cards],
        "offered_claim_types": sorted(snapshot.offered_claim_types()),
    }
    try:
        brief = await model.compose_brief(text=request.text, info=info)
        brief = sanitize_brief(
            brief, snapshot=snapshot, expected_kind="compose_post"
        )
        validate_brief(brief, snapshot=snapshot, expected_kind="compose_post")
    except (BriefValidationError, Exception) as exc:
        await data.async_set_state("final_failed", True, emit=False)
        trace.log(
            layer="business",
            event_type="business.matrix.briefed",
            status="failed",
            subject_id=request.task_id,
            error=exc,
        )
        raise
    await data.async_set_state("brief", brief.model_dump(mode="json"), emit=False)
    trace.log(
        layer="business",
        event_type="business.matrix.briefed",
        status="completed",
        subject_id=request.task_id,
        output=brief.model_dump(mode="json"),
        facts={"work_item_count": len(brief.work_items)},
    )
    return [item.model_dump(mode="json") for item in brief.work_items]


async def retrieve_and_compose_draft(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    work_item = WorkItem.model_validate(data.input)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    gated, cards = await retrieve_and_gate_draft(
        work_item=work_item,
        snapshot=snapshot,
        data_root=data.require_resource("data_root"),
        model=data.require_resource("model"),
        trace=cast(TraceLog, data.require_resource("trace")),
        kind="compose_post",
    )
    await data.async_append_state("drafts", gated.model_dump(mode="json"), emit=False)
    for card in cards:
        await data.async_append_state("evidence_cards", card, emit=False)
    return gated.model_dump(mode="json")


async def compose_review(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    model = data.require_resource("model")
    trace = cast(TraceLog, data.require_resource("trace"))
    drafts = [
        GatedDraft.model_validate(item)
        for item in cast(list[dict[str, Any]], data.get_state("drafts") or [])
    ]
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    try:
        review = await model.compose_review(
            package={
                "brief": data.get_state("brief"),
                "drafts": [item.model_dump(mode="json") for item in drafts],
                "limitations": limitations,
            },
            info={"snapshot_id": snapshot.snapshot_id},
        )
        review = ReviewOut.model_validate(review.model_dump(mode="json"))
    except Exception as exc:
        trace.log(
            layer="business",
            event_type="business.matrix.reviewed",
            status="failed",
            subject_id=cast(dict[str, Any], data.get_state("request"))["task_id"],
            error=exc,
        )
        raise
    matcher = AhoCorasickMatcher(snapshot.policy.terms)

    async def re_gate(current: GatedDraft, revised_text: str) -> GatedDraft:
        platform = snapshot.platform(current.platform_key)
        return await apply_constraint_gate(
            work_item_id=current.draft_key.removeprefix("d-"),
            kind=current.kind,
            platform_key=current.platform_key,
            source_comment_key=current.source_comment_key,
            text=revised_text,
            rationale=current.rationale,
            evidence_ids=current.evidence_ids,
            risk_flags=current.risk_flags,
            claim_types=[],
            reply_decision=None,
            proposed_degrade=None,
            max_chars=platform.max_chars,
            matcher=matcher,
            offered_refs=list(current.evidence_ids),
            retrieval_state="hits" if current.evidence_ids else "empty",
            templates=[item.model_dump(mode="json") for item in snapshot.templates],
        )

    drafts, extra = await apply_review(drafts, review, re_gate=re_gate)
    status = rollup_status(drafts)
    package = {
        "status": status,
        "task_type": "compose_post",
        "summary": review.package_summary,
        "drafts": [item.model_dump(mode="json") for item in drafts],
        "limitations": limitations + extra,
    }
    await data.async_set_state("package", package, emit=False)
    await data.async_set_state("final_failed", status == "failed", emit=False)
    task_id = cast(dict[str, Any], data.get_state("request"))["task_id"]
    trace.log(
        layer="business",
        event_type="business.matrix.reviewed",
        status="completed",
        subject_id=task_id,
        output={"status": status, "draft_count": len(drafts)},
    )
    trace.log(
        layer="business",
        event_type="business.matrix.packaged",
        status="completed" if status != "failed" else "failed",
        subject_id=task_id,
        output=package,
    )
    return package
