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
from integrated_agent.transports.http import create_http_app, create_matrix_api
from tests.fakes import fake_question_runner, install_scripted_ask


def _matrix_service(monkeypatch, **kwargs) -> MatrixTaskService:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    return build_matrix_service(
        PROJECT_ROOT,
        **kwargs,
    )


def test_t11_create_returns_202_and_one_package_ready(tmp_path: Path, monkeypatch) -> None:
    service = _matrix_service(monkeypatch)
    app = create_matrix_api(service)
    with TestClient(app) as client:
        accepted = client.post(
            "/api/create",
            json={"text": "为秋季上新写一条预热推文"},
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


def test_t13_question_tasks_still_accepted(tmp_path: Path, monkeypatch) -> None:
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
    matrix = _matrix_service(monkeypatch)
    static_root = Path(__file__).parents[1] / "static"
    app = create_http_app(
        question_service=question,
        matrix_service=matrix,
        static_root=static_root,
        artifacts_root=tmp_path / "artifacts",
    )
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/tasks",
            json={"question": "分析 2025 年 618 经营增长质量"},
        )
        assert accepted.status_code == 202
        assert client.get("/matrix").status_code == 200
        page = client.get("/matrix").text
        assert "写帖" in page
        assert 'id="postCount"' in page
        assert 'id="accountKey"' in page
        assert 'id="submit"' in page
        assert 'id="taskForm"' in page
        assert 'max="10"' in page
        assert "/static/matrix.css" in page
        assert 'class="workbench"' in page
        assert 'id="threadTurns"' in page
        assert 'id="taskHistory"' in page
        catalog = client.get("/api/accounts")
        assert catalog.status_code == 200
        assert len(catalog.json()["accounts"]) == 10
        auto = client.post("/v1/matrix/tasks", json={"text": "写帖", "scenario": "auto"})
        assert auto.status_code == 422


def test_reply_without_comments_is_422(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch))
    with TestClient(app) as client:
        rejected = client.post("/api/reply", json={"text": "回评"})
        assert rejected.status_code == 422


def test_compose_post_count_out_of_range_is_422(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch))
    with TestClient(app) as client:
        too_low = client.post("/api/create", json={"text": "写帖", "post_count": 0})
        too_high = client.post("/api/create", json={"text": "写帖", "post_count": 11})
        assert too_low.status_code == 422
        assert too_high.status_code == 422


def test_reply_must_not_include_post_count(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch))
    with TestClient(app) as client:
        rejected = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "回评",
                "scenario": "reply",
                "thread_key": "demo-1",
                "post_count": 2,
            },
        )
        assert rejected.status_code == 422


def test_account_catalog_endpoint(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch))
    with TestClient(app) as client:
        catalog = client.get("/api/accounts")
        assert catalog.status_code == 200
        keys = [item["account_key"] for item in catalog.json()["accounts"]]
        assert "indie-hacker" in keys
        assert len(keys) == 10
