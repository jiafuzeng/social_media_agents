from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.catalog import MatrixCatalog
from integrated_agent.runtimes.matrix.service import MatrixTaskService

from .routes import build_auth_router, build_catalog_router, build_task_router


def build_matrix_router(
    service: MatrixTaskService,
    *,
    static_root: Path | None = None,
    data_root: Path | None = None,
) -> APIRouter:
    router = APIRouter(tags=["matrix"])
    catalog_root = data_root or (PROJECT_ROOT / "data" / "matrix")
    catalog = MatrixCatalog(catalog_root)
    router.include_router(build_auth_router(service.identity))
    router.include_router(build_task_router(service, catalog_root=catalog_root))
    router.include_router(build_catalog_router(catalog))

    if static_root is not None:

        def matrix_page() -> FileResponse:
            return FileResponse(static_root / "matrix.html")

        @router.get("/", response_class=FileResponse)
        async def root_index() -> FileResponse:
            return matrix_page()

        @router.get("/matrix", response_class=FileResponse)
        async def matrix_index() -> FileResponse:
            return matrix_page()

    return router
