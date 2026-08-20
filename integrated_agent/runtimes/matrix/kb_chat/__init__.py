"""知识库召回聊天。独立 TriggerFlow，不经过写帖 / 回评。"""

from __future__ import annotations

from integrated_agent.rag.models import ChatKbIn, ChatKbOut
from integrated_agent.runtimes.matrix.rag.knowledge import KnowledgeStore

from .flow import KB_CHAT_FLOW, PIPELINE_VERSION, run_kb_chat


async def answer_kb_chat(
    knowledge: KnowledgeStore,
    user_id: str,
    command: ChatKbIn,
) -> ChatKbOut:
    return await run_kb_chat(command, knowledge=knowledge, user_id=user_id)


__all__ = [
    "KB_CHAT_FLOW",
    "PIPELINE_VERSION",
    "answer_kb_chat",
    "run_kb_chat",
]
