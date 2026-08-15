from __future__ import annotations

from pathlib import Path

from integrated_agent.config import PROJECT_ROOT, load_model_settings
from integrated_agent.runtimes.matrix.analysis.agently_model import AgentlyMatrixModel
from integrated_agent.runtimes.matrix.analysis.capability import run_matrix
from integrated_agent.runtimes.matrix.models import MatrixTaskRequest


DATA_ROOT = PROJECT_ROOT / "data/matrix"
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs/matrix"


async def run_live_matrix(request: MatrixTaskRequest, *, output_directory: Path):
    load_model_settings()
    return await run_matrix(
        request,
        data_root=DATA_ROOT,
        output_directory=output_directory,
        model=AgentlyMatrixModel(),
    )
