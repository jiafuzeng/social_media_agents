from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from integrated_agent.runtimes.matrix.models import MatrixTaskRequest

from .agently_model import AgentlyMatrixModel
from .workflows.compose_flow import run_compose
from .workflows.reply_flow import run_reply


RunMatrix = Callable[..., Awaitable[dict[str, Any]]]


class MatrixAnalysisCapability:
    def __init__(
        self,
        *,
        logs_root: Path,
        data_root: Path,
        model: Any | None = None,
        runner: RunMatrix | None = None,
    ) -> None:
        self.logs_root = logs_root
        self.data_root = data_root
        self.model = model if model is not None else AgentlyMatrixModel()
        self.runner = runner or run_matrix

    async def analyze(self, request: MatrixTaskRequest) -> dict[str, Any]:
        output_directory = self.logs_root / request.task_id
        run = await self.runner(
            request,
            data_root=self.data_root,
            output_directory=output_directory,
            model=self.model,
        )
        run["trace_ref"] = (output_directory / "run.json").resolve().as_uri()
        return run


async def run_matrix(
    request: MatrixTaskRequest,
    *,
    data_root: Path,
    output_directory: Path,
    model: Any,
) -> dict[str, Any]:
    if request.scenario == "compose":
        return await run_compose(
            request,
            data_root=data_root,
            output_directory=output_directory,
            model=model,
        )
    return await run_reply(
        request,
        data_root=data_root,
        output_directory=output_directory,
        model=model,
    )


__all__ = [
    "MatrixAnalysisCapability",
    "run_matrix",
]
