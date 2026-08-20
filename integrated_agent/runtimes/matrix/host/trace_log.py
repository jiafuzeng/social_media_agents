from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal


TraceLayer = Literal["business", "framework"]
TraceStatus = Literal["started", "completed", "failed", "retrying", "observed"]
_TRACE_LAYERS = {"business", "framework"}
_TRACE_STATUSES = {"started", "completed", "failed", "retrying", "observed"}


def bounded(
    value: Any,
    *,
    string_limit: int = 2_000,
    item_limit: int = 50,
    depth: int = 0,
) -> Any:
    if depth >= 8:
        return "<max-depth>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): bounded(
                item,
                string_limit=string_limit,
                item_limit=item_limit,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:item_limit]
        }
    if isinstance(value, (list, tuple)):
        return [
            bounded(
                item,
                string_limit=string_limit,
                item_limit=item_limit,
                depth=depth + 1,
            )
            for item in value[:item_limit]
        ]
    if isinstance(value, str) and len(value) > string_limit:
        return value[:string_limit] + "…"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return bounded(
            model_dump(mode="json"),
            string_limit=string_limit,
            item_limit=item_limit,
            depth=depth + 1,
        )
    return str(value)


class TraceLog:
    def __init__(self, task_id: str, execution_id: str) -> None:
        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        if not execution_id.strip():
            raise ValueError("execution_id must not be empty")
        self.task_id = task_id
        self.execution_id = execution_id
        self.events: list[dict[str, Any]] = []

    def log(
        self,
        *,
        layer: TraceLayer,
        event_type: str,
        status: TraceStatus,
        subject_id: str | None = None,
        input: Any = None,
        output: Any = None,
        facts: Mapping[str, Any] | None = None,
        error: Any = None,
    ) -> dict[str, Any]:
        if layer not in _TRACE_LAYERS:
            raise ValueError(f"unsupported trace layer: {layer}")
        if status not in _TRACE_STATUSES:
            raise ValueError(f"unsupported trace status: {status}")
        payload = {
            "event_id": f"{self.task_id}:event:{len(self.events) + 1:04d}",
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "layer": layer,
            "event_type": event_type,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "subject_id": subject_id,
            "input": bounded(input) if input is not None else None,
            "output": bounded(output) if output is not None else None,
            "facts": bounded(dict(facts or {})),
            "error": (
                {"type": type(error).__name__, "message": str(error)}
                if isinstance(error, BaseException)
                else error
            ),
        }
        self.events.append(payload)
        return payload


def save_run(run: dict[str, Any], output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    serializable = {
        str(key): bounded(value)
        for key, value in run.items()
        if key != "events"
    }
    events = [bounded(event) for event in run.get("events", [])]
    serializable["events"] = events
    (output_directory / "run.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        + ("\n" if events else ""),
        encoding="utf-8",
    )
