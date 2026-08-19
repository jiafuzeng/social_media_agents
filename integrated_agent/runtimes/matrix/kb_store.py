"""知识库 RecordStore。

库文件在 KB_RECORD_ROOT/records.db。必须先 LocalRecordStore(目录) 再交给 RecordStore：
RecordStore(路径) 会套成 目录/.agently/records/records.db。
向量用 Chroma（records/vectors/chroma）。
embeddings 配在全局 model_pool；按 profile_id 取对应 RecordStore。
"""

from __future__ import annotations

from agently.core.storage import (
    AgentEmbeddingProvider,
    ChromaVectorStoreProvider,
    LocalRecordStore,
    RecordStore,
)

from integrated_agent.config import KB_EMBEDDING_AGENTS, KB_RECORD_ROOT


def _open_store(profile_id: str) -> RecordStore:
    store = RecordStore(
        LocalRecordStore(KB_RECORD_ROOT, create=True, mode="read_write"),
        mode="read_write",
    )
    store.backend.embedding_provider = AgentEmbeddingProvider(
        KB_EMBEDDING_AGENTS[profile_id]
    )
    store.backend.vector_store_provider = ChromaVectorStoreProvider(
        store.backend.root / "vectors" / "chroma",
        create=True,
        mode="read_write",
        collection_name=f"kb_{profile_id}",
    )
    store.backend.ensure_vector_index()
    return store


kb_store = {
    profile_id: _open_store(profile_id) for profile_id in KB_EMBEDDING_AGENTS
}

if __name__ == "__main__":
    import asyncio

    PROFILE_ID = "bge-m3"
    USER_ID = "demo-user"
    DOC_ID = "demo-handbook"

    async def _seed() -> None:
        store = kb_store[PROFILE_ID]
        print(store.backend.db_path)

        doc = await store.put(
            {"title": "售后手册", "strategy": "sentence"},
            collection="kb",
            kind="document",
            summary="售后手册",
            scope={"user_id": USER_ID, "doc_id": DOC_ID},
            meta={
                "status": "ready",
                "enabled": True,
                "source": "paste",
                "title": "售后手册",
                "embedding_profile_id": PROFILE_ID,
                "chunk_count": 2,
            },
        )
        chunks = (
            {
                "chunk_id": "c1",
                "ordinal": 0,
                "header_path": "退款政策",
                "text": "七天无理由退款需提供购买凭证和完好包装。超过七天仅支持质量问题退换。",
            },
            {
                "chunk_id": "c2",
                "ordinal": 1,
                "header_path": "发票",
                "text": "电子发票在发货后自动开具，可在订单详情页下载。纸质发票需下单时备注。",
            },
        )
        for item in chunks:
            text = item["text"]
            chunk = await store.put(
                {
                    "text": text,
                    "window": text,
                    "header_path": item["header_path"],
                    "element_type": "paragraph",
                    "char_start": 0,
                    "char_end": len(text),
                },
                collection="kb",
                kind="chunk",
                summary=text,
                scope={
                    "user_id": USER_ID,
                    "doc_id": DOC_ID,
                    "chunk_id": item["chunk_id"],
                },
                meta={
                    "status": "active",
                    "enabled": True,
                    "doc_status": "ready",
                    "doc_enabled": True,
                    "embedding_profile_id": PROFILE_ID,
                    "ordinal": item["ordinal"],
                },
                indexed=True,
                vector=True,
            )
            await store.link(doc, chunk, "contains")
            print("put", item["chunk_id"], chunk["id"])

        package = await store.retrieve(
            "退款需要什么凭证",
            method="hybrid",
            rerank=False,
            selection="top_n",
            top_n=4,
            scope={"user_id": USER_ID},
            filters={
                "collection": "kb",
                "kind": "chunk",
                "meta.status": "active",
                "meta.enabled": True,
                "meta.doc_status": "ready",
                "meta.doc_enabled": True,
                "meta.embedding_profile_id": PROFILE_ID,
            },
        )
        for item in package["items"]:
            ref = item.get("ref")
            if ref is None:
                continue
            print("hit", ref.get("scope", {}).get("chunk_id"), item.get("score"), item.get("summary"))

    asyncio.run(_seed())
