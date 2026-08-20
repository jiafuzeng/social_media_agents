from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from integrated_agent.bootstrap.matrix_service import build_matrix_service
from integrated_agent.config import PROJECT_ROOT
from integrated_agent.rag.models import (
    CreateChunkIn,
    CreateDocumentIn,
    KnowledgeError,
    PreviewChunksOut,
    TextChunk,
    UpdateChunkIn,
    UpdateDocumentIn,
)
from tests.fakes import ScriptedMatrixModel, install_kb_chat_ask, install_scripted_ask
from integrated_agent.runtimes.matrix.kb_chat.scripted import ScriptedKbChatModel
from integrated_agent.runtimes.matrix.knowledge import KnowledgeStore
from integrated_agent.runtimes.matrix import kb_store as kb_store_mod
from integrated_agent.transports.http import create_matrix_api
from tests.test_kb_store import _ProbeProvider


class _FailNeedle(_ProbeProvider):
    def __init__(self, vector: list[float], needle: str) -> None:
        super().__init__(vector)
        self.needle = needle

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if any(self.needle in text for text in texts):
            raise RuntimeError("embed failed")
        return await super().embed_texts(texts)


def _open_stores(tmp_path: Path, monkeypatch) -> tuple[dict, dict[str, _ProbeProvider]]:
    monkeypatch.setattr(kb_store_mod, "KB_RECORD_ROOT", tmp_path / "kb" / "records")
    probes = {
        "openai-small": _ProbeProvider([1.0, 0.0, 0.0]),
        "bge-m3": _ProbeProvider([0.0, 1.0, 0.0]),
        "qwen3": _ProbeProvider([0.0, 0.0, 1.0]),
    }

    def _wrap(agent: object) -> _ProbeProvider:
        probe = probes[agent._active_model_key]  # type: ignore[attr-defined]
        probe.agent = agent  # type: ignore[attr-defined]
        return probe

    monkeypatch.setattr(kb_store_mod, "AgentEmbeddingProvider", _wrap)
    stores = {profile_id: kb_store_mod._open_store(profile_id) for profile_id in probes}
    return stores, probes


def _knowledge(tmp_path: Path, monkeypatch) -> tuple[KnowledgeStore, dict[str, _ProbeProvider]]:
    stores, probes = _open_stores(tmp_path, monkeypatch)
    return KnowledgeStore(stores=stores, files_root=tmp_path / "kb" / "files"), probes


def _auth_user(client: TestClient, username: str) -> tuple[dict[str, str], str]:
    created = client.post(
        "/api/users/register",
        json={"username": username, "password": "secret1"},
    )
    assert created.status_code == 201
    body = created.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["user"]["user_id"]


def _client(
    tmp_path: Path, monkeypatch
) -> tuple[TestClient, KnowledgeStore, dict[str, _ProbeProvider]]:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    install_kb_chat_ask(monkeypatch, ScriptedKbChatModel())
    knowledge, probes = _knowledge(tmp_path, monkeypatch)
    service = build_matrix_service(
        PROJECT_ROOT,
        identity_root=tmp_path / "identity",
        knowledge=knowledge,
    )
    return TestClient(create_matrix_api(service)), knowledge, probes


@pytest.mark.asyncio
async def test_paste_document_is_listed_and_retrievable(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            title="售后手册",
            text="七天无理由退款需提供购买凭证。电子发票在发货后自动开具。",
            embedding_profile_id="bge-m3",
        ),
    )
    assert doc.status == "ready"
    assert doc.chunk_count >= 1
    listed = await knowledge.list_documents("user-a")
    assert [item.doc_id for item in listed.documents] == [doc.doc_id]
    hits = await knowledge.retrieve("user-a", "退款", "bge-m3")
    assert any((item.get("scope") or {}).get("doc_id") == doc.doc_id for item in hits)
    other_profile = await knowledge.retrieve("user-a", "退款", "openai-small")
    assert other_profile == []
    cards = await knowledge.retrieve_draft_cards("user-a", "退款", "bge-m3")
    assert cards
    assert cards[0]["kb_id"] == "k1"
    assert "record_id" not in cards[0]
    assert cards[0]["chunk_id"]
    assert cards[0]["doc_id"] == doc.doc_id


@pytest.mark.asyncio
async def test_cross_user_document_is_forbidden(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(text="仅限本人。", embedding_profile_id="bge-m3"),
    )
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.get_document("user-b", doc.doc_id)
    assert caught.value.status == 403
    assert await knowledge.retrieve("user-a", "仅限本人", "bge-m3")
    assert await knowledge.retrieve("user-b", "仅限本人", "bge-m3") == []
    from integrated_agent.rag.models import SearchKbIn

    hidden = await knowledge.search(
        "user-b",
        SearchKbIn(query="仅限本人", embedding_profile_id="bge-m3"),
    )
    assert hidden.hits == []
    listed = await knowledge.list_documents("user-b")
    assert listed.documents == []


@pytest.mark.asyncio
async def test_unknown_profile_is_422(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.create_document(
            "user-a",
            CreateDocumentIn(text="第一句。", embedding_profile_id="missing-profile"),
        )
    assert caught.value.status == 422


@pytest.mark.asyncio
async def test_single_chunk_patch_does_not_reembed_siblings(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="第一句退款。第二句发票。",
            strategy="sentence_window",
            window_size=1,
            embedding_profile_id="bge-m3",
        ),
    )
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    assert len(chunks.chunks) >= 2
    sibling_text = chunks.chunks[1].text
    before = list(probes["bge-m3"].calls)
    updated = await knowledge.update_chunk(
        "user-a",
        doc.doc_id,
        chunks.chunks[0].chunk_id,
        UpdateChunkIn(text="改正后的退款说明。"),
    )
    assert updated.diverged is True
    after = probes["bge-m3"].calls[len(before) :]
    assert after == [["改正后的退款说明。"]]
    assert sibling_text not in [text for batch in after for text in batch]
    remaining = await knowledge.list_chunks("user-a", doc.doc_id)
    assert remaining.chunks[1].text == sibling_text


@pytest.mark.asyncio
async def test_edit_chunk_leaves_original_file(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    original = "原件退款政策。".encode("utf-8")
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(embedding_profile_id="bge-m3"),
        file_bytes=original,
        filename="policy.txt",
        mime="text/plain",
    )
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    old_id = str(
        (await knowledge._chunk_ref("user-a", doc.doc_id, chunks.chunks[0].chunk_id))["id"]
    )
    await knowledge.update_chunk(
        "user-a",
        doc.doc_id,
        chunks.chunks[0].chunk_id,
        UpdateChunkIn(text="手改后的正文。"),
    )
    assert await knowledge._catalog.backend.get_record(old_id) is None
    path, name = await knowledge.document_file("user-a", doc.doc_id)
    assert name == "policy.txt"
    assert path.read_bytes() == original
    edited = await knowledge.list_chunks("user-a", doc.doc_id)
    assert edited.chunks[0].diverged is True
    assert edited.chunks[0].text == "手改后的正文。"


@pytest.mark.asyncio
async def test_failed_chunk_does_not_archive_siblings(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    failing = _FailNeedle([0.0, 1.0, 0.0], "FAILME")
    store = knowledge._stores["bge-m3"]
    store.backend.embedding_provider = failing
    store.backend.vector_index.embedding_provider = failing
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(title="部分失败", embedding_profile_id="bge-m3"),
    )
    ok = await knowledge.create_chunk(
        "user-a",
        doc.doc_id,
        CreateChunkIn(text="成功入库的退款句。"),
    )
    with pytest.raises(KnowledgeError):
        await knowledge.create_chunk(
            "user-a",
            doc.doc_id,
            CreateChunkIn(text="FAILME 这句向量失败。"),
        )
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    assert [item.chunk_id for item in chunks.chunks] == [ok.chunk_id]
    hits = await knowledge.retrieve("user-a", "退款", "bge-m3")
    assert any((item.get("scope") or {}).get("chunk_id") == ok.chunk_id for item in hits)


@pytest.mark.asyncio
async def test_disable_chunk_drops_retrieve(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(text="七天无理由退款。", embedding_profile_id="bge-m3"),
    )
    chunk = (await knowledge.list_chunks("user-a", doc.doc_id)).chunks[0]
    await knowledge.update_chunk(
        "user-a",
        doc.doc_id,
        chunk.chunk_id,
        UpdateChunkIn(enabled=False),
    )
    hits = await knowledge.retrieve("user-a", "退款", "bge-m3")
    assert hits == []
    listed = await knowledge.list_chunks("user-a", doc.doc_id)
    assert listed.chunks[0].enabled is False


@pytest.mark.asyncio
async def test_delete_chunk_purges_sqlite_row(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(text="第一句退款。", embedding_profile_id="bge-m3"),
    )
    extra = await knowledge.create_chunk(
        "user-a",
        doc.doc_id,
        CreateChunkIn(text="后补的发票说明。"),
    )
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    assert extra.chunk_id in {item.chunk_id for item in chunks.chunks}
    target = chunks.chunks[0]
    old_id = str((await knowledge._chunk_ref("user-a", doc.doc_id, target.chunk_id))["id"])
    await knowledge.delete_chunk("user-a", doc.doc_id, target.chunk_id)
    assert await knowledge._catalog.backend.get_record(old_id) is None
    remaining = await knowledge.list_chunks("user-a", doc.doc_id)
    assert all(item.chunk_id != target.chunk_id for item in remaining.chunks)
    assert remaining.chunks


@pytest.mark.asyncio
async def test_delete_purges_records_and_removes_file(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(embedding_profile_id="bge-m3"),
        file_bytes="冷文件正文。".encode("utf-8"),
        filename="cold.txt",
        mime="text/plain",
    )
    artifact = tmp_path / "kb" / "files" / "user-a" / str(doc.artifact_id)
    assert artifact.is_dir()
    old_ids = [
        str(item["id"])
        for item in await knowledge._chunk_refs("user-a", doc.doc_id, active_only=False)
    ]
    doc_ref = await knowledge._document_ref("user-a", doc.doc_id)
    old_ids.append(str(doc_ref["id"]))
    await knowledge.delete_document("user-a", doc.doc_id)
    listed = await knowledge.list_documents("user-a")
    assert listed.documents == []
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.get_document("user-a", doc.doc_id)
    assert caught.value.status == 404
    assert not artifact.exists()
    hits = await knowledge.retrieve("user-a", "冷文件", "bge-m3")
    assert hits == []
    leftover = await knowledge._catalog.search(
        filters={
            "collection": "kb",
            "scope.doc_id": doc.doc_id,
        }
    )
    assert leftover == []
    for record_id in old_ids:
        assert await knowledge._catalog.backend.get_record(record_id) is None


@pytest.mark.asyncio
async def test_empty_document_then_single_chunk_ingest(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(title="空卡", embedding_profile_id="bge-m3"),
    )
    assert doc.chunk_count == 0
    chunk = await knowledge.create_chunk(
        "user-a",
        doc.doc_id,
        CreateChunkIn(text="后补的退款说明。"),
    )
    assert chunk.text == "后补的退款说明。"
    refreshed = await knowledge.get_document("user-a", doc.doc_id)
    assert refreshed.chunk_count == 1
    hits = await knowledge.retrieve("user-a", "退款", "bge-m3")
    assert any((item.get("scope") or {}).get("chunk_id") == chunk.chunk_id for item in hits)


@pytest.mark.asyncio
async def test_chunk_rejects_other_embedding_profile(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(text="锁定模型。", embedding_profile_id="bge-m3"),
    )
    chunk = (await knowledge.list_chunks("user-a", doc.doc_id)).chunks[0]
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.update_chunk(
            "user-a",
            doc.doc_id,
            chunk.chunk_id,
            UpdateChunkIn(text="换模型改正文。", embedding_profile_id="openai-small"),
        )
    assert caught.value.status == 422
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.update_document(
            "user-a",
            doc.doc_id,
            UpdateDocumentIn(embedding_profile_id="openai-small"),
        )
    assert caught.value.status == 422
    assert str(caught.value) == "cannot change embedding_profile_id without rechunk"


@pytest.mark.asyncio
async def test_too_many_ingest_chunks_is_422(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)

    async def _too_many(command):  # type: ignore[no-untyped-def]
        del command
        return PreviewChunksOut(
            strategy="sentence",
            chunks=[TextChunk(text="块")] * 2001,
        )

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.knowledge.preview_chunks", _too_many
    )
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.create_document(
            "user-a",
            CreateDocumentIn(text="任意。", embedding_profile_id="bge-m3"),
        )
    assert caught.value.status == 422
    listed = await knowledge.list_documents("user-a")
    assert listed.documents == []


@pytest.mark.asyncio
async def test_replace_file_keeps_doc_id(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(embedding_profile_id="bge-m3"),
        file_bytes="旧退款政策。".encode("utf-8"),
        filename="old.txt",
        mime="text/plain",
    )
    old_ids = [
        str(item["id"])
        for item in await knowledge._chunk_refs("user-a", doc.doc_id, active_only=True)
    ]
    updated = await knowledge.update_document(
        "user-a",
        doc.doc_id,
        UpdateDocumentIn(rechunk=True),
        file_bytes="新的发票说明。".encode("utf-8"),
        filename="new.txt",
        mime="text/plain",
    )
    assert updated.doc_id == doc.doc_id
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    assert any("发票" in item.text for item in chunks.chunks)
    assert all("旧退款" not in item.text for item in chunks.chunks)
    hits = await knowledge.retrieve("user-a", "发票", "bge-m3")
    assert hits
    path, name = await knowledge.document_file("user-a", doc.doc_id)
    assert name == "new.txt"
    assert "发票" in path.read_text(encoding="utf-8")
    for record_id in old_ids:
        assert await knowledge._catalog.backend.get_record(record_id) is None
    archived = await knowledge._catalog.search(
        filters={
            "collection": "kb",
            "kind": "chunk",
            "scope.doc_id": doc.doc_id,
            "meta.status": "archived",
        }
    )
    assert archived == []


@pytest.mark.asyncio
async def test_rechunk_purges_old_chunk_records(tmp_path: Path, monkeypatch) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="旧退款政策需要凭证。",
            embedding_profile_id="bge-m3",
        ),
    )
    old_ids = [
        str(item["id"])
        for item in await knowledge._chunk_refs("user-a", doc.doc_id, active_only=True)
    ]
    assert old_ids
    updated = await knowledge.update_document(
        "user-a",
        doc.doc_id,
        UpdateDocumentIn(
            rechunk=True,
            embedding_profile_id="openai-small",
            text="新的发票说明可下载。",
        ),
    )
    assert updated.embedding_profile_id == "openai-small"
    for record_id in old_ids:
        assert await knowledge._catalog.backend.get_record(record_id) is None
    leftover = await knowledge._catalog.search(
        filters={
            "collection": "kb",
            "kind": "chunk",
            "scope.doc_id": doc.doc_id,
            "meta.status": "archived",
        }
    )
    assert leftover == []
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    assert any("发票" in item.text for item in chunks.chunks)
    assert all("旧退款" not in item.text for item in chunks.chunks)
    assert await knowledge.retrieve("user-a", "发票", "openai-small")
    assert await knowledge.retrieve("user-a", "退款", "bge-m3") == []


@pytest.mark.asyncio
async def test_rechunk_keeps_old_chunks_when_ingest_fails(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="旧退款政策需要凭证。",
            embedding_profile_id="bge-m3",
        ),
    )
    old_ids = [
        str(item["id"])
        for item in await knowledge._chunk_refs("user-a", doc.doc_id, active_only=True)
    ]
    failing = _FailNeedle([1.0, 0.0, 0.0], "FAILME")
    store = knowledge._stores["openai-small"]
    store.backend.embedding_provider = failing
    store.backend.vector_index.embedding_provider = failing
    with pytest.raises(KnowledgeError) as caught:
        await knowledge.update_document(
            "user-a",
            doc.doc_id,
            UpdateDocumentIn(
                rechunk=True,
                embedding_profile_id="openai-small",
                text="FAILME 新的发票说明。",
            ),
        )
    assert caught.value.status == 500
    restored = await knowledge.get_document("user-a", doc.doc_id)
    assert restored.status == "ready"
    assert restored.embedding_profile_id == "bge-m3"
    for record_id in old_ids:
        row = await knowledge._catalog.backend.get_record(record_id)
        assert row is not None
        assert (row.get("meta") or {}).get("status") == "active"
    chunks = await knowledge.list_chunks("user-a", doc.doc_id)
    assert any("旧退款" in item.text for item in chunks.chunks)
    assert all("FAILME" not in item.text for item in chunks.chunks)
    assert await knowledge.retrieve("user-a", "退款", "bge-m3")


def test_http_documents_crud_and_forbidden(tmp_path: Path, monkeypatch) -> None:
    client, _knowledge_store, _probes = _client(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/kb/documents").status_code == 401
        owner, _owner_id = _auth_user(client, "owner")
        created = client.post(
            "/api/kb/documents",
            headers=owner,
            json={
                "title": "手册",
                "text": "七天无理由退款。电子发票自动开。",
                "embedding_profile_id": "bge-m3",
            },
        )
        assert created.status_code == 201, created.text
        doc_id = created.json()["doc_id"]
        listed = client.get("/api/kb/documents", headers=owner)
        assert listed.status_code == 200
        assert listed.json()["documents"][0]["doc_id"] == doc_id
        chunks = client.get(f"/api/kb/documents/{doc_id}/chunks", headers=owner)
        assert chunks.status_code == 200
        chunk_id = chunks.json()["chunks"][0]["chunk_id"]
        patched = client.patch(
            f"/api/kb/documents/{doc_id}/chunks/{chunk_id}",
            headers=owner,
            json={"text": "改正后的退款块。"},
        )
        assert patched.status_code == 200
        assert patched.json()["text"] == "改正后的退款块。"
        assert patched.json()["diverged"] is True
        listed_chunks = client.get(f"/api/kb/documents/{doc_id}/chunks", headers=owner)
        assert listed_chunks.status_code == 200
        found = next(
            item
            for item in listed_chunks.json()["chunks"]
            if item["chunk_id"] == chunk_id
        )
        assert found["text"] == "改正后的退款块。"
        disabled = client.patch(
            f"/api/kb/documents/{doc_id}/chunks/{chunk_id}",
            headers=owner,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        removed = client.delete(
            f"/api/kb/documents/{doc_id}/chunks/{chunk_id}",
            headers=owner,
        )
        assert removed.status_code == 204
        leftover = client.get(f"/api/kb/documents/{doc_id}/chunks", headers=owner)
        assert leftover.status_code == 200
        assert all(item["chunk_id"] != chunk_id for item in leftover.json()["chunks"])
        stranger, _sid = _auth_user(client, "stranger")
        denied = client.get(f"/api/kb/documents/{doc_id}", headers=stranger)
        assert denied.status_code == 403
        listed_other = client.get("/api/kb/documents", headers=stranger)
        assert listed_other.status_code == 200
        assert listed_other.json()["documents"] == []
        assert client.get(f"/api/kb/documents/{doc_id}/chunks", headers=stranger).status_code == 403
        search_other = client.post(
            "/api/kb/search",
            headers=stranger,
            json={"query": "退款", "embedding_profile_id": "bge-m3"},
        )
        assert search_other.status_code == 200
        assert search_other.json()["hits"] == []
        chat_other = client.post(
            "/api/kb/chat",
            headers=stranger,
            json={"query": "退款", "embedding_profile_id": "bge-m3"},
        )
        assert chat_other.status_code == 200
        assert chat_other.json()["hits"] == []
        unknown = client.post(
            "/api/kb/documents",
            headers=owner,
            json={"text": "第一句。", "embedding_profile_id": "no-such"},
        )
        assert unknown.status_code == 422
        uploaded = client.post(
            "/api/kb/documents",
            headers=owner,
            files={"file": ("note.txt", "上传原件正文。".encode("utf-8"), "text/plain")},
            data={"embedding_profile_id": "bge-m3"},
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = uploaded.json()["doc_id"]
        downloaded = client.get(f"/api/kb/documents/{file_id}/file", headers=owner)
        assert downloaded.status_code == 200
        assert downloaded.content == "上传原件正文。".encode("utf-8")
        assert client.get(f"/api/kb/documents/{file_id}/file", headers=stranger).status_code == 403
        paste_file = client.get(f"/api/kb/documents/{doc_id}/file", headers=owner)
        assert paste_file.status_code == 404
        deleted = client.delete(f"/api/kb/documents/{doc_id}", headers=owner)
        assert deleted.status_code == 204
        missing = client.get(f"/api/kb/documents/{doc_id}", headers=owner)
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_search_requires_profile_and_respects_lock(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    from integrated_agent.rag.models import SearchKbIn

    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(text="七天无理由退款需提供凭证。", embedding_profile_id="bge-m3"),
    )
    same = await knowledge.search(
        "user-a",
        SearchKbIn(query="退款", embedding_profile_id="bge-m3"),
    )
    assert any(hit.doc_id == doc.doc_id for hit in same.hits)
    other = await knowledge.search(
        "user-a",
        SearchKbIn(query="退款", embedding_profile_id="openai-small"),
    )
    assert other.hits == []
    assert other.empty_reason == "no_docs_for_profile"
    chunk = (await knowledge.list_chunks("user-a", doc.doc_id)).chunks[0]
    await knowledge.update_chunk(
        "user-a",
        doc.doc_id,
        chunk.chunk_id,
        UpdateChunkIn(enabled=False),
    )
    hidden = await knowledge.search(
        "user-a",
        SearchKbIn(query="退款", embedding_profile_id="bge-m3"),
    )
    assert hidden.hits == []


def test_http_search_omits_profile_is_422(tmp_path: Path, monkeypatch) -> None:
    client, _knowledge_store, _probes = _client(tmp_path, monkeypatch)
    with client:
        owner, _uid = _auth_user(client, "owner")
        created = client.post(
            "/api/kb/documents",
            headers=owner,
            json={"text": "七天无理由退款。", "embedding_profile_id": "bge-m3"},
        )
        assert created.status_code == 201, created.text
        omitted = client.post(
            "/api/kb/search",
            headers=owner,
            json={"query": "退款"},
        )
        assert omitted.status_code == 422
        ok = client.post(
            "/api/kb/search",
            headers=owner,
            json={"query": "退款", "embedding_profile_id": "bge-m3"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["embedding_profile_id"] == "bge-m3"
        assert ok.json()["hits"]
