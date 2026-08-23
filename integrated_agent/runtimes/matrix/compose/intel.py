"""M3 Intel 子图：prelude → plan_material → for_each(intel_reason) → merge_material。"""

from __future__ import annotations

import asyncio
import os
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
from integrated_agent.runtimes.matrix.compose.material import _resolve_post_count
from integrated_agent.runtimes.matrix.compose.web_media import (
    fetch_public_media_from_page,
    is_public_page_url,
    sanitize_public_media_links,
)
from integrated_agent.runtimes.matrix.host.models import media_links_as_dicts
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot
from integrated_agent.runtimes.matrix.host.tikhubtools import source_tools_list
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_PLAN_TASKS = 10
MAX_ACTION_ROUNDS = 2


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _intel_context(data: TriggerFlowRuntimeData, state: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = state or cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    user_instruction = str(
        payload.get("user_instruction")
        or data.get_state("user_instruction")
        or request.get("text")
        or ""
    ).strip()
    return {
        "user_instruction": user_instruction,
        "intent": str(payload.get("intent") or data.get_state("intent") or "compose"),
        "source_kind": str(payload.get("source_kind") or data.get_state("source_kind") or "none"),
        "source_anchor": str(payload.get("source_anchor") or data.get_state("source_anchor") or "").strip(),
        "need_trends": bool(payload.get("need_trends") or data.get_state("need_trends")),
        "task_id": str(payload.get("task_id") or data.get_state("task_id") or ""),
    }


def _tasks_from_plan(raw_tasks: Any, *, max_tasks: int) -> list[dict[str, Any]]:
    cap = max(1, min(int(max_tasks), MAX_PLAN_TASKS))
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw_tasks if isinstance(raw_tasks, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal") or "").strip()
        if not goal:
            continue
        tasks.append(
            {
                "task_id": str(item.get("task_id") or f"m{index}"),
                "goal": goal,
            }
        )
        if len(tasks) >= cap:
            break
    return tasks


def _pad_plan_tasks(
    tasks: list[dict[str, Any]],
    *,
    post_count: int,
    user_instruction: str,
) -> list[dict[str, Any]]:
    cap = max(1, min(int(post_count), MAX_PLAN_TASKS))
    if len(tasks) >= cap:
        return tasks[:cap]
    base = user_instruction.strip() or "创作素材"
    out = list(tasks)
    for index in range(len(out) + 1, cap + 1):
        suffix = f"（角度 {index}）" if cap > 1 else ""
        out.append({"task_id": f"m{index}", "goal": f"{base}{suffix}"})
    return out


def _tikhub_available() -> bool:
    return bool((os.environ.get("TIKHUB_API_KEY") or "").strip())


def _search_keywords(goal: str) -> list[str]:
    """从子任务 goal 提取若干搜索关键词，提高 TikHub 命中率。"""
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


async def _resolve_public_material_media(card: dict[str, Any], *, goal: str) -> dict[str, Any]:
    """把 LLM 占位链接替换为搜索+抓页得到的公网可访问配图。"""
    item = _normalize_material_card(card)
    if item.get("media_links"):
        return item

    page_url = str(item.get("link") or "").strip()
    if is_public_page_url(page_url):
        media = await fetch_public_media_from_page(page_url)
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
        media = await fetch_public_media_from_page(candidate)
        item["link"] = candidate
        title = str(row.get("title") or "").strip()
        if title and not str(item.get("title") or "").strip():
            item["title"] = title
        if media:
            item["media_links"] = media[:1]
            return item

    item["media_links"] = []
    return item


def _material_card_from_tweet(
    tweet: dict[str, Any],
    *,
    goal: str,
    task_id: str,
) -> dict[str, Any]:
    return _normalize_material_card(
        {
            "kind": "tweet",
            "title": str(tweet.get("screen_name") or tweet.get("tweet_id") or goal),
            "text": str(tweet.get("text") or ""),
            "link": "",
            "media": tweet.get("media"),
            "tweet_id": str(tweet.get("tweet_id") or ""),
            "screen_name": str(tweet.get("screen_name") or ""),
            "source_task_id": task_id,
            "source_goal": goal,
        }
    )


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


def _pick_tweet_for_goal(tweets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tweets:
        return None
    with_media = [tw for tw in tweets if _media_links_from_raw(tw.get("media"))]
    return (with_media or tweets)[0]


async def _host_fetch_tweet_material(
    *,
    goal: str,
    task_id: str,
) -> dict[str, Any] | None:
    """优先用 TikHub 搜索采集带 media 的推文素材卡。"""
    if not _tikhub_available():
        return None
    tool_name = "fetch_search_timeline"
    tool = source_tools_list.get(tool_name)
    if tool is None:
        return None
    for keyword in _search_keywords(goal):
        for search_type in ("Latest", "Media"):
            args = {"keyword": keyword, "search_type": search_type}
            try:
                result = await asyncio.to_thread(tool["func"], **args)
                if isinstance(result, dict) and (
                    result.get("error") or result.get("ok") is False
                ):
                    continue
                batch = [{"tool": tool_name, "args": args, "result": result}]
                cleaned = materialize_tool_batch(batch, max_tweets=8)
                tweet = _pick_tweet_for_goal(_tweet_items_from_cleaned(cleaned))
                if tweet is None:
                    continue
                return _material_card_from_tweet(tweet, goal=goal, task_id=task_id)
            except Exception:
                continue
    return None


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


def _dedupe_materials(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(
            item.get("tweet_id")
            or item.get("link")
            or item.get("title")
            or item.get("text")
            or ""
        ).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


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


async def intel_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收父图 capture，初始化本单 state。"""
    payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    intent = str(data.get_state("intent") or payload.get("intent") or "compose")
    source_kind = str(data.get_state("source_kind") or "none")
    source_anchor = str(data.get_state("source_anchor") or "").strip()
    user_instruction = str(
        data.get_state("user_instruction") or request.get("text") or ""
    )
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    need_trends = bool(request.get("need_trends"))
    task_id = str(request.get("task_id") or "")
    post_count = _resolve_post_count(request, snapshot)
    if not _tikhub_available() and "tikhub_unavailable" not in limitations:
        limitations.append("tikhub_unavailable")

    await data.async_set_state("task_id", task_id, emit=False)
    await data.async_set_state("intent", intent, emit=False)
    await data.async_set_state("source_kind", source_kind, emit=False)
    await data.async_set_state("source_anchor", source_anchor, emit=False)
    await data.async_set_state("user_instruction", user_instruction, emit=False)
    await data.async_set_state("need_trends", need_trends, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("post_count", post_count, emit=False)

    return {
        "intent": intent,
        "source_kind": source_kind,
        "source_anchor": source_anchor,
        "user_instruction": user_instruction,
        "need_trends": need_trends,
        "task_id": task_id,
        "post_count": post_count,
    }


async def plan_material(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """按 post_count 拆解为若干可并行执行的推文素材采集子任务。"""
    ctx = _intel_context(data)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    post_count = int(data.get_state("post_count") or _resolve_post_count(request, snapshot))
    user_instruction = ctx["user_instruction"]
    plan_summary = ""
    tasks: list[dict[str, Any]] = []
    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-intel-plan")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input(user_instruction)
            .info({**ctx, "post_count": post_count})
            .instruct(
                [
                    f"用户需要创作 {post_count} 条推文，请拆解恰好 {post_count} 个素材采集子任务。",
                    "每个子任务对应一条推文的参考素材，goal 写清楚要检索的主题或关键词。",
                    "优先规划可检索到 X/Twitter 推文与配图/视频的角度，不要写推文正文。",
                    "不要写推文正文，只输出任务计划。",
                ]
            )
            .output(
                {
                    "plan_summary": (str, "整体拆解说明", "not_null"),
                    "tasks": (
                        [
                            {
                                "task_id": (str, "子任务 id，如 m1", "not_null"),
                                "goal": (str, "该子任务目标", "not_null"),
                            }
                        ],
                        "not_null",
                    ),
                },
                format="json",
            )
            .async_start()
        )
        if isinstance(raw, dict):
            plan_summary = str(raw.get("plan_summary") or "")
            tasks = _tasks_from_plan(raw.get("tasks"), max_tasks=post_count)
        if not tasks:
            tasks = _pad_plan_tasks([], post_count=post_count, user_instruction=user_instruction)
        else:
            tasks = _pad_plan_tasks(tasks, post_count=post_count, user_instruction=user_instruction)
    except Exception as exc:
        limitations = list(cast(list[str], data.get_state("limitations") or []))
        code = f"intel_plan_error:{type(exc).__name__}"
        if code not in limitations:
            limitations.append(code)
        await data.async_set_state("limitations", limitations, emit=False)
        tasks = _pad_plan_tasks([], post_count=post_count, user_instruction=user_instruction)

    await data.async_set_state("material_plan", tasks, emit=False)
    await data.async_set_state("plan_summary", plan_summary, emit=False)
    return tasks


async def intel_reason(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """执行单个子任务：优先 TikHub 推文采集，失败则 Search/Browse。"""
    task = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    ctx = _intel_context(data)
    goal = str(task.get("goal") or "").strip()
    task_id = str(task.get("task_id") or "")

    host_card = await _host_fetch_tweet_material(goal=goal, task_id=task_id)
    if host_card is not None:
        return {
            "task_id": task_id,
            "goal": goal,
            "ok": True,
            "source": "tikhub",
            "answer": (
                f"已采集推文素材"
                f"（media={len(host_card.get('media_links') or [])}）"
            ),
            "material_list": [host_card],
        }

    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-intel-task")
            .input({"goal": goal})
            .info({**ctx, "task": task})
            .use_actions(_search_browse_actions())
            .set_action_loop(max_rounds=MAX_ACTION_ROUNDS)
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
            .async_start()
        )
    except Exception as exc:
        return {
            "task_id": task_id,
            "goal": goal,
            "ok": False,
            "error": str(exc),
            "answer": "",
            "material_list": [],
        }

    if not isinstance(raw, dict):
        raw = {}
    material_list = raw.get("material_list") or []
    if not isinstance(material_list, list):
        material_list = []
    cleaned = [_normalize_material_card(item) for item in material_list if isinstance(item, dict)]
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
    cleaned = resolved
    return {
        "task_id": task_id,
        "goal": goal,
        "ok": True,
        "source": "search_browse",
        "answer": str(raw.get("answer") or ""),
        "material_list": cleaned,
    }


async def merge_material(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """汇总 for_each 子任务结果，写回父图所需 state。"""
    ctx = _intel_context(data)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    post_count = int(data.get_state("post_count") or _resolve_post_count(request, snapshot))
    task_results = [
        item for item in cast(list[Any], data.input or []) if isinstance(item, dict)
    ]
    merged: list[dict[str, Any]] = []
    limitations = list(cast(list[str], data.get_state("limitations") or []))

    for result in task_results:
        if not result.get("ok", True):
            code = f"intel_task_failed:{result.get('task_id') or 'unknown'}"
            if code not in limitations:
                limitations.append(code)
        merged.extend(
            _normalize_material_card(item)
            for item in cast(list[Any], result.get("material_list") or [])
            if isinstance(item, dict)
        )

    material_list = _dedupe_materials(merged)
    if len(material_list) > post_count:
        material_list = material_list[:post_count]

    tweet_cards = _tweet_cards_from_materials(material_list)
    trend_cards: list[dict[str, Any]] = []
    evidence_cards: list[dict[str, Any]] = []
    _normalize_compose_cards(
        {
            "material_list": material_list,
            "tweet_cards": [],
            "trend_cards": trend_cards,
            "intel_result": str(data.get_state("plan_summary") or ""),
            "plan_summary": str(data.get_state("plan_summary") or ""),
            "material_plan": _as_list(data.get_state("material_plan")),
            "tool_logs": task_results,
        },
        evidence=evidence_cards,
        ref_index=0,
    )

    plan_summary = str(data.get_state("plan_summary") or "").strip()
    material_plan = [
        item
        for item in _as_list(data.get_state("material_plan"))
        if isinstance(item, dict)
    ]
    if not material_list and not any(
        str(item.get("error") or "") for item in task_results if isinstance(item, dict)
    ):
        limitations.append("intel_empty")

    media_count = sum(len(item.get("media_links") or []) for item in material_list)
    intel_result = plan_summary or (
        f"共采集 {len(material_list)} 条素材卡（含 {media_count} 个媒体链接）"
        if material_list
        else "未采集到可用素材卡"
    )

    await data.async_set_state("material_list", material_list, emit=False)
    await data.async_set_state("intel_result", intel_result, emit=False)
    await data.async_set_state("tool_logs", task_results, emit=False)
    await data.async_set_state("tweet_cards", tweet_cards, emit=False)
    await data.async_set_state("trend_cards", trend_cards, emit=False)
    await data.async_set_state("evidence_cards", evidence_cards, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.intel",
        status="completed",
        subject_id=ctx["task_id"],
        facts={
            "plan_tasks": len(task_results),
            "post_count": post_count,
            "material_list": len(material_list),
            "media_links": media_count,
            "limitations": limitations,
        },
    )
    return {
        "material_list": material_list,
        "intel_result": intel_result,
        "plan_summary": plan_summary,
        "material_plan": material_plan,
        "tool_logs": task_results,
        "tweet_cards": tweet_cards,
        "trend_cards": trend_cards,
        "evidence_cards": evidence_cards,
        "limitations": limitations,
    }


def build_intel_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-intel-v1")
    (
        flow.to(intel_prelude)
        .to(plan_material)
        .for_each(concurrency=5)
        .to(intel_reason)
        .end_for_each()
        .to(merge_material)
    )
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
    },
    "resources": {
        "trace": "resources.trace",
        "session_id": "resources.session_id",
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
    "intel_prelude",
    "intel_reason",
    "merge_material",
    "plan_material",
]
