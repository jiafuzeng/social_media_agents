"""问数服务进程的依赖组装。

只负责把分析能力、Worker、有界队列与 HTTP 传输层拼成可部署的 FastAPI 应用，
不包含业务规则。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

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
from integrated_agent.transports.http import create_question_api


# 仓库根目录（bootstrap → integrated_agent → 项目根）
ROOT = Path(__file__).parents[2]


def build_question_service(root: Path = ROOT) -> QuestionTaskService:
    """组装有界队列 + Worker 池的问数任务服务。

    events 由 Service 与 Worker 共享，保证 HTTP SSE 能读到流程阶段事件。
    """
    events = InMemoryEventStore()
    worker = QuestionWorkflowWorker(
        WorkerDependencies(
            question_analysis=QuestionAnalysisCapability(
                logs_root=root / "logs"
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


def create_production_app() -> FastAPI:
    """创建生产用问数 FastAPI 应用（含静态页与制品下载）。"""
    return create_question_api(
        build_question_service(),
        static_root=ROOT / "static",
        artifacts_root=ROOT / "workspace/artifacts",
    )
