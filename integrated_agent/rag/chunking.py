from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, Sequence

from llama_index.core.base.embeddings.base import BaseEmbedding, Embedding
from llama_index.core.bridge.pydantic import PrivateAttr
from llama_index.core.node_parser import (
    MarkdownElementNodeParser,
    MarkdownNodeParser,
    SemanticSplitterNodeParser,
    SentenceSplitter,
    SentenceWindowNodeParser,
    TokenTextSplitter,
)
from llama_index.core.schema import (
    BaseNode,
    Document,
    IndexNode,
    MetadataMode,
    TextNode,
)

from integrated_agent.config import KB_EMBEDDING_AGENTS
from integrated_agent.rag import embeddings as kb_embeddings
from integrated_agent.rag.clean import TextCleaner, preserve_structure_for
from integrated_agent.rag.models import (
    MARKDOWN_HEADING_DEPTH,
    ChunkPreviewError,
    ChunkStrategy,
    PreviewChunksIn,
    PreviewChunksOut,
    TextChunk,
)

_ZH_SENTENCE_RE = re.compile(r"(?<=[。！？；])")
_HEADING_RE = re.compile(r"^(#+)\s+(.*)$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _join_soft_breaks(text: str) -> str:
    """PDF 常见硬换行不当句界：同一段拼回去。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    glued = lines[0]
    for line in lines[1:]:
        if _CJK_RE.search(glued[-1:]) or _CJK_RE.search(line[:1]):
            glued += line
        else:
            glued += f" {line}"
    return glued


def split_zh_sentences(text: str) -> list[str]:
    """中文句边界：。！？；；空行分段。单行换行只是排版。"""
    if not text:
        return []
    parts: list[str] = []
    for block in re.split(r"\n{2,}", text):
        glued = _join_soft_breaks(block)
        if not glued:
            continue
        parts.extend(part for part in _ZH_SENTENCE_RE.split(glued) if part != "")
    return parts


class _ProfileEmbedding(BaseEmbedding):
    """把 Step 1 Agent embed_texts 接到 SemanticSplitterNodeParser。"""

    _profile_id: str = PrivateAttr()

    def __init__(self, profile_id: str) -> None:
        super().__init__(model_name=profile_id, embed_batch_size=10)
        self._profile_id = profile_id

    def _get_query_embedding(self, query: str) -> Embedding:
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query: str) -> Embedding:
        return await self._aget_text_embedding(query)

    def _get_text_embedding(self, text: str) -> Embedding:
        return self._get_text_embeddings([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[Embedding]:
        result = kb_embeddings.embed_profile_texts(self._profile_id, texts)
        if inspect.isawaitable(result):
            raise ChunkPreviewError(500, "semantic embed must run on the async parser path")
        return result

    async def _aget_text_embedding(self, text: str) -> Embedding:
        vectors = await self._aget_text_embeddings([text])
        return vectors[0]

    async def _aget_text_embeddings(self, texts: list[str]) -> list[Embedding]:
        return await kb_embeddings.embed_profile_texts(self._profile_id, texts)

    async def aget_text_embedding_batch(
        self,
        texts: list[str],
        show_progress: bool = False,
        **kwargs: Any,
    ) -> list[Embedding]:
        """按批顺序请求。官方默认 asyncio.gather 会并行打满网关额度。"""
        del show_progress, kwargs
        if not texts:
            return []
        results: list[Embedding] = []
        batch_size = max(1, int(self.embed_batch_size))
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            results.extend(await self._aget_text_embeddings(batch))
        return results


async def preview_chunks(command: PreviewChunksIn) -> PreviewChunksOut:
    """清洗 → 用户策略卡 Parser → TextChunk。扩展名只影响清洗，不改 Parser。不写 RecordStore。"""
    suffix = _normalize_source_suffix(command.source_suffix)
    documents = TextCleaner(
        preserve_structure=preserve_structure_for(command.strategy, suffix)
    )([Document(text=command.text)])
    if not documents:
        raise ChunkPreviewError(422, "text is empty")
    text = documents[0].get_content(metadata_mode=MetadataMode.NONE)

    notes = ""
    strategy: ChunkStrategy = command.strategy
    try:
        nodes = await _parse_nodes(command, documents)
    except Exception as exc:
        if command.strategy != "semantic" or isinstance(exc, ChunkPreviewError):
            raise
        notes = f"semantic embedding failed ({exc}); fell back to sentence"
        strategy = "sentence"
        nodes = _parse_sentence(documents, command.chunk_size, command.chunk_overlap)

    chunks = _project_chunks(text, nodes)
    return PreviewChunksOut(strategy=strategy, notes=notes, chunks=chunks)


def _normalize_source_suffix(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    name = Path(raw.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix:
        return suffix
    return raw if raw.startswith(".") else f".{raw}"


async def _parse_nodes(
    command: PreviewChunksIn, documents: Sequence[BaseNode]
) -> list[BaseNode]:
    docs = [node for node in documents if not isinstance(node, IndexNode)]
    if not docs:
        return []
    size, overlap = command.chunk_size, command.chunk_overlap
    strategy = command.strategy
    if strategy == "sentence":
        return _parse_sentence(docs, size, overlap)
    if strategy == "token":
        return TokenTextSplitter(
            chunk_size=size,
            chunk_overlap=overlap,
            backup_separators=["\n", "。", "！", "？", "；"],
        ).get_nodes_from_documents(docs, show_progress=False)
    if strategy == "markdown":
        capped = [
            Document(
                text=_cap_markdown_headings(
                    node.get_content(metadata_mode=MetadataMode.NONE),
                    MARKDOWN_HEADING_DEPTH,
                ),
                metadata=dict(node.metadata),
            )
            for node in docs
        ]
        sections = MarkdownNodeParser().get_nodes_from_documents(
            capped, show_progress=False
        )
        return _limit_length(sections, size, overlap)
    if strategy == "markdown_element":
        body = "\n\n".join(
            node.get_content(metadata_mode=MetadataMode.NONE) for node in docs
        )
        return _parse_markdown_element(body, size, overlap)
    if strategy == "semantic":
        profile_id = (command.embedding_profile_id or "").strip()
        if profile_id not in KB_EMBEDDING_AGENTS:
            raise ChunkPreviewError(422, f"unknown embedding_profile_id: {profile_id}")
        parser = SemanticSplitterNodeParser(
            embed_model=_ProfileEmbedding(profile_id),
            breakpoint_percentile_threshold=command.breakpoint_percentile_threshold,
            buffer_size=command.buffer_size,
            sentence_splitter=split_zh_sentences,
        )
        nodes = await parser.aget_nodes_from_documents(docs, show_progress=False)
        return _limit_length(nodes, size, overlap)
    if strategy == "sentence_window":
        windows = SentenceWindowNodeParser.from_defaults(
            sentence_splitter=split_zh_sentences,
            window_size=command.window_size,
            window_metadata_key="window",
            original_text_metadata_key="original_sentence",
        ).get_nodes_from_documents(docs, show_progress=False)
        return _limit_length(windows, size, overlap)
    raise ChunkPreviewError(422, f"unknown strategy: {strategy}")


def _parse_sentence(
    documents: Sequence[BaseNode], chunk_size: int, chunk_overlap: int
) -> list[BaseNode]:
    return _sentence_splitter(chunk_size, chunk_overlap).get_nodes_from_documents(
        list(documents), show_progress=False
    )


def _sentence_splitter(chunk_size: int, chunk_overlap: int) -> SentenceSplitter:
    return SentenceSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        paragraph_separator="\n\n",
        chunking_tokenizer_fn=split_zh_sentences,
        secondary_chunking_regex=r"[^。！？；\n]+[。！？；\n]?|[。！？；\n]",
    )


def _limit_length(
    nodes: Sequence[BaseNode], chunk_size: int, chunk_overlap: int
) -> list[BaseNode]:
    usable = [node for node in nodes if not isinstance(node, IndexNode)]
    if not usable:
        return []
    return _sentence_splitter(chunk_size, chunk_overlap).get_nodes_from_documents(
        usable, show_progress=False
    )


def _parse_markdown_element(
    text: str, chunk_size: int, chunk_overlap: int
) -> list[BaseNode]:
    """用官方 MarkdownElementNodeParser 抽元素，跳过表格 LLM 摘要。"""
    elements = MarkdownElementNodeParser().extract_elements(text)
    nodes: list[BaseNode] = []
    for element in elements:
        body = str(element.element).strip()
        if not body:
            continue
        element_type = element.type
        if element_type == "table_text":
            element_type = "table"
        nodes.append(Document(text=body, metadata={"element_type": element_type}))
    return _limit_length(nodes, chunk_size, chunk_overlap)


def _cap_markdown_headings(text: str, max_level: int) -> str:
    """超过三级的标题当成正文，避免再切一节。"""
    lines: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            lines.append(line)
            continue
        match = None if in_code else _HEADING_RE.match(line)
        if match and len(match.group(1)) > max_level:
            lines.append(f"**{match.group(2)}**")
        else:
            lines.append(line)
    return "\n".join(lines)


def _project_chunks(source: str, nodes: Sequence[BaseNode]) -> list[TextChunk]:
    cursor = 0
    chunks: list[TextChunk] = []
    for node in nodes:
        if isinstance(node, IndexNode):
            continue
        body = node.get_content(metadata_mode=MetadataMode.NONE)
        if not body.strip():
            continue
        start, end = _char_span(source, body, cursor, node)
        if start is not None:
            cursor = start + 1
        metadata = _public_metadata(node)
        chunks.append(
            TextChunk(
                text=body,
                char_start=start,
                char_end=end,
                metadata=metadata,
                header_path=_optional_str(metadata.get("header_path")),
                element_type=_optional_str(metadata.get("element_type")),
                window=_optional_str(metadata.get("window")),
            )
        )
    return chunks


def _char_span(
    source: str, body: str, cursor: int, node: BaseNode
) -> tuple[int | None, int | None]:
    if isinstance(node, TextNode) and node.start_char_idx is not None:
        start = node.start_char_idx
        end = node.end_char_idx
        if end is None:
            end = start + len(body)
        return start, end
    found = source.find(body, cursor)
    if found < 0:
        found = source.find(body)
    if found < 0:
        return None, None
    return found, found + len(body)


def _public_metadata(node: BaseNode) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(node.metadata).items()
        if not str(key).startswith("_")
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
