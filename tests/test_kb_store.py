from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agently.core.storage import RecordStore

from integrated_agent.config import KB_EMBEDDING_AGENTS, KB_RECORD_ROOT, PROJECT_ROOT
from integrated_agent.runtimes.matrix import kb_store as kb_store_mod
from integrated_agent.runtimes.matrix.kb_store import kb_store


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
        "openai-small": _ProbeProvider([1.0, 0.0, 0.0]),
        "bge-m3": _ProbeProvider([0.0, 1.0, 0.0]),
        "qwen3": _ProbeProvider([0.0, 0.0, 1.0]),
    }

    def _wrap(agent: object) -> _ProbeProvider:
        probe = probes[agent._active_model_key]  # type: ignore[attr-defined]
        probe.agent = agent  # type: ignore[attr-defined]
        return probe

    monkeypatch.setattr(kb_store_mod, "AgentEmbeddingProvider", _wrap)
    return kb_store_mod._open_store(profile_id), probes


def test_committed_profiles_yaml_shape() -> None:
    payload = yaml.safe_load(
        (PROJECT_ROOT / "data/matrix/embedding_profiles.yaml").read_text(encoding="utf-8")
    )
    assert payload["default"] in payload["profiles"]
    assert set(payload["profiles"]) == set(KB_EMBEDDING_AGENTS)


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


def test_db_path_is_config_root_without_agently() -> None:
    store = kb_store["bge-m3"]
    db_path = Path(store.backend.db_path)
    assert db_path == KB_RECORD_ROOT / "records.db"
    assert db_path == store.backend.root / "records.db"
    assert ".agently" not in db_path.parts
    assert store.backend.vector_store_provider.name == "chroma"
    assert (store.backend.root / "vectors" / "chroma").is_dir()


def test_record_store_path_constructor_nests_agently(tmp_path: Path) -> None:
    db_path = Path(
        RecordStore(tmp_path / "kb" / "records", mode="read_write").backend.db_path
    )
    assert ".agently" in db_path.parts


@pytest.mark.asyncio
async def test_put_vector_indexes_chroma(tmp_path: Path, monkeypatch) -> None:
    store, probes = _open(tmp_path, monkeypatch, "openai-small")
    assert store.backend.vector_store_provider.name == "chroma"
    ref = await store.put(
        {"text": "alpha handbook"},
        collection="kb",
        kind="chunk",
        vector=True,
    )
    assert probes["openai-small"].calls
    hits = await store.backend.vector_store_provider.search_by_embedding(
        [1.0, 0.0, 0.0],
        filters={"collection": "kb"},
        limit=5,
    )
    assert any(hit["id"] == ref["id"] for hit in hits)
