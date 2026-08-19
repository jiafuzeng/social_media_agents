from __future__ import annotations

from pathlib import Path
from typing import Any

from integrated_agent.runtimes.matrix.models import MatrixTaskRequest

from .workflows.compose_flow import run_compose
from .workflows.reply_flow import run_reply


class MatrixAnalysisCapability:
    """矩阵分析入口：绑定日志目录和快照数据，按任务跑完一套 Flow。"""

    def __init__(
        self,
        *,
        logs_root: Path,
        data_root: Path,
        knowledge: Any | None = None,
    ) -> None:
        self.logs_root = logs_root  # 每单 Trace：logs_root / task_id
        self.data_root = data_root  # 账号、平台、模板、案例夹具
        self.knowledge = knowledge

    async def analyze(self, request: MatrixTaskRequest) -> dict[str, Any]:
        output_directory = self.logs_root / request.task_id
        run = await run_matrix(
            request,
            data_root=self.data_root,
            output_directory=output_directory,
            knowledge=self.knowledge,
        )
        # Worker / SSE 用这条 URI 回指本单 run.json，不把 Trace 对象带出分析层。
        run["trace_ref"] = (output_directory / "run.json").resolve().as_uri()
        return run


async def run_matrix(
    request: MatrixTaskRequest,
    *,
    data_root: Path,
    output_directory: Path,
    knowledge: Any | None = None,
) -> dict[str, Any]:
    """按入口已绑定的 scenario 分发；compose 与 reply 是两张独立 TriggerFlow。"""

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
