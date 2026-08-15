from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from integrated_agent.bootstrap.matrix_service import build_matrix_service
from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from integrated_agent.runtimes.matrix.models import MatrixTaskRequest, MatrixTaskResult
from integrated_agent.runtimes.matrix.service import MatrixTaskService
from integrated_agent.runtimes.question.analysis import QuestionAnalysisCapability
from integrated_agent.runtimes.question.service import QuestionTaskService
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.runtimes.question.worker import (
    QuestionWorkflowWorker,
    WorkerDependencies,
)
from integrated_agent.transports.http import create_matrix_api, create_question_api, mount_matrix_routes
from tests.fakes import fake_question_runner


def _matrix_service(tmp_path: Path, **kwargs) -> MatrixTaskService:
    return build_matrix_service(
        PROJECT_ROOT,
        model=ScriptedMatrixModel(),
        **kwargs,
    )


def test_t11_create_returns_202_and_one_package_ready(tmp_path: Path) -> None:
    service = _matrix_service(tmp_path)
    app = create_matrix_api(service)
    with TestClient(app) as client:
        accepted = client.post(
            "/api/create",
            json={"text": "为秋季上新写一条预热推文", "platform_keys": ["x-twitter"]},
        )
        assert accepted.status_code == 202
        payload = accepted.json()
        snapshot: dict[str, object] = {}
        for _ in range(200):
            snapshot = client.get(payload["task_url"]).json()
            if snapshot["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert snapshot["status"] == "completed"
        stream = client.get(payload["events_url"])
        assert stream.status_code == 200
        assert stream.text.count("event: package.ready") == 1
        assert "event: task.submitted" in stream.text
        assert "event: task.completed" in stream.text


def test_t12_queue_full_returns_503(tmp_path: Path) -> None:
    class NeverCompletesWorker:
        async def execute_complex_task(self, request: MatrixTaskRequest) -> MatrixTaskResult:
            del request
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    from integrated_agent.runtimes.matrix.stores import (
        InMemoryEventStore,
        InMemoryTaskStore,
    )

    events = InMemoryEventStore()
    service = MatrixTaskService(
        worker=NeverCompletesWorker(),
        tasks=InMemoryTaskStore(),
        events=events,
        worker_count=1,
        queue_capacity=1,
    )
    app = create_matrix_api(service)
    with TestClient(app) as client:
        first = client.post("/api/create", json={"text": "第一单"})
        second = client.post("/api/create", json={"text": "第二单"})
        third = client.post("/api/create", json={"text": "第三单"})
        assert first.status_code == 202
        assert second.status_code == 202
        assert third.status_code == 503
        assert third.headers["retry-after"] == "1"


def test_t13_question_tasks_still_accepted(tmp_path: Path) -> None:
    events = InMemoryEventStore()
    question = QuestionTaskService(
        worker=QuestionWorkflowWorker(
            WorkerDependencies(
                question_analysis=QuestionAnalysisCapability(
                    logs_root=tmp_path,
                    runner=fake_question_runner,
                ),
                events=events,
            )
        ),
        tasks=InMemoryTaskStore(),
        events=events,
    )
    matrix = _matrix_service(tmp_path)
    app = create_question_api(
        question,
        static_root=Path(__file__).parents[1] / "static",
        artifacts_root=tmp_path / "artifacts",
        extra_startables=[matrix],
        extra_mount=lambda application: mount_matrix_routes(application, matrix),
    )
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/tasks",
            json={"question": "分析 2025 年 618 经营增长质量"},
        )
        assert accepted.status_code == 202
        assert client.get("/matrix").status_code == 200
        assert "写帖" in client.get("/matrix").text
        auto = client.post("/v1/matrix/tasks", json={"text": "写帖", "scenario": "auto"})
        assert auto.status_code == 422


def test_reply_without_comments_is_422(tmp_path: Path) -> None:
    app = create_matrix_api(_matrix_service(tmp_path))
    with TestClient(app) as client:
        rejected = client.post("/api/reply", json={"text": "回评"})
        assert rejected.status_code == 422
