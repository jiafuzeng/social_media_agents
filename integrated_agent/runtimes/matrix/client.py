from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from integrated_agent.gateway import GatewayEvent, GatewayRequest


# 企业微信里用「thread:demo-1 回评」进入回复 Flow；否则默认创作。
THREAD_RE = re.compile(r"^thread:([A-Za-z0-9._-]+)(?:\s+(.*))?$", re.DOTALL)


class MatrixServiceRuntime:
    """Gateway 侧的 matrix 运行时：把远端 HTTP/SSE 翻译成 GatewayEvent，不解释 Flow 内部对象。"""

    def __init__(self, base_url: str, *, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        body = _task_body(request)
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            trust_env=False,
        ) as client:
            response = await client.post("/v1/matrix/tasks", json=body)
            response.raise_for_status()
            accepted = cast(dict[str, Any], response.json())
            yield GatewayEvent(
                "run.created",
                {
                    "runtime_key": "matrix",
                    "task_id": accepted["task_id"],
                },
            )
            package_sent = False
            async with client.stream("GET", str(accepted["events_url"])) as stream:
                stream.raise_for_status()
                event_type = ""
                data_lines: list[str] = []
                async for line in stream.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line.removeprefix("event:").strip()
                    elif line.startswith("data:"):
                        data_lines.append(line.removeprefix("data:").strip())
                    elif not line and event_type:
                        payload = json.loads("\n".join(data_lines))
                        data = cast(dict[str, Any], payload.get("data", {}))
                        mapped = _map_event(
                            event_type,
                            data,
                            accepted=accepted,
                            package_sent=package_sent,
                        )
                        if event_type == "package.ready":
                            package_sent = True
                        for event in mapped:
                            yield event
                        if event_type == "task.failed":
                            return
                        if event_type == "task.completed":
                            # SSE 漏了 package.ready 时，用任务快照里的 summary 补一条正文。
                            if not package_sent:
                                snapshot = (
                                    await client.get(str(accepted["task_url"]))
                                ).json()
                                summary = str(
                                    (snapshot.get("result") or {}).get("summary") or ""
                                )
                                if summary:
                                    yield GatewayEvent(
                                        "message.delta",
                                        {"delta": summary},
                                    )
                            yield GatewayEvent(
                                "run.completed",
                                {
                                    "runtime_key": "matrix",
                                    "task_id": accepted["task_id"],
                                },
                            )
                            return
                        event_type = ""
                        data_lines = []


def _task_body(request: GatewayRequest) -> dict[str, Any]:
    """GatewayRequest → MatrixTaskCreate。这里绑定 scenario，禁止把 auto 交给 HTTP。"""

    match = THREAD_RE.match(request.text.strip())
    if match:
        thread_key = match.group(1)
        remainder = (match.group(2) or "").strip()
        return {
            "text": remainder or request.text.strip(),
            "scenario": "reply",
            "thread_key": thread_key,
            "requester": request.session_id,
            "channel": "gateway",
        }
    return {
        "text": request.text,
        "scenario": "compose",
        "requester": request.session_id,
        "channel": "gateway",
    }


def _map_event(
    event_type: str,
    data: dict[str, Any],
    *,
    accepted: dict[str, Any],
    package_sent: bool,
) -> list[GatewayEvent]:
    """矩阵稳定事件 → IM 能消费的 GatewayEvent。package.ready 只映射一次正文。"""

    del accepted
    if event_type in {"stage.started", "stage.completed", "work_item.ready"}:
        return [
            GatewayEvent(
                "status.update",
                {"event_type": event_type, **data},
            )
        ]
    if event_type == "draft.ready":
        return [GatewayEvent("evidence.ready", data)]
    if event_type == "package.ready":
        if package_sent:
            return []
        summary = str(data.get("summary") or "")
        if not summary:
            return []
        return [GatewayEvent("message.delta", {"delta": summary})]
    if event_type == "task.failed":
        return [GatewayEvent("run.failed", data)]
    return []
