"""包级阶段：prelude → brief →（for_each 子图在 flow）→ package。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.host.drafting import rollup_status
from integrated_agent.runtimes.matrix.host.snapshots import (
    OFFERED_CLAIM_TYPES,
    TWITTER_PLATFORM_KEY,
    Snapshot,
    merged_forbidden_topics,
)
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog
from integrated_agent.runtimes.matrix.host.models import (
    BriefOut,
    GatedDraft,
    MatrixTaskRequest,
    WorkItem,
)


def _extend(target: list[str], notes: list[str]) -> None:
    for note in notes:
        if note and note not in target:
            target.append(note)


def _as_gated(item: dict[str, Any]) -> GatedDraft:
    work = item["work_item"] if isinstance(item.get("work_item"), dict) else {}
    draft = item["draft"] if isinstance(item.get("draft"), dict) else item
    text = str(draft.get("draft_text") or "").strip()
    decision = str(draft.get("reply_decision") or "reply")
    if decision == "skip" and text:
        decision = "acknowledge"
    empty = not text
    work_item_id = str(work.get("work_item_id") or draft.get("work_item_id") or "")
    return GatedDraft.model_validate(
        {
            "draft_key": f"d-{work_item_id}",
            "kind": "reply_comment",
            "platform_key": work.get("platform_key") or "",
            "source_comment_key": work.get("source_comment_key"),
            "degrade_op": "skip" if empty else "pass",
            "text": text,
            "rationale": draft.get("rationale") or "",
            "decision": "skip" if empty else decision,
            "risk_flags": list(draft.get("risk_flags") or []),
            "status": "skipped" if empty else "ready",
            "issues": [],
        }
    )


async def reply_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    payload = cast(dict[str, Any], data.input)
    request = MatrixTaskRequest.model_validate(payload["request"])
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await data.async_set_state("limitations", list(payload.get("limitations") or []), emit=False)
    await data.async_set_state("drafts", [], emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    events = data.get_resource("events")
    if events is not None:
        await events.publish(request.task_id, "stage.started", {"stage": "snapshot"})
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
    if events is not None:
        await events.publish(
            request.task_id,
            "stage.completed",
            {
                "stage": "snapshot",
                "snapshot_id": snapshot.snapshot_id,
                "comment_count": len(snapshot.comments),
            },
        )
    return payload


def _reply_item_limit(request: MatrixTaskRequest, snapshot: Snapshot) -> int:
    offered = len(snapshot.comments)
    cap = snapshot.platform.max_posts
    if request.reply_count is None:
        return offered
    if offered <= 1:
        return min(request.reply_count, cap)
    return min(request.reply_count, offered, cap)


def _fit_reply_items(
    items: list[WorkItem],
    *,
    max_items: int,
    offered_keys: set[str],
    expand: bool,
) -> tuple[list[WorkItem], list[str]]:
    kept = [item for item in items if item.source_comment_key in offered_keys]
    extra: list[str] = []
    if len(kept) != len(items):
        extra.append("dropped_out_of_thread_comments")
    if max_items and len(kept) > max_items:
        kept = kept[:max_items]
        extra.append(f"truncated_to_reply_count:{max_items}")
    elif expand and kept and max_items and len(kept) < max_items:
        seed = kept[0]
        while len(kept) < max_items:
            n = len(kept) + 1
            kept.append(seed.model_copy(update={"work_item_id": f"{seed.work_item_id}-v{n}"}))
        extra.append(f"expanded_to_reply_count:{max_items}")
    return kept, extra


async def reply_brief(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    request = MatrixTaskRequest.model_validate(data.get_state("request"))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    events = data.get_resource("events")
    if events is not None:
        await events.publish(request.task_id, "stage.started", {"stage": "brief"})
    max_items = _reply_item_limit(request, snapshot)
    single_comment = len(snapshot.comments) == 1
    if request.reply_count is not None and single_comment:
        count_rule = (
            f"必须正好生成 {max_items} 条 work_item，全部使用同一条 source_comment_key，"
            "角度不同，不要另造评论。"
        )
    elif request.reply_count is not None:
        count_rule = f"必须正好生成 {max_items} 条 work_item。"
    else:
        count_rule = "为 info.snapshot.comments 中每一条评论生成 work_item。"
    info = {
        "platform": snapshot.platform.model_dump(mode="json"),
        "guardrails": [item.model_dump(mode="json") for item in snapshot.guardrails],
        "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
        "interaction": (
            snapshot.interaction.model_dump(mode="json") if snapshot.interaction else {}
        ),
        "comments": [item.model_dump(mode="json") for item in snapshot.comments],
        "offered_claim_types": sorted(OFFERED_CLAIM_TYPES),
        "reply_count": max_items,
    }
    try:
        result = await (
            Agently.create_agent(name="matrix-reply-brief")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input({"text": request.text})
            .info({"snapshot": info})
            .instruct(
                [
                    "只做回复拆解，不要判断这是创作还是回复，不要输出 scenario。",
                    "input.text 若与已签发评论原文相同，把它当待回评论，不要当写帖主题。",
                    "requirements 写运营目标，不要复述本页 instruct。",
                    "拆解必须符合 info.snapshot.interaction 的互动规则：goals、must_do、must_not 与 skip_guidance。回复服务答疑和该回才回，不服务涨粉或关注引导。",
                    count_rule,
                    f"每条 work_item 的 kind 必须是 reply_comment，source_comment_key 必须是当前线程已签发的 comment_key，platform_key 必须是 {TWITTER_PLATFORM_KEY}。不要发明线程外的评论。",
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
        offered_keys = {item.comment_key for item in snapshot.comments}
        kept, extra = _fit_reply_items(
            brief.work_items,
            max_items=max_items,
            offered_keys=offered_keys,
            expand=single_comment and request.reply_count is not None,
        )
        if kept:
            brief = brief.model_copy(update={"work_items": kept})
        if extra:
            limitations = list(cast(list[str], data.get_state("limitations") or []))
            limitations.extend(extra)
            await data.async_set_state("limitations", limitations, emit=False)
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
    for item in brief.work_items:
        if events is not None:
            await events.publish(
                request.task_id,
                "work_item.ready",
                {"work_item_id": item.work_item_id, "kind": item.kind},
            )
    if events is not None:
        await events.publish(
            request.task_id,
            "stage.completed",
            {"stage": "brief", "work_item_count": len(brief.work_items)},
        )
    return [item.model_dump(mode="json") for item in brief.work_items]


async def reply_package(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = cast(dict[str, Any], data.get_state("request") or {})
    task_id = str(request.get("task_id") or "")
    raw = data.input if isinstance(data.input, list) else list(data.get_state("drafts") or [])
    items = [item for item in raw if isinstance(item, dict)]
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    drafts: list[GatedDraft] = []
    for item in items:
        _extend(
            limitations,
            [str(note) for note in (item.get("limitations") or []) if str(note).strip()],
        )
        drafts.append(_as_gated(item))
    drafts.sort(key=lambda item: item.draft_key)
    dumped = [item.model_dump(mode="json") for item in drafts]
    status = rollup_status(drafts)
    ready = sum(1 for item in dumped if str(item.get("text") or "").strip())
    skipped = sum(1 for item in dumped if item.get("degrade_op") == "skip")
    if ready == 0:
        summary = "未生成可发回复"
    elif skipped:
        summary = f"已生成 {ready} 条回复草稿，{skipped} 条跳过并已告知创作者"
    else:
        summary = f"已生成 {ready} 条回复草稿"
    package = {
        "status": status,
        "intent": "reply",
        "task_type": "reply_comment",
        "summary": summary,
        "drafts": dumped,
        "limitations": limitations,
    }
    await data.async_set_state("drafts", dumped, emit=False)
    await data.async_set_state("package", package, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("final_failed", status == "failed", emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.packaged",
        status="completed" if status != "failed" else "failed",
        subject_id=task_id,
        output=package,
    )
    events = data.get_resource("events")
    if events is not None and task_id:
        await events.publish(
            task_id,
            "stage.completed",
            {"stage": "package", "status": status, "draft_count": len(dumped)},
        )
    return package
