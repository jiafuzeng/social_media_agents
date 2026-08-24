"""M3 Intel 子图：TikHub ReAct（Reason ↔ Act）+ need_trends 门闩 + Search/Browse fallback。

纯主题且 need_trends=false → 空卡进 Brief，0 次 HTTP。
need_trends=true 离开前宿主保证 1 次 fetch_trending(country=china)。
TikHub 无卡或不可用时，fallback Search/Browse 采集公网素材（非 SearchWeb 签发）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.builtins.actions import Browse, Search
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.compose.branch_hold import (
    _media_links_from_raw,
    _normalize_compose_cards,
)
from integrated_agent.runtimes.matrix.compose.materialize import materialize_tool_batch
from integrated_agent.runtimes.matrix.compose.web_media import (
    fetch_public_media_from_page,
    is_public_page_url,
    sanitize_public_media_links,
)
from integrated_agent.runtimes.matrix.host.models import media_links_as_dicts
from integrated_agent.runtimes.matrix.host.tikhubtools import compose_tools_list
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_THOUGHTS = 8
MAX_HTTP = 4
MAX_SEARCH_BROWSE_ROUNDS = 2
SEARCH_BROWSE_TIMEOUT_SEC = 45.0
_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s]+",
    re.IGNORECASE,
)
_HANDLE_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{1,15})\b")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tikhub_available() -> bool:
    return bool((os.environ.get("TIKHUB_API_KEY") or "").strip())


def _search_browse_actions() -> list[Any]:
    return [
        Search(
            proxy=os.environ.get("http_proxy", ""),
            timeout=20,
            backend="yahoo",
            max_attempts=1,
            region="cn-zh",
        ),
        Browse(
            proxy=os.environ.get("http_proxy", ""),
            timeout=20,
            fallback_order=("bs4",),
            enable_playwright=False,
            enable_bs4=True,
            max_content_length=12_000,
        ),
    ]


async def _resolve_public_material_media(card: dict[str, Any], *, goal: str) -> dict[str, Any]:
    """把占位链接替换为搜索+抓页得到的公网可访问配图。"""
    item = _normalize_material_card(card)
    if item.get("media_links"):
        return item

    page_url = str(item.get("link") or "").strip()
    if is_public_page_url(page_url):
        media = sanitize_public_media_links(await fetch_public_media_from_page(page_url))
        if media:
            item["media_links"] = media[:1]
            return item

    query = goal or str(item.get("title") or item.get("text") or "").strip()
    if not query:
        item["media_links"] = []
        return item

    search = _search_browse_actions()[0]
    try:
        results = await search.search(query, max_results=5)
    except Exception:
        results = []

    for row in results if isinstance(results, list) else []:
        if not isinstance(row, dict):
            continue
        candidate = str(
            row.get("href") or row.get("url") or row.get("link") or ""
        ).strip()
        if not is_public_page_url(candidate):
            continue
        media = sanitize_public_media_links(await fetch_public_media_from_page(candidate))
        item["link"] = candidate
        title = str(row.get("title") or "").strip()
        if title and not str(item.get("title") or "").strip():
            item["title"] = title
        if media:
            item["media_links"] = media[:1]
            return item

    item["media_links"] = []
    return item


def _material_card_from_answer(
    answer: str,
    *,
    goal: str,
    task_id: str,
) -> dict[str, Any] | None:
    text = str(answer or "").strip()
    if not text:
        return None
    return _normalize_material_card(
        {
            "kind": "article",
            "title": goal or text[:40],
            "text": text,
            "link": "",
            "media_links": [],
            "source_task_id": task_id,
            "source_goal": goal,
        }
    )


async def _collect_search_browse_materials(
    *,
    goal: str,
    task_id: str,
) -> list[dict[str, Any]]:
    """TikHub 无卡时的 Search/Browse fallback（仍不写正文）。"""

    async def _run() -> Any:
        return await (
            Agently.create_agent(name="matrix-compose-intel-task")
            .input({"goal": goal})
            .info({"task": {"task_id": task_id, "goal": goal}})
            .use_actions(_search_browse_actions())
            .set_action_loop(max_rounds=MAX_SEARCH_BROWSE_ROUNDS)
            .instruct(
                [
                    "只完成当前子任务，整理素材卡；不要写推文正文。",
                    "material_list 是主交付物：每条采集结果必须写成一张卡片，禁止只写在 answer 里。",
                    "最多 Search 1 次、Browse 1 个链接；完成后立刻输出 JSON。",
                    "每条素材：kind（tweet/article/trend）、title、text、link、media_links（对象数组）。",
                    "media_links 每项必须是对象：{type, thumb, preview_url}，禁止只写 URL 字符串。",
                    "禁止编造 example.com、placeholder 或无法访问的 URL；link/media_links 必须是公网真实 http(s) 地址。",
                    "若页面或推文含图片/视频，必须把真实 URL 写入 media_links。",
                    "answer 只允许一句话；正文、摘要、诗句等都放进 material_list。",
                    "即使搜索无结果，也要输出 material_list: []，不要省略该字段。",
                ]
            )
            .output(
                {
                    "answer": (str, "一句话采集结论"),
                    "material_list": (
                        list,
                        "[{kind, title, text, link, media_links}]",
                    ),
                },
                format="json",
            )
            .async_start(max_retries=1)
        )

    try:
        raw = await asyncio.wait_for(_run(), timeout=SEARCH_BROWSE_TIMEOUT_SEC)
    except Exception:
        return []

    if not isinstance(raw, dict):
        raw = {}
    material_list = raw.get("material_list") or []
    if not isinstance(material_list, list):
        material_list = []
    cleaned = [
        _normalize_material_card(item)
        for item in material_list
        if isinstance(item, dict)
    ]
    if not cleaned:
        fallback = _material_card_from_answer(
            str(raw.get("answer") or ""),
            goal=goal,
            task_id=task_id,
        )
        if fallback is not None:
            cleaned = [fallback]
    resolved: list[dict[str, Any]] = []
    for item in cleaned:
        item.setdefault("source_task_id", task_id)
        item.setdefault("source_goal", goal)
        resolved.append(await _resolve_public_material_media(item, goal=goal))
    return resolved


def _search_keywords(goal: str) -> list[str]:
    text = goal.strip()
    if not text:
        return []
    keywords: list[str] = []
    for candidate in (
        text,
        text.split("，", 1)[0].strip(),
        text.split(",", 1)[0].strip(),
    ):
        if candidate and candidate not in keywords:
            keywords.append(candidate)
    for prefix in ("查找", "搜索", "采集", "获取", "了解"):
        if text.startswith(prefix):
            short = text[len(prefix) :].strip(" ：:，,")
            if short and short not in keywords:
                keywords.append(short)
    return keywords[:3]


def _pick_tweet_for_goal(tweets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tweets:
        return None
    with_media = [tw for tw in tweets if _media_links_from_raw(tw.get("media"))]
    return (with_media or tweets)[0]


def _materials_have_media(items: list[dict[str, Any]]) -> bool:
    for item in items:
        if item.get("media_links"):
            return True
        if _media_links_from_raw(item.get("media")):
            return True
    return False


async def _host_fetch_tweet_material(
    *,
    goal: str,
    task_id: str,
) -> dict[str, Any] | None:
    """宿主侧优先搜带 media 的推文素材（search_type=Media）。"""
    if not _tikhub_available():
        return None
    tool_name = "fetch_search_timeline"
    if tool_name not in compose_tools_list:
        return None
    for keyword in _search_keywords(goal):
        for search_type in ("Media", "Latest"):
            args = {"keyword": keyword, "search_type": search_type}
            try:
                result = await _run_one_tool(tool_name, args, compose_tools_list)
                if isinstance(result.get("result"), dict) and (
                    result["result"].get("error") or result["result"].get("ok") is False
                ):
                    continue
                batch = [{"tool": tool_name, "args": args, "result": result.get("result")}]
                cleaned = materialize_tool_batch(batch, max_tweets=8)
                tweet = _pick_tweet_for_goal(_tweet_items_from_cleaned(cleaned))
                if tweet is None:
                    continue
                card = _material_card_from_tweet(tweet)
                card.setdefault("source_task_id", task_id)
                card.setdefault("source_goal", goal)
                return card
            except Exception:
                continue
    return None


async def _ensure_material_media(
    material_list: list[dict[str, Any]],
    *,
    goal: str,
    task_id: str,
    fallback_search_browse: bool,
    limitations: list[str],
) -> list[dict[str, Any]]:
    """TikHub Media 搜索 → Search/Browse，直到素材卡有 media_links 或耗尽。"""
    out = list(material_list)
    if goal and not _materials_have_media(out):
        host_card = await _host_fetch_tweet_material(goal=goal, task_id=task_id or "intel-host")
        if host_card:
            out = _dedupe_materials([host_card, *out])
    if fallback_search_browse and goal and not _materials_have_media(out):
        browse_cards = await _collect_search_browse_materials(
            goal=goal,
            task_id=task_id or "intel-fallback",
        )
        if browse_cards:
            out = _dedupe_materials([*browse_cards, *out])
        elif "search_browse_empty" not in limitations:
            limitations.append("search_browse_empty")
    return out


def _tool_call_signature(name: str, args: dict[str, Any]) -> tuple[str, str]:
    return name, json.dumps(args, sort_keys=True, ensure_ascii=False)


def _attempted_tool_signatures(cleaned: list[Any]) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if not tool:
            continue
        signatures.add(_tool_call_signature(tool, _as_dict(item.get("args"))))
    return signatures


def _filter_new_tools(
    pending: list[dict[str, Any]], cleaned: list[Any]
) -> list[dict[str, Any]]:
    attempted = _attempted_tool_signatures(cleaned)
    fresh: list[dict[str, Any]] = []
    for item in pending:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        args = _as_dict(item.get("args"))
        if _tool_call_signature(name, args) in attempted:
            continue
        fresh.append({"name": name, "args": args})
    return fresh


def _parse_pending_tools(raw: dict[str, Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for item in raw.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        pending.append({"name": name, "args": _as_dict(item.get("args"))})
    return pending


def _has_url_or_handle_candidate(
    *,
    source_kind: str,
    source_anchor: str,
    text: str,
) -> bool:
    kind = str(source_kind or "none").strip().lower()
    anchor = str(source_anchor or "").strip().lstrip("@")
    if kind in {"url", "tweet_id", "handle"} and anchor:
        return True
    if _URL_RE.search(text or ""):
        return True
    if _HANDLE_RE.search(text or ""):
        return True
    return False


def _intel_allowlist(*, need_trends: bool) -> dict[str, dict[str, Any]]:
    names = [
        "fetch_search_timeline",
        "fetch_user_profile",
        "fetch_user_post_tweet",
        "fetch_user_media",
    ]
    if need_trends:
        names.append("fetch_trending")
    return {name: compose_tools_list[name] for name in names if name in compose_tools_list}


def _normalize_trending_country(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return "China"
    mapping = {
        "china": "China",
        "unitedstates": "UnitedStates",
        "united states": "UnitedStates",
        "us": "UnitedStates",
        "usa": "UnitedStates",
    }
    return mapping.get(text.casefold(), text)


def confirm_intel_tool(
    name: str,
    args: dict[str, Any],
    *,
    need_trends: bool,
    allowlist: set[str],
) -> tuple[dict[str, Any] | None, str]:
    """机器 Confirm：通过返回规范化 args，失败返回原因。"""
    tool = str(name or "").strip()
    if tool not in allowlist:
        return None, f"confirm_reject:not_in_allowlist:{tool or 'empty'}"
    cleaned = {k: v for k, v in args.items() if v is not None}
    if tool == "fetch_trending":
        if not need_trends:
            return None, "confirm_reject:trending_disabled"
        cleaned["country"] = _normalize_trending_country(cleaned.get("country"))
        return cleaned, ""
    if tool == "fetch_search_timeline":
        keyword = str(cleaned.get("keyword") or "").strip()
        if not keyword:
            return None, "confirm_reject:empty_keyword"
        cleaned["keyword"] = keyword
        if not str(cleaned.get("search_type") or "").strip():
            cleaned["search_type"] = "Latest"
        return cleaned, ""
    if tool in {"fetch_user_profile", "fetch_user_post_tweet"}:
        screen = str(cleaned.get("screen_name") or "").strip().lstrip("@")
        rest_id = str(cleaned.get("rest_id") or "").strip()
        if bool(screen) == bool(rest_id):
            return None, "confirm_reject:screen_name_xor_rest_id"
        if screen:
            cleaned["screen_name"] = screen
            cleaned.pop("rest_id", None)
        else:
            cleaned["rest_id"] = rest_id
            cleaned.pop("screen_name", None)
        return cleaned, ""
    if tool == "fetch_user_media":
        screen = str(cleaned.get("screen_name") or "").strip().lstrip("@")
        if not screen:
            return None, "confirm_reject:media_needs_screen_name"
        cleaned["screen_name"] = screen
        return cleaned, ""
    return cleaned, ""


def _summarize_cleaned_for_reason(cleaned: list[Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "tweet":
            summary.append(
                {
                    "kind": "tweet",
                    "tweet_id": item.get("tweet_id"),
                    "screen_name": item.get("screen_name"),
                    "text": str(item.get("text") or "")[:240],
                }
            )
            continue
        if kind == "trend":
            trend = _as_dict(item.get("trend"))
            summary.append(
                {
                    "kind": "trend",
                    "name": trend.get("name") or item.get("name"),
                }
            )
            continue
        if kind == "error":
            summary.append(
                {
                    "kind": "error",
                    "tool": item.get("tool"),
                    "error": item.get("error"),
                }
            )
            continue
        summary.append({"kind": kind or "unknown", "tool": item.get("tool")})
    return summary


def _http_count(cleaned: list[Any]) -> int:
    seen: set[tuple[str, str]] = set()
    count = 0
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if not tool or str(item.get("kind") or "") == "error" and not tool:
            continue
        # Count each attempted tool call once (success or error card with tool)
        if "tool" not in item and not tool:
            continue
        sig = _tool_call_signature(tool, _as_dict(item.get("args")))
        if sig in seen:
            continue
        if tool:
            seen.add(sig)
            count += 1
    return count


def _has_trending(cleaned: list[Any]) -> bool:
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        if str(item.get("tool") or "") == "fetch_trending":
            return True
        if str(item.get("kind") or "").strip().lower() == "trend":
            return True
    return False


def _tweet_items_from_cleaned(cleaned: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip().lower() != "tweet":
            continue
        if not str(item.get("tweet_id") or "").strip():
            continue
        out.append(item)
    return out


def _trend_cards_from_cleaned(cleaned: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip().lower() != "trend":
            continue
        trend = _as_dict(item.get("trend"))
        name = str(trend.get("name") or item.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "kind": "trend",
                "name": name,
                "description": trend.get("description"),
                "context": trend.get("context"),
            }
        )
    return out


def _normalize_material_card(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "").strip().lower()
    media_raw = item.get("media")
    media_links = sanitize_public_media_links(media_links_as_dicts(item.get("media_links")))
    if not media_links:
        media_links = sanitize_public_media_links(
            media_links_as_dicts(_media_links_from_raw(media_raw))
        )
    tweet_id = str(item.get("tweet_id") or "").strip()
    screen_name = str(item.get("screen_name") or "").lstrip("@").strip()
    link = str(item.get("link") or "").strip()
    if link and not is_public_page_url(link):
        link = ""
    title = str(item.get("title") or screen_name or tweet_id or "").strip()
    text = str(item.get("text") or "").strip()
    if tweet_id and not kind:
        kind = "tweet"
    return {
        **item,
        "kind": kind or "article",
        "title": title,
        "text": text,
        "link": link,
        "media_links": media_links,
        "media": media_raw if isinstance(media_raw, list) else [],
        "tweet_id": tweet_id,
        "screen_name": screen_name,
    }


def _material_card_from_tweet(tweet: dict[str, Any]) -> dict[str, Any]:
    screen = str(tweet.get("screen_name") or "").lstrip("@")
    tid = str(tweet.get("tweet_id") or "")
    link = ""
    if screen and tid:
        link = f"https://x.com/{screen}/status/{tid}"
    return _normalize_material_card(
        {
            "kind": "tweet",
            "title": screen or tid,
            "text": str(tweet.get("text") or ""),
            "link": link,
            "media": tweet.get("media"),
            "tweet_id": tid,
            "screen_name": screen,
            "likes": tweet.get("likes"),
            "retweets": tweet.get("retweets"),
            "replies": tweet.get("replies"),
        }
    )


def _tweet_cards_from_materials(material_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for item in material_list:
        tweet_id = str(item.get("tweet_id") or "").strip()
        if not tweet_id:
            continue
        cards.append(
            {
                "kind": "tweet",
                "tweet_id": tweet_id,
                "screen_name": str(item.get("screen_name") or ""),
                "text": str(item.get("text") or ""),
                "media": item.get("media") if isinstance(item.get("media"), list) else [],
                "likes": item.get("likes"),
                "retweets": item.get("retweets"),
                "replies": item.get("replies"),
            }
        )
    return cards


def _dedupe_materials(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("tweet_id") or item.get("link") or item.get("title") or "").strip()
        if not key:
            key = str(hash(json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


async def _run_one_tool(
    name: str, args: dict[str, Any], tools: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    try:
        if name not in tools:
            result: Any = {"error": f"未知工具: {name}"}
        else:
            result = await asyncio.to_thread(tools[name]["func"], **(args or {}))
    except Exception as exc:
        result = {"error": str(exc)}
    return {"tool": name, "args": args or {}, "result": result}


async def _host_fetch_trending() -> list[dict[str, Any]]:
    if not _tikhub_available():
        return [
            {
                "kind": "error",
                "tool": "fetch_trending",
                "args": {"country": "China"},
                "ok": False,
                "error": "tikhub_unavailable",
            }
        ]
    batch = [
        await _run_one_tool(
            "fetch_trending",
            {"country": "China"},
            compose_tools_list,
        )
    ]
    return materialize_tool_batch(batch, max_trends=10)


async def intel_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """门闩：无趋势且无 URL/handle → 空卡；否则 emit Reason。"""
    payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    intent = str(data.get_state("intent") or payload.get("intent") or "compose")
    source_kind = str(data.get_state("source_kind") or "none")
    source_anchor = str(data.get_state("source_anchor") or "").strip()
    user_instruction = str(
        data.get_state("user_instruction") or request.get("text") or ""
    )
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    need_trends = bool(request.get("need_trends") or data.get_state("need_trends"))
    task_id = str(request.get("task_id") or "")
    text = str(request.get("text") or user_instruction)

    await data.async_set_state("task_id", task_id, emit=False)
    await data.async_set_state("intent", intent, emit=False)
    await data.async_set_state("source_kind", source_kind, emit=False)
    await data.async_set_state("source_anchor", source_anchor, emit=False)
    await data.async_set_state("user_instruction", user_instruction, emit=False)
    await data.async_set_state("need_trends", need_trends, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("tool_result_cleaned", [], emit=False)

    live = _has_url_or_handle_candidate(
        source_kind=source_kind,
        source_anchor=source_anchor,
        text=text,
    )
    ctx = {
        "intent": intent,
        "source_kind": source_kind,
        "source_anchor": source_anchor,
        "user_instruction": user_instruction,
        "need_trends": need_trends,
        "task_id": task_id,
    }
    if not need_trends and not live:
        return await _finalize_intel(
            data,
            tool_result=[],
            answer="纯主题且未开趋势，跳过 TikHub；尝试采配图",
            limitations=limitations,
            task_id=task_id,
            step=0,
            need_trends=False,
            fallback_search_browse=True,
            user_instruction=user_instruction,
        )

    if not _tikhub_available():
        if "tikhub_unavailable" not in limitations:
            limitations.append("tikhub_unavailable")
        return await _finalize_intel(
            data,
            tool_result=[],
            answer="TikHub 不可用，改用 Search/Browse",
            limitations=limitations,
            task_id=task_id,
            step=0,
            need_trends=need_trends,
            fallback_search_browse=True,
            user_instruction=user_instruction,
        )

    data.emit_nowait(
        "Reason",
        {
            "question": user_instruction or text,
            "source_kind": source_kind,
            "source_anchor": source_anchor,
            "user_instruction": user_instruction,
            "need_trends": need_trends,
            "tool_result_cleaned": [],
            "step": 0,
            "task_id": task_id,
        },
    )
    return ctx


async def intel_reason(data: TriggerFlowRuntimeData) -> dict[str, Any] | None:
    """Thought：决定下一跳 tool 或 stop；Confirm 后 emit Act。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    step = int(state.get("step") or 0) + 1
    need_trends = bool(state.get("need_trends") or data.get_state("need_trends"))
    tools = _intel_allowlist(need_trends=need_trends)
    tools_schema = [
        {"name": k, "desc": v["desc"], "args": v["args"]} for k, v in tools.items()
    ]
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    tool_result_cleaned = list(
        cast(list[Any], data.get_state("tool_result_cleaned") or [])
    )
    http_used = _http_count(tool_result_cleaned)
    budget_left = MAX_HTTP - http_used
    thoughts_left = MAX_THOUGHTS - step
    new_state = {
        **state,
        "step": step,
        "tool_result_cleaned": tool_result_cleaned,
        "need_trends": need_trends,
    }

    if thoughts_left < 0 or budget_left <= 0:
        return await _finalize_intel(
            data,
            tool_result=tool_result_cleaned,
            answer="预算用尽，提交已有观察",
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
            need_trends=need_trends,
            fallback_search_browse=True,
            user_instruction=str(state.get("user_instruction") or state.get("question") or ""),
        )

    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-intel-react")
            .input(str(state.get("question") or state.get("user_instruction") or ""))
            .info(
                {
                    "job": "为创作采集对标/热搜短卡；不要写推文正文。",
                    "branch": "intel",
                    "need_trends": need_trends,
                    "source_anchor": state.get("source_anchor"),
                    "source_kind": state.get("source_kind"),
                    "user_instruction": state.get("user_instruction"),
                    "tools": tools_schema,
                    "已完成步骤": _summarize_cleaned_for_reason(tool_result_cleaned),
                    "budget": {
                        "step": step,
                        "max_thoughts": MAX_THOUGHTS,
                        "http_used": http_used,
                        "max_http": MAX_HTTP,
                        "budget_left": budget_left,
                    },
                }
            )
            .instruct(
                [
                    "你只做决策，不执行工具。",
                    "只输出一个 JSON：type=tool|final，reasoning，tool_calls，answer。",
                    "type=tool 时 tool_calls 最多 1 个（串行一跳一个 call）。",
                    "name 必须来自 tools；args 只填该工具声明字段。",
                    "fetch_search_timeline 的 keyword 必填；search_type 省略则 Latest。",
                    "fetch_user_profile / fetch_user_post_tweet：screen_name 与 rest_id 二选一。",
                    "仅当 need_trends=true 才可调用 fetch_trending；省略 country 则用 china。",
                    "有足够 tweet/trend 观察后 type=final，tool_calls=[]。",
                    "不要写推文正文。",
                ]
            )
            .output(
                {
                    "type": (str, "tool 或 final", "not_null"),
                    "reasoning": (str, "为何选该工具或为何停止", "not_null"),
                    "tool_calls": (
                        [
                            {
                                "name": (str, "工具名", "not_null"),
                                "args": (dict, "工具入参"),
                            }
                        ],
                        "type=tool 时最多 1 个；final 时 []",
                    ),
                    "answer": (str, "final 时填结论，否则空串"),
                },
                format="json",
            )
            .async_start(max_retries=1)
        )
    except Exception as exc:
        code = f"intel_reason_error:{type(exc).__name__}"
        if code not in limitations:
            limitations.append(code)
        return await _finalize_intel(
            data,
            tool_result=tool_result_cleaned,
            answer="",
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
            need_trends=need_trends,
            fallback_search_browse=True,
            user_instruction=str(state.get("user_instruction") or state.get("question") or ""),
        )

    if not isinstance(raw, dict):
        raw = {}
    decision = str(raw.get("type") or "final").strip().lower()
    if decision not in {"tool", "final"}:
        decision = "final"

    pending = _filter_new_tools(_parse_pending_tools(raw), tool_result_cleaned)
    # 一跳一个 call
    if pending:
        pending = pending[:1]

    if decision == "tool" and pending:
        name = str(pending[0].get("name") or "")
        args = _as_dict(pending[0].get("args"))
        confirmed, reject = confirm_intel_tool(
            name,
            args,
            need_trends=need_trends,
            allowlist=set(tools.keys()),
        )
        if confirmed is None:
            observe = {
                "kind": "error",
                "tool": name,
                "args": args,
                "ok": False,
                "error": reject,
            }
            tool_result_cleaned = tool_result_cleaned + [observe]
            await data.async_set_state(
                "tool_result_cleaned", tool_result_cleaned, emit=False
            )
            if reject not in limitations:
                limitations.append(reject)
            await data.async_set_state("limitations", limitations, emit=False)
            data.emit_nowait(
                "Reason",
                {**new_state, "tool_result_cleaned": tool_result_cleaned},
            )
            return None
        data.emit_nowait(
            "Act",
            {
                **new_state,
                "pending_tools": [{"name": name, "args": confirmed}],
            },
        )
        return None

    return await _finalize_intel(
        data,
        tool_result=tool_result_cleaned,
        answer=str(raw.get("answer") or "已提交观察"),
        limitations=limitations,
        task_id=str(state.get("task_id") or ""),
        step=step,
        need_trends=need_trends,
        fallback_search_browse=True,
        user_instruction=str(state.get("user_instruction") or state.get("question") or ""),
    )


async def intel_act(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    pending = state.get("pending_tools") or []
    need_trends = bool(state.get("need_trends") or data.get_state("need_trends"))
    tools = _intel_allowlist(need_trends=need_trends)
    results = []
    for tc in pending:
        if not isinstance(tc, dict):
            continue
        results.append(
            await _run_one_tool(
                str(tc.get("name") or ""),
                _as_dict(tc.get("args")),
                tools,
            )
        )
    return {**state, "tool_batch": results}


async def clean_intel_tool_result(data: TriggerFlowRuntimeData) -> None:
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    batch = [
        item
        for item in cast(list[Any], state.get("tool_batch") or [])
        if isinstance(item, dict)
    ]
    cleaned = materialize_tool_batch(batch) if batch else []
    prior = list(cast(list[Any], data.get_state("tool_result_cleaned") or []))
    tool_result_cleaned = prior + cleaned
    await data.async_set_state("tool_result_cleaned", tool_result_cleaned, emit=False)
    next_state = {
        k: v for k, v in state.items() if k not in {"tool_batch", "pending_tools"}
    }
    data.emit_nowait(
        "Reason", {**next_state, "tool_result_cleaned": tool_result_cleaned}
    )


async def _finalize_intel(
    data: TriggerFlowRuntimeData,
    *,
    tool_result: list[dict[str, Any]],
    answer: str,
    limitations: list[str],
    task_id: str,
    step: int,
    need_trends: bool,
    fallback_search_browse: bool = False,
    user_instruction: str = "",
) -> dict[str, Any]:
    cleaned = list(tool_result)
    if need_trends and not _has_trending(cleaned):
        extra = await _host_fetch_trending()
        cleaned.extend(extra)
        if not _has_trending(cleaned) and "trending_fetch_failed" not in limitations:
            limitations.append("trending_fetch_failed")

    tweets = _tweet_items_from_cleaned(cleaned)
    material_list = _dedupe_materials([_material_card_from_tweet(tw) for tw in tweets])

    goal = str(user_instruction or data.get_state("user_instruction") or "").strip()
    material_list = await _ensure_material_media(
        material_list,
        goal=goal,
        task_id=task_id,
        fallback_search_browse=fallback_search_browse,
        limitations=limitations,
    )

    tweet_cards = _tweet_cards_from_materials(material_list)
    trend_cards = _trend_cards_from_cleaned(cleaned)

    evidence_cards: list[dict[str, Any]] = []
    _normalize_compose_cards(
        {
            "material_list": material_list,
            "tweet_cards": tweet_cards,
            "trend_cards": trend_cards,
            "intel_result": answer,
            "plan_summary": answer,
            "material_plan": [],
            "tool_logs": cleaned,
        },
        evidence=evidence_cards,
        ref_index=0,
    )

    if not material_list and not trend_cards and "intel_empty" not in limitations:
        # 纯主题跳过外挂不算失败缺口；有门闩进入却空卡才记
        if need_trends or any(
            str(item.get("tool") or "") for item in cleaned if isinstance(item, dict)
        ):
            limitations.append("intel_empty")

    media_count = sum(len(item.get("media_links") or []) for item in material_list)
    intel_result = answer or (
        f"共采集 {len(material_list)} 条素材卡、{len(trend_cards)} 条趋势"
        if material_list or trend_cards
        else "未采集到可用素材卡"
    )

    await data.async_set_state("material_list", material_list, emit=False)
    await data.async_set_state("intel_result", intel_result, emit=False)
    await data.async_set_state("tool_logs", cleaned, emit=False)
    await data.async_set_state("tool_result_cleaned", cleaned, emit=False)
    await data.async_set_state("tweet_cards", tweet_cards, emit=False)
    await data.async_set_state("trend_cards", trend_cards, emit=False)
    await data.async_set_state("evidence_cards", evidence_cards, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("plan_summary", intel_result, emit=False)
    await data.async_set_state("material_plan", [], emit=False)

    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.intel",
        status="completed",
        subject_id=task_id,
        facts={
            "react_steps": step,
            "material_list": len(material_list),
            "trend_cards": len(trend_cards),
            "media_links": media_count,
            "need_trends": need_trends,
            "limitations": limitations,
        },
    )
    return {
        "tweet_cards": tweet_cards,
        "trend_cards": trend_cards,
        "material_list": material_list,
        "material_plan": [],
        "plan_summary": intel_result,
        "tool_logs": cleaned,
        "intel_result": intel_result,
        "evidence_cards": evidence_cards,
        "limitations": limitations,
    }


def build_intel_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-intel-v1")
    flow.to(intel_prelude)
    flow.when("Act").to(intel_act).to(clean_intel_tool_result)
    flow.when("Reason").to(intel_reason)
    return flow


INTEL_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "request": "runtime_data.request",
        "intent": "runtime_data.intent",
        "source_kind": "runtime_data.source_kind",
        "source_anchor": "runtime_data.source_anchor",
        "user_instruction": "runtime_data.user_instruction",
        "limitations": "runtime_data.limitations",
        "need_trends": "runtime_data.need_trends",
    },
    "resources": {
        "trace": "resources.trace",
        "snapshot": "resources.snapshot",
    },
}

INTEL_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "tweet_cards": "result.tweet_cards",
        "trend_cards": "result.trend_cards",
        "material_list": "result.material_list",
        "material_plan": "result.material_plan",
        "plan_summary": "result.plan_summary",
        "tool_logs": "result.tool_logs",
        "intel_result": "result.intel_result",
        "evidence_cards": "result.evidence_cards",
        "limitations": "result.limitations",
    },
}


__all__ = [
    "INTEL_SUBFLOW_CAPTURE",
    "INTEL_SUBFLOW_WRITE_BACK",
    "build_intel_subflow",
    "confirm_intel_tool",
    "intel_prelude",
    "intel_reason",
    "_normalize_material_card",
    "_tweet_cards_from_materials",
]
