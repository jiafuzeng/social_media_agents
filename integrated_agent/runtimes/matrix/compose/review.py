"""M6：写帖包级 review（compose / rewrite 共用）。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.draft_media import resolve_draft_cta
from integrated_agent.runtimes.matrix.host.constraints import (
    AhoCorasickMatcher,
    KB_CITE_RE,
    apply_constraint_gate,
)
from integrated_agent.runtimes.matrix.host.drafting import apply_review, rollup_status
from integrated_agent.runtimes.matrix.host.models import GatedDraft, ReviewOut


def _coerce_gated_draft(item: dict[str, Any]) -> GatedDraft:
    allowed = set(GatedDraft.model_fields.keys())
    return GatedDraft.model_validate({key: value for key, value in item.items() if key in allowed})
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


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

    review_input: dict[str, Any] = {
        "brief": data.get_state("brief"),
        "rewrite_plan_card": data.get_state("rewrite_plan_card"),
        "drafts": [item.model_dump(mode="json") for item in drafts],
        "limitations": limitations,
    }
    if intent == "rewrite":
        review_input["source_post"] = data.get_state("source_post")
        review_input["source_media"] = data.get_state("source_media") or []
        review_input["author_card"] = data.get_state("author_card")

    try:
        result = await (
            Agently.create_agent(name="matrix-compose-review")
            .activate_session(session_id=str(data.require_resource("session_id")))
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
            .async_start()
        )
        review = ReviewOut.model_validate(result)
    except Exception as exc:
        trace.log(
            layer="business",
            event_type="business.matrix.reviewed",
            status="failed",
            subject_id=task_id,
            error=exc,
        )
        raise

    matcher = AhoCorasickMatcher(snapshot.policy.terms)
    offered_cta_urls = list(snapshot.account.offered_cta_urls or []) if snapshot.account else []
    source_text = str(data.get_state("source_text") or "").strip()
    if not source_text:
        source_post = data.get_state("source_post")
        if isinstance(source_post, dict):
            source_text = str(source_post.get("text") or "").strip()

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
        display_text = resolve_draft_cta(
            gated.text,
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

    reviewed, extra = await apply_review(drafts, review, re_gate=re_gate)
    if extra:
        for note in extra:
            if note not in limitations:
                limitations.append(note)

    await data.async_set_state("drafts", [item.model_dump(mode="json") for item in reviewed], emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    trace.log(
        layer="business",
        event_type="business.matrix.reviewed",
        status="completed",
        subject_id=task_id,
        output={"draft_count": len(reviewed), "intent": intent},
    )
    return {
        "drafts": [item.model_dump(mode="json") for item in reviewed],
        "limitations": limitations,
        "review_summary": review.package_summary,
        "rollup_status": rollup_status(reviewed),
    }


__all__ = ["compose_review"]
