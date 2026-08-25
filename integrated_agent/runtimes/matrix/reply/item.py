"""单条评论子图：写稿 → 评审；不合格 when(revise) 打回写稿，最多改写 3 次。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.host.models import (
    ReplyDraftOut,
    ReviewItemVerdict,
    WorkItem,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot, merged_forbidden_topics

MAX_REPLY_REWRITES = 3

REPLY_ITEM_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "brief": "runtime_data.brief",
        "request": "runtime_data.request",
        "limitations": "runtime_data.limitations",
    },
    "resources": {
        "trace": "resources.trace",
        "snapshot": "resources.snapshot",
        "session_id": "resources.session_id",
        "events": "resources.events",
    },
}
REPLY_ITEM_WRITE_BACK: TriggerFlowSubFlowWriteBack = {"value": "result.draft"}


def build_reply_item_flow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-reply-item-v1")
    flow.to(write_reply).to(review_reply)
    flow.when("revise").to(write_reply)
    return flow


async def write_reply(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    incoming = data.input if isinstance(data.input, dict) else {}
    work_item = WorkItem.model_validate(incoming.get("work_item") or incoming)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    comment = next(
        (
            item.text
            for item in snapshot.comments
            if item.comment_key == work_item.source_comment_key
        ),
        "",
    )
    work = work_item.model_dump(mode="json")
    result = await (
        Agently.create_agent(name="matrix-reply-draft")
        .input({"work_item": work, "repair": incoming.get("repair") or {}})
        .info(
            {
                "comment": comment,
                "max_chars": snapshot.platform.max_chars,
                "interaction": (
                    snapshot.interaction.model_dump(mode="json")
                    if snapshot.interaction
                    else {}
                ),
                "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
                "forbidden_terms": list(snapshot.policy.terms),
            }
        )
        .instruct(
            [
                "这是评论区回复，不是发帖写稿。对着 info.comment 说话，短、具体、答完即止。",
                "不要写钩子、系列预热、关注 CTA 或种草长文。",
                "遵守 info.interaction 的 voice_summary、goals、must_do、must_not 与 skip_guidance。",
                "不要使用 info.forbidden_terms 中的禁词，不要谈 info.forbidden_topics。",
                "先裁 reply 或 acknowledge，再写 draft_text。draft_text 必须非空。",
                "人身攻击、仇恨或无法核实的诱导：acknowledge，写一句克制收口，不要空正文。",
                "不要导关注或报名，不要换成别的身份。",
                "正文不得超过 info.max_chars。",
                "work_item_id 必须等于 input.work_item.work_item_id。",
                "若 input.repair 非空：按 repair.review_notes 重写，不要复读 repair.previous_text。",
            ]
        )
        .output(ReplyDraftOut, format="json")
        .async_start()
    )
    return {
        "work_item": work,
        "draft": ReplyDraftOut.model_validate(result).model_dump(mode="json"),
        "rewrites": int(incoming.get("rewrites") or 0),
    }


async def review_reply(data: TriggerFlowRuntimeData) -> dict[str, Any] | None:
    incoming = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    work_item = WorkItem.model_validate(incoming["work_item"])
    draft = ReplyDraftOut.model_validate(incoming["draft"])
    dumped = {
        "work_item": work_item.model_dump(mode="json"),
        "draft": draft.model_dump(mode="json"),
        "rewrites": int(incoming.get("rewrites") or 0),
    }
    if not draft.draft_text.strip():
        if dumped["rewrites"] < MAX_REPLY_REWRITES:
            await data.async_emit(
                "revise",
                {
                    "work_item": dumped["work_item"],
                    "rewrites": dumped["rewrites"] + 1,
                    "repair": {
                        "review_notes": "回复正文不能为空，写一句对着原评论的收口。",
                        "previous_text": "",
                    },
                },
            )
            return None
        draft = draft.model_copy(
            update={
                "draft_text": "这条我们不在评论区展开，有问题请走官方渠道。",
                "reply_decision": "acknowledge",
            }
        )
        dumped["draft"] = draft.model_dump(mode="json")
    else:
        snapshot = cast(Snapshot, data.require_resource("snapshot"))
        result = await (
            Agently.create_agent(name="matrix-reply-review")
            .input({"draft": dumped["draft"]})
            .info(
                {
                    "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
                    "forbidden_terms": list(snapshot.policy.terms),
                }
            )
            .instruct(
                [
                    "只按法规与禁词评审这条评论回复，不要改写成推文，也不要按获客写稿口径抬稿。",
                    "不得出现 info.forbidden_terms 中的用语。",
                    "不得触碰 info.forbidden_topics。",
                    "不得承诺收益、保本、疗效或绝对化效果。",
                    "正文必须非空。不合格则 revise，notes 写明违反哪条。",
                ]
            )
            .output(ReviewItemVerdict, format="json")
            .async_start()
        )
        verdict = ReviewItemVerdict.model_validate(result)
        if verdict.verdict == "revise" and dumped["rewrites"] < MAX_REPLY_REWRITES:
            await data.async_emit(
                "revise",
                {
                    "work_item": dumped["work_item"],
                    "rewrites": dumped["rewrites"] + 1,
                    "repair": {
                        "review_notes": (verdict.notes or "").strip(),
                        "previous_text": draft.draft_text,
                    },
                },
            )
            return None
    await data.async_set_state("draft", dumped, emit=False)
    return dumped


__all__ = [
    "REPLY_ITEM_CAPTURE",
    "REPLY_ITEM_WRITE_BACK",
    "build_reply_item_flow",
    "write_reply",
    "review_reply",
]
