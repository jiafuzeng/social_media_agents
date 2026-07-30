"""问数服务的 FastAPI 入站协议。

负责：任务受理（202）、快照查询、SSE 事件流、制品下载与静态页。
队列满时返回 503 + Retry-After；SSE 在终态事件后结束。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from integrated_agent.runtimes.question.models import (
    TaskAccepted,
    TaskCreate,
    TaskEvent,
    TaskSnapshot,
)
from integrated_agent.runtimes.question.service import (
    QuestionTaskService,
    ServiceBusyError,
)
from integrated_agent.storage import ArtifactStore


def event_to_sse(event: TaskEvent) -> str:
    """把内部 TaskEvent 编码为标准 SSE 帧（含 id/event/data）。"""
    payload = {
        "task_id": event.task_id,
        "sequence": event.sequence,
        "data": event.data,
    }
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def create_question_api(
    service: QuestionTaskService,
    *,
    static_root: Path,
    artifacts_root: Path,
) -> FastAPI:
    """创建问数 API；生命周期内启动/停止 Worker 池。"""
    artifact_store = ArtifactStore(artifacts_root)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="问数智能体流式服务", version="1.0.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", response_class=FileResponse)
    async def index() -> FileResponse:
        """演示前端首页。"""
        return FileResponse(static_root / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """存活探针。"""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        """就绪探针：Worker 池全部存活才返回 ready。"""
        if not service.ready:
            raise HTTPException(status_code=503, detail="worker is not ready")
        return {"status": "ready"}

    @app.get("/v1/artifacts/{artifact_id}/{filename}")
    async def download_artifact(
        artifact_id: str,
        filename: str,
    ) -> FileResponse:
        """按不透明 ID 下载已发布制品。"""
        artifact = artifact_store.resolve(artifact_id, filename)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(
            artifact.path,
            media_type=artifact.mime_type,
            filename=artifact.filename,
        )

    @app.post("/v1/tasks", response_model=TaskAccepted, status_code=202)
    async def create_task(command: TaskCreate) -> TaskAccepted:
        """受理问数任务；队列满时 503。"""
        try:
            return await service.submit(command)
        except ServiceBusyError as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc),
                headers={"Retry-After": "1"},
            ) from exc

    @app.get("/v1/tasks/{task_id}", response_model=TaskSnapshot)
    async def get_task(task_id: str) -> TaskSnapshot:
        """查询任务快照（状态 / 结果 / 错误）。"""
        snapshot = await service.get(task_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="task not found")
        return snapshot

    @app.get("/v1/tasks/{task_id}/events")
    async def stream_events(
        task_id: str,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        """从 sequence=after 之后订阅任务事件；支持断线续传。"""
        if await service.get(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")

        async def generate() -> AsyncIterator[str]:
            cursor = after
            while True:
                batch = service.events.list_for(task_id)[cursor:]
                for event in batch:
                    cursor = event.sequence
                    yield event_to_sse(event)
                    if event.event_type in {"task.completed", "task.failed"}:
                        return
                snapshot = await service.get(task_id)
                # 任务已终态但尚无终态事件时，继续等事件补齐，避免提前断开
                if snapshot is not None and snapshot.status in {"completed", "failed"}:
                    terminal_events = {"task.completed", "task.failed"}
                    if any(
                        event.event_type in terminal_events
                        for event in service.events.list_for(task_id)[cursor:]
                    ):
                        continue
                try:
                    await service.events.wait_for_change(task_id, cursor, timeout=15.0)
                except asyncio.TimeoutError:
                    # SSE 心跳，防止代理断开空闲连接
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
