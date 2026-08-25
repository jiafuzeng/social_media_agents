from __future__ import annotations

from integrated_agent.runtimes.matrix.host.models import GatedDraft, ReviewOut


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
