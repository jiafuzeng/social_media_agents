from __future__ import annotations

from pathlib import Path

from integrated_agent.runtimes.question.analysis import (
    QuestionAnalysisCapability,
)
from integrated_agent.runtimes.question.service import QuestionTaskService
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.runtimes.question.worker import (
    QuestionWorkflowWorker,
    WorkerDependencies,
)


ROOT = Path(__file__).parents[2]


def build_question_service(root: Path = ROOT) -> QuestionTaskService:
    events = InMemoryEventStore()
    worker = QuestionWorkflowWorker(
        WorkerDependencies(
            question_analysis=QuestionAnalysisCapability(
                logs_root=root / "logs/question",
            ),
            events=events,
        )
    )
    return QuestionTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=events,
        worker_count=4,
        queue_capacity=32,
    )
