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
from integrated_agent.runtimes.matrix.host.identity import (
    IdentityError,
    IdentityStore,
    UserOut,
    parse_bearer_token,
)
from integrated_agent.runtimes.matrix.kb_chat import answer_kb_chat
from integrated_agent.runtimes.matrix.rag.knowledge import KnowledgeStore

_LOG = logging.getLogger(__name__)
_CREATE_FIELDS = set(CreateDocumentIn.model_fields)
_UPDATE_FIELDS = set(UpdateDocumentIn.model_fields)


def _raise_identity(error: IdentityError) -> None:
    """把 IdentityStore 的业务错误转成 HTTP 状态码，不在路由里重写文案。"""
    raise HTTPException(status_code=error.status, detail=str(error)) from error


def _raise_knowledge(error: KnowledgeError) -> None:
    """把 KnowledgeStore 的业务错误转成 HTTP 状态码，不在路由里重写文案。"""
    raise HTTPException(status_code=error.status, detail=str(error)) from error


def _token(
    authorization: str | None,
    x_user_token: str | None,
) -> str | None:
    """优先 X-User-Token，否则 Authorization: Bearer。"""
    return parse_bearer_token(authorization, x_user_token)


def _http_validation(error: ValidationError) -> None:
    """把 Pydantic ValidationError 转成 422。"""
    raise HTTPException(status_code=422, detail=error.errors()) from error


def _form_payload(form: Any, keys: set[str]) -> dict[str, Any]:
    """从表单取出模型字段，跳过空值和文件。"""
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
    """从表单读 file 字段的字节、文件名和 MIME。"""
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
        """用当前 token 解析登录用户；失败转 HTTP。"""
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
        """列出可用 embedding profile，供向导入库选择。"""
        await _require_user(authorization, x_user_token)
        return list_embedding_profiles()

    @router.get("/api/kb/chunk-strategies", response_model=ChunkStrategyListOut)
    async def list_chunk_strategies(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> ChunkStrategyListOut:
        """列出切分策略目录。"""
        await _require_user(authorization, x_user_token)
        return CHUNK_STRATEGY_CATALOG

    @router.post("/api/kb/preview-chunks", response_model=PreviewChunksOut)
    async def preview_kb_chunks(
        command: PreviewChunksIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> PreviewChunksOut:
        """按所选策略预览切片，不写库。"""
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
        """按 query 检索当前用户知识库切片。"""
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
        """RAG 召回聊天：改写拆分检索后生成带 [[kb:]] 的回答。"""
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
        """新建文档并切分入库；支持 JSON 或 multipart。"""
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
        """列出当前用户知识库文档。"""
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
        """打开一篇文档卡。"""
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
        """改标题/启用/重切；可换文件。"""
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
        """归档删除文档及其切片。"""
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
        """下载文档冷文件。"""
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
        """列出文档当前有效切片。"""
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
        """单块入库，不重写兄弟块。"""
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
        """改单块文本或启用状态。"""
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
        """归档删除单块。"""
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
    """解析新建文档请求：multipart 带文件或 JSON 纯文本。"""
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
    """解析更新文档请求：multipart 换文件或 JSON 字段。"""
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
