from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field, model_validator

from integrated_agent.runtimes.matrix.models import (
    CommentIn,
    DomainModel,
    MatrixTaskCreate,
    TaskAccepted,
    TaskEvent,
    TaskSnapshot,
)
from integrated_agent.runtimes.matrix.service import (
    MatrixTaskService,
    ServiceBusyError,
)


class ComposeHttpIn(DomainModel):
    text: str = Field(min_length=1)
    platform_keys: list[str] = Field(default_factory=list)
    account_key: str = "default"
    brand_key: str = "default"
    need_trends: bool = False
    requester: str = "course-user"
    channel: str = "web"


class ReplyHttpIn(DomainModel):
    text: str = Field(min_length=1)
    platform_keys: list[str] = Field(default_factory=list)
    account_key: str = "default"
    brand_key: str = "default"
    thread_key: str | None = None
    comments: list[CommentIn] | None = None
    requester: str = "course-user"
    channel: str = "web"

    @model_validator(mode="after")
    def require_comments(self) -> "ReplyHttpIn":
        if not self.thread_key and not self.comments:
            raise ValueError("reply requires thread_key or comments")
        return self


def event_to_sse(event: TaskEvent) -> str:
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


async def _submit(service: MatrixTaskService, command: MatrixTaskCreate) -> TaskAccepted:
    try:
        return await service.submit(command)
    except ServiceBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc


def build_matrix_router(
    service: MatrixTaskService,
    *,
    static_root: Path | None = None,
) -> APIRouter:
    router = APIRouter(tags=["matrix"])

    if static_root is not None:

        @router.get("/matrix", response_class=FileResponse)
        async def matrix_index() -> FileResponse:
            return FileResponse(static_root / "matrix.html")

    @router.post("/api/create", response_model=TaskAccepted, status_code=202)
    async def create_compose(command: ComposeHttpIn) -> TaskAccepted:
        return await _submit(
            service,
            MatrixTaskCreate(scenario="compose", **command.model_dump()),
        )

    @router.post("/api/reply", response_model=TaskAccepted, status_code=202)
    async def create_reply(command: ReplyHttpIn) -> TaskAccepted:
        return await _submit(
            service,
            MatrixTaskCreate(scenario="reply", **command.model_dump()),
        )

    @router.post("/v1/matrix/tasks", response_model=TaskAccepted, status_code=202)
    async def create_matrix_task(command: MatrixTaskCreate) -> TaskAccepted:
        return await _submit(service, command)

    @router.get("/v1/matrix/tasks/{task_id}", response_model=TaskSnapshot)
    async def get_matrix_task(task_id: str) -> TaskSnapshot:
        snapshot = await service.get(task_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="task not found")
        return snapshot

    @router.get("/v1/matrix/tasks/{task_id}/events")
    async def stream_matrix_events(
        task_id: str,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
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
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
