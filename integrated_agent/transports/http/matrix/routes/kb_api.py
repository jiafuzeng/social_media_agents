from __future__ import annotations

import logging

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from integrated_agent.rag.chunking import preview_chunks
from integrated_agent.rag.embeddings import list_embedding_profiles
from integrated_agent.rag.extract import extract_upload
from integrated_agent.rag.models import (
    CHUNK_STRATEGY_CATALOG,
    ChunkPreviewError,
    ChunkStrategyListOut,
    EmbeddingProfileListOut,
    ExtractError,
    ExtractOut,
    PreviewChunksIn,
    PreviewChunksOut,
)
from integrated_agent.runtimes.matrix.identity import (
    IdentityError,
    IdentityStore,
    parse_bearer_token,
)

_LOG = logging.getLogger(__name__)


def _raise_identity(error: IdentityError) -> None:
    raise HTTPException(status_code=error.status, detail=str(error)) from error


def _token(
    authorization: str | None,
    x_user_token: str | None,
) -> str | None:
    return parse_bearer_token(authorization, x_user_token)


def build_kb_router(store: IdentityStore) -> APIRouter:
    """知识库切分预览 / 抽文本。Step 2 不入库；鉴权对齐收藏夹。"""

    router = APIRouter(tags=["matrix-kb"])

    async def _require_user(
        authorization: str | None,
        x_user_token: str | None,
    ) -> None:
        try:
            await store.user_for_token(_token(authorization, x_user_token))
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.get("/api/kb/embedding-profiles", response_model=EmbeddingProfileListOut)
    async def get_embedding_profiles(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> EmbeddingProfileListOut:
        await _require_user(authorization, x_user_token)
        return list_embedding_profiles()

    @router.get("/api/kb/chunk-strategies", response_model=ChunkStrategyListOut)
    async def list_chunk_strategies(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> ChunkStrategyListOut:
        await _require_user(authorization, x_user_token)
        return CHUNK_STRATEGY_CATALOG

    @router.post("/api/kb/preview-chunks", response_model=PreviewChunksOut)
    async def preview_kb_chunks(
        command: PreviewChunksIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> PreviewChunksOut:
        await _require_user(authorization, x_user_token)
        try:
            return await preview_chunks(command)
        except ChunkPreviewError as error:
            _LOG.warning("preview-chunks rejected: %s", error)
            raise HTTPException(status_code=error.status, detail=str(error)) from error

    @router.post("/api/kb/extract", response_model=ExtractOut)
    async def extract_kb_text(
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> ExtractOut:
        await _require_user(authorization, x_user_token)
        data = await file.read()
        try:
            return extract_upload(file.filename, data, file.content_type)
        except ExtractError as error:
            raise HTTPException(status_code=error.status, detail=str(error)) from error

    return router
