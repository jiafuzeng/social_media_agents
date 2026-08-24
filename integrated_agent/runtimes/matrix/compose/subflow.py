"""子图接线：capture / write_back 由字段名生成，父图只保留拓扑。"""

from __future__ import annotations

from typing import Any, Iterable, NamedTuple

from agently import TriggerFlow
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

CORE_RESOURCES = ("trace", "snapshot", "events")
DRAFT_RESOURCES = (
    "trace",
    "snapshot",
    "data_root",
    "knowledge",
    "kb_user_id",
    "events",
)
ROUTE_STATE = (
    "request",
    "intent",
    "source_kind",
    "source_anchor",
    "user_instruction",
    "limitations",
)


class SubFlow(NamedTuple):
    flow: TriggerFlow
    capture: TriggerFlowSubFlowCapture
    write_back: TriggerFlowSubFlowWriteBack


def capture_state(
    *keys: str,
    resources: Iterable[str] = CORE_RESOURCES,
) -> TriggerFlowSubFlowCapture:
    return {
        "input": "value",
        "runtime_data": {key: f"runtime_data.{key}" for key in keys},
        "resources": {key: f"resources.{key}" for key in resources},
    }


def write_back_result(*keys: str) -> TriggerFlowSubFlowWriteBack:
    return {"runtime_data": {key: f"result.{key}" for key in keys}}


def attach_subflows(process: Any, *subflows: SubFlow) -> Any:
    for item in subflows:
        process = process.to_sub_flow(
            item.flow,
            capture=item.capture,
            write_back=item.write_back,
        )
    return process


__all__ = [
    "CORE_RESOURCES",
    "DRAFT_RESOURCES",
    "ROUTE_STATE",
    "SubFlow",
    "attach_subflows",
    "capture_state",
    "write_back_result",
]
