"""队列 Worker 侧的回评入口。不经过写帖。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from integrated_agent.runtimes.matrix.host.models import MatrixTaskRequest

from .flow import run_reply


def make_analyze_reply(
    *,
    logs_root: Path,
    data_root: Path,
    knowledge: Any | None = None,
    events: Any | None = None,
):
    async def analyze_reply(request: MatrixTaskRequest) -> dict[str, Any]:
        if request.scenario != "reply":
            raise ValueError("analyze_reply requires scenario=reply")
        output_directory = logs_root / request.task_id
        run = await run_reply(
            request,
            data_root=data_root,
            output_directory=output_directory,
            knowledge=knowledge,
            events=events,
        )
        run["trace_ref"] = (output_directory / "run.json").resolve().as_uri()
        return run

    return analyze_reply
