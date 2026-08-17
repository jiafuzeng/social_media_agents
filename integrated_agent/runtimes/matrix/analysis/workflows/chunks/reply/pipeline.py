from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.models import (
    BriefOut,
    GatedDraft,
    MatrixTaskRequest,
    ReplyDraftOut,
    ReviewOut,
    WorkItem,
)

from ....constraints import AhoCorasickMatcher, apply_constraint_gate
from ....drafting import apply_review, retrieve_and_gate_draft, rollup_status
from ....snapshots import (
    OFFERED_CLAIM_TYPES,
    TWITTER_PLATFORM_KEY,
    Snapshot,
    merged_forbidden_topics,
)
from ....trace_log import TraceLog


async def reply_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    payload = cast(dict[str, Any], data.input)
    request = MatrixTaskRequest.model_validate(payload["request"])
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await data.async_set_state("limitations", list(payload.get("limitations") or []), emit=False)
    await data.async_set_state("drafts", [], emit=False)
    await data.async_set_state("evidence_cards", [], emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.snapshot_bound",
        status="completed",
        subject_id=request.task_id,
        facts={
            "snapshot_id": snapshot.snapshot_id,
            "comment_count": len(snapshot.comments),
        },
    )
    return payload


async def reply_brief(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    request = MatrixTaskRequest.model_validate(data.get_state("request"))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    info = {
        "platform": snapshot.platform.model_dump(mode="json"),
        "guardrails": [item.model_dump(mode="json") for item in snapshot.guardrails],
        "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
        "account": snapshot.account.model_dump(mode="json"),
        "comments": [item.model_dump(mode="json") for item in snapshot.comments],
        "offered_claim_types": sorted(OFFERED_CLAIM_TYPES),
    }
    try:
        result = await (
            Agently.create_agent(name="matrix-reply-brief")
            .input({"text": request.text})
            .info({"snapshot": info})
            .instruct(
                [
                    "只做回复拆解，不要判断这是创作还是回复，不要输出 scenario。",
                    "requirements 写运营目标，不要复述本页 instruct。",
                    "拆解必须符合 info.snapshot.account 的人设与 reply_stance；回复也服务于口碑和涨粉，但不为互动而放松 skip。",
                    f"为 info.snapshot.comments 中每一条评论生成 work_item，kind 必须是 reply_comment，source_comment_key 必须是已签发的 comment_key，platform_key 必须是 {TWITTER_PLATFORM_KEY}。",
                    "每个 requirement 必须被引用；claim_types 只能从 info.snapshot.offered_claim_types 选取，不要把 template_key 写进去。",
                    "不写回复正文。",
                ]
            )
            .output(
                {
                    "normalized_brief": (str, "not_null"),
                    "requirements": (
                        [
                            {
                                "requirement_id": (str, "not_null"),
                                "description": (str, "not_null"),
                            }
                        ],
                        "not_null",
                    ),
                    "work_items": (
                        [
                            {
                                "work_item_id": (str, "not_null"),
                                "kind": (str, "必须是 reply_comment", "not_null"),
                                "requirement_ids": ([str], "not_null"),
                                "platform_key": (str, "not_null"),
                                "source_comment_key": (str, "not_null"),
                                "goal": (str, "not_null"),
                                "talking_points": [str],
                                "claim_types": [str],
                            }
                        ],
                        "not_null",
                    ),
                },
                format="json",
            )
            .async_start()
        )
        brief = BriefOut.model_validate(result)
    except Exception as exc:
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


async def retrieve_and_reply_draft(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    work_item = WorkItem.model_validate(data.input)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    comment = next(
        (
            item.model_dump(mode="json")
            for item in snapshot.comments
            if item.comment_key == work_item.source_comment_key
        ),
        None,
    )
    async def reply_draft(
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ReplyDraftOut:
        result = await (
            Agently.create_agent(name="matrix-reply-draft")
            .input({"work_item": work_item, "repair": repair or {}})
            .info({"context": info})
            .instruct(
                [
                    "先裁 reply、acknowledge 或 skip，再写正文。人身攻击、仇恨或无法核实的诱导默认 skip。",
                    "用 info.context.account 的声量与 reply_stance 回复，优先可被围观的帮助和关注引导，不要换成别的身份。",
                    "skip 时 draft_text 必须是空串。不得输出 escalate。",
                    "证据只能引用 info.context.offered_refs 的 ref_id；不得承诺稳赚或治愈。",
                    "正文长度不得超过 info.context.max_chars。",
                ]
            )
            .output(
                {
                    "work_item_id": (str, "not_null"),
                    "stance_assessment": (str, "not_null"),
                    "reply_decision": (str, "reply、acknowledge 或 skip", "not_null"),
                    "claim_types": [str],
                    "risk_flags": [str],
                    "draft_text": str,
                    "rationale": (str, "not_null"),
                    "evidence_ids": [str],
                    "proposed_degrade": str,
                },
                format="json",
            )
            .async_start()
        )
        return ReplyDraftOut.model_validate(result)

    gated, cards = await retrieve_and_gate_draft(
        work_item=work_item,
        snapshot=snapshot,
        data_root=data.require_resource("data_root"),
        draft_once=reply_draft,
        trace=cast(TraceLog, data.require_resource("trace")),
        kind="reply_comment",
        comment=comment,
    )
    await data.async_append_state("drafts", gated.model_dump(mode="json"), emit=False)
    for card in cards:
        await data.async_append_state("evidence_cards", card, emit=False)
    return gated.model_dump(mode="json")


async def reply_review(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    drafts = [
        GatedDraft.model_validate(item)
        for item in cast(list[dict[str, Any]], data.get_state("drafts") or [])
    ]
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    try:
        result = await (
            Agently.create_agent(name="matrix-reply-review")
            .input(
                {
                    "package": {
                        "brief": data.get_state("brief"),
                        "drafts": [item.model_dump(mode="json") for item in drafts],
                        "limitations": limitations,
                    }
                }
            )
            .info({"snapshot": {"snapshot_id": snapshot.snapshot_id}})
            .instruct(
                [
                    "只输出 item_verdicts、package_summary、limitations，不要额外字段。",
                    "对齐官方语气，只能评审已有 draft_key。",
                    "verdict 只能是 accept、revise 或 reject。",
                    "不得把 skip 改回可发回复，也不得放宽 template_fallback。",
                    "攻击项必须保持空正文。",
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
            subject_id=cast(dict[str, Any], data.get_state("request"))["task_id"],
            error=exc,
        )
        raise
    matcher = AhoCorasickMatcher(snapshot.policy.terms)

    async def re_gate(current: GatedDraft, revised_text: str) -> GatedDraft:
        platform = snapshot.platform
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
            reply_decision="reply",
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
        "task_type": "reply_comment",
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
