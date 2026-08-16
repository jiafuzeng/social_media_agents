"""COMPOSE_FLOW 的四个可观察阶段：prelude → brief → 按 work_item 检索写稿 → review 打包。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.models import (
    BriefOut,
    ComposeDraftOut,
    GatedDraft,
    MatrixTaskRequest,
    ReviewOut,
    WorkItem,
)

from ....constraints import AhoCorasickMatcher, apply_constraint_gate
from ....drafting import apply_review, retrieve_and_gate_draft, rollup_status
from ....snapshots import OFFERED_CLAIM_TYPES, Snapshot
from ....trace_log import TraceLog


async def compose_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """快照已在 run_compose 绑好。这里只初始化 state；P0 不真抓热帖。"""

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
    """模型只拆 work_item，不判 scenario。返回列表供 for_each 扇出。"""

    request = MatrixTaskRequest.model_validate(data.get_state("request"))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    info = {
        "platforms": [item.model_dump(mode="json") for item in snapshot.platforms],
        "brand": snapshot.brand.model_dump(mode="json"),
        "account": snapshot.account.model_dump(mode="json"),
        "trend_cards": [item.model_dump(mode="json") for item in snapshot.trend_cards],
        "offered_claim_types": sorted(OFFERED_CLAIM_TYPES),
    }
    try:
        result = await (
            Agently.create_agent(name="matrix-compose-brief")
            .input({"text": request.text})
            .info({"snapshot": info})
            .instruct(
                [
                    "只做创作拆解，不要判断这是创作还是回复，不要输出 scenario。",
                    "为 info.snapshot.platforms 中每一个 platform_key 生成一条 work_item，kind 必须是 compose_post。",
                    "claim_types 只能从 info.snapshot.offered_claim_types 选取，例如 format；不要把 template_key、品牌名或人设词写进去。",
                    "每个 requirement 必须被至少一条 work_item 引用；platform_key 只能使用已提供平台。",
                    "talking_points 保持矩阵口径一致，按平台只改形态不改主张。",
                    "不写正文，不引用评论，不要设置 source_comment_key。",
                ]
            )
            .output(
                {
                    "normalized_brief": (str, "保留主题与矩阵要求的改写", "not_null"),
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
                                "kind": (str, "必须是 compose_post", "not_null"),
                                "requirement_ids": ([str], "not_null"),
                                "platform_key": (str, "not_null"),
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


async def retrieve_and_compose_draft(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """for_each 的每一条：先 RetrieveCases，再写稿，最后硬门 Gate。"""

    work_item = WorkItem.model_validate(data.input)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))

    async def compose_draft(
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ComposeDraftOut:
        result = await (
            Agently.create_agent(name="matrix-compose-draft")
            .input({"work_item": work_item, "repair": repair or {}})
            .info({"context": info})
            .instruct(
                [
                    "为这一条平台稿写正文和评理，不要输出 reply_decision。",
                    "不得承诺稳赚、治愈、保本或未授权最高级；证据只能引用 info.context.offered_refs 的 ref_id。",
                    "正文长度不得超过 info.context.max_chars。",
                    "skip 时正文必须空串。若 repair.issues 含 over_limit，删减卖点直到不超限。",
                    "评理必须说明依据和未写的内容。",
                ]
            )
            .output(
                {
                    "work_item_id": (str, "not_null"),
                    "stance_assessment": (str, "not_null"),
                    "claim_types": [str],
                    "risk_flags": [str],
                    "draft_text": str,
                    "rationale": (str, "not_null"),
                    "evidence_ids": [str],
                    "proposed_degrade": (
                        str,
                        "pass、rewrite_safe、template_fallback、skip 或空",
                    ),
                },
                format="json",
            )
            .async_start()
        )
        return ComposeDraftOut.model_validate(result)

    gated, cards = await retrieve_and_gate_draft(
        work_item=work_item,
        snapshot=snapshot,
        data_root=data.require_resource("data_root"),
        draft_once=compose_draft,
        trace=cast(TraceLog, data.require_resource("trace")),
        kind="compose_post",
    )
    await data.async_append_state("drafts", gated.model_dump(mode="json"), emit=False)
    for card in cards:
        await data.async_append_state("evidence_cards", card, emit=False)
    return gated.model_dump(mode="json")


async def compose_review(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """口径对齐。skip/template 不可被 Review 回抬；改写正文必须再过 Gate。"""

    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    drafts = [
        GatedDraft.model_validate(item)
        for item in cast(list[dict[str, Any]], data.get_state("drafts") or [])
    ]
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    try:
        result = await (
            Agently.create_agent(name="matrix-compose-review")
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
                    "对齐矩阵口径，只能评审已有 draft_key。",
                    "不得把 skip 或 template_fallback 改回可发正文。",
                    "revise 只允许收紧表述，不能放宽硬门。",
                ]
            )
            .output(
                {
                    "item_verdicts": [
                        {
                            "draft_key": (str, "not_null"),
                            "verdict": (str, "accept、revise 或 reject", "not_null"),
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
