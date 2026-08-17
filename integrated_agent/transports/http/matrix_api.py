from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field, ValidationError, model_validator

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.catalog import (
    CatalogError,
    MatrixCatalog,
    PolicyDoc,
)
from integrated_agent.runtimes.matrix.analysis.snapshots import (
    AccountCard,
    GuardrailCard,
    PlatformCard,
    SnapshotError,
    TemplateCard,
    list_account_catalog,
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


class PolicyHttpIn(DomainModel):
    term_list_id: str = Field(min_length=1)
    disclaimer: str = ""
    terms: list[str] = Field(min_length=1)


class CatalogDumpOut(DomainModel):
    accounts: list[AccountCard]
    guardrails: list[GuardrailCard]
    platforms: list[PlatformCard]
    policy: PolicyHttpIn
    templates: list[TemplateCard]


class TermInsertIn(DomainModel):
    term: str = Field(min_length=1)
    index: int | None = Field(default=None, ge=0)


class GuardrailAttachIn(DomainModel):
    guardrail_key: str = Field(min_length=1)
    index: int | None = Field(default=None, ge=0)


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
    account_key: str = "default"
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


def _catalog_call(action):
    try:
        return action()
    except CatalogError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except SnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def build_matrix_router(
    service: MatrixTaskService,
    *,
    static_root: Path | None = None,
    data_root: Path | None = None,
) -> APIRouter:
    router = APIRouter(tags=["matrix"])
    catalog_root = data_root or (PROJECT_ROOT / "data" / "matrix")
    catalog = MatrixCatalog(catalog_root)

    if static_root is not None:

        @router.get("/matrix", response_class=FileResponse)
        async def matrix_index() -> FileResponse:
            return FileResponse(static_root / "matrix.html")

    @router.get("/api/accounts", response_model=AccountCatalogOut)
    async def list_accounts() -> AccountCatalogOut:
        return AccountCatalogOut(accounts=list_account_catalog(catalog_root))

    @router.get("/api/catalog", response_model=CatalogDumpOut)
    async def get_catalog() -> CatalogDumpOut:
        return CatalogDumpOut.model_validate(_catalog_call(catalog.dump_all))

    @router.post("/api/catalog/accounts", response_model=AccountCard, status_code=201)
    async def create_account(
        command: AccountCard,
        index: int | None = Query(default=None, ge=0),
    ) -> AccountCard:
        return _catalog_call(lambda: catalog.create_account(command, index=index))

    @router.put("/api/catalog/accounts/{account_key}", response_model=AccountCard)
    async def update_account(account_key: str, command: AccountCard) -> AccountCard:
        return _catalog_call(lambda: catalog.update_account(account_key, command))

    @router.delete("/api/catalog/accounts/{account_key}", status_code=204)
    async def delete_account(account_key: str) -> None:
        _catalog_call(lambda: catalog.delete_account(account_key))

    @router.post(
        "/api/catalog/accounts/{account_key}/guardrails",
        response_model=AccountCard,
    )
    async def attach_account_guardrail(
        account_key: str, command: GuardrailAttachIn
    ) -> AccountCard:
        return _catalog_call(
            lambda: catalog.insert_account_guardrail(
                account_key, command.guardrail_key, index=command.index
            )
        )

    @router.post("/api/catalog/guardrails", response_model=GuardrailCard, status_code=201)
    async def create_guardrail(
        command: GuardrailCard,
        index: int | None = Query(default=None, ge=0),
    ) -> GuardrailCard:
        return _catalog_call(lambda: catalog.create_guardrail(command, index=index))

    @router.put("/api/catalog/guardrails/{guardrail_key}", response_model=GuardrailCard)
    async def update_guardrail(guardrail_key: str, command: GuardrailCard) -> GuardrailCard:
        return _catalog_call(lambda: catalog.update_guardrail(guardrail_key, command))

    @router.delete("/api/catalog/guardrails/{guardrail_key}", status_code=204)
    async def delete_guardrail(guardrail_key: str) -> None:
        _catalog_call(lambda: catalog.delete_guardrail(guardrail_key))

    @router.post("/api/catalog/platforms", response_model=PlatformCard, status_code=201)
    async def create_platform(
        command: PlatformCard,
        index: int | None = Query(default=None, ge=0),
    ) -> PlatformCard:
        return _catalog_call(lambda: catalog.create_platform(command, index=index))

    @router.put("/api/catalog/platforms/{platform_key}", response_model=PlatformCard)
    async def update_platform(platform_key: str, command: PlatformCard) -> PlatformCard:
        return _catalog_call(lambda: catalog.update_platform(platform_key, command))

    @router.delete("/api/catalog/platforms/{platform_key}", status_code=204)
    async def delete_platform(platform_key: str) -> None:
        _catalog_call(lambda: catalog.delete_platform(platform_key))

    @router.put("/api/catalog/policy", response_model=PolicyHttpIn)
    async def update_policy(command: PolicyHttpIn) -> PolicyHttpIn:
        updated = _catalog_call(
            lambda: catalog.update_policy(
                PolicyDoc(
                    term_list_id=command.term_list_id,
                    disclaimer=command.disclaimer,
                    terms=command.terms,
                )
            )
        )
        return PolicyHttpIn.model_validate(updated.as_dict())

    @router.post("/api/catalog/policy/terms", response_model=PolicyHttpIn)
    async def insert_policy_term(command: TermInsertIn) -> PolicyHttpIn:
        updated = _catalog_call(
            lambda: catalog.insert_term(command.term, index=command.index)
        )
        return PolicyHttpIn.model_validate(updated.as_dict())

    @router.delete("/api/catalog/policy/terms/{term}", response_model=PolicyHttpIn)
    async def delete_policy_term(term: str) -> PolicyHttpIn:
        updated = _catalog_call(lambda: catalog.delete_term(term))
        return PolicyHttpIn.model_validate(updated.as_dict())

    @router.post("/api/catalog/templates", response_model=TemplateCard, status_code=201)
    async def create_template(
        command: TemplateCard,
        index: int | None = Query(default=None, ge=0),
    ) -> TemplateCard:
        return _catalog_call(lambda: catalog.create_template(command, index=index))

    @router.put("/api/catalog/templates/{template_key}", response_model=TemplateCard)
    async def update_template(template_key: str, command: TemplateCard) -> TemplateCard:
        return _catalog_call(lambda: catalog.update_template(template_key, command))

    @router.delete("/api/catalog/templates/{template_key}", status_code=204)
    async def delete_template(template_key: str) -> None:
        _catalog_call(lambda: catalog.delete_template(template_key))

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
