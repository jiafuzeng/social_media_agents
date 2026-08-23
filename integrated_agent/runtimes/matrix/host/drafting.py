from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from integrated_agent.config import KB_DEFAULT_EMBEDDING_PROFILE
from integrated_agent.runtimes.matrix.host.models import (
    ComposeDraftOut,
    GatedDraft,
    ReplyDraftOut,
    ReviewOut,
    WorkItem,
    WorkItemKind,
)

from .constraints import AhoCorasickMatcher, KB_CITE_RE, apply_constraint_gate
from .retrieval import RetrieveQuery, retrieve_cases
from .snapshots import Snapshot, merged_forbidden_topics
from .trace_log import TraceLog


DraftOnce = Callable[..., Awaitable[ComposeDraftOut | ReplyDraftOut]]


async def retrieve_and_gate_draft(
    *,
    work_item: WorkItem,
    snapshot: Snapshot,
    data_root,
    draft_once: DraftOnce,
    trace: TraceLog,
    kind: WorkItemKind,
    comment: dict[str, Any] | None = None,
    knowledge: Any | None = None,
    user_id: str | None = None,
    embedding_profile_id: str | None = None,
    extra_info: dict[str, Any] | None = None,
    offered_cta_urls: list[str] | None = None,
    offered_media_keys: list[str] | None = None,
    source_text: str = "",
) -> tuple[GatedDraft, list[dict[str, Any]], list[str]]:
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
        return skipped, [], []

    kb_cards: list[dict[str, Any]] = []
    limitations: list[str] = []
    profile_id = (embedding_profile_id or KB_DEFAULT_EMBEDDING_PROFILE).strip()
    if knowledge is not None and user_id:
        kb_query = " ".join(
            part for part in [work_item.goal, *work_item.talking_points] if part
        ).strip() or work_item.goal
        try:
            kb_cards = await knowledge.retrieve_draft_cards(
                user_id,
                kb_query,
                profile_id,
            )
            trace.log(
                layer="business",
                event_type="business.matrix.kb_retrieved",
                status="completed",
                subject_id=work_item.work_item_id,
                output={"card_count": len(kb_cards), "embedding_profile_id": profile_id},
                facts={"card_count": len(kb_cards), "embedding_profile_id": profile_id},
            )
        except Exception as exc:
            limitations.append("kb_retrieve_failed")
            trace.log(
                layer="business",
                event_type="business.matrix.kb_retrieved",
                status="failed",
                subject_id=work_item.work_item_id,
                error=exc,
                facts={"embedding_profile_id": profile_id},
            )

    platform = snapshot.platform
    matcher = AhoCorasickMatcher(snapshot.policy.terms)
    offered_kb_ids = [str(card.get("kb_id") or "") for card in kb_cards]
    info: dict[str, Any] = {
        "guardrails": [item.model_dump(mode="json") for item in snapshot.guardrails],
        "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
        "max_chars": platform.max_chars,
        "mention_rules": platform.mention_rules,
        "offered_refs": cards,
        "offered_kbs": kb_cards,
        "embedding_profile_id": profile_id,
        "policy": {
            "term_list_id": snapshot.policy.term_list_id,
            "ac_ready": snapshot.policy.ac_ready,
        },
        "comment": comment,
    }
    if snapshot.account is not None:
        info["account"] = snapshot.account.model_dump(mode="json")
    if snapshot.interaction is not None:
        info["interaction"] = snapshot.interaction.model_dump(mode="json")
    if extra_info:
        info.update(extra_info)
    draft = await draft_once(
        work_item=work_item.model_dump(mode="json"),
        info=info,
    )
    text = draft.draft_text
    reply_decision = (
        None if kind == "compose_post" else cast(ReplyDraftOut, draft).reply_decision
    )
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
        repaired = await draft_once(
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
        offered_kbs=offered_kb_ids,
        retrieval_state=retrieved.state,
        templates=[item.model_dump(mode="json") for item in snapshot.templates],
        rewrite_once=rewrite_once,
        offered_cta_urls=offered_cta_urls or [],
        offered_media_keys=offered_media_keys or [],
        source_text=source_text,
    )
    cited = [
        token
        for token in KB_CITE_RE.findall(gated.text or text)
        if token in offered_kb_ids
    ]
    for item in evidence_ids:
        if item in offered_kb_ids and item not in cited:
            cited.append(item)
    gated = gated.model_copy(update={"kb_ids": cited})
    trace.log(
        layer="business",
        event_type="business.matrix.gated",
        status="completed",
        subject_id=gated.draft_key,
        output=gated.model_dump(mode="json"),
        facts={"degrade_op": gated.degrade_op, "issues": gated.issues},
    )
    return gated, cards, limitations


def rollup_status(drafts: list[GatedDraft]) -> str:
    if not drafts:
        return "failed"
    ready = sum(item.status == "ready" for item in drafts)
    degraded = sum(item.status == "degraded" for item in drafts)
    skipped = sum(item.status == "skipped" for item in drafts)
    failed = sum(item.status == "failed" for item in drafts)
    if ready == len(drafts):
        return "completed"
    if ready == 0 and degraded == 0:
        return "failed"
    if (ready + degraded) > 0 and (skipped + failed) > 0:
        return "partial"
    return "completed"


async def apply_review(
    drafts: list[GatedDraft],
    review: ReviewOut,
    *,
    re_gate,
) -> tuple[list[GatedDraft], list[str]]:
    by_key = {item.draft_key: item for item in drafts}
    limitations = list(review.limitations)
    for verdict in review.item_verdicts:
        current = by_key.get(verdict.draft_key)
        if current is None:
            limitations.append(f"unknown_draft_key:{verdict.draft_key}")
            continue
        if current.degrade_op in {"skip", "template_fallback"}:
            continue
        if verdict.verdict == "revise" and verdict.revised_text:
            gated = await re_gate(current, verdict.revised_text)
            if gated.status in {"ready", "degraded"}:
                by_key[current.draft_key] = gated
            else:
                limitations.append(f"revise_rejected:{current.draft_key}")
        elif verdict.verdict == "reject" and current.degrade_op == "pass":
            skipped = current.model_copy(
                update={
                    "degrade_op": "skip",
                    "text": "",
                    "decision": "skip",
                    "status": "skipped",
                    "issues": current.issues + ["review_reject"],
                }
            )
            by_key[current.draft_key] = skipped
    ordered = [by_key[item.draft_key] for item in drafts]
    return ordered, limitations
