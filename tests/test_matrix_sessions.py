from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from integrated_agent.bootstrap.matrix_service import build_matrix_service
from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from integrated_agent.transports.http import create_matrix_api
from tests.fakes import install_scripted_ask


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    service = build_matrix_service(
        PROJECT_ROOT,
        identity_root=tmp_path / "identity",
    )
    return TestClient(create_matrix_api(service))


def _auth(client: TestClient, username: str) -> dict[str, str]:
    created = client.post(
        "/api/users/register",
        json={"username": username, "password": "secret1"},
    )
    assert created.status_code == 201
    return {"Authorization": f"Bearer {created.json()['token']}"}


def test_user_owns_multiple_sessions_and_other_user_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        owner = _auth(client, "owner")
        other = _auth(client, "other")
        first = client.post(
            "/api/sessions",
            headers=owner,
            json={"title": "秋季上新", "last_scenario": "compose"},
        )
        second = client.post(
            "/api/sessions",
            headers=owner,
            json={"title": "客服回评", "last_scenario": "reply"},
        )
        assert first.status_code == 201
        assert second.status_code == 201
        first_id = first.json()["session_id"]
        listed = client.get("/api/sessions", headers=owner)
        assert [item["title"] for item in listed.json()["sessions"]] == [
            "客服回评",
            "秋季上新",
        ]
        stranger = client.get(f"/api/sessions/{first_id}", headers=other)
        assert stranger.status_code == 403
        assert stranger.json()["detail"] == "session access denied"
        hidden = client.get("/api/sessions", headers=other)
        assert hidden.json()["sessions"] == []
        denied_delete = client.delete(f"/api/sessions/{first_id}", headers=other)
        assert denied_delete.status_code == 403
        deleted = client.delete(f"/api/sessions/{first_id}", headers=owner)
        assert deleted.status_code == 204
        missing = client.get(f"/api/sessions/{first_id}", headers=owner)
        assert missing.status_code == 404


def test_create_with_session_appends_turn(
    tmp_path: Path, monkeypatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        owner = _auth(client, "owner")
        session = client.post("/api/sessions", headers=owner, json={}).json()
        session_id = session["session_id"]
        created = client.post(
            "/api/create",
            headers=owner,
            json={"text": "为秋季上新写一条预热推文", "session_id": session_id},
        )
        assert created.status_code == 202
        detail = client.get(f"/api/sessions/{session_id}", headers=owner)
        assert detail.status_code == 200
        turns = detail.json()["turns"]
        assert len(turns) == 1
        assert turns[0]["text"] == "为秋季上新写一条预热推文"
        assert turns[0]["task_id"] == created.json()["task_id"]
        assert turns[0]["task_url"] == created.json()["task_url"]
        assert detail.json()["title"] == "为秋季上新写一条预热推文"[:28]
        missing_session = client.post("/api/create", json={"text": "无会话不可建任务"})
        assert missing_session.status_code == 422
