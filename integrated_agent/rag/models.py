from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RagModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ChunkStrategy = Literal[
    "sentence",
    "token",
    "markdown",
    "markdown_element",
    "semantic",
    "sentence_window",
]

DEFAULT_CHUNK_STRATEGY: ChunkStrategy = "sentence"
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 64
MARKDOWN_HEADING_DEPTH = 3
DEFAULT_WINDOW_SIZE = 3
DEFAULT_BREAKPOINT_PERCENTILE = 95
DEFAULT_BUFFER_SIZE = 1


class ChunkPreviewError(Exception):
    """切分预览的业务错误，由 HTTP 层转成 4xx。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ExtractError(Exception):
    """抽文本失败，不入库。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class TextChunk(RagModel):
    """Parser Node 的预览投影；入库仍走同一形状（Step 3）。"""

    text: str
    char_start: int | None = None
    char_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    header_path: str | None = None
    element_type: str | None = None
    window: str | None = None


class PreviewChunksIn(RagModel):
    """切分预览（向导第二步）。非 semantic 不需要 embedding_profile_id。"""
    text: str = Field(min_length=1)
    strategy: ChunkStrategy = DEFAULT_CHUNK_STRATEGY
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, ge=1)
    chunk_overlap: int = Field(default=DEFAULT_CHUNK_OVERLAP, ge=0)
    embedding_profile_id: str | None = None
    breakpoint_percentile_threshold: int = Field(
        default=DEFAULT_BREAKPOINT_PERCENTILE, ge=0, le=100
    )
    buffer_size: int = Field(default=DEFAULT_BUFFER_SIZE, ge=1)
    window_size: int = Field(default=DEFAULT_WINDOW_SIZE, ge=1)
    source_suffix: str | None = None

    @model_validator(mode="after")
    def check_length_and_semantic(self) -> "PreviewChunksIn":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        if self.strategy == "semantic" and not (
            self.embedding_profile_id and self.embedding_profile_id.strip()
        ):
            raise ValueError("embedding_profile_id is required for semantic")
        return self


class PreviewChunksOut(RagModel):
    strategy: ChunkStrategy
    notes: str = ""
    chunks: list[TextChunk]


class ChunkStrategyCard(RagModel):
    id: ChunkStrategy
    label: str
    parser: str
    extra_params: list[str] = Field(default_factory=list)


class ChunkStrategyListOut(RagModel):
    default: ChunkStrategy
    strategies: list[ChunkStrategyCard]


CHUNK_STRATEGY_CATALOG = ChunkStrategyListOut(
    default=DEFAULT_CHUNK_STRATEGY,
    strategies=[
        ChunkStrategyCard(
            id="sentence",
            label="按句子",
            parser="SentenceSplitter",
        ),
        ChunkStrategyCard(
            id="token",
            label="按 Token",
            parser="TokenTextSplitter",
        ),
        ChunkStrategyCard(
            id="markdown",
            label="按 Markdown 标题",
            parser="MarkdownNodeParser",
            extra_params=["heading_depth"],
        ),
        ChunkStrategyCard(
            id="markdown_element",
            label="按 Markdown 元素",
            parser="MarkdownElementNodeParser",
        ),
        ChunkStrategyCard(
            id="semantic",
            label="按语义",
            parser="SemanticSplitterNodeParser",
            extra_params=[
                "breakpoint_percentile_threshold",
                "buffer_size",
                "embedding_profile_id",
            ],
        ),
        ChunkStrategyCard(
            id="sentence_window",
            label="句子窗口",
            parser="SentenceWindowNodeParser",
            extra_params=["window_size"],
        ),
    ],
)


class ExtractOut(RagModel):
    text: str
    filename: str
    content_type: str | None = None


class EmbeddingProfileListOut(RagModel):
    default: str
    profiles: list[str]


# --- 文档 / 切片 CRUD 与工作区检索（Step 3–4）---

MAX_INGEST_CHUNKS = 2000  # 整篇入库上限；预览不走这条

# ingesting=切分嵌入中；ready=可检索；failed=本轮失败（记录仍在）；archived=已删出列表
DocumentStatus = Literal["ingesting", "ready", "failed", "archived"]
# active 可检索；archived 对检索不可见。文档删除仍归档；重切成功后宿主硬删旧 chunk 行
ChunkRecordStatus = Literal["active", "archived"]
KbSource = Literal["upload", "paste"]  # upload 落冷文件，paste 只有正文


class KnowledgeError(Exception):
    """知识库门面错误，由 HTTP 层转成 4xx/5xx。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class _ChunkParams(RagModel):
    """切分参数；overlap 必须小于 chunk_size。"""

    strategy: ChunkStrategy = DEFAULT_CHUNK_STRATEGY
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, ge=1)
    chunk_overlap: int = Field(default=DEFAULT_CHUNK_OVERLAP, ge=0)
    breakpoint_percentile_threshold: int = Field(
        default=DEFAULT_BREAKPOINT_PERCENTILE, ge=0, le=100
    )  # semantic 用
    buffer_size: int = Field(default=DEFAULT_BUFFER_SIZE, ge=1)  # semantic 用
    window_size: int = Field(default=DEFAULT_WINDOW_SIZE, ge=1)  # sentence_window 用
    source_suffix: str | None = None  # 如 .md；只作记录，不替用户选 Parser

    @model_validator(mode="after")
    def check_overlap(self) -> "_ChunkParams":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self


class CreateDocumentIn(_ChunkParams):
    """新建文档。JSON=粘贴；multipart 另带 file。切片锁定本次 embedding_profile_id。"""

    title: str | None = None
    text: str | None = None  # 粘贴正文；上传时由抽取补齐
    source: KbSource = "paste"
    filename: str | None = None
    mime: str | None = None
    embedding_profile_id: str | None = None  # 缺省走 yaml default；此后改块不能换


class UpdateDocumentIn(RagModel):
    """改文档卡。换 embedding_profile_id 必须 rechunk=true，禁止静默改徽章。"""

    title: str | None = None
    enabled: bool | None = None  # 停用后检索看不到整篇
    text: str | None = None  # 无冷文件时重切用这份正文
    rechunk: bool = False  # true=先归档旧块再写入新块，成功后硬删旧记录
    embedding_profile_id: str | None = None
    strategy: ChunkStrategy | None = None
    chunk_size: int | None = Field(default=None, ge=1)
    chunk_overlap: int | None = Field(default=None, ge=0)
    breakpoint_percentile_threshold: int | None = Field(default=None, ge=0, le=100)
    buffer_size: int | None = Field(default=None, ge=1)
    window_size: int | None = Field(default=None, ge=1)
    source_suffix: str | None = None

    @model_validator(mode="after")
    def check_overlap_when_both(self) -> "UpdateDocumentIn":
        if (
            self.chunk_size is not None
            and self.chunk_overlap is not None
            and self.chunk_overlap >= self.chunk_size
        ):
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self


class CreateChunkIn(RagModel):
    """单块入库：当场 embed + put。不重写兄弟块。profile 必须等于文档徽章。"""

    text: str = Field(min_length=1)
    ordinal: int | None = Field(default=None, ge=0)
    header_path: str | None = None
    element_type: str | None = None
    window: str | None = None  # sentence_window 的窗口文；向量打在 text 上
    embedding_profile_id: str | None = None  # 若传则必须与文档一致，否则 422


class UpdateChunkIn(RagModel):
    """改一块。改正文会 put 新记录并归档旧行，diverged=true，原件不变。"""

    text: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None  # 停用后检索不可见
    ordinal: int | None = Field(default=None, ge=0)
    header_path: str | None = None
    element_type: str | None = None
    window: str | None = None
    embedding_profile_id: str | None = None  # 不允许单块换模型

    @model_validator(mode="after")
    def require_patch_field(self) -> "UpdateChunkIn":
        if (
            self.text is None
            and self.enabled is None
            and self.ordinal is None
            and self.header_path is None
            and self.element_type is None
            and self.window is None
            and self.embedding_profile_id is None
        ):
            raise ValueError("at least one field is required")
        return self


class KbDocumentOut(RagModel):
    """列表/详情投影。archived 对 GET 表现为 404，不出现在列表。"""

    doc_id: str
    title: str
    status: DocumentStatus
    enabled: bool
    source: KbSource
    filename: str | None = None
    mime: str | None = None
    artifact_id: str | None = None  # 有则表示可下载冷文件
    sha256: str | None = None
    size_bytes: int | None = None
    strategy: ChunkStrategy
    chunk_size: int
    chunk_overlap: int
    window_size: int
    breakpoint_percentile_threshold: int
    buffer_size: int
    source_suffix: str | None = None
    chunk_count: int  # 当前未归档块数
    embedding_profile_id: str  # 只读徽章；切片 CRUD 锁定此值
    error: str | None = None
    notes: str = ""  # 如 semantic 降级说明


class KbDocumentListOut(RagModel):
    """当前用户可见文档（不含 archived）。"""

    documents: list[KbDocumentOut]


class KbChunkOut(RagModel):
    """单块投影。检索只命中 status=active 且 enabled 的块。"""

    chunk_id: str
    doc_id: str
    text: str
    window: str | None = None
    header_path: str | None = None
    element_type: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    ordinal: int
    status: ChunkRecordStatus
    enabled: bool
    diverged: bool  # 改正文后为 true，冷文件未改
    embedding_profile_id: str


class KbChunkListOut(RagModel):
    chunks: list[KbChunkOut]


class SearchKbIn(RagModel):
    """工作区召回。query 与 embedding_profile_id 都必填，省略 profile 则 422。"""

    query: str = Field(min_length=1)
    embedding_profile_id: str = Field(min_length=1)


# 空串=有命中；其余三种空库语义互斥，前端用来区分「没入库 / 模型不对 / 未命中」
SearchEmptyReason = Literal["", "library_empty", "no_docs_for_profile", "no_match"]


class SearchKbHit(RagModel):
    """一条召回。只含当前 profile 下启用文档的启用块。"""

    chunk_id: str
    doc_id: str
    text: str
    window: str | None = None
    header_path: str | None = None
    score: float | None = None
    embedding_profile_id: str


class SearchKbOut(RagModel):
    """hybrid top_n=4。不跨 profile 融合。"""

    query: str
    embedding_profile_id: str
    hits: list[SearchKbHit]
    empty_reason: SearchEmptyReason = ""
    profile_doc_count: int = 0  # 当前模型下可检索文档数
    other_profile_doc_count: int = 0  # 其他模型的文档数，提示去换顶栏而非融合检索


class ChatKbTurn(RagModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=800)


class ChatKbIn(RagModel):
    """工作区召回聊天。检索契约与 search 相同；history 只作指代，证据仍以本次 hits 为准。"""

    query: str = Field(min_length=1, max_length=2000)
    embedding_profile_id: str = Field(min_length=1)
    history: list[ChatKbTurn] = Field(default_factory=list, max_length=8)


class ChatKbHit(SearchKbHit):
    kb_id: str
    source_query: str | None = None
    hit_text: str | None = None  # 兼容旧字段；引用锚点以 text 为准
    context: str | None = None  # 补全后的阅读上下文，不作为独立引用


class ChatKbOut(RagModel):
    query: str
    embedding_profile_id: str
    answer: str
    hits: list[ChatKbHit]
    cited_kb_ids: list[str] = Field(default_factory=list)
    rewritten_query: str = ""
    retrieval_queries: list[str] = Field(default_factory=list)
    empty_reason: SearchEmptyReason = ""
    profile_doc_count: int = 0
    other_profile_doc_count: int = 0
    limitations: list[str] = Field(default_factory=list)
