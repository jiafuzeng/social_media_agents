"""独立召回聊天 TriggerFlow。与 COMPOSE_FLOW / REPLY_FLOW 无节点或契约共享。"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlow

from integrated_agent.rag.models import ChatKbIn, ChatKbOut
from integrated_agent.runtimes.matrix.knowledge import KnowledgeStore

from .pipeline import (
    kb_chat_analyze,
    kb_chat_expand,
    kb_chat_prelude,
    kb_chat_retrieve,
    kb_chat_rewrite,
    kb_chat_split,
    kb_chat_summarize,
)

PIPELINE_VERSION = "kb-chat-v3"

KB_CHAT_FLOW = TriggerFlow(name="kb-chat-v3")
(
    KB_CHAT_FLOW.to(kb_chat_prelude)
    .to(kb_chat_rewrite)
    .to(kb_chat_split)
    .for_each(concurrency=4)
    .to(kb_chat_retrieve)
    .end_for_each()
    .to(kb_chat_expand)
    .to(kb_chat_analyze)
    .to(kb_chat_summarize)
)


async def run_kb_chat(
    command: ChatKbIn,
    *,
    knowledge: KnowledgeStore,
    user_id: str,
) -> ChatKbOut:
    execution = KB_CHAT_FLOW.create_execution(
        concurrency=4,
        runtime_resources={
            "knowledge": knowledge,
            "kb_user_id": user_id,
        },
        auto_close=False,
        record_store=False,
    )
    await execution.async_start({"command": command.model_dump(mode="json")})
    state = await execution.async_close()
    package = cast(dict[str, Any], state["package"])
    return ChatKbOut.model_validate(package)


__all__ = ["KB_CHAT_FLOW", "PIPELINE_VERSION", "run_kb_chat"]
