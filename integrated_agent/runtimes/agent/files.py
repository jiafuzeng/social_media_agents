"""工作区上传文件的确定性转换服务。

支持 xlsx/docx → Markdown 预览，以及 Markdown → PDF 制品。
供企微上传落盘与 AgentlyAgentRuntime 处理附件时调用；
对外稳定下载仍由 ArtifactStore 负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from docx import Document
from openpyxl import load_workbook
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer


MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 单文件上传上限
MAX_TEXT_CHARS = 12_000  # 回给 Agent 的文本预览截断长度


@dataclass(frozen=True)
class FileOperationResult:
    """一次文件转换的结果：文本预览 + 可选制品路径。"""

    operation_key: str  # 如 xlsx_to_markdown
    source_name: str  # 源文件名
    text: str  # 供模型阅读的预览文本
    artifact_path: Path | None = None  # 若生成了新文件（如 PDF）
    artifact_mime_type: str | None = None


FileHandler = Callable[[Path], FileOperationResult]


def _clean_component(value: str, fallback: str) -> str:
    """清洗路径分量，避免会话 id / 文件名注入非法字符。"""
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return cleaned[:100] or fallback


def _cell_text(value: object) -> str:
    """单元格转 Markdown 安全文本（转义 |、压平换行）。"""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_table(rows: list[list[str]]) -> list[str]:
    """把二维行数据编成 GFM 表格行。"""
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *[
            "| " + " | ".join(row) + " |"
            for row in normalized[1:]
        ],
    ]


class WorkspaceFileService:
    """按扩展名分发文件处理，并管理 uploads / artifacts 目录。

    目录约定：
    - uploads/<session_id>/  企微等渠道上传原文件
    - artifacts/<session>/   过程转换产物（如 md→pdf）
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.uploads_root = workspace_root / "uploads"
        self.artifacts_root = workspace_root / "artifacts"
        # 扩展名 → 转换函数；未注册的后缀在 process 中拒绝
        self._handlers: dict[str, FileHandler] = {
            ".xlsx": self._xlsx_to_markdown,
            ".docx": self._docx_to_markdown,
            ".md": self._markdown_to_pdf,
            ".markdown": self._markdown_to_pdf,
        }

    @property
    def supported_suffixes(self) -> tuple[str, ...]:
        """当前支持的文件后缀列表。"""
        return tuple(sorted(self._handlers))

    def save_upload(
        self,
        *,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> Path:
        """校验并落盘上传文件，返回本地绝对路径。

        由 WeComAssistant.on_file 调用；空内容与超限直接报错。
        """
        if not content:
            raise ValueError("上传文件为空")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("上传文件超过20MB限制")
        safe_session = _clean_component(session_id, "session")
        safe_name = _clean_component(Path(filename).name, "upload.bin")
        destination = self.uploads_root / safe_session / safe_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination

    def process(self, source_path: Path) -> FileOperationResult:
        """按后缀分发到对应转换器；不支持则抛 ValueError。"""
        suffix = source_path.suffix.lower()
        handler = self._handlers.get(suffix)
        if handler is None:
            supported = "、".join(self.supported_suffixes)
            raise ValueError(f"暂不支持 {suffix or '无扩展名'}；支持：{supported}")
        return handler(source_path)

    def _xlsx_to_markdown(self, source_path: Path) -> FileOperationResult:
        """Excel → Markdown：每表最多 100 行、每行最多 20 列。"""
        workbook = load_workbook(source_path, read_only=True, data_only=True)
        sections: list[str] = [f"# {source_path.name}"]
        try:
            for worksheet in workbook.worksheets:
                rows = [
                    [_cell_text(value) for value in row[:20]]
                    for row in worksheet.iter_rows(values_only=True)
                ][:100]
                rows = [
                    row
                    for row in rows
                    if any(cell.strip() for cell in row)
                ]
                sections.extend(
                    [
                        "",
                        f"## 工作表：{worksheet.title}",
                        "",
                        *_markdown_table(rows),
                    ]
                )
        finally:
            workbook.close()
        text = "\n".join(sections).strip()
        return FileOperationResult(
            operation_key="xlsx_to_markdown",
            source_name=source_path.name,
            text=text[:MAX_TEXT_CHARS],
        )

    def _docx_to_markdown(self, source_path: Path) -> FileOperationResult:
        """Word → Markdown：保留标题层级与表格。"""
        document = Document(str(source_path))
        sections: list[str] = [f"# {source_path.name}"]
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = (
                paragraph.style.name.lower()
                if paragraph.style is not None and paragraph.style.name
                else ""
            )
            if style_name.startswith("heading"):
                level_text = "".join(character for character in style_name if character.isdigit())
                level = min(max(int(level_text or "2"), 1), 6)
                sections.extend(["", f"{'#' * level} {text}"])
            else:
                sections.extend(["", text])
        for table_index, table in enumerate(document.tables, start=1):
            rows = [
                [_cell_text(cell.text) for cell in row.cells]
                for row in table.rows
            ]
            sections.extend(
                [
                    "",
                    f"## 表格 {table_index}",
                    "",
                    *_markdown_table(rows),
                ]
            )
        text = "\n".join(sections).strip()
        return FileOperationResult(
            operation_key="docx_to_markdown",
            source_name=source_path.name,
            text=text[:MAX_TEXT_CHARS],
        )

    def _markdown_to_pdf(self, source_path: Path) -> FileOperationResult:
        """Markdown → PDF：写入 artifacts，并返回预览文本。"""
        markdown_text = source_path.read_text(encoding="utf-8")
        # 用源文件父目录名（通常是 session）隔离产物
        session_artifacts = self.artifacts_root / source_path.parent.name
        session_artifacts.mkdir(parents=True, exist_ok=True)
        output_path = session_artifacts / f"{source_path.stem}.pdf"
        self._render_markdown_pdf(markdown_text, output_path)
        preview = markdown_text.strip()[:MAX_TEXT_CHARS]
        return FileOperationResult(
            operation_key="markdown_to_pdf",
            source_name=source_path.name,
            text=(
                f"已将 {source_path.name} 转换为 PDF。\n\n"
                f"处理后的文本预览：\n\n{preview}"
            ),
            artifact_path=output_path,
            artifact_mime_type="application/pdf",
        )

    @staticmethod
    def _render_markdown_pdf(markdown_text: str, output_path: Path) -> None:
        """用 reportlab + 中文字体把简化 Markdown 渲成 A4 PDF。"""
        font_name = "STSong-Light"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "ChineseBody",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=16,
            alignment=TA_LEFT,
            spaceAfter=4,
        )
        heading = ParagraphStyle(
            "ChineseHeading",
            parent=body,
            fontSize=16,
            leading=22,
            spaceBefore=8,
            spaceAfter=8,
        )
        story: list[Flowable] = []
        for raw_line in markdown_text.splitlines():
            line = raw_line.strip()
            if not line:
                story.append(Spacer(1, 3 * mm))
                continue
            if line.startswith("#"):
                text = line.lstrip("#").strip()
                story.append(Paragraph(escape(text), heading))
                continue
            if line.startswith(("- ", "* ")):
                line = f"• {line[2:].strip()}"
            story.append(Paragraph(escape(line), body))
        document = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
        )
        document.build(story)
