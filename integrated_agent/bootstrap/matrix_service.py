from __future__ import annotations

from pathlib import Path

from integrated_agent.runtimes.matrix.analysis import MatrixAnalysisCapability
from integrated_agent.runtimes.matrix.service import MatrixTaskService
from integrated_agent.runtimes.matrix.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.runtimes.matrix.worker import (
    MatrixWorkflowWorker,
    WorkerDependencies,
)


ROOT = Path(__file__).parents[2]


def build_matrix_service(
    root: Path = ROOT,
    *,
    worker_count: int = 4,
    queue_capacity: int = 32,
) -> MatrixTaskService:
    events = InMemoryEventStore()
    worker = MatrixWorkflowWorker(
        WorkerDependencies(
            matrix_analysis=MatrixAnalysisCapability(
                logs_root=root / "logs" / "matrix",
                data_root=root / "data" / "matrix",
            ),
            events=events,
        )
    )
    return MatrixTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=events,
        worker_count=worker_count,
        queue_capacity=queue_capacity,
    )
