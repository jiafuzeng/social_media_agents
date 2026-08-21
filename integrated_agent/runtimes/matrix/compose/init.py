"""M1：snapshot 已在 Flow 外绑定；这里只初始化本单 state。"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.host.models import MatrixTaskRequest
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


async def compose_init(data: TriggerFlowRuntimeData) -> dict[str, Any]:
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


__all__ = ["compose_init"]
