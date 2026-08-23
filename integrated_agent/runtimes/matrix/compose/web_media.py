"""校验公网 URL，并从网页抓取真实配图。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from integrated_agent.runtimes.matrix.host.models import media_links_as_dicts

_PLACEHOLDER_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "example.invalid",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "test",
    "invalid",
}

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_META_IMAGE_PATTERNS = (
    re.compile(
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        re.I,
    ),
    re.compile(
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
        re.I,
    ),
)

_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return ""


def is_public_page_url(url: str) -> bool:
    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    host = _hostname(text)
    if not host:
        return False
    if host in _PLACEHOLDER_HOSTS:
        return False
    for suffix in (".example", ".invalid", ".test", ".localhost"):
        if host.endswith(suffix):
            return False
    return True


def is_public_media_url(url: str) -> bool:
    text = str(url or "").strip()
    if not is_public_page_url(text):
        return False
    lower = text.lower()
    if lower.startswith("data:") or lower.startswith("blob:"):
        return False
    if any(token in lower for token in ("pixel", "spacer.gif", "1x1", "tracking")):
        return False
    return True


def sanitize_public_media_links(raw: Any) -> list[dict[str, Any]]:
    """去掉 example.com 等占位链接，只保留公网可访问媒体。"""
    links = media_links_as_dicts(raw)
    kept: list[dict[str, Any]] = []
    for item in links:
        preview = str(item.get("preview_url") or item.get("thumb") or "").strip()
        if is_public_media_url(preview):
            kept.append(
                {
                    "type": str(item.get("type") or "photo"),
                    "thumb": preview,
                    "preview_url": preview,
                    **(
                        {"video_url": item["video_url"]}
                        if item.get("video_url") and is_public_media_url(str(item["video_url"]))
                        else {}
                    ),
                    **(
                        {"file_url": item["file_url"]}
                        if item.get("file_url") and is_public_media_url(str(item["file_url"]))
                        else {}
                    ),
                }
            )
    return kept


def extract_media_urls_from_html(html: str, base_url: str) -> list[dict[str, Any]]:
    found: list[str] = []
    for pattern in _META_IMAGE_PATTERNS:
        for match in pattern.finditer(html):
            url = urljoin(base_url, str(match.group(1) or "").strip())
            if is_public_media_url(url):
                found.append(url)
    for match in _IMG_SRC_RE.finditer(html):
        url = urljoin(base_url, str(match.group(1) or "").strip())
        lower = url.lower()
        if not is_public_media_url(url):
            continue
        if lower.endswith(".svg") or lower.endswith(".ico"):
            continue
        found.append(url)

    deduped: list[str] = []
    seen: set[str] = set()
    for url in found:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)

    return [
        {"type": "photo", "thumb": url, "preview_url": url}
        for url in deduped[:3]
    ]


async def fetch_public_media_from_page(
    url: str,
    *,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    if not is_public_page_url(url):
        return []
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return []
            content_type = str(response.headers.get("content-type") or "").lower()
            if content_type.startswith("image/"):
                return [{"type": "photo", "thumb": str(response.url), "preview_url": str(response.url)}]
            html = response.text
    except Exception:
        return []
    return extract_media_urls_from_html(html, str(url))


__all__ = [
    "extract_media_urls_from_html",
    "fetch_public_media_from_page",
    "is_public_media_url",
    "is_public_page_url",
    "sanitize_public_media_links",
]
