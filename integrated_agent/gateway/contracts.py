"""Gateway 与 Runtime 之间的稳定契约。

Transport（企微 / HTTP）只构造 GatewayRequest，并消费 GatewayEvent；
运行时不读取企业微信原始 frame。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class GatewayAttachment:
    """客户端无关的附件描述（本地已落盘路径）。"""

    path: Path
    filename: str
    mime_type: str | None = None


@dataclass(frozen=True)
class GatewayRequest:
    """统一入站请求：文本、会话与可选附件。"""

    text: str
    session_id: str
    attachments: tuple[GatewayAttachment, ...] = ()


@dataclass(frozen=True)
class GatewayEvent:
    """统一出站事件；type 约定见各 Runtime / Presenter。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


class RouteDecision(BaseModel):
    """意图模型的结构化输出：只能选宿主提供的 runtime_key。"""

    model_config = ConfigDict(extra="forbid")

    runtime_key: str


class IntentModel(Protocol):
    """在 offered 运行时卡片中做语义路由。"""

    async def classify(
        self,
        *,
        text: str,
        offered: list[dict[str, str]],
    ) -> RouteDecision: ...


class AgentRuntime(Protocol):
    """可被 Gateway 调度的执行运行时协议。"""

    def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayEvent]: ...
