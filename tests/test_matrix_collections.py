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


def test_user_owns_collections_and_other_user_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        owner = _auth(client, "owner")
        other = _auth(client, "other")
        created = client.post(
            "/api/collections",
            headers=owner,
            json={"name": "秋天系列"},
        )
        assert created.status_code == 201
        collection_id = created.json()["collection_id"]
        listed = client.get("/api/collections", headers=owner)
        assert [item["name"] for item in listed.json()["collections"]] == ["秋天系列"]
        stranger = client.get(f"/api/collections/{collection_id}", headers=other)
        assert stranger.status_code == 403
        assert stranger.json()["detail"] == "collection access denied"
        hidden = client.get("/api/collections", headers=other)
        assert hidden.json()["collections"] == []
        denied_delete = client.delete(
            f"/api/collections/{collection_id}", headers=other
        )
        assert denied_delete.status_code == 403
        deleted = client.delete(f"/api/collections/{collection_id}", headers=owner)
        assert deleted.status_code == 204
        missing = client.get(f"/api/collections/{collection_id}", headers=owner)
        assert missing.status_code == 404


def test_duplicate_folder_name_is_rejected(tmp_path: Path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        owner = _auth(client, "owner")
        first = client.post(
            "/api/collections", headers=owner, json={"name": "爆款系列"}
        )
        assert first.status_code == 201
        duplicate = client.post(
            "/api/collections", headers=owner, json={"name": "爆款系列"}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "folder name already exists"


def test_save_tweet_and_bind_reply(tmp_path: Path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        owner = _auth(client, "owner")
        folder = client.post(
            "/api/collections", headers=owner, json={"name": "秋天系列"}
        ).json()
        collection_id = folder["collection_id"]
        saved = client.post(
            f"/api/collections/{collection_id}/items",
            headers=owner,
            json={
                "items": [
                    {
                        "key": "tweet-1",
                        "text": "秋天的热牛奶",
                        "platform_key": "x-twitter",
                    }
                ]
            },
        )
        assert saved.status_code == 201
        assert saved.json()["added"] == 1
        assert saved.json()["collection"]["item_count"] == 1
        tweet = saved.json()["collection"]["items"][0]
        assert tweet["key"] == "tweet-1"
        assert tweet["platform_key"] == "x-twitter"

        bound = client.post(
            f"/api/collections/{collection_id}/items",
            headers=owner,
            json={
                "bind_replies": True,
                "items": [
                    {
                        "key": "reply-1",
                        "text": "先喝一口再出门",
                        "parent_key": "tweet-1",
                        "parent_text": "秋天的热牛奶",
                        "platform_key": "x-twitter",
                    }
                ],
            },
        )
        assert bound.status_code == 201
        assert bound.json()["bound"] == 1
        assert bound.json()["created_parents"] == 0
        item = bound.json()["collection"]["items"][0]
        assert item["text"] == "秋天的热牛奶"
        assert item["replies"][0]["key"] == "reply-1"

        again = client.post(
            f"/api/collections/{collection_id}/items",
            headers=owner,
            json={
                "bind_replies": True,
                "items": [
                    {
                        "key": "reply-1",
                        "text": "先喝一口再出门",
                        "parent_key": "tweet-1",
                    }
                ],
            },
        )
        assert again.json()["bound"] == 0

        created_parent = client.post(
            f"/api/collections/{collection_id}/items",
            headers=owner,
            json={
                "bind_replies": True,
                "items": [
                    {
                        "key": "reply-2",
                        "text": "温度刚好",
                        "parent_text": "原评不在收藏夹",
                    }
                ],
            },
        )
        assert created_parent.json()["created_parents"] == 1
        assert created_parent.json()["bound"] == 1
        listed = client.get(
            f"/api/collections/{collection_id}", headers=owner
        ).json()
        assert listed["item_count"] == 2


def test_delete_item_cascades_replies(tmp_path: Path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        owner = _auth(client, "owner")
        folder = client.post(
            "/api/collections", headers=owner, json={"name": "money"}
        ).json()
        collection_id = folder["collection_id"]
        saved = client.post(
            f"/api/collections/{collection_id}/items",
            headers=owner,
            json={
                "items": [
                    {
                        "key": "tweet-1",
                        "text": "推文正文",
                        "replies": [{"key": "reply-1", "text": "回复正文"}],
                    }
                ]
            },
        )
        assert saved.json()["added"] == 1
        assert saved.json()["bound"] == 1
        removed_reply = client.delete(
            f"/api/collections/{collection_id}/items/reply-1",
            headers=owner,
        )
        assert removed_reply.status_code == 204
        after_reply = client.get(
            f"/api/collections/{collection_id}", headers=owner
        ).json()
        assert after_reply["items"][0]["replies"] == []
        removed_tweet = client.delete(
            f"/api/collections/{collection_id}/items/tweet-1",
            headers=owner,
        )
        assert removed_tweet.status_code == 204
        empty = client.get(f"/api/collections/{collection_id}", headers=owner).json()
        assert empty["items"] == []
        assert empty["item_count"] == 0
