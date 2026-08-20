from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, ValidationError

from integrated_agent.runtimes.matrix.host.catalog import (
    CatalogError,
    MatrixCatalog,
)
from integrated_agent.runtimes.matrix.host.snapshots import (
    AccountCard,
    GuardrailCard,
    InteractionCard,
    PlatformCard,
    SnapshotError,
    TemplateCard,
    TermListCard,
)
from integrated_agent.runtimes.matrix.host.models import DomainModel


class CatalogDumpOut(DomainModel):
    """配置台一次拉全量，对应 matrix-catalog.js 的 GET /api/catalog。"""

    accounts: list[AccountCard]
    interactions: list[InteractionCard]
    guardrails: list[GuardrailCard]
    platforms: list[PlatformCard]
    policy: list[TermListCard]
    templates: list[TemplateCard]


class TermInsertIn(DomainModel):
    """往硬禁词清单插入一条；index 省略则追加到末尾。"""

    term: str = Field(min_length=1)
    index: int | None = Field(default=None, ge=0)


class GuardrailAttachIn(DomainModel):
    """把已有护栏挂到人设或互动规则上，不新建护栏正文。"""

    guardrail_key: str = Field(min_length=1)
    index: int | None = Field(default=None, ge=0)


class TermListAttachIn(DomainModel):
    """把已有词表挂到人设或互动规则上。"""

    term_list_id: str = Field(min_length=1)
    index: int | None = Field(default=None, ge=0)


def _catalog_call(action):
    """把 MatrixCatalog 的领域错误映射成 HTTP，路由里不再包一层文案。"""
    try:
        return action()
    except CatalogError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except SnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def build_catalog_router(catalog: MatrixCatalog) -> APIRouter:
    """配置台 HTTP 入口。

    catalog 由 matrix_api 注入，读写 data/matrix 快照；本文件只做协议适配。
    路径与 static/matrix-catalog.js 对齐。工作台下拉用的 /api/accounts、
    /api/interactions 在 task_api，不在这里。
    """

    router = APIRouter(tags=["matrix-catalog"])

    @router.get("/api/catalog", response_model=CatalogDumpOut)
    async def get_catalog() -> CatalogDumpOut:
        """打开配置台时一次取出全部卡片。"""
        return CatalogDumpOut.model_validate(_catalog_call(catalog.dump_all))

    # 人设：写帖身份。index 控制插入位置，省略则追加。
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
        """给人设挂一条已有护栏，返回更新后的人设卡。"""
        return _catalog_call(
            lambda: catalog.insert_account_guardrail(
                account_key, command.guardrail_key, index=command.index
            )
        )

    @router.post(
        "/api/catalog/accounts/{account_key}/term-lists",
        response_model=AccountCard,
    )
    async def attach_account_term_list(
        account_key: str, command: TermListAttachIn
    ) -> AccountCard:
        """给人设挂一张硬禁词表。"""
        return _catalog_call(
            lambda: catalog.insert_account_term_list(
                account_key, command.term_list_id, index=command.index
            )
        )

    # 互动规则：回评口径。结构与人设对称。
    @router.post("/api/catalog/interactions", response_model=InteractionCard, status_code=201)
    async def create_interaction(
        command: InteractionCard,
        index: int | None = Query(default=None, ge=0),
    ) -> InteractionCard:
        return _catalog_call(lambda: catalog.create_interaction(command, index=index))

    @router.put(
        "/api/catalog/interactions/{interaction_key}",
        response_model=InteractionCard,
    )
    async def update_interaction(
        interaction_key: str, command: InteractionCard
    ) -> InteractionCard:
        return _catalog_call(
            lambda: catalog.update_interaction(interaction_key, command)
        )

    @router.delete("/api/catalog/interactions/{interaction_key}", status_code=204)
    async def delete_interaction(interaction_key: str) -> None:
        _catalog_call(lambda: catalog.delete_interaction(interaction_key))

    @router.post(
        "/api/catalog/interactions/{interaction_key}/guardrails",
        response_model=InteractionCard,
    )
    async def attach_interaction_guardrail(
        interaction_key: str, command: GuardrailAttachIn
    ) -> InteractionCard:
        """给互动规则挂一条已有护栏。"""
        return _catalog_call(
            lambda: catalog.insert_interaction_guardrail(
                interaction_key, command.guardrail_key, index=command.index
            )
        )

    @router.post(
        "/api/catalog/interactions/{interaction_key}/term-lists",
        response_model=InteractionCard,
    )
    async def attach_interaction_term_list(
        interaction_key: str, command: TermListAttachIn
    ) -> InteractionCard:
        """给互动规则挂一张硬禁词表。"""
        return _catalog_call(
            lambda: catalog.insert_interaction_term_list(
                interaction_key, command.term_list_id, index=command.index
            )
        )

    # 护栏 / 平台 / 硬禁词表 / 核准模板：独立资源，人设与互动只引用 key。
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
        """平台字数、条数上限等硬约束。"""
        return _catalog_call(lambda: catalog.create_platform(command, index=index))

    @router.put("/api/catalog/platforms/{platform_key}", response_model=PlatformCard)
    async def update_platform(platform_key: str, command: PlatformCard) -> PlatformCard:
        return _catalog_call(lambda: catalog.update_platform(platform_key, command))

    @router.delete("/api/catalog/platforms/{platform_key}", status_code=204)
    async def delete_platform(platform_key: str) -> None:
        _catalog_call(lambda: catalog.delete_platform(platform_key))

    @router.post("/api/catalog/policy", response_model=TermListCard, status_code=201)
    async def create_term_list(
        command: TermListCard,
        index: int | None = Query(default=None, ge=0),
    ) -> TermListCard:
        """新建硬禁词清单；词条增删走下面的 /terms。"""
        return _catalog_call(lambda: catalog.create_term_list(command, index=index))

    @router.put("/api/catalog/policy/{term_list_id}", response_model=TermListCard)
    async def update_term_list(term_list_id: str, command: TermListCard) -> TermListCard:
        return _catalog_call(lambda: catalog.update_term_list(term_list_id, command))

    @router.delete("/api/catalog/policy/{term_list_id}", status_code=204)
    async def delete_term_list(term_list_id: str) -> None:
        _catalog_call(lambda: catalog.delete_term_list(term_list_id))

    @router.post(
        "/api/catalog/policy/{term_list_id}/terms",
        response_model=TermListCard,
    )
    async def insert_policy_term(term_list_id: str, command: TermInsertIn) -> TermListCard:
        """往清单插入一条拦截词。"""
        return _catalog_call(
            lambda: catalog.insert_term(
                command.term, term_list_id=term_list_id, index=command.index
            )
        )

    @router.delete(
        "/api/catalog/policy/{term_list_id}/terms/{term}",
        response_model=TermListCard,
    )
    async def delete_policy_term(term_list_id: str, term: str) -> TermListCard:
        """从清单删掉一条拦截词。"""
        return _catalog_call(
            lambda: catalog.delete_term(term, term_list_id=term_list_id)
        )

    @router.post("/api/catalog/templates", response_model=TemplateCard, status_code=201)
    async def create_template(
        command: TemplateCard,
        index: int | None = Query(default=None, ge=0),
    ) -> TemplateCard:
        """核准模板：硬门降级时的兜底文案。"""
        return _catalog_call(lambda: catalog.create_template(command, index=index))

    @router.put("/api/catalog/templates/{template_key}", response_model=TemplateCard)
    async def update_template(template_key: str, command: TemplateCard) -> TemplateCard:
        return _catalog_call(lambda: catalog.update_template(template_key, command))

    @router.delete("/api/catalog/templates/{template_key}", status_code=204)
    async def delete_template(template_key: str) -> None:
        _catalog_call(lambda: catalog.delete_template(template_key))

    return router
