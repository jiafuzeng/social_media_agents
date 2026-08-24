"""M3 Source 子图（改写）：prelude → Reason ↔ Act → materialize → Reason。

- source_reason：对照可用工具与任务目标，只决策 name+args
- source_act：执行 pending_tools，本批结果交给 clean
- clean_tool_result：materialize 确定性物化工具返回为素材卡，再参与 Reason

改写不看 need_trends。tweet_id / keyword 由 Reason 根据 source_anchor 与指令自行填参。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.compose.materialize import materialize_tool_batch
from integrated_agent.runtimes.matrix.compose.retrieval import (
    extract_search_query,
    pick_primary_tweet,
    prefer_search_over_handle,
)
from integrated_agent.runtimes.matrix.host.tikhubtools import source_tools_list
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog
from integrated_agent.runtimes.matrix.host.progress import emit_stage

MAX_STEPS = 3
# Reason：放大 session 窗口与输出额度，容纳累计 tool_result_cleaned
_REACT_SESSION_MAX_LENGTH = 64_000
_REACT_MAX_TOKENS = 8_192
_PASTED_TWEET_ID = "pasted-local"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
    candidates = raw.get("tool_calls")
    if candidates is None:
        candidates = raw.get("tools")
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        args = _as_dict(item.get("args"))
        if not name:
            continue
        pending.append({"name": name, "args": args})
    return pending


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


def _is_tweet_card(item: dict[str, Any]) -> bool:
    if item.get("ok") is False:
        return False
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "tweet":
        return bool(str(item.get("tweet_id") or "").strip())
    tid = str(item.get("tweet_id") or "").strip()
    text = str(item.get("text") or "").strip()
    return bool(tid and text)


def _tweet_cards_from_cleaned(items: list[Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if isinstance(item, dict) and _is_tweet_card(item)
    ]


def _tweet_status_url(screen_name: str, tweet_id: str) -> str:
    screen = str(screen_name or "").lstrip("@").strip()
    tid = str(tweet_id or "").strip()
    if screen and tid:
        return f"https://x.com/{screen}/status/{tid}"
    return ""


def _source_media_entries(media: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in media:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or raw.get("kind") or "photo").lower()
        entry: dict[str, Any] = {
            "kind": kind if kind in {"video", "gif"} else "photo",
            "preview_url": str(
                raw.get("thumb") or raw.get("preview_url") or ""
            ).strip(),
        }
        thumb = str(raw.get("thumb") or "").strip()
        if thumb:
            entry["thumb"] = thumb
        if raw.get("video_url"):
            entry["video_url"] = str(raw["video_url"])
        if raw.get("width") is not None:
            entry["width"] = raw["width"]
        if raw.get("height") is not None:
            entry["height"] = raw["height"]
        out.append(entry)
    return out


def _source_post_from_card(card: dict[str, Any]) -> dict[str, Any]:
    screen = str(card.get("screen_name") or "").lstrip("@")
    tid = str(card.get("tweet_id") or "")
    media_raw = card.get("media")
    media = media_raw if isinstance(media_raw, list) else []
    return {
        "tweet_id": tid,
        "text": str(card.get("text") or ""),
        "screen_name": screen,
        "url": _tweet_status_url(screen, tid),
        "media": media,
    }


def _related_card_from_tweet(card: dict[str, Any]) -> dict[str, Any]:
    screen = str(card.get("screen_name") or "").lstrip("@")
    tid = str(card.get("tweet_id") or "")
    media_raw = card.get("media")
    media = media_raw if isinstance(media_raw, list) else []
    return {
        "tweet_id": tid,
        "text": str(card.get("text") or ""),
        "screen_name": screen,
        "url": _tweet_status_url(screen, tid),
        "media": media,
    }


def _pasted_tweet_card(source_text: str) -> dict[str, Any]:
    return {
        "kind": "tweet",
        "tweet_id": _PASTED_TWEET_ID,
        "screen_name": "",
        "text": str(source_text or "").strip(),
        "media": [],
    }


def _materialize_source_package(
    tool_result_cleaned: list[Any],
    *,
    search_query: str = "",
) -> dict[str, Any]:
    tweets = _tweet_cards_from_cleaned(tool_result_cleaned)
    if not tweets:
        return {
            "source_post": None,
            "source_media": [],
            "related_tweet_cards": [],
            "tweet_cards": [],
        }
    primary, relevance_score = pick_primary_tweet(
        tweets,
        search_query=search_query,
    )
    if primary is None:
        primary = tweets[0]
        relevance_score = 0
    related = [item for item in tweets if item is not primary]
    media_raw = primary.get("media")
    media = media_raw if isinstance(media_raw, list) else []
    return {
        "source_post": _source_post_from_card(primary),
        "source_media": _source_media_entries(media),
        "related_tweet_cards": [_related_card_from_tweet(item) for item in related],
        "tweet_cards": tweets,
        "retrieval_relevance_score": relevance_score,
        "retrieval_query": str(search_query or "").strip(),
    }


def _host_fallback_tools(
    *,
    source_kind: str,
    source_anchor: str,
    search_query: str,
    user_instruction: str,
    request_text: str,
    tool_result_cleaned: list[Any] | None = None,
) -> list[dict[str, Any]]:
    tried_tools = {
        str(item.get("tool") or "").strip()
        for item in (tool_result_cleaned or [])
        if isinstance(item, dict) and str(item.get("tool") or "").strip()
    }
    anchor = str(source_anchor or "").strip().lstrip("@")
    kind = str(source_kind or "none").strip().lower()
    resolved_query = extract_search_query(
        search_query=search_query,
        user_instruction=user_instruction,
        request_text=request_text,
    )

    if anchor.isdigit() and "fetch_tweet_detail" not in tried_tools:
        return [{"name": "fetch_tweet_detail", "args": {"tweet_id": anchor}}]

    use_search = prefer_search_over_handle(
        source_kind=kind,
        source_anchor=anchor,
        search_query=resolved_query,
    )
    if use_search and resolved_query and "fetch_search_timeline" not in tried_tools:
        return [
            {
                "name": "fetch_search_timeline",
                "args": {"keyword": resolved_query, "search_type": "Latest"},
            }
        ]

    if kind == "handle" and anchor and "fetch_user_post_tweet" not in tried_tools:
        return [{"name": "fetch_user_post_tweet", "args": {"screen_name": anchor}}]

    if resolved_query and "fetch_search_timeline" not in tried_tools:
        return [
            {
                "name": "fetch_search_timeline",
                "args": {"keyword": resolved_query, "search_type": "Latest"},
            }
        ]
    return []


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


async def source_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收父图 capture；emit Reason 进入信号环。"""
    payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    intent = str(data.get_state("intent") or payload.get("intent") or "rewrite")
    source_kind = str(data.get_state("source_kind") or "none")
    source_anchor = str(data.get_state("source_anchor") or "").strip()
    user_instruction = str(
        data.get_state("user_instruction") or request.get("text") or ""
    )
    source_text = str(data.get_state("source_text") or "").strip()
    search_query = str(data.get_state("search_query") or "").strip()
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    task_id = str(request.get("task_id") or "")
    text = str(request.get("text") or "")

    resolved_query = extract_search_query(
        search_query=search_query,
        user_instruction=user_instruction,
        request_text=text,
    )

    await data.async_set_state("task_id", task_id, emit=False)
    await data.async_set_state("intent", intent, emit=False)
    await data.async_set_state("source_kind", source_kind, emit=False)
    await data.async_set_state("source_anchor", source_anchor, emit=False)
    await data.async_set_state("user_instruction", user_instruction, emit=False)
    await data.async_set_state("source_text", source_text, emit=False)
    await data.async_set_state("search_query", resolved_query, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("tool_result_cleaned", [], emit=False)
    await emit_stage(data, "source", started=True)

    if source_kind == "paste" and source_text:
        await _finalize_source(
            data,
            tool_result=[_pasted_tweet_card(source_text)],
            answer="已使用用户粘贴原文作为改写素材",
            limitations=limitations,
            task_id=task_id,
            step=0,
            search_query="",
        )
        return {
            "intent": intent,
            "source_kind": source_kind,
            "source_anchor": source_anchor,
            "user_instruction": user_instruction,
            "source_text": source_text,
            "search_query": resolved_query,
            "task_id": task_id,
        }

    data.emit_nowait(
        "Reason",
        {
            "question": user_instruction or text,
            "search_query": resolved_query,
            "source_kind": source_kind,
            "user_instruction": user_instruction,
            "tool_result_cleaned": [],
            "step": 0,
            "source_anchor": source_anchor,
            "task_id": task_id,
        },
    )
    return {
        "intent": intent,
        "source_kind": source_kind,
        "source_anchor": source_anchor,
        "user_instruction": user_instruction,
        "source_text": source_text,
        "search_query": resolved_query,
        "task_id": task_id,
    }


async def source_reason(data: TriggerFlowRuntimeData) -> None:
    """根据任务目标与可用工具，决定调用哪些函数并填写入参；不执行。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    step = int(state.get("step") or 0) + 1
    budget_left = MAX_STEPS - step
    tools = source_tools_list
    tools_schema = [
        {"name": k, "desc": v["desc"], "args": v["args"]} for k, v in tools.items()
    ]
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    tool_result_cleaned = list(cast(list[Any], data.get_state("tool_result_cleaned") or []))
    tweet_cards = _tweet_cards_from_cleaned(tool_result_cleaned)
    has_tweet_cards = bool(tweet_cards)
    new_state = {
        **state,
        "step": step,
        "tool_result_cleaned": tool_result_cleaned,
    }
    source_anchor = str(state.get("source_anchor") or "")
    source_kind = str(
        state.get("source_kind") or data.get_state("source_kind") or "none"
    )
    question = str(state.get("question") or "")
    search_query = str(
        state.get("search_query") or data.get_state("search_query") or ""
    ).strip()
    user_instruction = str(
        state.get("user_instruction") or data.get_state("user_instruction") or ""
    )
    request = cast(dict[str, Any], data.get_state("request") or {})
    request_text = str(request.get("text") or "")
    resolved_query = extract_search_query(
        search_query=search_query,
        user_instruction=user_instruction,
        request_text=request_text,
    )

    if has_tweet_cards:
        await _finalize_source(
            data,
            tool_result=tool_result_cleaned,
            answer="已拿到推文素材卡",
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
            search_query=resolved_query,
        )
        return

    if budget_left <= 0:
        if "source_no_tweet_cards" not in limitations:
            limitations.append("source_no_tweet_cards")
        await _finalize_source(
            data,
            tool_result=tool_result_cleaned,
            answer="已达到最大步骤数，仍未拿到推文素材卡",
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
            search_query=resolved_query,
        )
        return

    host_tools = _filter_new_tools(
        _host_fallback_tools(
            source_kind=source_kind,
            source_anchor=source_anchor,
            search_query=resolved_query,
            user_instruction=user_instruction,
            request_text=request_text,
            tool_result_cleaned=tool_result_cleaned,
        ),
        tool_result_cleaned,
    )
    if host_tools:
        data.emit_nowait("Act", {**new_state, "pending_tools": host_tools})
        return

    if budget_left <= 1:
        extra = [
            f"步骤预算只剩 {budget_left} 步；"
            "仅当「已完成步骤」里已有推文素材卡（kind=tweet）时才能 type=final，"
            "否则继续用 tool_calls 拉取推文。"
        ]
    else:
        extra = [
            "检索关键字必须用 info.search_query（实体/主题），不要把 user_instruction 里的改口吻、创作推文等任务说明当 keyword。",
            "有 tweet_id 则优先 fetch_tweet_detail；source_kind=handle 时用 fetch_user_post_tweet；否则 fetch_search_timeline(keyword=search_query)。",
            "禁止 fetch_user_media / fetch_trending。",
            "必须至少拿到一条推文素材卡（kind=tweet，含 tweet_id 与正文）后才能 type=final；"
            "用户粘贴文字不能代替工具拉取的推文卡（source_kind=paste 已在 prelude 处理）。",
        ]

    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-source-react")
            .input(question)
            .info(
                {
                    "job": "为改写组装原文包；必须拿到推文素材卡后才能 final。不要写改写正文。",
                    "branch": "source",
                    "source_anchor": source_anchor,
                    "source_kind": source_kind,
                    "user_instruction": user_instruction,
                    "search_query": resolved_query,
                    "tools": tools_schema,
                    "已完成步骤": _summarize_cleaned_for_reason(tool_result_cleaned),
                    "budget": {
                        "step": step,
                        "max_steps": MAX_STEPS,
                        "budget_left": budget_left,
                    },
                }
            )
            .instruct(
                [
                    "你只做决策，不执行工具。",
                    "对照「可用工具」与「已完成步骤」，判断还缺什么原文材料。",
                    "只输出一个 JSON 对象，不要 markdown 代码块，不要额外说明。",
                    "若需采集:type=tool，在 tool_calls 中列出 [{name, args}]（必须是数组）。",
                    "name 必须来自 tools; args 只填该工具声明的字段。",
                    "仅当「已完成步骤」中已有推文素材卡时:type=final，填写 answer，tool_calls 必须为 []。",
                    *extra,
                ]
            )
            .output(
                {
                    "type": (str, "tool 或 final", "not_null"),
                    "reasoning": (str, "推理：目标还缺什么、为何选这些工具", "not_null"),
                    "tool_calls": (
                        [
                            {
                                "name": (str, "工具名", "not_null"),
                                "args": (dict, "工具入参"),
                            }
                        ],
                        "type=tool 时填写；type=final 时必须为 []",
                    ),
                    "answer": (str, "final 时填最终结论，否则空串"),
                },
                format="json",
            )
            .async_start(max_retries=1)
        )
    except Exception as exc:
        code = f"source_reason_error:{type(exc).__name__}"
        if code not in limitations:
            limitations.append(code)
        if "source_no_tweet_cards" not in limitations:
            limitations.append("source_no_tweet_cards")
        await _finalize_source(
            data,
            tool_result=tool_result_cleaned,
            answer="",
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
            search_query=resolved_query,
        )
        return

    if not isinstance(raw, dict):
        raw = {}
    decision = str(raw.get("type") or "final").strip().lower()
    if decision not in {"tool", "final"}:
        decision = "final"

    pending_tools = _filter_new_tools(_parse_pending_tools(raw), tool_result_cleaned)
    answer = str(raw.get("answer") or "")

    if decision == "tool" and pending_tools:
        data.emit_nowait("Act", {**new_state, "pending_tools": pending_tools})
        return

    if "source_no_tweet_cards" not in limitations:
        limitations.append("source_no_tweet_cards")
    await _finalize_source(
        data,
        tool_result=tool_result_cleaned,
        answer=answer or "无法继续拉取推文素材卡",
        limitations=limitations,
        task_id=str(state.get("task_id") or ""),
        step=step,
        search_query=resolved_query,
    )


async def source_act(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """执行 pending_tools；本批原始结果交给 clean。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    pending = state.get("pending_tools") or []
    tools = source_tools_list
    results = await asyncio.gather(
        *[
            _run_one_tool(
                str(tc.get("name") or ""),
                cast(dict[str, Any], tc.get("args") or {}),
                tools,
            )
            for tc in pending
            if isinstance(tc, dict)
        ]
    )
    return {**state, "tool_batch": list(results)}


async def clean_tool_result(data: TriggerFlowRuntimeData) -> None:
    """用 materialize 确定性物化 tool_batch → 素材卡；追加进 tool_result_cleaned 后 emit Reason。"""
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


async def _finalize_source(
    data: TriggerFlowRuntimeData,
    *,
    tool_result: list[dict[str, Any]],
    answer: str,
    limitations: list[str],
    task_id: str,
    step: int,
    search_query: str = "",
) -> None:
    cleaned = list(tool_result)
    resolved_query = str(
        search_query or data.get_state("search_query") or ""
    ).strip()
    package = _materialize_source_package(cleaned, search_query=resolved_query)
    tweet_cards = package["tweet_cards"]
    if not tweet_cards and "source_no_tweet_cards" not in limitations:
        limitations.append("source_no_tweet_cards")
    relevance = int(package.get("retrieval_relevance_score") or 0)
    if resolved_query and tweet_cards and relevance <= 0:
        if "source_low_relevance" not in limitations:
            limitations.append("source_low_relevance")

    used = {
        str(item.get("tool") or "").strip()
        for item in cleaned
        if isinstance(item, dict) and str(item.get("tool") or "").strip()
    }
    source_post = package["source_post"]
    await data.async_set_state("tool_logs", cleaned, emit=False)
    await data.async_set_state("tool_result_cleaned", cleaned, emit=False)
    await data.async_set_state("source_result", answer, emit=False)
    await data.async_set_state("source_post", source_post, emit=False)
    await data.async_set_state("source_media", package["source_media"], emit=False)
    await data.async_set_state("author_card", None, emit=False)
    await data.async_set_state(
        "related_tweet_cards", package["related_tweet_cards"], emit=False
    )
    await data.async_set_state("limitations", limitations, emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.source",
        status="completed" if tweet_cards else "failed",
        subject_id=task_id,
        facts={
            "tweet_cards": len(tweet_cards),
            "tool_calls": len(cleaned),
            "tool_names": sorted(used),
            "react_steps": step,
            "limitations": limitations,
            "search_query": resolved_query,
            "selected_tweet_id": (
                str(source_post.get("tweet_id") or "") if isinstance(source_post, dict) else ""
            ),
            "selected_screen_name": (
                str(source_post.get("screen_name") or "")
                if isinstance(source_post, dict)
                else ""
            ),
            "retrieval_relevance_score": relevance,
        },
    )
    await emit_stage(
        data,
        "source",
        started=False,
        tweet_count=len(tweet_cards),
    )


def build_source_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-source-v1")
    flow.to(source_prelude)
    flow.when("Act").to(source_act).to(clean_tool_result)
    flow.when("Reason").to(source_reason)
    return flow


SOURCE_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "request": "runtime_data.request",
        "intent": "runtime_data.intent",
        "source_kind": "runtime_data.source_kind",
        "source_anchor": "runtime_data.source_anchor",
        "user_instruction": "runtime_data.user_instruction",
        "source_text": "runtime_data.source_text",
        "search_query": "runtime_data.search_query",
        "limitations": "runtime_data.limitations",
    },
    "resources": {
        "trace": "resources.trace",
        "snapshot": "resources.snapshot",
        "events": "resources.events",
    },
}

SOURCE_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "tool_logs": "result.tool_logs",
        "tool_result_cleaned": "result.tool_result_cleaned",
        "source_result": "result.source_result",
        "source_post": "result.source_post",
        "source_media": "result.source_media",
        "author_card": "result.author_card",
        "related_tweet_cards": "result.related_tweet_cards",
        "limitations": "result.limitations",
    },
}


__all__ = [
    "SOURCE_SUBFLOW_CAPTURE",
    "SOURCE_SUBFLOW_WRITE_BACK",
    "build_source_subflow",
    "clean_tool_result",
    "source_act",
    "source_prelude",
    "source_reason",
]
