"""统一 AgentGateway：会话级运行时选择与请求转发。

路由优先级：附件强制走 agent → 会话手动绑定 → auto 时调用意图模型。
自动路由仅覆盖 AUTO_RUNTIMES（agent / question），Codex 不参与自动选择。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .contracts import (
    AgentRuntime,
    GatewayEvent,
    GatewayRequest,
    IntentModel,
)


class AgentGateway:
    """把 GatewayRequest 路由到已注册 Runtime，并透传 GatewayEvent 流。"""

    # 允许意图模型自动选择的运行时卡片（不含 codex）
    AUTO_RUNTIMES = {
        "agent": "通用任务，包括搜索、Skills、Actions、文件处理与沙盒计算",
        "question": "查询企业经营数据库、计算指标或分析经营表现",
    }

    def __init__(
        self,
        *,
        intent_model: IntentModel,
        runtimes: dict[str, AgentRuntime],
    ) -> None:
        missing = set(self.AUTO_RUNTIMES) - set(runtimes)
        if missing:
            raise ValueError(f"missing auto-route runtimes: {sorted(missing)}")
        self.intent_model = intent_model
        self.runtimes = dict(runtimes)
        # session_id → "auto" | 已注册 runtime_key
        self._session_runtimes: dict[str, str] = {}

    def current_runtime(self, session_id: str) -> str:
        """返回会话当前运行时绑定，默认 auto。"""
        return self._session_runtimes.get(session_id, "auto")

    async def handle_command(self, text: str, *, session_id: str) -> str:
        """处理 /agent 显式切换命令。"""
        parts = text.split(maxsplit=1)
        if parts[0] != "/agent":
            raise ValueError("unsupported command")
        requested = parts[1].strip() if len(parts) == 2 else ""
        if not requested:
            current = self.current_runtime(session_id)
            return (
                f"当前运行时：{current}。"
                "用法：/agent auto | agent | question | codex"
            )
        if requested != "auto" and requested not in self.runtimes:
            raise ValueError(f"unknown runtime: {requested}")
        self._session_runtimes[session_id] = requested
        return f"已切换到 {requested} 运行时。"

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        """解析路由后把请求交给对应 Runtime，并先发出 route.selected。"""
        runtime_key = self.current_runtime(request.session_id)
        route_mode = "manual"

        # 有附件时强制走通用 Agent（文件处理能力在 agent 侧）
        if request.attachments:
            runtime_key = "agent"
            route_mode = "attachment"
        elif runtime_key == "auto":
            offered = [
                {"runtime_key": key, "description": description}
                for key, description in self.AUTO_RUNTIMES.items()
            ]
            decision = await self.intent_model.classify(
                text=request.text,
                offered=offered,
            )
            runtime_key = decision.runtime_key
            # 宿主侧再校验：防止模型选出未授权 key（如 codex）
            if runtime_key not in self.AUTO_RUNTIMES:
                raise ValueError(
                    f"intent model selected unauthorized runtime: {runtime_key}"
                )
            route_mode = "auto"

        yield GatewayEvent(
            "route.selected",
            {"runtime_key": runtime_key, "mode": route_mode},
        )
        runtime = self.runtimes.get(runtime_key)
        if runtime is None:
            raise ValueError(f"runtime is unavailable: {runtime_key}")
        async for event in runtime.stream(request):
            yield event
