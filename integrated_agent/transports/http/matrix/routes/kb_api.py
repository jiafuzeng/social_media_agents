from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from integrated_agent.rag.chunking import preview_chunks
from integrated_agent.rag.embeddings import list_embedding_profiles
from integrated_agent.rag.extract import extract_upload
from integrated_agent.rag.models import (
    CHUNK_STRATEGY_CATALOG,
    ChatKbIn,
    ChatKbOut,
    ChunkPreviewError,
    ChunkStrategyListOut,
    CreateChunkIn,
    CreateDocumentIn,
    EmbeddingProfileListOut,
    ExtractError,
    ExtractOut,
    KbChunkListOut,
    KbChunkOut,
    KbDocumentListOut,
    KbDocumentOut,
    KnowledgeError,
    PreviewChunksIn,
    PreviewChunksOut,
    SearchKbIn,
    SearchKbOut,
    UpdateChunkIn,
    UpdateDocumentIn,
)
from integrated_agent.runtimes.matrix.identity import (
    IdentityError,
    IdentityStore,
    UserOut,
    parse_bearer_token,
)
from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat
from integrated_agent.runtimes.matrix.knowledge import KnowledgeStore

_LOG = logging.getLogger(__name__)
_CREATE_FIELDS = set(CreateDocumentIn.model_fields)
_UPDATE_FIELDS = set(UpdateDocumentIn.model_fields)


def _raise_identity(error: IdentityError) -> None:
    raise HTTPException(status_code=error.status, detail=str(error)) from error


def _raise_knowledge(error: KnowledgeError) -> None:
    raise HTTPException(status_code=error.status, detail=str(error)) from error


def _token(
    authorization: str | None,
    x_user_token: str | None,
) -> str | None:
    return parse_bearer_token(authorization, x_user_token)


def _http_validation(error: ValidationError) -> None:
    raise HTTPException(status_code=422, detail=error.errors()) from error


def _form_payload(form: Any, keys: set[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in keys:
        value = form.get(key)
        if value is None or value == "":
            continue
        if hasattr(value, "read"):
            continue
        payload[key] = value
    return payload


async def _read_upload(form: Any) -> tuple[bytes | None, str | None, str | None]:
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return None, None, None
    data = await upload.read()
    filename = getattr(upload, "filename", None)
    mime = getattr(upload, "content_type", None)
    return data, filename, mime


def build_kb_router(identity: IdentityStore, knowledge: KnowledgeStore) -> APIRouter:
    """知识库预览 / 抽文本 / 文档与切片 CRUD。鉴权对齐收藏夹。"""

    router = APIRouter(tags=["matrix-kb"])

    async def _require_user(
        authorization: str | None,
        x_user_token: str | None,
    ) -> UserOut:
        try:
            return await identity.user_for_token(_token(authorization, x_user_token))
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
        """向导第一步：只抽文本。不接收、不处理 embedding_profile_id。"""
        await _require_user(authorization, x_user_token)
        data = await file.read()
        try:
            return extract_upload(file.filename, data, file.content_type)
        except ExtractError as error:
            raise HTTPException(status_code=error.status, detail=str(error)) from error

    @router.post("/api/kb/search", response_model=SearchKbOut)
    async def search_kb(
        command: SearchKbIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> SearchKbOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await knowledge.search(user.user_id, command)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.post("/api/kb/chat", response_model=ChatKbOut)
    async def chat_kb(
        command: ChatKbIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> ChatKbOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await answer_kb_chat(knowledge, user.user_id, command)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.post("/api/kb/documents", response_model=KbDocumentOut, status_code=201)
    async def create_document(
        request: Request,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbDocumentOut:
        user = await _require_user(authorization, x_user_token)
        try:
            command, file_bytes, filename, mime = await _parse_create(request)
            return await knowledge.create_document(
                user.user_id,
                command,
                file_bytes=file_bytes,
                filename=filename,
                mime=mime,
            )
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.get("/api/kb/documents", response_model=KbDocumentListOut)
    async def list_documents(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbDocumentListOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await knowledge.list_documents(user.user_id)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.get("/api/kb/documents/{doc_id}", response_model=KbDocumentOut)
    async def get_document(
        doc_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbDocumentOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await knowledge.get_document(user.user_id, doc_id)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.patch("/api/kb/documents/{doc_id}", response_model=KbDocumentOut)
    async def patch_document(
        doc_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbDocumentOut:
        user = await _require_user(authorization, x_user_token)
        try:
            command, file_bytes, filename, mime = await _parse_update(request)
            return await knowledge.update_document(
                user.user_id,
                doc_id,
                command,
                file_bytes=file_bytes,
                filename=filename,
                mime=mime,
            )
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.delete("/api/kb/documents/{doc_id}", status_code=204)
    async def delete_document(
        doc_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        user = await _require_user(authorization, x_user_token)
        try:
            await knowledge.delete_document(user.user_id, doc_id)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.get("/api/kb/documents/{doc_id}/file")
    async def download_document_file(
        doc_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> FileResponse:
        user = await _require_user(authorization, x_user_token)
        try:
            path, download_name = await knowledge.document_file(user.user_id, doc_id)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise
        return FileResponse(path, filename=download_name)

    @router.get("/api/kb/documents/{doc_id}/chunks", response_model=KbChunkListOut)
    async def list_chunks(
        doc_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbChunkListOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await knowledge.list_chunks(user.user_id, doc_id)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.post(
        "/api/kb/documents/{doc_id}/chunks",
        response_model=KbChunkOut,
        status_code=201,
    )
    async def create_chunk(
        doc_id: str,
        command: CreateChunkIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbChunkOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await knowledge.create_chunk(user.user_id, doc_id, command)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.patch(
        "/api/kb/documents/{doc_id}/chunks/{chunk_id}",
        response_model=KbChunkOut,
    )
    async def patch_chunk(
        doc_id: str,
        chunk_id: str,
        command: UpdateChunkIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> KbChunkOut:
        user = await _require_user(authorization, x_user_token)
        try:
            return await knowledge.update_chunk(user.user_id, doc_id, chunk_id, command)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    @router.delete(
        "/api/kb/documents/{doc_id}/chunks/{chunk_id}",
        status_code=204,
    )
    async def delete_chunk(
        doc_id: str,
        chunk_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        user = await _require_user(authorization, x_user_token)
        try:
            await knowledge.delete_chunk(user.user_id, doc_id, chunk_id)
        except KnowledgeError as error:
            _raise_knowledge(error)
            raise

    return router


async def _parse_create(
    request: Request,
) -> tuple[CreateDocumentIn, bytes | None, str | None, str | None]:
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            file_bytes, filename, mime = await _read_upload(form)
            command = CreateDocumentIn.model_validate(_form_payload(form, _CREATE_FIELDS))
            return command, file_bytes, filename, mime
        try:
            payload = await request.json()
        except Exception as error:
            raise HTTPException(status_code=422, detail="invalid json") from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="invalid json")
        return CreateDocumentIn.model_validate(payload), None, None, None
    except ValidationError as error:
        _http_validation(error)
        raise


async def _parse_update(
    request: Request,
) -> tuple[UpdateDocumentIn, bytes | None, str | None, str | None]:
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            file_bytes, filename, mime = await _read_upload(form)
            command = UpdateDocumentIn.model_validate(_form_payload(form, _UPDATE_FIELDS))
            return command, file_bytes, filename, mime
        try:
            payload = await request.json()
        except Exception as error:
            raise HTTPException(status_code=422, detail="invalid json") from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="invalid json")
        return UpdateDocumentIn.model_validate(payload), None, None, None
    except ValidationError as error:
        _http_validation(error)
        raise
