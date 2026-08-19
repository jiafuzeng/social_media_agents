from __future__ import annotations

from agently.core.storage import AgentEmbeddingProvider

from integrated_agent.config import (
    KB_DEFAULT_EMBEDDING_PROFILE,
    KB_EMBEDDING_AGENTS,
)
from integrated_agent.rag.models import EmbeddingProfileListOut


async def embed_profile_texts(profile_id: str, texts: list[str]) -> list[list[float]]:
    """走 Step 1 的 embeddings Agent，不写 RecordStore。"""
    return await AgentEmbeddingProvider(KB_EMBEDDING_AGENTS[profile_id]).embed_texts(texts)


def list_embedding_profiles() -> EmbeddingProfileListOut:
    """给顶栏用的可选模型。只回 id，不含密钥。"""
    return EmbeddingProfileListOut(
        default=KB_DEFAULT_EMBEDDING_PROFILE,
        profiles=list(KB_EMBEDDING_AGENTS.keys()),
    )
