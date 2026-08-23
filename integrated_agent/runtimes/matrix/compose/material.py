"""Compose 支共享：post_count、素材卡对齐与媒体 bundle。"""

from __future__ import annotations

from typing import Any

from agently import TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.branch_hold import _media_links_from_raw
from integrated_agent.runtimes.matrix.host.models import (
    MAX_COMPOSE_POSTS,
    MIN_COMPOSE_POSTS,
    media_links_as_dicts,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot

_DRAFT_ANGLE_HINTS = (
    "直入主题，信息密度高",
    "轻度提问或互动口吻",
    "故事化或场景化开头",
    "对比或清单式表达",
    "引用素材中的具体事实点",
    "简短有力的行动号召",
    "温和科普口吻",
    "情绪共鸣但不夸张",
    "突出差异化卖点",
    "收尾留一句开放式互动",
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def resolve_post_count(request: dict[str, Any], snapshot: Snapshot) -> int:
    platform_cap = max(
        MIN_COMPOSE_POSTS,
        min(int(snapshot.platform.max_posts), MAX_COMPOSE_POSTS),
    )
    raw = request.get("post_count")
    if raw is None:
        return MIN_COMPOSE_POSTS
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return MIN_COMPOSE_POSTS
    return max(MIN_COMPOSE_POSTS, min(count, platform_cap))


def draft_angle_hint(index: int) -> str:
    return _DRAFT_ANGLE_HINTS[(index - 1) % len(_DRAFT_ANGLE_HINTS)]


def _material_card_key(card: dict[str, Any]) -> str:
    return str(
        card.get("tweet_id")
        or card.get("link")
        or card.get("title")
        or card.get("text")
        or ""
    ).strip()


def _tweet_card_as_material(card: dict[str, Any]) -> dict[str, Any]:
    tweet_id = str(card.get("tweet_id") or "").strip()
    screen_name = str(card.get("screen_name") or "").lstrip("@").strip()
    link = ""
    if tweet_id and screen_name:
        link = f"https://x.com/{screen_name}/status/{tweet_id}"
    elif tweet_id:
        link = f"https://x.com/i/web/status/{tweet_id}"
    media = card.get("media") if isinstance(card.get("media"), list) else []
    media_links = media_links_as_dicts(card.get("media_links"))
    if not media_links:
        media_links = media_links_as_dicts(_media_links_from_raw(media))
    return {
        "kind": "tweet",
        "title": screen_name or tweet_id or str(card.get("title") or ""),
        "text": str(card.get("text") or ""),
        "link": link,
        "tweet_id": tweet_id,
        "screen_name": screen_name,
        "media": media,
        "media_links": media_links,
    }


def collect_material_cards(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """合并 Intel 写回的 material_list 与 tweet_cards，去重后返回素材卡列表。"""
    material_list = [
        item for item in _as_list(data.get_state("material_list")) if isinstance(item, dict)
    ]
    tweet_cards = [
        item for item in _as_list(data.get_state("tweet_cards")) if isinstance(item, dict)
    ]
    seen = {_material_card_key(card) for card in material_list if _material_card_key(card)}
    merged = list(material_list)
    for card in tweet_cards:
        normalized = _tweet_card_as_material(card)
        key = _material_card_key(normalized)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(normalized)
    return merged


def align_material_cards(
    cards: list[dict[str, Any]],
    post_count: int,
) -> tuple[list[dict[str, Any]], str]:
    """按 post_count 对齐素材卡：多则截断，少则循环补齐。"""
    if post_count <= 0:
        return [], "empty"
    if not cards:
        return [{} for _ in range(post_count)], "no_cards"
    if len(cards) > post_count:
        return cards[:post_count], "trimmed"
    if len(cards) == post_count:
        return list(cards), "one_to_one"
    aligned = list(cards)
    while len(aligned) < post_count:
        aligned.append(dict(cards[len(aligned) % len(cards)]))
    return aligned, "padded"


def compose_media_bundle(
    card: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从单张素材卡签发 offered_media / media_catalog（最多 1 张配图）。"""
    if not card:
        return [], []
    media_links = media_links_as_dicts(card.get("media_links"))
    if not media_links:
        media_links = media_links_as_dicts(_media_links_from_raw(card.get("media")))
    if not media_links:
        return [], []
    item = media_links[0]
    media_key = "m1"
    kind = str(item.get("type") or "photo")
    preview_url = str(item.get("preview_url") or item.get("thumb") or "").strip()
    catalog_item: dict[str, Any] = {
        "media_key": media_key,
        "kind": kind,
        "preview_url": preview_url,
    }
    file_url = str(item.get("file_url") or item.get("video_url") or "").strip()
    if file_url:
        catalog_item["file_url"] = file_url
    return [{"media_key": media_key}], [catalog_item]


def focus_hint_for_card(card: dict[str, Any], angle_hint: str) -> str:
    if not card:
        return angle_hint
    kind = str(card.get("kind") or "").strip().lower()
    title = str(card.get("title") or card.get("screen_name") or "").strip()
    if kind == "tweet" and title:
        return f"{angle_hint}；主要参考 @{title.lstrip('@')} 这条推文素材"
    if title:
        return f"{angle_hint}；主要参考素材《{title}》"
    return f"{angle_hint}；主要参考当前分配到的素材卡"


# Back-compat aliases used across compose package
_resolve_post_count = resolve_post_count
_draft_angle_hint = draft_angle_hint
_collect_material_cards = collect_material_cards
_align_material_cards = align_material_cards
_compose_media_bundle = compose_media_bundle
_focus_hint_for_card = focus_hint_for_card


__all__ = [
    "align_material_cards",
    "collect_material_cards",
    "compose_media_bundle",
    "draft_angle_hint",
    "focus_hint_for_card",
    "resolve_post_count",
    "_align_material_cards",
    "_collect_material_cards",
    "_compose_media_bundle",
    "_draft_angle_hint",
    "_focus_hint_for_card",
    "_resolve_post_count",
]
