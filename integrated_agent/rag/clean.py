from __future__ import annotations

import re
from typing import Sequence

from llama_index.core.schema import (
    BaseNode,
    MetadataMode,
    TransformComponent,
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_UNICODE_SPACE = re.compile(r"[\u00a0\u1680\u180e\u2000-\u200a\u202f\u205f\u3000]")
_EXTRA_NEWLINES = re.compile(r"\n{3,}")
_EXTRA_SPACES = re.compile(r"[ \t]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_QUOTE_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)
_MARKDOWN_STRATEGIES = frozenset({"markdown", "markdown_element"})
_STRUCTURED_SUFFIXES = frozenset({".md", ".markdown", ".html", ".htm"})


def preserve_structure_for(strategy: str, source_suffix: str = "") -> bool:
    """Markdown / HTML 结构依赖换行与标签，不能把段落硬折拼回去。"""
    return strategy in _MARKDOWN_STRATEGIES or source_suffix in _STRUCTURED_SUFFIXES


def clean_document_text(text: str, *, preserve_structure: bool = False) -> str:
    """切分前的文件清洗：控制符、空白、PDF 硬折行。

    对齐 LlamaIndex ingestion 的 Transform（TextCleaner）位置：Load 之后、
    Node Parser 之前。官方示例会 ``re.sub(r"[^0-9A-Za-z ]", "")``，会毁掉中文，
    这里只做确定性、可逆性更好的文件级整理。
    """
    if not text:
        return ""
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x0c", "\n\n")
    text = _CONTROL.sub("", text)
    text = _ZERO_WIDTH.sub("", text)
    text = _UNICODE_SPACE.sub(" ", text)
    text = text.translate(_QUOTE_TABLE)
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    if not preserve_structure:
        text = unwrap_soft_breaks(text)
        text = _EXTRA_SPACES.sub(" ", text)
    text = _EXTRA_NEWLINES.sub("\n\n", text)
    return text.strip()


def unwrap_soft_breaks(text: str) -> str:
    """同一段里的视觉换行拼回句子；空行仍是段界。"""
    paragraphs: list[str] = []
    buf: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if buf:
                paragraphs.append(_join_wrapped_lines(buf))
                buf = []
            continue
        buf.append(line)
        if re.search(r"[。！？；.!?]$", line):
            paragraphs.append(_join_wrapped_lines(buf))
            buf = []
    if buf:
        paragraphs.append(_join_wrapped_lines(buf))
    return "\n\n".join(item for item in paragraphs if item)


def _join_wrapped_lines(lines: list[str]) -> str:
    glued = lines[0]
    for line in lines[1:]:
        if glued.endswith("-") and re.match(r"[A-Za-z]", line):
            glued = glued[:-1] + line
        elif _CJK.search(glued[-1:]) or _CJK.search(line[:1]):
            glued += line
        else:
            glued += f" {line}"
    return glued


class TextCleaner(TransformComponent):
    """LlamaIndex TransformComponent：在 Node Parser 之前清洗 Document 文本。"""

    preserve_structure: bool = False

    def __call__(self, nodes: Sequence[BaseNode], **kwargs: object) -> list[BaseNode]:
        del kwargs
        cleaned: list[BaseNode] = []
        for node in nodes:
            body = clean_document_text(
                node.get_content(metadata_mode=MetadataMode.NONE),
                preserve_structure=self.preserve_structure,
            )
            if not body:
                continue
            node.set_content(body)
            cleaned.append(node)
        return cleaned
