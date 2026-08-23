"""compose / rewrite 草稿配图解析与签发。"""

from __future__ import annotations

import re
from typing import Any

_MEDIA_TOKEN_RE = re.compile(r"\[\[media:(m\d+)\]\]")
_CTA_TOKEN_RE = re.compile(r"\[\[cta:(\d+)\]\]")
_REF_CITE_RE = re.compile(r"\[\[ref:([^\]]+)\]\]")


def media_kind(raw_type: str) -> str:
    lowered = str(raw_type or "photo").strip().lower()
    if lowered == "video":
        return "video"
    if lowered in {"gif", "animated_gif"}:
        return "gif"
    return "photo"


def resolve_draft_refs(draft_text: str) -> str:
    """Gate 通过后剥掉 [[ref:e*]]；合规证据只保留在 evidence_ids。"""
    display_text = _REF_CITE_RE.sub("", draft_text or "")
    display_text = re.sub(r"\s{2,}", " ", display_text).strip()
    if not display_text:
        display_text = (draft_text or "").strip()
    return display_text


def resolve_draft_cta(
    draft_text: str,
    *,
    offered_cta_urls: list[str],
) -> str:
    """Gate 通过后把 [[cta:N]] 展开为已签发的官方 URL，推文字段不出现占位符。"""

    def repl(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 0 <= index < len(offered_cta_urls):
            return str(offered_cta_urls[index]).strip()
        return ""

    display_text = _CTA_TOKEN_RE.sub(repl, draft_text or "")
    display_text = re.sub(r"\s{2,}", " ", display_text).strip()
    if not display_text:
        display_text = (draft_text or "").strip()
    return display_text


def resolve_draft_media(
    draft_text: str,
    *,
    media_catalog: list[dict[str, Any]],
    default_reuse: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """解析 [[media:m*]] 占位，默认保留首张配图。"""
    keys = _MEDIA_TOKEN_RE.findall(draft_text)
    if not keys and default_reuse and media_catalog:
        keys = [str(media_catalog[0]["media_key"])]

    by_key = {
        str(item.get("media_key") or ""): item
        for item in media_catalog
        if str(item.get("media_key") or "")
    }
    attached = [dict(by_key[key]) for key in keys if key in by_key]

    display_text = _MEDIA_TOKEN_RE.sub("", draft_text)
    display_text = re.sub(r"\s{2,}", " ", display_text).strip()
    if not display_text:
        display_text = draft_text.strip()
    return display_text, attached


def to_draft_media_cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        media_key = str(item.get("media_key") or "").strip()
        if not media_key:
            continue
        card: dict[str, Any] = {
            "media_key": media_key,
            "kind": media_kind(str(item.get("kind") or item.get("type") or "photo")),
            "preview_url": str(
                item.get("preview_url")
                or item.get("thumb")
                or item.get("media_url_https")
                or ""
            ).strip(),
        }
        if item.get("width") is not None:
            card["width"] = item["width"]
        if item.get("height") is not None:
            card["height"] = item["height"]
        file_url = str(item.get("file_url") or item.get("video_url") or "").strip()
        if file_url:
            card["file_url"] = file_url
        cards.append(card)
    return cards


__all__ = ["media_kind", "resolve_draft_cta", "resolve_draft_media", "resolve_draft_refs", "to_draft_media_cards"]
