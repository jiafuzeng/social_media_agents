from __future__ import annotations

from pathlib import Path

import pytest
from agently.core.storage import RecordStore

from integrated_agent.config import (
    KB_DEFAULT_EMBEDDING_PROFILE,
    KB_EMBEDDING_AGENTS,
    KB_RECORD_ROOT,
)
from integrated_agent.rag.embeddings import list_embedding_profiles
from integrated_agent.runtimes.matrix.rag import kb_store as kb_store_mod
from integrated_agent.runtimes.matrix.rag.kb_store import kb_store


class _ProbeProvider:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self.vector) for _ in texts]


def _open(tmp_path: Path, monkeypatch, profile_id: str = "bge-m3") -> tuple[RecordStore, dict[str, _ProbeProvider]]:
    monkeypatch.setattr(kb_store_mod, "KB_RECORD_ROOT", tmp_path / "kb" / "records")
    probes = {
        "text-embedding-v3": _ProbeProvider([0.5, 0.5, 0.0]),
        "bge-m3": _ProbeProvider([0.0, 1.0, 0.0]),
        "qwen3": _ProbeProvider([0.0, 0.0, 1.0]),
    }

    def _wrap(agent: object) -> _ProbeProvider:
        probe = probes[agent._active_model_key]  # type: ignore[attr-defined]
        probe.agent = agent  # type: ignore[attr-defined]
        return probe

    monkeypatch.setattr(kb_store_mod, "AgentEmbeddingProvider", _wrap)
    return kb_store_mod._open_store(profile_id), probes


def test_embedding_profiles_are_agent_ids() -> None:
    payload = list_embedding_profiles()
    assert payload.default == KB_DEFAULT_EMBEDDING_PROFILE
    assert payload.profiles == list(KB_EMBEDDING_AGENTS)


def test_kb_record_root_is_config_dir_without_agently() -> None:
    assert KB_RECORD_ROOT.name == "records"
    assert ".agently" not in KB_RECORD_ROOT.parts


def test_kb_store_maps_each_profile() -> None:
    assert set(kb_store) == set(KB_EMBEDDING_AGENTS)
    for profile_id, store in kb_store.items():
        assert store.backend.embedding_provider.agent._active_model_key == profile_id
        assert (
            store.backend.vector_index.embedding_provider
            is store.backend.embedding_provider
        )


def test_profile_stores_share_sqlite_vectors() -> None:
    providers = [store.backend.vector_store_provider for store in kb_store.values()]
    assert providers
    assert all(provider is providers[0] for provider in providers)
    assert all(provider.name == "sqlite" for provider in providers)


def test_db_path_is_config_root_without_agently() -> None:
    store = kb_store["bge-m3"]
    db_path = Path(store.backend.db_path)
    assert db_path == KB_RECORD_ROOT / "records.db"
    assert db_path == store.backend.root / "records.db"
    assert ".agently" not in db_path.parts
    assert store.backend.vector_store_provider.name == "sqlite"
    assert Path(store.backend.vector_store_provider.db_path) == db_path


def test_record_store_path_constructor_nests_agently(tmp_path: Path) -> None:
    db_path = Path(
        RecordStore(tmp_path / "kb" / "records", mode="read_write").backend.db_path
    )
    assert ".agently" in db_path.parts


@pytest.mark.asyncio
async def test_cross_profile_delete_then_put_keeps_sqlite_writable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(kb_store_mod, "KB_RECORD_ROOT", tmp_path / "kb" / "records")
    probes = {
        "text-embedding-v3": _ProbeProvider([0.5, 0.5, 0.0]),
        "bge-m3": _ProbeProvider([0.0, 1.0, 0.0]),
        "qwen3": _ProbeProvider([0.0, 0.0, 1.0]),
    }

    def _wrap(agent: object) -> _ProbeProvider:
        probe = probes[agent._active_model_key]  # type: ignore[attr-defined]
        probe.agent = agent  # type: ignore[attr-defined]
        return probe

    monkeypatch.setattr(kb_store_mod, "AgentEmbeddingProvider", _wrap)
    old_store = kb_store_mod._open_store("bge-m3")
    new_store = kb_store_mod._open_store("text-embedding-v3")
    assert (
        old_store.backend.vector_store_provider
        is new_store.backend.vector_store_provider
    )
    old = await old_store.put(
        "旧退款政策。",
        collection="kb",
        kind="chunk",
        vector=True,
    )
    await old_store.backend.vector_store_provider.delete_records([old["id"]])
    fresh = await new_store.put(
        "新的发票说明。",
        collection="kb",
        kind="chunk",
        vector=True,
    )
    hits = await new_store.backend.vector_store_provider.search_by_embedding(
        [0.5, 0.5, 0.0],
        filters={"collection": "kb"},
        limit=5,
    )
    assert any(hit["id"] == fresh["id"] for hit in hits)


@pytest.mark.asyncio
async def test_put_vector_indexes_sqlite(tmp_path: Path, monkeypatch) -> None:
    store, probes = _open(tmp_path, monkeypatch, "text-embedding-v3")
    assert store.backend.vector_store_provider.name == "sqlite"
    ref = await store.put(
        {"text": "alpha handbook"},
        collection="kb",
        kind="chunk",
        vector=True,
    )
    assert probes["text-embedding-v3"].calls
    hits = await store.backend.vector_store_provider.search_by_embedding(
        [0.5, 0.5, 0.0],
        filters={"collection": "kb"},
        limit=5,
    )
    assert any(hit["id"] == ref["id"] for hit in hits)
