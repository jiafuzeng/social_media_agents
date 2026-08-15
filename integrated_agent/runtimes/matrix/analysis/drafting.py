from __future__ import annotations

from typing import Any

from integrated_agent.runtimes.matrix.models import (
    ComposeDraftOut,
    GatedDraft,
    ReplyDraftOut,
    WorkItem,
    WorkItemKind,
)

from .constraints import AhoCorasickMatcher, apply_constraint_gate
from .retrieval import RetrieveQuery, retrieve_cases
from .snapshots import Snapshot
from .trace_log import TraceLog


async def retrieve_and_gate_draft(
    *,
    work_item: WorkItem,
    snapshot: Snapshot,
    data_root,
    model,
    trace: TraceLog,
    kind: WorkItemKind,
    comment: dict[str, Any] | None = None,
) -> tuple[GatedDraft, list[dict[str, Any]]]:
    query = RetrieveQuery(
        work_item_id=work_item.work_item_id,
        platform_key=work_item.platform_key,
        claim_types=list(work_item.claim_types),
        goal=work_item.goal,
    )
    retrieved = retrieve_cases(query, data_root=data_root)
    trace.log(
        layer="business",
        event_type="business.matrix.retrieved",
        status="failed" if retrieved.state == "failed" else "completed",
        subject_id=work_item.work_item_id,
        input=query.model_dump(mode="json"),
        output={"state": retrieved.state, "card_count": len(retrieved.cards)},
        facts={"state": retrieved.state, "card_count": len(retrieved.cards)},
    )
    cards = [card.model_dump(mode="json") for card in retrieved.cards]
    if retrieved.state == "failed":
        skipped = GatedDraft(
            draft_key=f"d-{work_item.work_item_id}",
            kind=kind,
            platform_key=work_item.platform_key,
            source_comment_key=work_item.source_comment_key,
            degrade_op="skip",
            degrade_trace=[],
            text="",
            rationale="案例检索失败，本项跳过。",
            decision="skip",
            evidence_ids=[],
            risk_flags=["retrieval_failed"],
            status="failed",
            issues=["retrieval_failed"],
        )
        return skipped, []

    platform = snapshot.platform(work_item.platform_key)
    matcher = AhoCorasickMatcher(snapshot.policy.terms)
    info = {
        "account": snapshot.account.model_dump(mode="json"),
        "brand": snapshot.brand.model_dump(mode="json"),
        "max_chars": platform.max_chars,
        "mention_rules": platform.mention_rules,
        "offered_refs": cards,
        "policy": {
            "term_list_id": snapshot.policy.term_list_id,
            "ac_ready": snapshot.policy.ac_ready,
        },
        "comment": comment,
    }
    if kind == "compose_post":
        draft = await model.compose_draft(
            work_item=work_item.model_dump(mode="json"),
            info=info,
        )
        draft = ComposeDraftOut.model_validate(
            {**draft.model_dump(mode="json"), "work_item_id": work_item.work_item_id}
        )
        text = draft.draft_text
        reply_decision = None
        evidence_ids = draft.evidence_ids
        rationale = draft.rationale
        risk_flags = draft.risk_flags
        claim_types = draft.claim_types or work_item.claim_types
        proposed = draft.proposed_degrade
    else:
        draft = await model.reply_draft(
            work_item=work_item.model_dump(mode="json"),
            info=info,
        )
        draft = ReplyDraftOut.model_validate(
            {**draft.model_dump(mode="json"), "work_item_id": work_item.work_item_id}
        )
        text = draft.draft_text
        reply_decision = draft.reply_decision
        evidence_ids = draft.evidence_ids
        rationale = draft.rationale
        risk_flags = draft.risk_flags
        claim_types = draft.claim_types or work_item.claim_types
        proposed = draft.proposed_degrade

    trace.log(
        layer="business",
        event_type="business.matrix.drafted",
        status="completed",
        subject_id=work_item.work_item_id,
        output={"work_item_id": work_item.work_item_id},
        facts={"reply_decision": reply_decision},
    )

    async def rewrite_once(issues: list[str]) -> str:
        repair = {"issues": issues, "previous_text": text}
        if kind == "compose_post":
            repaired = await model.compose_draft(
                work_item=work_item.model_dump(mode="json"),
                info=info,
                repair=repair,
            )
            return repaired.draft_text
        repaired = await model.reply_draft(
            work_item=work_item.model_dump(mode="json"),
            info=info,
            repair=repair,
        )
        return repaired.draft_text

    gated = await apply_constraint_gate(
        work_item_id=work_item.work_item_id,
        kind=kind,
        platform_key=work_item.platform_key,
        source_comment_key=work_item.source_comment_key,
        text=text,
        rationale=rationale,
        evidence_ids=evidence_ids,
        risk_flags=risk_flags,
        claim_types=claim_types,
        reply_decision=reply_decision,
        proposed_degrade=proposed,
        max_chars=platform.max_chars,
        matcher=matcher,
        offered_refs=[card["ref_id"] for card in cards],
        retrieval_state=retrieved.state,
        templates=[item.model_dump(mode="json") for item in snapshot.templates],
        rewrite_once=rewrite_once,
    )
    trace.log(
        layer="business",
        event_type="business.matrix.gated",
        status="completed",
        subject_id=gated.draft_key,
        output=gated.model_dump(mode="json"),
        facts={"degrade_op": gated.degrade_op, "issues": gated.issues},
    )
    return gated, cards
