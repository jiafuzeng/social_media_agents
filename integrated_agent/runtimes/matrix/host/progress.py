"""把业务阶段投影成稳定 SSE 事件。缺 events 资源时静默跳过（脚本/单测直跑 Flow）。"""

from __future__ import annotations

from typing import Any

from agently import TriggerFlowRuntimeData

from .stores import InMemoryEventStore


def _optional_resource(data: TriggerFlowRuntimeData, key: str) -> Any:
    try:
        return data.require_resource(key)
    except Exception:
        return None


def _task_id(data: TriggerFlowRuntimeData) -> str:
    request = data.get_state("request") or {}
    if isinstance(request, dict):
        return str(request.get("task_id") or "")
    return ""


async def publish_progress(
    data: TriggerFlowRuntimeData,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    events = _optional_resource(data, "events")
    if not isinstance(events, InMemoryEventStore):
        return
    task_id = _task_id(data)
    if not task_id:
        return
    await events.publish(task_id, event_type, payload or {})


async def emit_stage(
    data: TriggerFlowRuntimeData,
    stage: str,
    *,
    started: bool,
    **values: Any,
) -> None:
    event_type = "stage.started" if started else "stage.completed"
    await publish_progress(data, event_type, {"stage": stage, **values})


__all__ = ["emit_stage", "publish_progress"]
