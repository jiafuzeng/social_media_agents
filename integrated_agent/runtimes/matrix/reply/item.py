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
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot

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
                "max_chars": snapshot.platform.max_chars,
                "comment": comment,
                "interaction": (
                    snapshot.interaction.model_dump(mode="json")
                    if snapshot.interaction
                    else {}
                ),
            }
        )
        .instruct(
            [
                "只为 info.comment 写一条回复，不要拆解任务，不要评审。",
                "遵守 info.interaction 的 voice_summary、must_do、must_not 与 skip_guidance。",
                "先裁 reply、acknowledge 或 skip，再写 draft_text。",
                "人身攻击、仇恨或无法核实的诱导：skip，且 draft_text 必须为空。",
                "用互动口吻回复，不要导关注或报名，不要换成别的身份。",
                "不得承诺稳赚、翻倍或疗效。正文不得超过 info.max_chars。",
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
    if draft.reply_decision != "skip" and draft.draft_text.strip():
        result = await (
            Agently.create_agent(name="matrix-reply-review")
            .input({"draft": dumped["draft"]})
            .instruct("判断这条回复是否满足要求。不合格则 revise，并在 notes 写明原因。")
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
