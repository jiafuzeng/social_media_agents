from __future__ import annotations

from pathlib import Path

import pytest

from integrated_agent.rag.models import (
    ChatKbHit,
    ChatKbIn,
    CreateChunkIn,
    CreateDocumentIn,
    KbChunkOut,
    SearchKbIn,
)
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from tests.test_kb_knowledge import _auth_user, _client, _knowledge


@pytest.mark.asyncio
async def test_chat_empty_library_does_not_need_model(
    tmp_path: Path, monkeypatch
) -> None:
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    payload = await knowledge.search(
        "user-a",
        SearchKbIn(query="退款", embedding_profile_id="bge-m3"),
    )
    assert payload.hits == []
    from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat

    out = await answer_kb_chat(
        knowledge,
        "user-a",
        ChatKbIn(query="退款要凭证吗", embedding_profile_id="bge-m3"),
    )
    assert out.hits == []
    assert out.cited_kb_ids == []
    assert "无法根据知识库作答" in out.answer
    assert out.limitations == []
    assert out.rewritten_query == "退款要凭证吗"
    assert out.retrieval_queries == []


def _chunk(**kwargs) -> KbChunkOut:
    return KbChunkOut(
        chunk_id=str(kwargs.get("chunk_id") or "c1"),
        doc_id="d1",
        text=str(kwargs["text"]),
        window=kwargs.get("window"),
        header_path=kwargs.get("header_path"),
        ordinal=int(kwargs.get("ordinal") or 0),
        status="active",
        enabled=True,
        diverged=False,
        embedding_profile_id="bge-m3",
    )


def _hit(chunk_id: str, text: str) -> ChatKbHit:
    return ChatKbHit(
        kb_id="k1",
        chunk_id=chunk_id,
        doc_id="d1",
        text=text,
        embedding_profile_id="bge-m3",
    )


def test_complete_passage_prefers_window() -> None:
    from integrated_agent.runtimes.matrix.analysis.workflows.chunks.kb_chat.pipeline import (
        _complete_passage,
    )

    passage, original = _complete_passage(
        _hit("c1", "需提供凭证。"),
        [
            _chunk(
                chunk_id="c1",
                text="需提供凭证。",
                window="七天无理由退款需提供凭证。超过七天仅支持质量问题。",
                ordinal=0,
            )
        ],
    )
    assert original == "需提供凭证。"
    assert "超过七天" in passage


def test_complete_passage_joins_header_section() -> None:
    from integrated_agent.runtimes.matrix.analysis.workflows.chunks.kb_chat.pipeline import (
        _complete_passage,
    )

    passage, original = _complete_passage(
        _hit("c2", "需提供凭证。"),
        [
            _chunk(chunk_id="c1", text="退款时限七天。", header_path="售后/退款", ordinal=0),
            _chunk(chunk_id="c2", text="需提供凭证。", header_path="售后/退款", ordinal=1),
            _chunk(chunk_id="c3", text="发票说明。", header_path="售后/发票", ordinal=2),
        ],
    )
    assert original == "需提供凭证。"
    assert "退款时限七天" in passage
    assert "需提供凭证" in passage
    assert "发票说明" not in passage


def test_complete_passage_joins_neighbors() -> None:
    from integrated_agent.runtimes.matrix.analysis.workflows.chunks.kb_chat.pipeline import (
        _complete_passage,
    )

    passage, _original = _complete_passage(
        _hit("c1", "七天无理由退款需提供凭证。"),
        [
            _chunk(chunk_id="c1", text="七天无理由退款需提供凭证。", ordinal=0),
            _chunk(chunk_id="c2", text="电子发票在发货后自动开具。", ordinal=1),
        ],
    )
    assert "电子发票" in passage


def test_complete_passage_keeps_local_span() -> None:
    from integrated_agent.runtimes.matrix.analysis.workflows.chunks.kb_chat.pipeline import (
        _complete_passage,
    )

    siblings = [
        _chunk(
            chunk_id=f"c{index}",
            text=f"条款{index}。",
            header_path="红线",
            ordinal=index,
        )
        for index in range(6)
    ]
    passage, original = _complete_passage(_hit("c2", "条款2。"), siblings)
    assert original == "条款2。"
    assert "条款2" in passage
    assert "条款5" not in passage
    assert "条款0" not in passage


@pytest.mark.asyncio
async def test_chat_projects_k1_and_cites(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.fakes import install_scripted_ask
    from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat

    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="七天无理由退款需提供凭证。",
            embedding_profile_id="bge-m3",
        ),
    )
    out = await answer_kb_chat(
        knowledge,
        "user-a",
        ChatKbIn(query="退款要凭证吗", embedding_profile_id="bge-m3"),
    )
    assert out.hits
    assert out.hits[0].kb_id == "k1"
    assert "record_id" not in out.hits[0].model_dump()
    assert "[[kb:k1]]" in out.answer
    assert out.cited_kb_ids == ["k1"]
    assert out.rewritten_query
    assert out.retrieval_queries == [out.rewritten_query]


@pytest.mark.asyncio
async def test_chat_generate_failure_keeps_hits(
    tmp_path: Path, monkeypatch
) -> None:
    from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat

    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="七天无理由退款需提供凭证。",
            embedding_profile_id="bge-m3",
        ),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.analysis.workflows.chunks.kb_chat.pipeline.Agently.create_agent",
        boom,
    )
    out = await answer_kb_chat(
        knowledge,
        "user-a",
        ChatKbIn(query="退款要凭证吗", embedding_profile_id="bge-m3"),
    )
    assert out.hits
    assert out.hits[0].kb_id == "k1"
    assert "kb_chat_failed" in out.limitations
    assert "生成回答失败" in out.answer
    assert out.rewritten_query
    assert out.retrieval_queries


class _SplitTwo(ScriptedMatrixModel):
    async def kb_chat_split(self, *, rewritten_query: str, history: list | None = None) -> dict:
        del history
        text = str(rewritten_query or "").strip()
        return {"retrieval_queries": [text, "购买凭证要求"]}


@pytest.mark.asyncio
async def test_chat_split_retrieves_each_query(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.fakes import install_scripted_ask
    from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat

    install_scripted_ask(monkeypatch, _SplitTwo())
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="七天无理由退款需提供凭证。",
            embedding_profile_id="bge-m3",
        ),
    )
    out = await answer_kb_chat(
        knowledge,
        "user-a",
        ChatKbIn(query="退款要凭证吗", embedding_profile_id="bge-m3"),
    )
    assert out.retrieval_queries == [out.rewritten_query, "购买凭证要求"]
    assert out.hits
    assert out.hits[0].kb_id == "k1"
    assert "[[kb:k1]]" in out.answer


@pytest.mark.asyncio
async def test_chat_expand_joins_neighbor_chunk(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.fakes import install_scripted_ask
    from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat

    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="七天无理由退款需提供凭证。",
            embedding_profile_id="bge-m3",
        ),
    )
    await knowledge.create_chunk(
        "user-a",
        doc.doc_id,
        CreateChunkIn(text="电子发票在发货后自动开具。"),
    )
    out = await answer_kb_chat(
        knowledge,
        "user-a",
        ChatKbIn(query="退款要凭证吗", embedding_profile_id="bge-m3"),
    )
    assert out.hits
    assert any(
        item.context and "电子发票" in item.context and "退款" in item.context
        for item in out.hits
    )
    assert not any(
        "电子发票" in item.text and "退款" in item.text for item in out.hits
    )
    kb_ids = [item.kb_id for item in out.hits]
    assert kb_ids == [f"k{index}" for index in range(1, len(kb_ids) + 1)]
    if len(out.hits) >= 2:
        assert len({item.text for item in out.hits}) == len(out.hits)


@pytest.mark.asyncio
async def test_chat_expand_failure_keeps_original_hits(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.fakes import install_scripted_ask
    from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat

    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    knowledge, _probes = _knowledge(tmp_path, monkeypatch)
    doc = await knowledge.create_document(
        "user-a",
        CreateDocumentIn(
            text="七天无理由退款需提供凭证。",
            embedding_profile_id="bge-m3",
        ),
    )
    await knowledge.create_chunk(
        "user-a",
        doc.doc_id,
        CreateChunkIn(text="电子发票在发货后自动开具。"),
    )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("list_chunks down")

    monkeypatch.setattr(knowledge, "list_chunks", boom)
    out = await answer_kb_chat(
        knowledge,
        "user-a",
        ChatKbIn(query="退款要凭证吗", embedding_profile_id="bge-m3"),
    )
    assert out.hits
    assert "kb_expand_failed" in out.limitations
    assert not any(
        "电子发票" in item.text and "退款" in item.text for item in out.hits
    )
    assert all(not item.hit_text for item in out.hits)
    assert all(not item.context for item in out.hits)


def test_http_chat_requires_auth_and_profile(tmp_path: Path, monkeypatch) -> None:
    client, _knowledge_store, _probes = _client(tmp_path, monkeypatch)
    with client:
        owner, _uid = _auth_user(client, "owner")
        created = client.post(
            "/api/kb/documents",
            headers=owner,
            json={"text": "七天无理由退款需提供凭证。", "embedding_profile_id": "bge-m3"},
        )
        assert created.status_code == 201, created.text
        missing = client.post(
            "/api/kb/chat",
            json={"query": "退款", "embedding_profile_id": "bge-m3"},
        )
        assert missing.status_code == 401
        omitted = client.post(
            "/api/kb/chat",
            headers=owner,
            json={"query": "退款"},
        )
        assert omitted.status_code == 422
        unknown = client.post(
            "/api/kb/chat",
            headers=owner,
            json={"query": "退款", "embedding_profile_id": "no-such"},
        )
        assert unknown.status_code == 422
        ok = client.post(
            "/api/kb/chat",
            headers=owner,
            json={"query": "退款要凭证吗", "embedding_profile_id": "bge-m3"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["hits"][0]["kb_id"] == "k1"
        assert "[[kb:k1]]" in body["answer"]
        assert body["cited_kb_ids"] == ["k1"]
        assert body["rewritten_query"]
        assert body["retrieval_queries"]
