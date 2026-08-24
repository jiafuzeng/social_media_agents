from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from integrated_agent.runtimes.matrix.host.service import MatrixTaskService
from integrated_agent.runtimes.question.service import QuestionTaskService

from .matrix import build_matrix_router
from .question import build_question_router


def create_http_app(
    *,
    question_service: QuestionTaskService | None = None,
    matrix_service: MatrixTaskService | None = None,
    static_root: Path | None = None,
    artifacts_root: Path | None = None,
    matrix_data_root: Path | None = None,
) -> FastAPI:
    if question_service is None and matrix_service is None:
        raise ValueError("create_http_app requires question_service or matrix_service")

    services: list[QuestionTaskService | MatrixTaskService] = []
    if question_service is not None:
        services.append(question_service)
    if matrix_service is not None:
        services.append(matrix_service)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        for item in services:
            await item.start()
        try:
            yield
        finally:
            for item in reversed(services):
                await item.stop()

    app = FastAPI(title="综合智能体 HTTP 服务", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(asyncio.QueueFull)
    async def service_busy(_request: Request, exc: asyncio.QueueFull) -> JSONResponse:
        del exc
        return JSONResponse(
            status_code=503,
            content={"detail": "task queue is full"},
            headers={"Retry-After": "1"},
        )

    if static_root is not None:
        app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if any(not item.ready for item in services):
            raise HTTPException(status_code=503, detail="worker is not ready")
        return {"status": "ready"}

    if question_service is not None:
        if static_root is None or artifacts_root is None:
            raise ValueError("static_root and artifacts_root are required for question routes")
        app.include_router(
            build_question_router(
                question_service,
                static_root=static_root,
                artifacts_root=artifacts_root,
            )
        )
    if matrix_service is not None:
        app.include_router(
            build_matrix_router(
                matrix_service,
                static_root=static_root,
                data_root=matrix_data_root,
            )
        )
    return app


def create_question_api(
    service: QuestionTaskService,
    *,
    static_root: Path,
    artifacts_root: Path,
) -> FastAPI:
    return create_http_app(
        question_service=service,
        static_root=static_root,
        artifacts_root=artifacts_root,
    )


def create_matrix_api(
    service: MatrixTaskService,
    *,
    static_root: Path | None = None,
    data_root: Path | None = None,
) -> FastAPI:
    return create_http_app(
        matrix_service=service,
        static_root=static_root,
        matrix_data_root=data_root,
    )
