"""队列 Worker 侧的写帖入口。不经过回评。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from integrated_agent.runtimes.matrix.host.models import MatrixTaskRequest

from .flow import run_compose


def make_analyze_compose(
    *,
    logs_root: Path,
    data_root: Path,
    knowledge: Any | None = None,
):
    async def analyze_compose(request: MatrixTaskRequest) -> dict[str, Any]:
        if request.scenario != "compose":
            raise ValueError("analyze_compose requires scenario=compose")
        output_directory = logs_root / request.task_id
        run = await run_compose(
            request,
            data_root=data_root,
            output_directory=output_directory,
            knowledge=knowledge,
        )
        run["trace_ref"] = (output_directory / "run.json").resolve().as_uri()
        return run

    return analyze_compose
