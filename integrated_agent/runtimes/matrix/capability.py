"""矩阵任务入口：按已绑定 scenario 分发到写帖或回评模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from integrated_agent.runtimes.matrix.compose import run_compose
from integrated_agent.runtimes.matrix.models import MatrixTaskRequest
from integrated_agent.runtimes.matrix.reply import run_reply


class MatrixAnalysisCapability:
    """队列 Worker 用的分析入口。本身不含写帖/回评业务节点。"""

    def __init__(
        self,
        *,
        logs_root: Path,
        data_root: Path,
        knowledge: Any | None = None,
    ) -> None:
        self.logs_root = logs_root
        self.data_root = data_root
        self.knowledge = knowledge

    async def analyze(self, request: MatrixTaskRequest) -> dict[str, Any]:
        output_directory = self.logs_root / request.task_id
        run = await run_matrix(
            request,
            data_root=self.data_root,
            output_directory=output_directory,
            knowledge=self.knowledge,
        )
        run["trace_ref"] = (output_directory / "run.json").resolve().as_uri()
        return run


async def run_matrix(
    request: MatrixTaskRequest,
    *,
    data_root: Path,
    output_directory: Path,
    knowledge: Any | None = None,
) -> dict[str, Any]:
    """入口已绑定 scenario。这里只分发，不把两套 Flow 画成一张图。"""

    if request.scenario == "compose":
        return await run_compose(
            request,
            data_root=data_root,
            output_directory=output_directory,
            knowledge=knowledge,
        )
    return await run_reply(
        request,
        data_root=data_root,
        output_directory=output_directory,
        knowledge=knowledge,
    )


__all__ = [
    "MatrixAnalysisCapability",
    "run_matrix",
]
