"""M3+ 分支汇合：收录 Intel / Source 卡片，归一化后供 M4 Brief 消费。"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.source import _source_media_entries
from integrated_agent.runtimes.matrix.host.models import media_links_as_dicts
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _collect_upstream(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """收集 Intel / Source 写回父图的原始字段。"""
    keys = (
        "tweet_cards",
        "trend_cards",
        "material_list",
        "tool_logs",
        "tool_result_cleaned",
        "source_post",
        "source_media",
        "source_text",
        "search_query",
        "author_card",
        "related_tweet_cards",
        "source_result",
        "intel_result",
        "plan_summary",
        "material_plan",
    )
    out: dict[str, Any] = {}
    for key in keys:
        value = data.get_state(key)
        if value is None or value == [] or value == "":
            continue
        out[key] = value
    return out


def _media_links_from_raw(media: Any) -> list[dict[str, Any]]:
    if not isinstance(media, list):
        return []
    links: list[dict[str, Any]] = []
    for item in media:
        if not isinstance(item, dict):
            continue
        thumb = str(
            item.get("thumb")
            or item.get("preview_url")
            or item.get("media_url_https")
            or ""
        ).strip()
        entry: dict[str, Any] = {
            "type": str(item.get("type") or item.get("kind") or "photo"),
            "thumb": thumb,
            "preview_url": thumb,
        }
        if item.get("video_url"):
            entry["video_url"] = str(item["video_url"])
        if item.get("file_url"):
            entry["file_url"] = str(item["file_url"])
        links.append(entry)
    return links


def _append_evidence(
    cards: list[dict[str, Any]],
    *,
    ref_id: str,
    kind: str,
    title: str,
    text: str,
    link: str = "",
    branch: str,
    media_links: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    body = str(text or title or link).strip()
    if not body and not media_links:
        return
    card: dict[str, Any] = {
        "ref_id": ref_id,
        "kind": kind,
        "title": title,
        "text": text,
        "ruling": body,
        "link": link,
        "branch": branch,
        "media_links": media_links or [],
    }
    if meta:
        card["meta"] = meta
    cards.append(card)


def _normalize_compose_cards(
    upstream: dict[str, Any],
    *,
    evidence: list[dict[str, Any]],
    ref_index: int,
) -> tuple[dict[str, Any], int]:
    material_list = [
        item for item in _as_list(upstream.get("material_list")) if isinstance(item, dict)
    ]
    tweet_cards = [
        item for item in _as_list(upstream.get("tweet_cards")) if isinstance(item, dict)
    ]
    trend_cards = [
        item for item in _as_list(upstream.get("trend_cards")) if isinstance(item, dict)
    ]

    for item in material_list:
        ref_index += 1
        media = media_links_as_dicts(item.get("media_links"))
        if not media:
            media = media_links_as_dicts(_media_links_from_raw(item.get("media")))
        _append_evidence(
            evidence,
            ref_id=f"e{ref_index}",
            kind=str(item.get("kind") or "material"),
            title=str(item.get("title") or ""),
            text=str(item.get("text") or ""),
            link=str(item.get("link") or ""),
            branch="compose",
            media_links=media,
            meta={
                k: item[k]
                for k in ("source_task_id", "source_goal")
                if item.get(k)
            },
        )

    for item in tweet_cards:
        ref_index += 1
        media_links = _media_links_from_raw(item.get("media"))
        screen = str(item.get("screen_name") or "").lstrip("@")
        tid = str(item.get("tweet_id") or "")
        link = (
            f"https://x.com/{screen}/status/{tid}"
            if screen and tid
            else (f"https://x.com/i/web/status/{tid}" if tid else "")
        )
        _append_evidence(
            evidence,
            ref_id=f"e{ref_index}",
            kind="tweet",
            title=screen or tid,
            text=str(item.get("text") or ""),
            link=link,
            branch="compose",
            media_links=media_links,
            meta={
                k: item[k]
                for k in ("tweet_id", "likes", "retweets", "replies", "screen_name")
                if item.get(k) is not None
            },
        )

    for item in trend_cards:
        trend = _as_dict(item.get("trend"))
        name = str(trend.get("name") or item.get("name") or "")
        if not name:
            continue
        ref_index += 1
        _append_evidence(
            evidence,
            ref_id=f"e{ref_index}",
            kind="trend",
            title=name,
            text=str(trend.get("description") or trend.get("context") or ""),
            branch="compose",
            meta={"trend": trend} if trend else None,
        )

    return {
        "material_list": material_list,
        "tweet_cards": tweet_cards,
        "trend_cards": trend_cards,
        "intel_result": str(upstream.get("intel_result") or ""),
        "plan_summary": str(upstream.get("plan_summary") or ""),
        "material_plan": _as_list(upstream.get("material_plan")),
        "tool_logs": _as_list(upstream.get("tool_logs")),
    }, ref_index


def _normalize_rewrite_cards(
    upstream: dict[str, Any],
    *,
    evidence: list[dict[str, Any]],
    ref_index: int,
) -> tuple[dict[str, Any], int]:
    source_post = upstream.get("source_post")
    source_post_dict = _as_dict(source_post) if source_post is not None else None
    source_media = [
        item for item in _as_list(upstream.get("source_media")) if isinstance(item, dict)
    ]
    if not source_media and source_post_dict:
        post_media = source_post_dict.get("media")
        if isinstance(post_media, list) and post_media:
            source_media = _source_media_entries(post_media)
    author_card = upstream.get("author_card")
    author_dict = _as_dict(author_card) if author_card is not None else None
    related = [
        item
        for item in _as_list(upstream.get("related_tweet_cards"))
        if isinstance(item, dict)
    ]

    if source_post_dict:
        ref_index += 1
        media_links = _media_links_from_raw(source_post_dict.get("media"))
        _append_evidence(
            evidence,
            ref_id=f"e{ref_index}",
            kind="source_post",
            title=str(source_post_dict.get("screen_name") or "source"),
            text=str(source_post_dict.get("text") or ""),
            link=str(source_post_dict.get("url") or ""),
            branch="rewrite",
            media_links=media_links,
            meta={"tweet_id": source_post_dict.get("tweet_id")},
        )

    for item in related:
        ref_index += 1
        media_links = _media_links_from_raw(item.get("media"))
        _append_evidence(
            evidence,
            ref_id=f"e{ref_index}",
            kind="related_tweet",
            title=str(item.get("screen_name") or item.get("tweet_id") or ""),
            text=str(item.get("text") or ""),
            link=str(item.get("url") or ""),
            branch="rewrite",
            media_links=media_links,
            meta={"tweet_id": item.get("tweet_id")},
        )

    return {
        "source_post": source_post_dict,
        "source_media": source_media,
        "source_text": str(upstream.get("source_text") or "").strip(),
        "search_query": str(upstream.get("search_query") or "").strip(),
        "author_card": author_dict,
        "related_tweet_cards": related,
        "source_result": str(upstream.get("source_result") or ""),
        "tool_result_cleaned": _as_list(upstream.get("tool_result_cleaned")),
        "tool_logs": _as_list(upstream.get("tool_logs")),
    }, ref_index


def _normalize_branch_context(
    *,
    intent: str,
    upstream: dict[str, Any],
    user_instruction: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    ref_index = 0
    compose_ctx: dict[str, Any] | None = None
    rewrite_ctx: dict[str, Any] | None = None

    if intent == "compose":
        compose_ctx, ref_index = _normalize_compose_cards(
            upstream, evidence=evidence, ref_index=ref_index
        )
    elif intent == "rewrite":
        rewrite_ctx, ref_index = _normalize_rewrite_cards(
            upstream, evidence=evidence, ref_index=ref_index
        )

    branch_context = {
        "intent": intent,
        "user_instruction": user_instruction,
        "compose": compose_ctx,
        "rewrite": rewrite_ctx,
        "evidence_count": len(evidence),
    }
    return branch_context, evidence


async def compose_branch_hold(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    intent = str(data.get_state("intent") or "compose")
    user_instruction = str(data.get_state("user_instruction") or "")
    upstream = _collect_upstream(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))

    branch_context, evidence_cards = _normalize_branch_context(
        intent=intent,
        upstream=upstream,
        user_instruction=user_instruction,
    )

    if intent == "compose" and not evidence_cards:
        if "intel_empty" not in limitations:
            limitations.append("intel_empty")

    material_cards = list(evidence_cards)
    if intent == "compose":
        compose = _as_dict(branch_context.get("compose"))
        raw_material = _as_list(compose.get("material_list"))
        if raw_material and not material_cards:
            material_cards = [
                item for item in raw_material if isinstance(item, dict)
            ]
        summary = f"共收录 {len(material_cards)} 张素材卡"
    else:
        summary = str(_as_dict(branch_context.get("rewrite")).get("source_result") or "")

    await data.async_set_state("branch_context", branch_context, emit=False)
    await data.async_set_state("evidence_cards", evidence_cards, emit=False)
    await data.async_set_state("material_list", material_cards, emit=False)

    package = {
        "status": "completed",
        "intent": intent,
        "task_type": "compose_post",
        "summary": summary,
        "material_cards": material_cards,
        "evidence_cards": evidence_cards,
        "branch_context": branch_context,
        "drafts": [],
        "limitations": limitations,
    }
    await data.async_set_state("package", package, emit=False)

    trace = cast(TraceLog | None, data.get_resource("trace"))
    if trace is not None:
        trace.log(
            layer="business",
            event_type="business.matrix.branch_hold",
            status="completed",
            subject_id=str(data.get_state("task_id") or ""),
            facts={
                "intent": intent,
                "evidence_cards": len(evidence_cards),
                "limitations": limitations,
            },
        )
    print(package)
    return package


__all__ = ["compose_branch_hold"]
