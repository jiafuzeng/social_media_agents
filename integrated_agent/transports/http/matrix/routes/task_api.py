from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import Field

from integrated_agent.runtimes.matrix.analysis.snapshots import (
    AccountCard,
    InteractionCard,
    list_account_catalog,
    list_interaction_catalog,
)
from integrated_agent.runtimes.matrix.models import (
    MAX_COMPOSE_POSTS,
    MIN_COMPOSE_POSTS,
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


class AccountCatalogOut(DomainModel):
    accounts: list[AccountCard]


class InteractionCatalogOut(DomainModel):
    interactions: list[InteractionCard]


class ComposeHttpIn(DomainModel):
    text: str = Field(min_length=1)
    account_key: str = "default"
    need_trends: bool = False
    post_count: int | None = Field(
        default=None,
        ge=MIN_COMPOSE_POSTS,
        le=MAX_COMPOSE_POSTS,
    )
    requester: str = "course-user"
    channel: str = "web"


class ReplyHttpIn(DomainModel):
    text: str = Field(min_length=1)
    interaction_key: str = "help-first"
    reply_count: int | None = Field(
        default=None,
        ge=MIN_COMPOSE_POSTS,
        le=MAX_COMPOSE_POSTS,
    )
    comments: list[CommentIn] | None = None
    requester: str = "course-user"
    channel: str = "web"


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


def build_task_router(
    service: MatrixTaskService,
    *,
    catalog_root: Path,
) -> APIRouter:
    """工作台：人设/互动选择与写帖回评任务，对应 static/matrix.js。"""

    router = APIRouter(tags=["matrix-task"])

    @router.get("/api/accounts", response_model=AccountCatalogOut)
    async def list_accounts() -> AccountCatalogOut:
        return AccountCatalogOut(accounts=list_account_catalog(catalog_root))

    @router.get("/api/interactions", response_model=InteractionCatalogOut)
    async def list_interactions() -> InteractionCatalogOut:
        return InteractionCatalogOut(interactions=list_interaction_catalog(catalog_root))

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
