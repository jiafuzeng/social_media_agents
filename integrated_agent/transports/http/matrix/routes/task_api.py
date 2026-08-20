from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import Field

from integrated_agent.config import KB_DEFAULT_EMBEDDING_PROFILE, KB_EMBEDDING_AGENTS
from integrated_agent.runtimes.matrix.host.snapshots import (
    AccountCard,
    InteractionCard,
    list_account_catalog,
    list_interaction_catalog,
)
from integrated_agent.runtimes.matrix.identity import (
    IdentityError,
    parse_bearer_token,
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
from integrated_agent.runtimes.matrix.service import MatrixTaskService


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
    session_id: str = Field(min_length=1)
    embedding_profile_id: str | None = None


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
    session_id: str = Field(min_length=1)
    embedding_profile_id: str | None = None


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


def build_task_router(
    service: MatrixTaskService,
    *,
    catalog_root: Path,
) -> APIRouter:
    """工作台：人设/互动选择与写帖回评任务，对应 static/matrix.js。"""

    router = APIRouter(tags=["matrix-task"])

    def _token(
        authorization: str | None,
        x_user_token: str | None,
    ) -> str | None:
        return parse_bearer_token(authorization, x_user_token)

    def _raise_identity(error: IdentityError) -> None:
        raise HTTPException(status_code=error.status, detail=str(error)) from error

    def _resolve_profile(raw: str | None) -> str:
        profile = (raw or KB_DEFAULT_EMBEDDING_PROFILE).strip() or KB_DEFAULT_EMBEDDING_PROFILE
        if profile not in KB_EMBEDDING_AGENTS:
            raise HTTPException(status_code=422, detail="unknown embedding_profile_id")
        return profile

    async def _web_user(
        command: ComposeHttpIn | ReplyHttpIn,
        authorization: str | None,
        x_user_token: str | None,
    ):
        try:
            token = _token(authorization, x_user_token)
            user = await service.identity.user_for_token(token)
            await service.identity.get_session(token, command.session_id)
            return user
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.get("/api/accounts", response_model=AccountCatalogOut)
    async def list_accounts() -> AccountCatalogOut:
        return AccountCatalogOut(accounts=list_account_catalog(catalog_root))

    @router.get("/api/interactions", response_model=InteractionCatalogOut)
    async def list_interactions() -> InteractionCatalogOut:
        return InteractionCatalogOut(interactions=list_interaction_catalog(catalog_root))

    @router.post("/api/create", response_model=TaskAccepted, status_code=202)
    async def create_compose(
        command: ComposeHttpIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> TaskAccepted:
        user = await _web_user(command, authorization, x_user_token)
        payload = command.model_dump()
        payload["embedding_profile_id"] = _resolve_profile(command.embedding_profile_id)
        accepted = await service.submit(
            MatrixTaskCreate(scenario="compose", **payload),
            user_id=user.user_id,
        )
        await service.identity.append_turn(
            command.session_id,
            text=command.text,
            task_id=accepted.task_id,
            extra={
                "scenario": "compose",
                "account_key": command.account_key,
                "post_count": command.post_count,
                "need_trends": command.need_trends,
                "embedding_profile_id": payload["embedding_profile_id"],
            },
            last_scenario="compose",
        )
        return accepted

    @router.post("/api/reply", response_model=TaskAccepted, status_code=202)
    async def create_reply(
        command: ReplyHttpIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> TaskAccepted:
        user = await _web_user(command, authorization, x_user_token)
        payload = command.model_dump()
        payload["embedding_profile_id"] = _resolve_profile(command.embedding_profile_id)
        accepted = await service.submit(
            MatrixTaskCreate(scenario="reply", **payload),
            user_id=user.user_id,
        )
        await service.identity.append_turn(
            command.session_id,
            text=command.text,
            task_id=accepted.task_id,
            extra={
                "scenario": "reply",
                "interaction_key": command.interaction_key,
                "reply_count": command.reply_count,
                "embedding_profile_id": payload["embedding_profile_id"],
            },
            last_scenario="reply",
        )
        return accepted

    @router.post("/v1/matrix/tasks", response_model=TaskAccepted, status_code=202)
    async def create_matrix_task(command: MatrixTaskCreate) -> TaskAccepted:
        return await service.submit(command)

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
