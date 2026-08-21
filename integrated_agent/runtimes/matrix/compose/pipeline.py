"""写帖 TriggerFlow：M1 Snapshot 入态 + M2 Route 分流。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.host.models import (
    MatrixTaskRequest,
    RouteIntentOut,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


async def compose_init(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """M1：snapshot 已在 Flow 外绑定；这里只初始化本单 state。"""
    payload = cast(dict[str, Any], data.input)
    request = MatrixTaskRequest.model_validate(payload["request"])
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await data.async_set_state("limitations", [], emit=False)
    await data.async_set_state("drafts", [], emit=False)
    await data.async_set_state("evidence_cards", [], emit=False)
    await data.async_set_state("tweet_cards", [], emit=False)
    await data.async_set_state("trend_cards", [], emit=False)
    await data.async_set_state("work_items", [], emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.snapshot_bound",
        status="completed",
        subject_id=request.task_id,
        facts={"snapshot_id": snapshot.snapshot_id, "need_trends": request.need_trends},
    )
    return payload


async def compose_route(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """M2：模型意图识别。成功 emit compose|rewrite；失败 emit PACKAGE。"""
    request = cast(dict[str, Any], data.get_state("request") or {})
    text = str(request.get("text") or "")
    task_id = str(request.get("task_id") or "")
    trace = cast(TraceLog, data.require_resource("trace"))

    try:
        result = await (
            Agently.create_agent(name="matrix-compose-route-intent")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input({"text": text})
            .info(
                {
                    "job": "识别用户输入文本的意图：走创作还是改写。只签发一个确定结果，并写明理由。",
                }
            )
            .instruct(
                [
                    "先看用户原文里有没有「被写对象」，再给出唯一意图。必须选 compose 或 rewrite 之一，不要骑墙。",
                    "改写特征：要把某一条现成帖/粘贴正文改口吻、转写、改成我们的。创作特征：对标结构自己写、只给主题要新写一组。",
                    "有链接或「参考这条写」倾向改写；「对标这条的结构自己写」倾向创作。",
                    "reason 用一两句话指出原文线索；intent 只填 compose 或 rewrite。",
                    "rewrite 且原文是帖：source_kind 为 url 或 tweet_id，source_anchor 填原文里的 tweet_id。paste 时 source_anchor 留空。compose：source_kind 多为 none。",
                    "不得发明原文没有的 tweet_id 或 handle。user_instruction 填去掉原文或链接后的任务说明。",
                ]
            )
            .output(
                {
                    "reason": (str, "判定理由，须引用原文线索", "not_null"),
                    "intent": (str, "compose 或 rewrite，唯一结果", "not_null"),
                    "source_kind": (str, "paste、url、tweet_id、handle 或 none", "not_null"),
                    "source_anchor": (str, "tweet_id 或 handle，没有则空"),
                    "user_instruction": (str, "去掉原文后的任务说明"),
                    "confidence": (str, "high 或 low", "not_null"),
                },
                format="json",
            )
            .async_start()
        )
        routed = RouteIntentOut.model_validate(result)
    except Exception as exc:
        package = {
            "status": "failed",
            "intent": None,
            "task_type": "compose_post",
            "summary": "意图识别失败。",
            "drafts": [],
            "limitations": [f"route_intent_error:{type(exc).__name__}"],
        }
        await data.async_set_state("package", package, emit=False)
        await data.async_set_state("final_failed", True, emit=False)
        trace.log(
            layer="business",
            event_type="business.matrix.routed",
            status="failed",
            subject_id=task_id,
            facts={"limitations": package["limitations"]},
        )
        await data.async_emit("PACKAGE", package)
        return package

    instruction = str(routed.user_instruction or "").strip() or text.strip()
    await data.async_set_state("intent", routed.intent, emit=False)
    await data.async_set_state("source_kind", routed.source_kind, emit=False)
    await data.async_set_state("source_anchor", str(routed.source_anchor or "").strip(), emit=False)
    await data.async_set_state("user_instruction", instruction, emit=False)
    trace.log(
        layer="business",
        event_type="business.matrix.routed",
        status="completed",
        subject_id=task_id,
        facts={
            "intent": routed.intent,
            "reason": routed.reason,
            "source_kind": routed.source_kind,
            "source_anchor": routed.source_anchor,
        },
    )
    await data.async_emit(str(routed.intent), {"intent": routed.intent})
    return {
        "intent": routed.intent,
        "reason": routed.reason,
        "source_kind": routed.source_kind,
        "source_anchor": str(routed.source_anchor or "").strip(),
        "user_instruction": instruction,
    }


async def compose_branch_hold(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """M3+ 尚未接线：分流成功后暂以空草稿包收尾，保留 intent。"""
    intent = cast(str | None, data.get_state("intent"))
    package = {
        "status": "completed",
        "intent": intent,
        "task_type": "compose_post",
        "summary": "",
        "drafts": [],
        "limitations": list(cast(list[str], data.get_state("limitations") or [])),
    }
    await data.async_set_state("package", package, emit=False)
    return package


async def compose_package(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    existing = data.get_state("package")
    if isinstance(existing, dict):
        package = existing
    else:
        package = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    await data.async_set_state("package", package, emit=False)
    return package


__all__ = [
    "compose_branch_hold",
    "compose_init",
    "compose_package",
    "compose_route",
]
