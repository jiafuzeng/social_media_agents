"""M4 改写支：rewrite_plan_card 与 WorkItem 宿主规则（纯函数）。"""

from __future__ import annotations

import re
from typing import Any

from integrated_agent.runtimes.matrix.host.models import WorkItem

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*")

_CTA_HINTS = ("官方", "渠道", "链接", "官网", "置顶", "店铺", "购买")


def split_source_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def wants_official_cta(user_instruction: str) -> bool:
    text = str(user_instruction or "")
    return any(hint in text for hint in _CTA_HINTS)


def talking_points_from_source(source_text: str, *, limit: int = 3) -> list[str]:
    sentences = split_source_sentences(source_text)
    return [item[:120] for item in sentences[:limit] if item.strip()]


def build_rewrite_plan_card(
    *,
    source_media: list[dict[str, Any]],
    source_post: dict[str, Any] | None,
    offered_cta_urls: list[str],
    user_instruction: str,
    limitations: list[str],
) -> dict[str, Any]:
    source_issues = list(limitations)
    media_choice = "none"
    if source_media or (
        isinstance(source_post, dict) and isinstance(source_post.get("media"), list)
    ):
        media_choice = "reuse_source_media"

    cta_url = ""
    if offered_cta_urls and wants_official_cta(user_instruction):
        cta_url = offered_cta_urls[0]

    if cta_url and cta_url not in offered_cta_urls:
        source_issues.append("rewrite_plan_invalid_cta")
        cta_url = ""

    return {
        "media_choice": media_choice,
        "cta_url": cta_url,
        "source_issues": source_issues,
    }


def build_rewrite_work_item(
    *,
    user_instruction: str,
    source_text: str,
    platform_key: str,
    source_issues: list[str],
) -> WorkItem:
    goal = user_instruction.strip() or "改写成此人设口吻，保留可核验事实，结尾一个增长 CTA"
    talking_points = talking_points_from_source(source_text)
    if not talking_points and source_text.strip():
        talking_points = [source_text.strip()[:120]]
    del source_issues
    return WorkItem(
        work_item_id="rw1",
        kind="compose_post",
        requirement_ids=["r1"],
        platform_key=platform_key,
        source_comment_key=None,
        goal=goal,
        talking_points=talking_points,
        claim_types=["format"],
    )


__all__ = [
    "build_rewrite_plan_card",
    "build_rewrite_work_item",
    "split_source_sentences",
    "talking_points_from_source",
    "wants_official_cta",
]
