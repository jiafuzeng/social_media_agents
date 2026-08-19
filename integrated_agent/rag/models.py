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
