from __future__ import annotations

from pathlib import Path

from integrated_agent.runtimes.matrix.compose.worker import make_analyze_compose
from integrated_agent.runtimes.matrix.host.db.settings import load_identity_db_settings
from integrated_agent.runtimes.matrix.host.identity import IdentityStore
from integrated_agent.runtimes.matrix.host.service import MatrixTaskService
from integrated_agent.runtimes.matrix.host.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.runtimes.matrix.host.worker import MatrixWorkflowWorker
from integrated_agent.runtimes.matrix.rag.knowledge import KnowledgeStore
from integrated_agent.runtimes.matrix.reply.worker import make_analyze_reply


ROOT = Path(__file__).parents[2]


def build_matrix_service(
    root: Path = ROOT,
    *,
    worker_count: int = 4,
    queue_capacity: int = 32,
    identity_root: Path | None = None,
    identity: IdentityStore | None = None,
    knowledge: KnowledgeStore | None = None,
    logs_root: Path | None = None,
) -> MatrixTaskService:
    events = InMemoryEventStore()
    settings = None
    if identity is None and identity_root is None:
        settings = load_identity_db_settings(root)
    store = identity or IdentityStore(
        identity_root or (root / "workspace" / "identity"),
        settings=settings,
    )
    knowledge_store = knowledge or KnowledgeStore()
    matrix_logs_root = logs_root or (root / "logs" / "matrix")
    data_root = root / "data" / "matrix"
    worker = MatrixWorkflowWorker(
        analyze_compose=make_analyze_compose(
            logs_root=matrix_logs_root,
            data_root=data_root,
            knowledge=knowledge_store,
            events=events,
        ),
        analyze_reply=make_analyze_reply(
            logs_root=matrix_logs_root,
            data_root=data_root,
            events=events,
        ),
        events=events,
    )
    return MatrixTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=events,
        worker_count=worker_count,
        queue_capacity=queue_capacity,
        identity=store,
        knowledge=knowledge_store,
        logs_root=matrix_logs_root,
    )
