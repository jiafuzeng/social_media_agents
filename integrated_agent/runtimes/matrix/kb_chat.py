"""知识库召回聊天门面。检索走 KnowledgeStore，编排走 TriggerFlow。"""

from __future__ import annotations

from integrated_agent.rag.models import ChatKbIn, ChatKbOut
from integrated_agent.runtimes.matrix.analysis.workflows.kb_chat_flow import run_kb_chat
from integrated_agent.runtimes.matrix.knowledge import KnowledgeStore


async def answer_kb_chat(
    knowledge: KnowledgeStore,
    user_id: str,
    command: ChatKbIn,
) -> ChatKbOut:
    return await run_kb_chat(command, knowledge=knowledge, user_id=user_id)
