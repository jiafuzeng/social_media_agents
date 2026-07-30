"""把 GatewayEvent 流渲染为企业微信流式文本 + 终态文件消息。

中间帧约每 250ms 节流一次；final 帧只发送一次。
制品优先用原生文件消息回传，失败时用 markdown 说明错误。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aibot import WSClient, generate_req_id

from integrated_agent.gateway import GatewayEvent
from integrated_agent.storage import ArtifactStore

from .media import WeComMediaClient


class WeComEventPresenter:
    """消费 Gateway 事件并驱动企微 reply_stream / 文件投递。"""

    def __init__(
        self,
        *,
        client: WSClient,
        media: WeComMediaClient,
        artifact_store: ArtifactStore,
    ) -> None:
        self.client = client
        self.media = media
        self.artifact_store = artifact_store

    async def reply(
        self,
        frame: dict[str, Any],
        events: AsyncIterator[GatewayEvent],
        *,
        session_id: str,
    ) -> None:
        """流式回传文本，结束后按序发送已收集的制品文件。"""
        stream_id = generate_req_id("agent")
        cumulative = ""
        status = ""
        last_sent_at = 0.0
        generated_files: list[tuple[str, Path]] = []
        async for event in events:
            if event.type == "route.selected":
                status = (
                    f"正在调用 {event.data['runtime_key']} 运行时…\n\n"
                )
            elif event.type == "run.created" and event.data.get(
                "attachment_count"
            ):
                status = "文件已接收，正在处理…\n\n"
            elif event.type == "status.update":
                status = (
                    f"正在执行 {event.data.get('stage', 'analysis')}…\n\n"
                )
            elif event.type == "chart.ready":
                chart_count = int(event.data.get("chart_count", 0))
                if chart_count:
                    cumulative += (
                        f"\n\n已生成 {chart_count} 个图表，可在 Web 端查看。"
                    )
            elif event.type == "message.delta":
                cumulative += str(event.data.get("delta", ""))
            elif event.type == "artifact.ready":
                filename = str(event.data.get("filename", "生成文件"))
                artifact_path = self._resolve_artifact_path(
                    event,
                    filename=filename,
                )
                if artifact_path is not None:
                    generated_files.append((filename, artifact_path))
                    cumulative += f"\n\n已生成文件：{filename}"
                else:
                    cumulative += (
                        "\n\n文件生成成功，但没有找到可回传的产物："
                        f"{filename}"
                    )
            elif event.type == "run.failed":
                cumulative += (
                    "\n\n处理失败："
                    f"{event.data.get('message', 'unknown')}"
                )
            # 节流：避免每个 delta 都打企微接口
            now = time.monotonic()
            if now - last_sent_at >= 0.25:
                await self.client.reply_stream(
                    frame,
                    stream_id,
                    status + cumulative + "▍",
                    False,
                )
                last_sent_at = now
        # final=True 只发一次，去掉流式光标
        await self.client.reply_stream(
            frame,
            stream_id,
            cumulative or status or "（无输出）",
            True,
        )
        for filename, artifact_path in generated_files:
            try:
                await self.media.send_file(session_id, artifact_path)
            except Exception as exc:
                await self.client.send_message(
                    session_id,
                    {
                        "msgtype": "markdown",
                        "markdown": {
                            "content": (
                                f"文件附件回传失败：{filename}。"
                                f"请稍后重试。\n\n错误信息：{exc}"
                            ),
                        },
                    },
                )

    def _resolve_artifact_path(
        self,
        event: GatewayEvent,
        *,
        filename: str,
    ) -> Path | None:
        """优先用事件内本地 path，否则按 artifact_id 从仓库解析。"""
        raw_path = str(event.data.get("path", ""))
        if raw_path:
            path = Path(raw_path)
            return path if path.is_file() else None
        artifact_id = str(event.data.get("artifact_id", ""))
        artifact = self.artifact_store.resolve(artifact_id, filename)
        return artifact.path if artifact is not None else None
