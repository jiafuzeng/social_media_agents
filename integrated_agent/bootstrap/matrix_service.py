from __future__ import annotations

from pathlib import Path

from integrated_agent.runtimes.matrix.analysis import MatrixAnalysisCapability
from integrated_agent.runtimes.matrix.identity import IdentityStore
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
    identity_root: Path | None = None,
    identity: IdentityStore | None = None,
) -> MatrixTaskService:
    events = InMemoryEventStore()
    store = identity or IdentityStore(
        identity_root or (root / "workspace" / "identity")
    )
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
        identity=store,
    )
