from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from integrated_agent.bootstrap.matrix_service import build_matrix_service
from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.host.identity import IdentityStore
from integrated_agent.runtimes.matrix.host.models import (
    MatrixTaskCreate,
    MatrixTaskRequest,
    MatrixTaskResult,
)
from integrated_agent.runtimes.matrix.host.service import MatrixTaskService
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
from tests.fakes import EmptyKnowledgeStore, ScriptedMatrixModel, fake_question_runner, install_scripted_ask


def _matrix_service(monkeypatch, tmp_path: Path, **kwargs) -> MatrixTaskService:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    kwargs.setdefault("identity_root", tmp_path / "identity")
    kwargs.setdefault("knowledge", EmptyKnowledgeStore())
    return build_matrix_service(
        PROJECT_ROOT,
        **kwargs,
    )


def test_t11_create_returns_202_and_one_package_ready(tmp_path: Path, monkeypatch) -> None:
    service = _matrix_service(monkeypatch, tmp_path)
    app = create_matrix_api(service)
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "为秋季上新写一条预热推文",
                "scenario": "compose",
                "session_id": "s1",
            },
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
        assert "event: stage.started" in stream.text
        assert "event: task.completed" in stream.text


def test_t12_queue_full_returns_503(tmp_path: Path) -> None:
    class NeverCompletesWorker:
        async def execute_complex_task(self, request: MatrixTaskRequest) -> MatrixTaskResult:
            del request
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    from integrated_agent.runtimes.matrix.host.stores import (
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
        identity=IdentityStore(tmp_path / "identity"),
    )
    app = create_matrix_api(service)
    with TestClient(app) as client:
        first = client.post(
            "/v1/matrix/tasks",
            json={"text": "第一单", "scenario": "compose", "session_id": "s1"},
        )
        second = client.post(
            "/v1/matrix/tasks",
            json={"text": "第二单", "scenario": "compose", "session_id": "s1"},
        )
        third = client.post(
            "/v1/matrix/tasks",
            json={"text": "第三单", "scenario": "compose", "session_id": "s1"},
        )
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
    matrix = _matrix_service(monkeypatch, tmp_path)
    static_root = Path(__file__).parents[1] / "static"
    app = create_http_app(
        question_service=question,
        matrix_service=matrix,
        static_root=static_root,
        artifacts_root=tmp_path / "artifacts",
    )
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/question/tasks",
            json={"question": "分析 2025 年 618 经营增长质量"},
        )
        assert accepted.status_code == 202
        assert client.get("/").status_code == 200
        assert "auth-locked" in client.get("/").text
        assert client.get("/matrix").status_code == 200
        page = client.get("/matrix").text
        assert "写帖" in page
        assert 'id="postCount"' in page
        assert 'id="threadKey"' not in page
        assert 'id="accountKey"' in page
        assert 'id="interactionKey"' in page
        assert "互动规则" in page
        assert 'id="submit"' in page
        assert 'id="taskForm"' in page
        assert 'max="10"' in page
        assert "/static/matrix.css" in page
        assert 'class="workbench"' in page
        assert 'id="threadTurns"' in page
        assert 'id="taskHistory"' in page
        catalog = client.get("/api/accounts")
        assert catalog.status_code == 200
        assert len(catalog.json()["accounts"]) == 8
        auto = client.post("/v1/matrix/tasks", json={"text": "写帖", "scenario": "auto"})
        assert auto.status_code == 422


def test_reply_without_comments_issues_text_as_comment() -> None:
    created = MatrixTaskCreate(
        text="成分表在哪看？", scenario="reply", session_id="s1"
    )
    assert created.comments is not None
    assert len(created.comments) == 1
    assert created.comments[0].text == "成分表在哪看？"
    assert created.comments[0].role == "root"


def test_reply_rejects_thread_key() -> None:
    with pytest.raises(ValidationError):
        MatrixTaskCreate(
            text="处理 demo 线程", scenario="reply", thread_key="demo-1", session_id="s1"
        )


def test_reply_without_thread_stays_on_demo(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "成分表在哪看？",
                "scenario": "reply",
                "session_id": "s1",
            },
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
        result = snapshot["result"]
        assert isinstance(result, dict)
        drafts = result["drafts"]
        assert isinstance(drafts, list)
        assert len(drafts) == 1
        assert drafts[0]["source_comment_key"] == "c1"
        assert drafts[0]["text"]


def test_compose_post_count_out_of_range_is_422(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        too_low = client.post(
            "/api/create", json={"text": "写帖", "post_count": 0, "session_id": "s1"}
        )
        too_high = client.post(
            "/api/create", json={"text": "写帖", "post_count": 11, "session_id": "s1"}
        )
        assert too_low.status_code == 422
        assert too_high.status_code == 422


def test_reply_count_out_of_range_is_422(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        too_low = client.post(
            "/api/reply", json={"text": "回评", "reply_count": 0, "session_id": "s1"}
        )
        too_high = client.post(
            "/api/reply", json={"text": "回评", "reply_count": 11, "session_id": "s1"}
        )
        assert too_low.status_code == 422
        assert too_high.status_code == 422


def test_compose_must_not_include_reply_count(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        rejected = client.post(
            "/v1/matrix/tasks",
            json={"text": "写帖", "scenario": "compose", "reply_count": 2, "session_id": "s1"},
        )
        assert rejected.status_code == 422


def test_reply_must_not_include_post_count(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        rejected = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "回评",
                "scenario": "reply",
                "post_count": 2,
                "session_id": "s1",
            },
        )
        assert rejected.status_code == 422


def test_reply_must_not_include_account_key(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        rejected = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "回评",
                "scenario": "reply",
                "account_key": "default",
            },
        )
        assert rejected.status_code == 422
        extra = client.post(
            "/api/reply",
            json={
                "text": "回评",
                "account_key": "default",
            },
        )
        assert extra.status_code == 422


def test_compose_must_not_include_interaction_key(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        rejected = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "写帖",
                "scenario": "compose",
                "interaction_key": "help-first",
            },
        )
        assert rejected.status_code == 422


def test_account_catalog_endpoint(tmp_path: Path, monkeypatch) -> None:
    app = create_matrix_api(_matrix_service(monkeypatch, tmp_path))
    with TestClient(app) as client:
        catalog = client.get("/api/accounts")
        assert catalog.status_code == 200
        keys = [item["account_key"] for item in catalog.json()["accounts"]]
        assert "indie-hacker" in keys
        assert len(keys) == 8
        interactions = client.get("/api/interactions")
        assert interactions.status_code == 200
        interaction_keys = [
            item["interaction_key"] for item in interactions.json()["interactions"]
        ]
        assert "help-first" in interaction_keys
        assert "support-handoff" in interaction_keys
        assert len(interaction_keys) == 4


def test_task_survives_service_restart_from_logs(tmp_path: Path, monkeypatch) -> None:
    logs_root = tmp_path / "logs" / "matrix"
    service = _matrix_service(monkeypatch, tmp_path, logs_root=logs_root)
    app = create_matrix_api(service)
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/matrix/tasks",
            json={
                "text": "为秋季上新写一条预热推文",
                "scenario": "compose",
                "session_id": "s1",
            },
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

    restarted = _matrix_service(monkeypatch, tmp_path, logs_root=logs_root)
    restarted_app = create_matrix_api(restarted)
    with TestClient(restarted_app) as client:
        reloaded = client.get(payload["task_url"])
        assert reloaded.status_code == 200
        body = reloaded.json()
        assert body["status"] == "completed"
        assert body["result"]["drafts"]
