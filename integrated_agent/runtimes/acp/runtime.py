"""ACP / Codex 运行时：每个 Gateway session 对应一个长期 ACP client。

不同 IM 会话不共享 ACP session；仅通过 /agent codex 显式进入。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Protocol
from uuid import uuid4

from integrated_agent.gateway import GatewayEvent, GatewayRequest

from .client import CodexAcpClient


class AcpSession(Protocol):
    """ACP 会话最小协议：流式 prompt + 关闭。"""

    session_id: str | None

    def prompt_stream(self, text: str) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


class AcpAgentRuntime:
    """把 Codex ACP 文本流包装为 GatewayEvent。"""

    def __init__(
        self,
        client_factory: Callable[[], AcpSession] | None = None,
    ) -> None:
        self._client_factory = client_factory or (
            lambda: CodexAcpClient(
                cwd=os.environ.get("CODEX_CWD"),
                auto_approve=(
                    os.environ.get("CODEX_AUTO_APPROVE", "").lower()
                    in {"1", "true", "yes"}
                ),
            )
        )
        # gateway session_id → 独立 ACP client（进程+会话）
        self._clients: dict[str, AcpSession] = {}

    def _get_client(self, session_id: str) -> AcpSession:
        """懒创建并缓存会话级 ACP client。"""
        client = self._clients.get(session_id)
        if client is None:
            client = self._client_factory()
            self._clients[session_id] = client
        return client

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        """在对应 ACP 会话中跑一轮 prompt，并透传 message.delta。"""
        client = self._get_client(request.session_id)
        yield GatewayEvent(
            "run.created",
            {
                "runtime_key": "codex",
                "run_id": uuid4().hex,
                "acp_session_id": client.session_id,
            },
        )
        try:
            async for delta in client.prompt_stream(request.text):
                yield GatewayEvent(
                    "message.delta",
                    {"delta": delta},
                )
        except Exception as exc:
            yield GatewayEvent(
                "run.failed",
                {
                    "runtime_key": "codex",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return
        yield GatewayEvent(
            "run.completed",
            {
                "runtime_key": "codex",
                "acp_session_id": client.session_id,
            },
        )

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

