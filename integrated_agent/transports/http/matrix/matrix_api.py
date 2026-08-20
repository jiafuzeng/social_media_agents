from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.host.catalog import MatrixCatalog
from integrated_agent.runtimes.matrix.host.service import MatrixTaskService

from .routes import (
    build_auth_router,
    build_catalog_router,
    build_collection_router,
    build_kb_router,
    build_session_router,
    build_task_router,
)


def build_matrix_router(
    service: MatrixTaskService,
    *,
    static_root: Path | None = None,
    data_root: Path | None = None,
) -> APIRouter:
    router = APIRouter(tags=["matrix"])
    # 账号/平台/案例夹具目录；省略则用仓库内 data/matrix
    catalog_root = data_root or (PROJECT_ROOT / "data" / "matrix")
    catalog = MatrixCatalog(catalog_root)
    router.include_router(build_auth_router(service.identity))  # 登录与本机账号
    router.include_router(build_session_router(service.identity))  # 对话 session
    router.include_router(build_collection_router(service.identity))  # 收藏夹
    router.include_router(build_kb_router(service.identity, service.knowledge))  # 知识库 CRUD / 检索 / 召回聊天
    router.include_router(build_task_router(service, catalog_root=catalog_root))  # 写稿 / 回评任务
    router.include_router(build_catalog_router(catalog))  # 人设与互动规则目录

    if static_root is not None:
        page = static_root / "matrix.html"

        @router.get("/", response_class=FileResponse)
        @router.get("/matrix", response_class=FileResponse)
        async def matrix_index() -> FileResponse:
            return FileResponse(page)

    return router
