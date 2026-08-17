from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from integrated_agent.bootstrap.matrix_service import build_matrix_service
from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from integrated_agent.runtimes.matrix.identity import IdentityError, IdentityStore
from integrated_agent.transports.http import create_matrix_api
from tests.fakes import install_scripted_ask


def _store(tmp_path: Path) -> IdentityStore:
    return IdentityStore(tmp_path / "identity")


@pytest.mark.asyncio
async def test_register_persists_to_sqlite_and_rejects_duplicate(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    created = await store.register("alice", "secret1")
    assert created.user.username == "alice"
    assert created.user.role == "admin"
    assert created.token
    assert (tmp_path / "identity" / "identity.sqlite").is_file()
    with pytest.raises(IdentityError) as caught:
        await store.register("alice", "secret1")
    assert caught.value.status == 409


@pytest.mark.asyncio
async def test_login_returns_token_and_rejects_bad_password(tmp_path: Path) -> None:
    store = _store(tmp_path)
    registered = await store.register("bob", "secret1")
    logged_in = await store.login("bob", "secret1")
    assert logged_in.user.user_id == registered.user.user_id
    assert logged_in.token != registered.token
    with pytest.raises(IdentityError) as caught:
        await store.login("bob", "wrong-password")
    assert caught.value.status == 401


@pytest.mark.asyncio
async def test_user_for_token_survives_reload(tmp_path: Path) -> None:
    root = tmp_path / "identity"
    store = IdentityStore(root)
    registered = await store.register("carol", "secret1")
    restored = IdentityStore(root)
    user = await restored.user_for_token(registered.token)
    assert user.user_id == registered.user.user_id
    assert user.username == "carol"


def test_http_register_login_and_me(tmp_path: Path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    service = build_matrix_service(
        PROJECT_ROOT,
        identity_root=tmp_path / "identity",
    )
    app = create_matrix_api(service)
    with TestClient(app) as client:
        created = client.post(
            "/api/users/register",
            json={"username": "erin", "password": "secret1"},
        )
        assert created.status_code == 201
        assert created.json()["user"]["role"] == "admin"
        token = created.json()["token"]
        me = client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200
        assert me.json()["username"] == "erin"
        assert me.json()["role"] == "admin"
        logged_in = client.post(
            "/api/users/login",
            json={"username": "erin", "password": "secret1"},
        )
        assert logged_in.status_code == 200
        missing = client.get("/api/users/me")
        assert missing.status_code == 401
        duplicate = client.post(
            "/api/users/register",
            json={"username": "erin", "password": "secret1"},
        )
        assert duplicate.status_code == 409


def test_http_user_crud_logout_and_last_user_protected(
    tmp_path: Path, monkeypatch
) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    service = build_matrix_service(
        PROJECT_ROOT,
        identity_root=tmp_path / "identity",
    )
    app = create_matrix_api(service)
    with TestClient(app) as client:
        owner = client.post(
            "/api/users/register",
            json={"username": "owner", "password": "secret1"},
        )
        token = owner.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        listed = client.get("/api/users", headers=headers)
        assert listed.status_code == 200
        assert [item["username"] for item in listed.json()["users"]] == ["owner"]
        assert listed.json()["users"][0]["role"] == "admin"
        created = client.post(
            "/api/users",
            headers=headers,
            json={"username": "member", "password": "secret2"},
        )
        assert created.status_code == 201
        assert created.json()["role"] == "user"
        member_id = created.json()["user_id"]
        still_me = client.get("/api/users/me", headers=headers)
        assert still_me.json()["username"] == "owner"
        renamed = client.patch(
            f"/api/users/{member_id}",
            headers=headers,
            json={"username": "member2"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["username"] == "member2"
        self_rename = client.patch(
            f"/api/users/{owner.json()['user']['user_id']}",
            headers=headers,
            json={"username": "owner2", "current_password": "secret1"},
        )
        assert self_rename.status_code == 200
        assert self_rename.json()["username"] == "owner2"
        denied = client.patch(
            f"/api/users/{owner.json()['user']['user_id']}",
            headers=headers,
            json={"new_password": "secret9"},
        )
        assert denied.status_code == 422
        deleted = client.delete(f"/api/users/{member_id}", headers=headers)
        assert deleted.status_code == 204
        last = client.delete(
            f"/api/users/{owner.json()['user']['user_id']}",
            headers=headers,
        )
        assert last.status_code == 422
        assert last.json()["detail"] == "cannot delete the last admin"
        logged_out = client.post("/api/users/logout", headers=headers)
        assert logged_out.status_code == 204
        stale = client.get("/api/users/me", headers=headers)
        assert stale.status_code == 401


def test_http_roles_split_admin_and_member(tmp_path: Path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    service = build_matrix_service(
        PROJECT_ROOT,
        identity_root=tmp_path / "identity",
    )
    app = create_matrix_api(service)
    with TestClient(app) as client:
        admin = client.post(
            "/api/users/register",
            json={"username": "owner", "password": "secret1"},
        )
        member_reg = client.post(
            "/api/users/register",
            json={"username": "member", "password": "secret2"},
        )
        assert admin.json()["user"]["role"] == "admin"
        assert member_reg.json()["user"]["role"] == "user"
        admin_headers = {"Authorization": f"Bearer {admin.json()['token']}"}
        member_headers = {"Authorization": f"Bearer {member_reg.json()['token']}"}
        admin_id = admin.json()["user"]["user_id"]
        member_id = member_reg.json()["user"]["user_id"]

        member_list = client.get("/api/users", headers=member_headers)
        assert [item["username"] for item in member_list.json()["users"]] == ["member"]
        forbidden_create = client.post(
            "/api/users",
            headers=member_headers,
            json={"username": "other", "password": "secret3"},
        )
        assert forbidden_create.status_code == 403
        assert forbidden_create.json()["detail"] == "admin role required"
        forbidden_patch = client.patch(
            f"/api/users/{admin_id}",
            headers=member_headers,
            json={"username": "hijack"},
        )
        assert forbidden_patch.status_code == 403
        forbidden_role = client.patch(
            f"/api/users/{member_id}",
            headers=member_headers,
            json={"role": "admin", "current_password": "secret2"},
        )
        assert forbidden_role.status_code == 403
        forbidden_delete = client.delete(
            f"/api/users/{admin_id}",
            headers=member_headers,
        )
        assert forbidden_delete.status_code == 403

        demote_only = client.patch(
            f"/api/users/{admin_id}",
            headers=admin_headers,
            json={"role": "user", "current_password": "secret1"},
        )
        assert demote_only.status_code == 422
        assert demote_only.json()["detail"] == "cannot demote the last admin"
        created_admin = client.post(
            "/api/users",
            headers=admin_headers,
            json={"username": "second", "password": "secret9", "role": "admin"},
        )
        assert created_admin.status_code == 201
        assert created_admin.json()["role"] == "admin"
        extra_id = created_admin.json()["user_id"]
        demote_extra = client.patch(
            f"/api/users/{extra_id}",
            headers=admin_headers,
            json={"role": "user"},
        )
        assert demote_extra.status_code == 200
        assert demote_extra.json()["role"] == "user"
        still_last = client.patch(
            f"/api/users/{admin_id}",
            headers=admin_headers,
            json={"role": "user", "current_password": "secret1"},
        )
        assert still_last.status_code == 422
        assert still_last.json()["detail"] == "cannot demote the last admin"
        last_admin = client.delete(
            f"/api/users/{admin_id}",
            headers=admin_headers,
        )
        assert last_admin.status_code == 422
        assert last_admin.json()["detail"] == "cannot delete the last admin"
