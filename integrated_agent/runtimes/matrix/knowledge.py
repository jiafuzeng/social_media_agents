"""知识库文档 / 切片门面。所有写入口只走这里，不直接 RecordStore.put。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from agently.core.storage import RecordStore
from agently.types.data.record_store import RecordRef

from integrated_agent.config import (
    KB_DEFAULT_EMBEDDING_PROFILE,
    KB_EMBEDDING_AGENTS,
    KB_FILES_ROOT,
)
from integrated_agent.rag.chunking import preview_chunks
from integrated_agent.rag.extract import extract_upload
from integrated_agent.rag.models import (
    DEFAULT_BREAKPOINT_PERCENTILE,
    DEFAULT_BUFFER_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_STRATEGY,
    DEFAULT_WINDOW_SIZE,
    MAX_INGEST_CHUNKS,
    ChunkPreviewError,
    ChunkStrategy,
    CreateChunkIn,
    CreateDocumentIn,
    ExtractError,
    KbChunkListOut,
    KbChunkOut,
    KbDocumentListOut,
    KbDocumentOut,
    KnowledgeError,
    PreviewChunksIn,
    SearchKbHit,
    SearchKbIn,
    SearchKbOut,
    TextChunk,
    UpdateChunkIn,
    UpdateDocumentIn,
)
from integrated_agent.runtimes.matrix.kb_store import kb_store as default_kb_stores

_LOG = logging.getLogger(__name__)

KB_COLLECTION = "kb"  # RecordStore 集合名，与身份库/案例记录隔离
KIND_DOCUMENT = "document"  # 文档卡
KIND_CHUNK = "chunk"  # 切片；向量与 hybrid 检索打在这一层
_LIVE_DOC_STATUSES = ("ingesting", "ready", "failed")  # 列表可见；archived 不出现
_UNSAFE_NAME = re.compile(r"[^\w.\u4e00-\u9fff-]+", re.UNICODE)  # 冷文件名非法字符


class KnowledgeStore:
    """用户私有知识库门面：文档/切片 CRUD、切分入库、按 embedding profile 检索。

    记录走 RecordStore（每模型一个库），冷文件在 files_root；身份库只解析 user_id。
    写入口只经本类，不直接 RecordStore.put，也不把 LlamaIndex 当产品存储。
    """
    def __init__(
        self,
        stores: dict[str, RecordStore] | None = None,
        *,
        files_root: Path | None = None,
        default_profile: str = KB_DEFAULT_EMBEDDING_PROFILE,
    ) -> None:
        """绑定各 embedding profile 的 RecordStore 与冷文件根目录。"""
        self._stores = stores if stores is not None else default_kb_stores
        if not self._stores:
            raise RuntimeError("KnowledgeStore requires at least one embedding profile store")
        self.files_root = Path(files_root or KB_FILES_ROOT)
        self.default_profile = default_profile
        self._catalog = self._stores.get(default_profile) or next(iter(self._stores.values()))
        self._lock = asyncio.Lock()

    def _store(self, profile_id: str) -> RecordStore:
        """按 profile 取 RecordStore；未知 id 则 422。"""
        store = self._stores.get(profile_id)
        if store is None:
            raise KnowledgeError(422, "unknown embedding_profile_id")
        return store

    def _require_profile(self, profile_id: str | None) -> str:
        """解析 embedding profile，缺省用 yaml default；未知则 422。"""
        resolved = (profile_id or self.default_profile).strip()
        if resolved not in KB_EMBEDDING_AGENTS or resolved not in self._stores:
            raise KnowledgeError(422, "unknown embedding_profile_id")
        return resolved

    async def list_documents(self, user_id: str) -> KbDocumentListOut:
        """列出该用户未归档文档。"""
        refs = await self._catalog.search(
            filters={
                "collection": KB_COLLECTION,
                "kind": KIND_DOCUMENT,
                "scope.user_id": user_id,
                "meta.status": list(_LIVE_DOC_STATUSES),
            }
        )
        return KbDocumentListOut(documents=[self._document_out(ref) for ref in refs])

    async def get_document(self, user_id: str, doc_id: str) -> KbDocumentOut:
        """取一篇文档卡；跨用户 403。"""
        return self._document_out(await self._document_ref(user_id, doc_id))

    async def create_document(
        self,
        user_id: str,
        command: CreateDocumentIn,
        *,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        mime: str | None = None,
    ) -> KbDocumentOut:
        """新建文档并切分入库；切片锁定本次 embedding_profile_id。"""
        async with self._lock:
            return await self._create_document(
                user_id,
                command,
                file_bytes=file_bytes,
                filename=filename,
                mime=mime,
            )

    async def update_document(
        self,
        user_id: str,
        doc_id: str,
        command: UpdateDocumentIn,
        *,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        mime: str | None = None,
    ) -> KbDocumentOut:
        """改文档卡。换 embedding 必须 rechunk，禁止静默改徽章。"""
        async with self._lock:
            return await self._update_document(
                user_id,
                doc_id,
                command,
                file_bytes=file_bytes,
                filename=filename,
                mime=mime,
            )

    async def delete_document(self, user_id: str, doc_id: str) -> None:
        """删文档：硬删记录与向量，并去掉冷文件。"""
        async with self._lock:
            ref = await self._document_ref(user_id, doc_id)
            meta = dict(ref.get("meta") or {})
            profile_id = str(meta.get("embedding_profile_id") or self.default_profile)
            chunks = await self._chunk_refs(user_id, doc_id, active_only=False)
            await self._purge_records(profile_id, [*chunks, ref])
            artifact_id = meta.get("artifact_id")
            if artifact_id:
                shutil.rmtree(
                    self.files_root / user_id / str(artifact_id),
                    ignore_errors=True,
                )

    async def document_file(self, user_id: str, doc_id: str) -> tuple[Path, str]:
        """返回上传原件路径与下载名；粘贴文档无文件则 404。"""
        ref = await self._document_ref(user_id, doc_id)
        meta = dict(ref.get("meta") or {})
        artifact_id = meta.get("artifact_id")
        if not artifact_id:
            raise KnowledgeError(404, "document has no file")
        folder = self.files_root / user_id / str(artifact_id)
        stored = str(meta.get("stored_filename") or "")
        path = folder / stored if stored else None
        if path is None or not path.is_file():
            files = [item for item in folder.glob("*") if item.is_file()] if folder.is_dir() else []
            if len(files) != 1:
                raise KnowledgeError(404, "document has no file")
            path = files[0]
        download_name = str(meta.get("filename") or path.name)
        return path, download_name

    async def list_chunks(self, user_id: str, doc_id: str) -> KbChunkListOut:
        """列出该文档当前有效切片，按 ordinal 排序。"""
        await self._document_ref(user_id, doc_id)
        refs = await self._chunk_refs(user_id, doc_id, active_only=True)
        chunks = [await self._chunk_out(ref) for ref in refs]
        chunks.sort(key=lambda item: (item.ordinal, item.chunk_id))
        return KbChunkListOut(chunks=chunks)

    async def create_chunk(
        self, user_id: str, doc_id: str, command: CreateChunkIn
    ) -> KbChunkOut:
        """单块入库：当场 embed + put，不重写兄弟块。"""
        async with self._lock:
            doc = await self._document_ref(user_id, doc_id)
            self._reject_foreign_profile(doc, command.embedding_profile_id)
            ordinal = command.ordinal
            if ordinal is None:
                ordinal = self._next_ordinal(await self._chunk_refs(user_id, doc_id, active_only=True))
            chunk = TextChunk(
                text=command.text,
                header_path=command.header_path,
                element_type=command.element_type,
                window=command.window,
            )
            try:
                stored = await self._put_chunk(
                    doc,
                    chunk,
                    ordinal=ordinal,
                    diverged=False,
                )
            except KnowledgeError:
                raise
            except Exception as exc:
                raise KnowledgeError(500, "chunk ingest failed") from exc
            await self._set_chunk_count(doc)
            return stored

    async def update_chunk(
        self, user_id: str, doc_id: str, chunk_id: str, command: UpdateChunkIn
    ) -> KbChunkOut:
        """改一块。改正文会 put 新记录并归档旧行，diverged=true。"""
        async with self._lock:
            doc = await self._document_ref(user_id, doc_id)
            self._reject_foreign_profile(doc, command.embedding_profile_id)
            current = await self._chunk_ref(user_id, doc_id, chunk_id)
            meta = dict(current.get("meta") or {})
            if command.text is not None:
                replacement = TextChunk(
                    text=command.text,
                    header_path=(
                        command.header_path
                        if command.header_path is not None
                        else _optional_str(meta.get("header_path"))
                    ),
                    element_type=(
                        command.element_type
                        if command.element_type is not None
                        else _optional_str(meta.get("element_type"))
                    ),
                    window=(
                        command.window
                        if command.window is not None
                        else _optional_str(meta.get("window"))
                    ),
                    char_start=_optional_int(meta.get("char_start")),
                    char_end=_optional_int(meta.get("char_end")),
                )
                try:
                    stored = await self._put_chunk(
                        doc,
                        replacement,
                        ordinal=(
                            command.ordinal
                            if command.ordinal is not None
                            else int(meta.get("ordinal") or 0)
                        ),
                        diverged=True,
                        enabled=(
                            command.enabled
                            if command.enabled is not None
                            else bool(meta.get("enabled", True))
                        ),
                        chunk_id=chunk_id,
                        replaces=current,
                    )
                except KnowledgeError:
                    raise
                except Exception as exc:
                    raise KnowledgeError(500, "chunk ingest failed") from exc
                await self._set_chunk_count(doc)
                return stored
            updates: dict[str, Any] = {}
            if command.enabled is not None:
                updates["enabled"] = command.enabled
            if command.ordinal is not None:
                updates["ordinal"] = command.ordinal
            if command.header_path is not None:
                updates["header_path"] = command.header_path
            if command.element_type is not None:
                updates["element_type"] = command.element_type
            if command.window is not None:
                updates["window"] = command.window
            updated = _with_meta(current, **updates)
            await self._catalog.backend.put_record(updated)
            await self._sync_vector_meta(str(meta.get("embedding_profile_id") or ""), updated)
            await self._set_chunk_count(doc)
            return await self._chunk_out(updated)

    async def delete_chunk(self, user_id: str, doc_id: str, chunk_id: str) -> None:
        """删除一块及其历史行和向量。"""
        async with self._lock:
            doc = await self._document_ref(user_id, doc_id)
            current = await self._chunk_ref(user_id, doc_id, chunk_id)
            profile_id = str((current.get("meta") or {}).get("embedding_profile_id") or "")
            history = await self._catalog.search(
                filters={
                    "collection": KB_COLLECTION,
                    "kind": KIND_CHUNK,
                    "scope.user_id": user_id,
                    "scope.doc_id": doc_id,
                    "scope.chunk_id": chunk_id,
                }
            )
            await self._purge_records(profile_id, history or [current])
            await self._set_chunk_count(doc)

    async def retrieve(
        self,
        user_id: str,
        query: str,
        embedding_profile_id: str | None = None,
        *,
        top_n: int = 4,
    ) -> list[RecordRef]:
        """hybrid 检索启用块；不跨 profile 融合。"""
        profile_id = self._require_profile(embedding_profile_id)
        package = await self._store(profile_id).retrieve(
            query,
            method="hybrid",
            rerank=False,
            selection="top_n",
            top_n=top_n,
            scope={"user_id": user_id},
            filters={
                "collection": KB_COLLECTION,
                "kind": KIND_CHUNK,
                "meta.status": "active",
                "meta.enabled": True,
                "meta.doc_status": "ready",
                "meta.doc_enabled": True,
                "meta.embedding_profile_id": profile_id,
            },
        )
        refs: list[RecordRef] = []
        for item in package.get("items") or []:
            if not isinstance(item, dict):
                continue
            ref = item.get("ref")
            if isinstance(ref, dict) and self._owned_ref(user_id, ref):
                refs.append(ref)
        return refs

    async def retrieve_draft_cards(
        self,
        user_id: str,
        query: str,
        embedding_profile_id: str | None = None,
        *,
        top_n: int = 4,
    ) -> list[dict[str, Any]]:
        """写稿投影：签发 k1…kN。RecordStore id 不出卡片。"""
        refs = await self.retrieve(
            user_id, query, embedding_profile_id, top_n=top_n
        )
        cards: list[dict[str, Any]] = []
        for index, ref in enumerate(refs, start=1):
            chunk = await self._chunk_out(ref)
            cards.append(
                {
                    "kb_id": f"k{index}",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "text": chunk.text,
                    "window": chunk.window,
                    "header_path": chunk.header_path,
                    "embedding_profile_id": chunk.embedding_profile_id,
                }
            )
        return cards

    async def search(
        self, user_id: str, command: SearchKbIn
    ) -> SearchKbOut:
        """工作区召回。query 与 embedding_profile_id 必填；空库语义写入 empty_reason。"""
        query = command.query.strip()
        profile_id = self._require_profile(command.embedding_profile_id.strip())
        listed = await self.list_documents(user_id)
        live = [
            item
            for item in listed.documents
            if item.status == "ready" and item.enabled
        ]
        profile_docs = [
            item for item in live if item.embedding_profile_id == profile_id
        ]
        other_docs = [
            item for item in live if item.embedding_profile_id != profile_id
        ]
        package = await self._store(profile_id).retrieve(
            query,
            method="hybrid",
            rerank=False,
            selection="top_n",
            top_n=4,
            scope={"user_id": user_id},
            filters={
                "collection": KB_COLLECTION,
                "kind": KIND_CHUNK,
                "meta.status": "active",
                "meta.enabled": True,
                "meta.doc_status": "ready",
                "meta.doc_enabled": True,
                "meta.embedding_profile_id": profile_id,
            },
        )
        hits: list[SearchKbHit] = []
        for item in package.get("items") or []:
            if not isinstance(item, dict):
                continue
            ref = item.get("ref")
            if not isinstance(ref, dict) or not self._owned_ref(user_id, ref):
                continue
            chunk = await self._chunk_out(ref)
            score = item.get("score")
            hits.append(
                SearchKbHit(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    text=chunk.text,
                    window=chunk.window,
                    header_path=chunk.header_path,
                    score=float(score) if isinstance(score, (int, float)) else None,
                    embedding_profile_id=chunk.embedding_profile_id,
                )
            )
        empty: str = ""
        if not live:
            empty = "library_empty"
        elif not profile_docs:
            empty = "no_docs_for_profile"
        elif not hits:
            empty = "no_match"
        return SearchKbOut(
            query=query,
            embedding_profile_id=profile_id,
            hits=hits,
            empty_reason=empty,
            profile_doc_count=len(profile_docs),
            other_profile_doc_count=len(other_docs),
        )

    async def _create_document(
        self,
        user_id: str,
        command: CreateDocumentIn,
        *,
        file_bytes: bytes | None,
        filename: str | None,
        mime: str | None,
    ) -> KbDocumentOut:
        """解析正文、写冷文件、put 文档行并逐块 ingest。"""
        profile_id = self._require_profile(command.embedding_profile_id)
        source = "upload" if file_bytes is not None else command.source
        if source == "upload" and file_bytes is None:
            raise KnowledgeError(422, "file is required")
        name = filename or command.filename
        content_type = mime or command.mime
        text = (command.text or "").strip()
        artifact: dict[str, Any] = {}
        suffix = command.source_suffix or (Path(name).suffix if name else None)
        if file_bytes is not None:
            extracted = _extract(name, file_bytes, content_type)
            text = extracted.text
            name = extracted.filename or name
            content_type = extracted.content_type or content_type
            suffix = Path(name).suffix if name else suffix
            artifact = self._write_artifact(user_id, name or "upload", file_bytes)
        chunks, notes = await self._parse_chunks(text, command, profile_id, suffix)
        doc_id = str(uuid4())
        title = _title(command.title, name, text)
        doc_ref = await self._catalog.put(
            {
                "title": title,
                "strategy": command.strategy,
                "excerpt": text[:500],
            },
            collection=KB_COLLECTION,
            kind=KIND_DOCUMENT,
            summary=title,
            scope={"user_id": user_id, "doc_id": doc_id},
            meta=_document_meta(
                status="ingesting",
                enabled=True,
                source=source,
                title=title,
                filename=name,
                mime=content_type,
                artifact=artifact,
                command=command,
                profile_id=profile_id,
                chunk_count=0,
                notes=notes,
            ),
            indexed=False,
            vector=False,
        )
        try:
            await self._ingest_chunks(doc_ref, chunks)
        except Exception as exc:
            leftovers = await self._chunk_refs(user_id, doc_id, active_only=False)
            try:
                await self._purge_records(profile_id, leftovers)
            except Exception:
                _LOG.warning("kb ingest rollback purge failed doc=%s", doc_id, exc_info=True)
            failed = _with_meta(doc_ref, status="failed", error=str(exc), chunk_count=0)
            await self._catalog.backend.put_record(failed)
            raise KnowledgeError(500, "document ingest failed") from exc
        return await self.get_document(user_id, doc_id)

    async def _update_document(
        self,
        user_id: str,
        doc_id: str,
        command: UpdateDocumentIn,
        *,
        file_bytes: bytes | None,
        filename: str | None,
        mime: str | None,
    ) -> KbDocumentOut:
        """改标题/启用，或按 rechunk 重切并换 embedding。"""
        doc = await self._document_ref(user_id, doc_id)
        meta = dict(doc.get("meta") or {})
        current_profile = str(meta.get("embedding_profile_id") or self.default_profile)
        requested_profile = command.embedding_profile_id
        if requested_profile is not None:
            requested_profile = self._require_profile(requested_profile)
        else:
            requested_profile = current_profile
        strategy_changed = (
            command.strategy is not None and command.strategy != meta.get("strategy")
        )
        rechunking = bool(
            command.rechunk
            or file_bytes is not None
            or (command.text or "").strip()
            or strategy_changed
        )
        if requested_profile != current_profile and not rechunking:
            raise KnowledgeError(422, "cannot change embedding_profile_id without rechunk")
        title = command.title.strip() if command.title and command.title.strip() else str(
            meta.get("title") or ""
        )
        enabled = meta.get("enabled", True) if command.enabled is None else command.enabled
        if rechunking:
            await self._rechunk_document(
                doc,
                command,
                profile_id=requested_profile,
                title=title,
                enabled=bool(enabled),
                file_bytes=file_bytes,
                filename=filename,
                mime=mime,
            )
        else:
            updated = _with_meta(doc, title=title, enabled=bool(enabled))
            updated["summary"] = title
            await self._catalog.backend.put_record(updated)
            if bool(enabled) != bool(meta.get("enabled", True)):
                await self._rewrite_doc_enabled(user_id, doc_id, bool(enabled), current_profile)
        return await self.get_document(user_id, doc_id)

    async def _rechunk_document(
        self,
        doc: RecordRef,
        command: UpdateDocumentIn,
        *,
        profile_id: str,
        title: str,
        enabled: bool,
        file_bytes: bytes | None,
        filename: str | None,
        mime: str | None,
    ) -> None:
        """先归档旧块再写入新块；失败则恢复旧记录。"""
        user_id = str(doc["scope"]["user_id"])
        doc_id = str(doc["scope"]["doc_id"])
        meta = dict(doc.get("meta") or {})
        old_profile = str(meta.get("embedding_profile_id") or self.default_profile)
        name = filename or meta.get("filename")
        content_type = mime or meta.get("mime")
        text = (command.text or "").strip()
        artifact = {
            key: meta.get(key)
            for key in ("artifact_id", "sha256", "size_bytes", "stored_filename")
            if meta.get(key) is not None
        }
        if filename:
            artifact["filename"] = filename
        elif meta.get("filename"):
            artifact["filename"] = meta.get("filename")
        suffix = command.source_suffix
        old_artifact_id = meta.get("artifact_id")
        if file_bytes is not None:
            extracted = _extract(name if isinstance(name, str) else None, file_bytes, content_type)
            text = extracted.text
            name = extracted.filename or name
            content_type = extracted.content_type or content_type
            suffix = Path(str(name)).suffix if name else suffix
            artifact = self._write_artifact(user_id, str(name or "upload"), file_bytes)
            artifact["filename"] = name
            if old_artifact_id and old_artifact_id != artifact.get("artifact_id"):
                shutil.rmtree(
                    self.files_root / user_id / str(old_artifact_id),
                    ignore_errors=True,
                )
        elif not text:
            text = self._reload_text(user_id, meta)
            if not text:
                raise KnowledgeError(422, "text is required to rechunk")
            suffix = suffix or meta.get("source_suffix")
        raw_source = meta.get("source")
        source: Literal["upload", "paste"] = (
            raw_source if raw_source in {"upload", "paste"} else "paste"
        )
        params = CreateDocumentIn(
            title=title,
            text=text,
            source=source,
            filename=str(name) if name else None,
            mime=str(content_type) if content_type else None,
            embedding_profile_id=profile_id,
            strategy=command.strategy or meta.get("strategy") or DEFAULT_CHUNK_STRATEGY,
            chunk_size=command.chunk_size or int(meta.get("chunk_size") or DEFAULT_CHUNK_SIZE),
            chunk_overlap=command.chunk_overlap
            if command.chunk_overlap is not None
            else int(meta.get("chunk_overlap") or DEFAULT_CHUNK_OVERLAP),
            breakpoint_percentile_threshold=(
                command.breakpoint_percentile_threshold
                if command.breakpoint_percentile_threshold is not None
                else int(
                    meta.get("breakpoint_percentile_threshold") or DEFAULT_BREAKPOINT_PERCENTILE
                )
            ),
            buffer_size=(
                command.buffer_size
                if command.buffer_size is not None
                else int(meta.get("buffer_size") or DEFAULT_BUFFER_SIZE)
            ),
            window_size=(
                command.window_size
                if command.window_size is not None
                else int(meta.get("window_size") or DEFAULT_WINDOW_SIZE)
            ),
            source_suffix=str(suffix) if suffix else None,
        )
        chunks, notes = await self._parse_chunks(text, params, profile_id, params.source_suffix)
        updates: dict[str, Any] = {
            "status": "ingesting",
            "enabled": enabled,
            "title": title,
            "filename": params.filename,
            "mime": params.mime,
            "strategy": params.strategy,
            "chunk_size": params.chunk_size,
            "chunk_overlap": params.chunk_overlap,
            "breakpoint_percentile_threshold": params.breakpoint_percentile_threshold,
            "buffer_size": params.buffer_size,
            "window_size": params.window_size,
            "source_suffix": params.source_suffix,
            "embedding_profile_id": profile_id,
            "notes": notes,
            "error": None,
            "chunk_count": 0,
        }
        for key in ("artifact_id", "sha256", "size_bytes", "stored_filename", "filename"):
            if artifact.get(key) is not None:
                updates[key] = artifact[key]
        original_doc = _copy_ref(doc)
        working = _with_meta(doc, **updates)
        working["summary"] = title
        await self._catalog.backend.put_record(working)
        existing = [
            _copy_ref(item) for item in await self._chunk_refs(user_id, doc_id, active_only=False)
        ]
        old_ids = {str(item["id"]) for item in existing}
        live_old = [
            item
            for item in existing
            if (item.get("meta") or {}).get("status") != "archived"
        ]
        await self._archive_chunks(old_profile, live_old, drop_vectors=False)
        try:
            await self._ingest_chunks(working, chunks)
        except Exception as exc:
            leftovers = [
                item
                for item in await self._chunk_refs(user_id, doc_id, active_only=False)
                if str(item.get("id") or "") not in old_ids
            ]
            try:
                await self._purge_records(profile_id, leftovers)
            except Exception:
                _LOG.warning("kb rechunk rollback purge failed doc=%s", doc_id, exc_info=True)
            await self._restore_chunks(old_profile, live_old)
            await self._catalog.backend.put_record(original_doc)
            raise KnowledgeError(500, "document ingest failed") from exc
        await self._purge_records(old_profile, existing)

    def _reload_text(self, user_id: str, meta: dict[str, Any]) -> str:
        """从冷文件重新抽正文，供重切使用。"""
        artifact_id = meta.get("artifact_id")
        if not artifact_id:
            return ""
        folder = self.files_root / user_id / str(artifact_id)
        stored = str(meta.get("stored_filename") or "")
        path = folder / stored if stored else None
        if path is None or not path.is_file():
            files = [item for item in folder.glob("*") if item.is_file()] if folder.is_dir() else []
            if not files:
                return ""
            path = files[0]
        filename = str(meta.get("filename") or path.name)
        mime = meta.get("mime") if isinstance(meta.get("mime"), str) else None
        try:
            return _extract(filename, path.read_bytes(), mime).text
        except KnowledgeError:
            return ""

    async def _parse_chunks(
        self,
        text: str,
        command: CreateDocumentIn,
        profile_id: str,
        suffix: str | None,
    ) -> tuple[list[TextChunk], str]:
        """调用 Parser 预览切分；超 80 块 422。"""
        if not (text or "").strip():
            return [], ""
        try:
            payload = await preview_chunks(
                PreviewChunksIn(
                    text=text,
                    strategy=command.strategy,
                    chunk_size=command.chunk_size,
                    chunk_overlap=command.chunk_overlap,
                    embedding_profile_id=(
                        profile_id if command.strategy == "semantic" else None
                    ),
                    breakpoint_percentile_threshold=command.breakpoint_percentile_threshold,
                    buffer_size=command.buffer_size,
                    window_size=command.window_size,
                    source_suffix=suffix,
                )
            )
        except ChunkPreviewError as error:
            raise KnowledgeError(error.status, str(error)) from error
        if len(payload.chunks) > MAX_INGEST_CHUNKS:
            raise KnowledgeError(422, "too many chunks")
        return payload.chunks, payload.notes

    async def _ingest_chunks(self, doc: RecordRef, chunks: list[TextChunk]) -> None:
        """按序 put 各块；一块失败抛出，由调用方回滚。"""
        for ordinal, chunk in enumerate(chunks):
            try:
                await self._put_chunk(doc, chunk, ordinal=ordinal, diverged=False)
            except Exception as exc:
                _LOG.warning(
                    "kb chunk ingest failed doc=%s ordinal=%s: %s",
                    (doc.get("scope") or {}).get("doc_id"),
                    ordinal,
                    exc,
                )
                raise
        await self._set_chunk_count(doc, status="ready", error=None)

    async def _put_chunk(
        self,
        doc: RecordRef,
        chunk: TextChunk,
        *,
        ordinal: int,
        diverged: bool,
        enabled: bool = True,
        chunk_id: str | None = None,
        replaces: RecordRef | None = None,
    ) -> KbChunkOut:
        """embed + put 一块，并链到文档；替换时归档旧行。"""
        scope = dict(doc.get("scope") or {})
        user_id = str(scope["user_id"])
        doc_id = str(scope["doc_id"])
        meta = dict(doc.get("meta") or {})
        profile_id = str(meta.get("embedding_profile_id") or "")
        store = self._store(profile_id)
        assigned_id = chunk_id or str(uuid4())
        body = chunk.text
        try:
            ref = await store.put(
                body,
                collection=KB_COLLECTION,
                kind=KIND_CHUNK,
                summary=body[:200],
                scope={"user_id": user_id, "doc_id": doc_id, "chunk_id": assigned_id},
                meta={
                    "status": "active",
                    "enabled": enabled,
                    "doc_status": "ready",
                    "doc_enabled": bool(meta.get("enabled", True)),
                    "embedding_profile_id": profile_id,
                    "artifact_id": meta.get("artifact_id"),
                    "diverged": diverged,
                    "ordinal": ordinal,
                    "header_path": chunk.header_path,
                    "element_type": chunk.element_type,
                    "window": chunk.window,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                },
                indexed=True,
                vector=True,
            )
            await store.link(doc, ref, "contains")
            if replaces is not None:
                await store.link(ref, replaces, "replaces")
                try:
                    await self._purge_records(profile_id, [replaces])
                except Exception:
                    _LOG.warning(
                        "kb replace purge failed doc=%s chunk=%s",
                        doc_id,
                        assigned_id,
                        exc_info=True,
                    )
                    await self._archive_chunks(profile_id, [replaces])
            return await self._chunk_out(ref)
        except Exception:
            leftovers = await self._catalog.search(
                filters={
                    "collection": KB_COLLECTION,
                    "kind": KIND_CHUNK,
                    "scope.user_id": user_id,
                    "scope.doc_id": doc_id,
                    "scope.chunk_id": assigned_id,
                }
            )
            keep_id = str(replaces["id"]) if replaces is not None else None
            try:
                await self._archive_chunks(
                    profile_id,
                    [
                        item
                        for item in leftovers
                        if (item.get("meta") or {}).get("status") != "archived"
                        and str(item.get("id") or "") != keep_id
                    ],
                )
            except Exception:
                _LOG.warning(
                    "kb leftover archive failed doc=%s chunk=%s",
                    doc_id,
                    assigned_id,
                    exc_info=True,
                )
            raise

    async def _set_chunk_count(
        self,
        doc: RecordRef,
        *,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        """回写文档的启用块数量与状态。"""
        user_id = str(doc["scope"]["user_id"])
        doc_id = str(doc["scope"]["doc_id"])
        live = await self._chunk_refs(user_id, doc_id, active_only=True)
        count = sum(1 for item in live if bool((item.get("meta") or {}).get("enabled", True)))
        updates: dict[str, Any] = {"chunk_count": count}
        if status is not None:
            updates["status"] = status
        if error is not None or status == "ready":
            updates["error"] = error
        latest = await self._document_ref(user_id, doc_id)
        await self._catalog.backend.put_record(_with_meta(latest, **updates))

    async def _rewrite_doc_enabled(
        self, user_id: str, doc_id: str, enabled: bool, profile_id: str
    ) -> None:
        """把各块 meta.doc_enabled 与文档启用状态对齐。"""
        for ref in await self._chunk_refs(user_id, doc_id, active_only=True):
            updated = _with_meta(ref, doc_enabled=enabled)
            await self._catalog.backend.put_record(updated)
            await self._sync_vector_meta(profile_id, updated)

    async def _archive_chunks(
        self,
        profile_id: str,
        refs: list[RecordRef],
        *,
        drop_vectors: bool = True,
    ) -> None:
        """把切片标成 archived，可选删除向量。"""
        if not refs:
            return
        store = self._stores.get(profile_id)
        ids: list[str] = []
        for ref in refs:
            archived = _with_meta(ref, status="archived", enabled=False)
            await self._catalog.backend.put_record(archived)
            ids.append(str(ref["id"]))
        if drop_vectors and store is not None and ids:
            try:
                await store.backend.vector_store_provider.delete_records(ids)
            except Exception:
                _LOG.warning(
                    "kb vector archive failed profile=%s count=%s",
                    profile_id,
                    len(ids),
                    exc_info=True,
                )

    async def _restore_chunks(self, profile_id: str, refs: list[RecordRef]) -> None:
        """重切失败时把旧块记录和向量写回去。"""
        for ref in refs:
            await self._catalog.backend.put_record(ref)
            try:
                await self._sync_vector_meta(profile_id, ref)
            except Exception:
                _LOG.warning(
                    "kb restore vector sync failed profile=%s record=%s",
                    profile_id,
                    ref.get("id"),
                    exc_info=True,
                )

    async def _purge_records(self, profile_id: str, refs: list[RecordRef]) -> None:
        """RecordStore 无公开 delete：按 sqlite 行硬删 chunk / document 与向量。"""
        ids = list(dict.fromkeys(str(ref["id"]) for ref in refs if ref.get("id")))
        if not ids:
            return
        store = self._stores.get(profile_id)
        if store is not None:
            try:
                await store.backend.vector_store_provider.delete_records(ids)
            except Exception:
                _LOG.warning(
                    "kb vector purge failed profile=%s count=%s",
                    profile_id,
                    len(ids),
                    exc_info=True,
                )
        backend = self._catalog.backend
        lock = getattr(backend, "_lock", None)
        connect = getattr(backend, "_connect", None)
        table_exists = getattr(backend, "_table_exists", None)
        if connect is None or table_exists is None:
            _LOG.warning("kb sqlite purge skipped: backend has no connect")
            return
        placeholders = ",".join("?" for _ in ids)
        parameters = tuple(ids)

        def _delete_rows(connection: Any) -> list[str]:
            """在同一事务里删 links / checkpoints / vectors / records。"""
            link_ids: list[str] = []
            if table_exists(connection, "links"):
                rows = connection.execute(
                    (
                        f"SELECT id FROM links WHERE source_id IN ({placeholders}) "
                        f"OR target_id IN ({placeholders})"
                    ),
                    (*parameters, *parameters),
                ).fetchall()
                link_ids = [str(row["id"]) for row in rows]
                connection.execute(
                    (
                        f"DELETE FROM links WHERE source_id IN ({placeholders}) "
                        f"OR target_id IN ({placeholders})"
                    ),
                    (*parameters, *parameters),
                )
            if table_exists(connection, "checkpoints"):
                connection.execute(
                    f"DELETE FROM checkpoints WHERE record_id IN ({placeholders})",
                    parameters,
                )
            if table_exists(connection, "record_store_vectors"):
                connection.execute(
                    f"DELETE FROM record_store_vectors WHERE record_id IN ({placeholders})",
                    parameters,
                )
            if table_exists(connection, "records"):
                connection.execute(
                    f"DELETE FROM records WHERE id IN ({placeholders})",
                    parameters,
                )
            connection.commit()
            if table_exists(connection, "records_fts"):
                try:
                    connection.execute(
                        f"DELETE FROM records_fts WHERE id IN ({placeholders})",
                        parameters,
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    _LOG.warning(
                        "kb fts purge skipped ids=%s",
                        ids,
                        exc_info=True,
                    )
            return link_ids

        async def _run() -> list[str]:
            """持写锁执行 sqlite 删除。"""
            with connect(write=True) as connection:
                return _delete_rows(connection)

        if lock is not None:
            async with lock:
                link_ids = await _run()
        else:
            link_ids = await _run()
        identity = getattr(backend, "_identity_catalog", None)
        discard = getattr(identity, "discard", None) if identity is not None else None
        if discard is not None:
            await discard([*ids, *link_ids])

    async def _sync_vector_meta(self, profile_id: str, ref: RecordRef) -> None:
        """块启用变化后同步向量：不可见则删，可见则复用或重 embed。"""
        store = self._stores.get(profile_id)
        if store is None:
            return
        provider = store.backend.vector_store_provider
        collection = getattr(provider, "_collection", None)
        record_id = str(ref["id"])
        visible = (ref.get("meta") or {}).get("status") == "active" and bool(
            (ref.get("meta") or {}).get("enabled", True)
        ) and bool((ref.get("meta") or {}).get("doc_enabled", True))
        if not visible:
            await provider.delete_records([record_id])
            return
        vector: list[float] | None = None
        if collection is not None:
            try:
                existing = collection.get(ids=[record_id], include=["embeddings"])
                embeddings = existing.get("embeddings") or []
                raw = embeddings[0] if embeddings else None
                if raw is not None and len(raw) > 0:
                    vector = [float(value) for value in raw]
            except Exception:
                vector = None
        if vector:
            await provider.index_record(ref, vector)
            return
        try:
            text = await self._catalog.get(ref)
        except Exception:
            return
        embeddings = await store.backend.embedding_provider.embed_texts([text])
        if embeddings and embeddings[0]:
            await provider.index_record(ref, embeddings[0])

    def _owned_ref(self, user_id: str, ref: RecordRef) -> bool:
        """命中必须仍属于当前用户，不单信检索过滤器。"""
        return str((ref.get("scope") or {}).get("user_id") or "") == user_id

    async def _document_ref(
        self, user_id: str, doc_id: str, *, include_archived: bool = False
    ) -> RecordRef:
        """按 doc_id 取文档 RecordRef；校验归属。"""
        hits = await self._catalog.search(
            filters={
                "collection": KB_COLLECTION,
                "kind": KIND_DOCUMENT,
                "scope.doc_id": doc_id,
            }
        )
        if not hits:
            raise KnowledgeError(404, "document not found")
        owners = {str((item.get("scope") or {}).get("user_id") or "") for item in hits}
        if user_id not in owners:
            raise KnowledgeError(403, "forbidden")
        mine = [
            item
            for item in hits
            if str((item.get("scope") or {}).get("user_id") or "") == user_id
        ]
        ref = mine[0]
        status = str((ref.get("meta") or {}).get("status") or "")
        if status == "archived" and not include_archived:
            raise KnowledgeError(404, "document not found")
        return ref

    async def _chunk_refs(
        self, user_id: str, doc_id: str, *, active_only: bool
    ) -> list[RecordRef]:
        """列出文档下切片记录。"""
        filters: dict[str, Any] = {
            "collection": KB_COLLECTION,
            "kind": KIND_CHUNK,
            "scope.user_id": user_id,
            "scope.doc_id": doc_id,
        }
        if active_only:
            filters["meta.status"] = "active"
        return await self._catalog.search(filters=filters)

    async def _chunk_ref(self, user_id: str, doc_id: str, chunk_id: str) -> RecordRef:
        """取一块当前有效记录；跨用户 403。"""
        hits = await self._catalog.search(
            filters={
                "collection": KB_COLLECTION,
                "kind": KIND_CHUNK,
                "scope.user_id": user_id,
                "scope.doc_id": doc_id,
                "scope.chunk_id": chunk_id,
                "meta.status": "active",
            }
        )
        if not hits:
            others = await self._catalog.search(
                filters={
                    "collection": KB_COLLECTION,
                    "kind": KIND_CHUNK,
                    "scope.doc_id": doc_id,
                    "scope.chunk_id": chunk_id,
                }
            )
            if others and str((others[0].get("scope") or {}).get("user_id") or "") != user_id:
                raise KnowledgeError(403, "forbidden")
            raise KnowledgeError(404, "chunk not found")
        return hits[0]

    async def _chunk_out(self, ref: RecordRef) -> KbChunkOut:
        """RecordRef 投影成 KbChunkOut。"""
        meta = dict(ref.get("meta") or {})
        scope = dict(ref.get("scope") or {})
        try:
            text = str(await self._catalog.get(ref))
        except Exception:
            text = str(ref.get("summary") or "")
        return KbChunkOut(
            chunk_id=str(scope.get("chunk_id") or ""),
            doc_id=str(scope.get("doc_id") or ""),
            text=text,
            window=_optional_str(meta.get("window")),
            header_path=_optional_str(meta.get("header_path")),
            element_type=_optional_str(meta.get("element_type")),
            char_start=_optional_int(meta.get("char_start")),
            char_end=_optional_int(meta.get("char_end")),
            ordinal=int(meta.get("ordinal") or 0),
            status="archived" if meta.get("status") == "archived" else "active",
            enabled=bool(meta.get("enabled", True)),
            diverged=bool(meta.get("diverged", False)),
            embedding_profile_id=str(meta.get("embedding_profile_id") or ""),
        )

    def _document_out(self, ref: RecordRef) -> KbDocumentOut:
        """RecordRef 投影成 KbDocumentOut。"""
        meta = dict(ref.get("meta") or {})
        raw_source = meta.get("source")
        source: Literal["upload", "paste"] = (
            raw_source if raw_source in {"upload", "paste"} else "paste"
        )
        status = meta.get("status")
        if status not in {"ingesting", "ready", "failed", "archived"}:
            status = "ready"
        raw_strategy = meta.get("strategy")
        strategy: ChunkStrategy = (
            raw_strategy if raw_strategy in {
                "sentence",
                "token",
                "markdown",
                "markdown_element",
                "semantic",
                "sentence_window",
            } else DEFAULT_CHUNK_STRATEGY
        )
        return KbDocumentOut(
            doc_id=str((ref.get("scope") or {}).get("doc_id") or ""),
            title=str(meta.get("title") or ref.get("summary") or ""),
            status=status,
            enabled=bool(meta.get("enabled", True)),
            source=source,
            filename=_optional_str(meta.get("filename")),
            mime=_optional_str(meta.get("mime")),
            artifact_id=_optional_str(meta.get("artifact_id")),
            sha256=_optional_str(meta.get("sha256")),
            size_bytes=_optional_int(meta.get("size_bytes")),
            strategy=strategy,
            chunk_size=int(meta.get("chunk_size") or DEFAULT_CHUNK_SIZE),
            chunk_overlap=int(meta.get("chunk_overlap") or DEFAULT_CHUNK_OVERLAP),
            window_size=int(meta.get("window_size") or DEFAULT_WINDOW_SIZE),
            breakpoint_percentile_threshold=int(
                meta.get("breakpoint_percentile_threshold") or DEFAULT_BREAKPOINT_PERCENTILE
            ),
            buffer_size=int(meta.get("buffer_size") or DEFAULT_BUFFER_SIZE),
            source_suffix=_optional_str(meta.get("source_suffix")),
            chunk_count=int(meta.get("chunk_count") or 0),
            embedding_profile_id=str(meta.get("embedding_profile_id") or self.default_profile),
            error=_optional_str(meta.get("error")),
            notes=str(meta.get("notes") or ""),
        )

    def _write_artifact(self, user_id: str, filename: str, data: bytes) -> dict[str, Any]:
        """把上传原件写入 files_root/{user_id}/{artifact_id}/。"""
        artifact_id = str(uuid4())
        stored = _safe_filename(filename)
        folder = self.files_root / user_id / artifact_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / stored).write_bytes(data)
        return {
            "artifact_id": artifact_id,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "stored_filename": stored,
            "filename": filename,
        }

    def _reject_foreign_profile(self, doc: RecordRef, profile_id: str | None) -> None:
        """单块请求不得换文档已锁定的 embedding。"""
        if not profile_id:
            return
        expected = str((doc.get("meta") or {}).get("embedding_profile_id") or "")
        if self._require_profile(profile_id) != expected:
            raise KnowledgeError(422, "embedding_profile_id does not match document")

    @staticmethod
    def _next_ordinal(refs: list[RecordRef]) -> int:
        """下一切片序号：当前最大 ordinal + 1。"""
        if not refs:
            return 0
        return max(int((item.get("meta") or {}).get("ordinal") or 0) for item in refs) + 1


def _extract(filename: str | None, data: bytes, mime: str | None):
    """抽上传文件正文；失败转成 KnowledgeError。"""
    try:
        return extract_upload(filename, data, mime)
    except ExtractError as error:
        raise KnowledgeError(error.status, str(error)) from error


def _document_meta(
    *,
    status: str,
    enabled: bool,
    source: str,
    title: str,
    filename: str | None,
    mime: str | None,
    artifact: dict[str, Any],
    command: CreateDocumentIn,
    profile_id: str,
    chunk_count: int,
    notes: str,
) -> dict[str, Any]:
    """组装文档 meta：切分参数、profile、冷文件指纹。"""
    meta: dict[str, Any] = {
        "status": status,
        "enabled": enabled,
        "source": source,
        "title": title,
        "strategy": command.strategy,
        "chunk_size": command.chunk_size,
        "chunk_overlap": command.chunk_overlap,
        "breakpoint_percentile_threshold": command.breakpoint_percentile_threshold,
        "buffer_size": command.buffer_size,
        "window_size": command.window_size,
        "chunk_count": chunk_count,
        "embedding_profile_id": profile_id,
        "notes": notes,
    }
    if filename:
        meta["filename"] = filename
    if mime:
        meta["mime"] = mime
    if command.source_suffix:
        meta["source_suffix"] = command.source_suffix
    for key in ("artifact_id", "sha256", "size_bytes", "stored_filename"):
        if artifact.get(key) is not None:
            meta[key] = artifact[key]
    if artifact.get("filename") and not meta.get("filename"):
        meta["filename"] = artifact["filename"]
    return meta


def _copy_ref(ref: RecordRef) -> RecordRef:
    """浅拷贝 RecordRef 的 meta/scope，避免原地改旧快照。"""
    copied = dict(ref)
    copied["meta"] = dict(ref.get("meta") or {})
    scope = ref.get("scope")
    if isinstance(scope, dict):
        copied["scope"] = dict(scope)
    return cast(RecordRef, copied)


def _with_meta(ref: RecordRef, **updates: Any) -> RecordRef:
    """复制一条记录并合并 meta 字段。"""
    updated = dict(ref)
    meta = dict(ref.get("meta") or {})
    for key, value in updates.items():
        if value is None and key in {"error", "source_suffix", "notes"}:
            meta.pop(key, None)
        else:
            meta[key] = value
    updated["meta"] = meta
    return cast(RecordRef, updated)


def _title(title: str | None, filename: str | None, text: str) -> str:
    """文档标题：显式 title > 文件名 > 正文首行。"""
    if title and title.strip():
        return title.strip()
    if filename:
        stem = Path(filename).stem.strip()
        if stem:
            return stem
    snippet = (text or "").strip().splitlines()
    if snippet and snippet[0].strip():
        return snippet[0].strip()[:80]
    return "未命名文档"


def _safe_filename(name: str) -> str:
    """冷文件名去掉路径与危险字符。"""
    base = Path(name).name.strip() or "upload"
    cleaned = _UNSAFE_NAME.sub("_", base).strip("._") or "upload"
    return cleaned[:180]


def _optional_str(value: Any) -> str | None:
    """空串视为 None。"""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: Any) -> int | None:
    """空值视为 None，否则转 int。"""
    if value is None or value == "":
        return None
    return int(value)
