"""独立召回聊天阶段：改写问题 → 拆检索问句 → 并行 RetrieveKb → 汇总回答。

不依赖写帖 / 回评 Flow、ConstraintGate 或 MatrixTask 契约。
"""

from __future__ import annotations

import re
from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.rag.models import (
    ChatKbHit,
    ChatKbIn,
    ChatKbOut,
    ChatKbTurn,
    KbChunkOut,
    SearchKbIn,
)
from integrated_agent.runtimes.matrix.rag.knowledge import KnowledgeStore

KB_CITE_RE = re.compile(r"\[\[kb:([^\]]+)\]\]")
REF_CITE_RE = re.compile(r"\[\[ref:([^\]]+)\]\]")
EMPTY_ANSWER = "当前模型下没有检索到可用手册段落，无法根据知识库作答。"
FAILED_ANSWER = "生成回答失败，下方仍列出本次召回的手册段落。"
MAX_RETRIEVAL_QUERIES = 4
MAX_MERGED_HITS = 8
MAX_CONTEXT_CHARS = 1200
MAX_GROW_SPAN = 3
MAX_ANALYSIS_POINTS = 12


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        payload = dump()
        if isinstance(payload, dict):
            return payload
    return {"answer": str(result or "")}


def _clip_history(turns: list[ChatKbTurn]) -> list[dict[str, str]]:
    clipped: list[dict[str, str]] = []
    for turn in turns[-8:]:
        text = turn.text.strip()[:800]
        if text:
            clipped.append({"role": turn.role, "text": text})
    return clipped


async def _note(data: TriggerFlowRuntimeData, code: str) -> None:
    notes = list(cast(list[str], data.get_state("limitations") or []))
    if code not in notes:
        notes.append(code)
        await data.async_set_state("limitations", notes, emit=False)


def _sanitize_answer(answer: str, offered: list[str]) -> tuple[str, list[str], list[str]]:
    text = (answer or "").strip()
    limitations: list[str] = []
    if REF_CITE_RE.search(text):
        limitations.append("mixed_ref")
        text = REF_CITE_RE.sub("", text).strip()
    offered_set = set(offered)
    cited_in_text = KB_CITE_RE.findall(text)
    if any(token not in offered_set for token in cited_in_text):
        limitations.append("unknown_kb")
    cited = [token for token in cited_in_text if token in offered_set]
    return text, cited, limitations


def _normalize_queries(raw: Any, fallback: str) -> list[str]:
    values = raw if isinstance(raw, list) else [raw]
    queries: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in queries:
            queries.append(text)
        if len(queries) >= MAX_RETRIEVAL_QUERIES:
            break
    return queries or [fallback]


def _merge_hits(raw_hits: list[dict[str, Any]]) -> list[ChatKbHit]:
    best: dict[str, dict[str, Any]] = {}
    for item in raw_hits:
        chunk_id = str(item.get("chunk_id") or "")
        if not chunk_id:
            continue
        current = best.get(chunk_id)
        if current is None or (item.get("score") or 0) > (current.get("score") or 0):
            best[chunk_id] = item
    ranked = sorted(
        best.values(),
        key=lambda row: float(row.get("score") or 0),
        reverse=True,
    )[:MAX_MERGED_HITS]
    cards: list[ChatKbHit] = []
    for index, row in enumerate(ranked, start=1):
        payload = dict(row)
        payload.pop("kb_id", None)
        payload["kb_id"] = f"k{index}"
        cards.append(ChatKbHit.model_validate(payload))
    return cards


def _join_chunk_texts(chunks: list[KbChunkOut]) -> str:
    parts = [item.text.strip() for item in chunks if (item.text or "").strip()]
    return "\n\n".join(parts)


def _grow_around(
    chunks: list[KbChunkOut], index: int, *, max_chars: int, max_span: int = MAX_GROW_SPAN
) -> list[KbChunkOut]:
    lo = hi = index
    while (hi - lo + 1) < max_span:
        grew = False
        if (
            lo > 0
            and (hi - (lo - 1) + 1) <= max_span
            and len(_join_chunk_texts(chunks[lo - 1 : hi + 1])) <= max_chars
        ):
            lo -= 1
            grew = True
        if (
            hi + 1 < len(chunks)
            and ((hi + 1) - lo + 1) <= max_span
            and len(_join_chunk_texts(chunks[lo : hi + 2])) <= max_chars
        ):
            hi += 1
            grew = True
        if not grew:
            break
    return chunks[lo : hi + 1]


def _select_span(
    hit: ChatKbHit, siblings: list[KbChunkOut]
) -> tuple[list[KbChunkOut], str | None, str]:
    """选出可独立引用的相邻块。window 仍属同一 chunk，不升成新卡。"""

    original = (hit.text or "").strip()
    live = sorted(
        [item for item in siblings if item.enabled],
        key=lambda item: (item.ordinal, item.chunk_id),
    )
    current = next((item for item in live if item.chunk_id == hit.chunk_id), None)
    if current is None:
        window = (hit.window or "").strip()
        if window and len(window) > len(original):
            return [], window[:MAX_CONTEXT_CHARS], original
        return [], None, original
    original = (current.text or "").strip() or original
    window = (current.window or hit.window or "").strip()
    if window and len(window) > len(original):
        return [current], window[:MAX_CONTEXT_CHARS], original
    if current.header_path:
        section = [
            item for item in live if item.header_path == current.header_path
        ]
        if len(section) > 1:
            index = next(
                i for i, item in enumerate(section) if item.chunk_id == current.chunk_id
            )
            return (
                _grow_around(section, index, max_chars=MAX_CONTEXT_CHARS),
                None,
                original,
            )
    index = next(i for i, item in enumerate(live) if item.chunk_id == current.chunk_id)
    return (
        _grow_around(live, index, max_chars=MAX_CONTEXT_CHARS),
        None,
        original,
    )


def _complete_passage(
    hit: ChatKbHit, siblings: list[KbChunkOut]
) -> tuple[str, str]:
    """补全检索块：window → 同标题节 → 相邻 ordinal。返回 (上下文, 原始命中句)。"""

    span, window, original = _select_span(hit, siblings)
    if window:
        return window, original
    if span:
        return _join_chunk_texts(span)[:MAX_CONTEXT_CHARS], original
    return original, original


def _dedupe_expanded(cards: list[ChatKbHit]) -> list[ChatKbHit]:
    """保留不同命中块的 kb_id；只丢掉空正文。"""

    kept: list[ChatKbHit] = []
    seen: set[str] = set()
    for card in cards:
        chunk_id = (card.chunk_id or "").strip()
        if not chunk_id or chunk_id in seen:
            continue
        if not (card.text or "").strip():
            continue
        seen.add(chunk_id)
        kept.append(card)
    return [
        card.model_copy(update={"kb_id": f"k{index}"})
        for index, card in enumerate(kept, start=1)
    ]


def _card_from_chunk(
    seed: ChatKbHit, chunk: KbChunkOut, *, context: str | None
) -> ChatKbHit:
    payload = seed.model_dump()
    payload.update(
        {
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "text": (chunk.text or "").strip(),
            "window": chunk.window,
            "header_path": chunk.header_path,
            "embedding_profile_id": chunk.embedding_profile_id
            or seed.embedding_profile_id,
            "hit_text": None,
            "context": context if chunk.chunk_id == seed.chunk_id else None,
        }
    )
    return ChatKbHit.model_validate(payload)


async def _expand_cards(
    knowledge: KnowledgeStore,
    user_id: str,
    cards: list[ChatKbHit],
) -> tuple[list[ChatKbHit], bool]:
    """命中块的相邻段升成独立 kb_id，避免整节事实都挂在 k1 上。"""

    by_doc: dict[str, list[KbChunkOut]] = {}
    expanded: list[ChatKbHit] = []
    failed = False
    for card in cards:
        siblings = by_doc.get(card.doc_id)
        if siblings is None:
            try:
                listed = await knowledge.list_chunks(user_id, card.doc_id)
                siblings = list(listed.chunks)
            except Exception:
                siblings = []
                failed = True
            by_doc[card.doc_id] = siblings
        span, window, original = _select_span(card, siblings)
        if span:
            for chunk in span:
                if not (chunk.text or "").strip():
                    continue
                expanded.append(
                    _card_from_chunk(
                        card,
                        chunk,
                        context=window if chunk.chunk_id == card.chunk_id else None,
                    )
                )
            continue
        needle = original or card.text
        payload = card.model_dump()
        payload["text"] = needle
        payload["hit_text"] = None
        payload["context"] = window if window and window != needle else None
        expanded.append(ChatKbHit.model_validate(payload))
    return _dedupe_expanded(expanded), failed


def _offered_for_summarize(card: ChatKbHit) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kb_id": card.kb_id,
        "text": card.text,
        "header_path": card.header_path,
    }
    if card.context:
        row["context"] = card.context
    return row


def _fallback_points(cards: list[ChatKbHit]) -> list[dict[str, str]]:
    points: list[dict[str, str]] = []
    for card in cards:
        claim = (card.text or "").strip()
        if not claim:
            continue
        points.append(
            {
                "point_id": f"p{len(points) + 1}",
                "claim": claim[:400],
                "kb_id": card.kb_id,
            }
        )
        if len(points) >= MAX_ANALYSIS_POINTS:
            break
    return points


def _sanitize_points(
    raw: Any, offered: list[str]
) -> tuple[list[dict[str, str]], list[str]]:
    offered_set = set(offered)
    points: list[dict[str, str]] = []
    extra: list[str] = []
    seen: set[tuple[str, str]] = set()
    values = raw if isinstance(raw, list) else []
    for item in values:
        if not isinstance(item, dict):
            continue
        kb_id = str(item.get("kb_id") or "").strip()
        claim = str(item.get("claim") or "").strip()
        if kb_id not in offered_set:
            if kb_id:
                extra.append("unknown_kb")
            continue
        if not claim:
            continue
        key = (claim, kb_id)
        if key in seen:
            continue
        seen.add(key)
        points.append(
            {
                "point_id": str(item.get("point_id") or f"p{len(points) + 1}"),
                "claim": claim[:400],
                "kb_id": kb_id,
            }
        )
        if len(points) >= MAX_ANALYSIS_POINTS:
            break
    return points, extra


def _assemble_from_points(points: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for item in points:
        claim = item["claim"].rstrip("。.;；,， ")
        if not claim:
            continue
        parts.append(f"{claim}[[kb:{item['kb_id']}]]。")
    return "".join(parts)


def _cited_from_answer(answer: str, offered: list[str]) -> list[str]:
    offered_set = set(offered)
    cited: list[str] = []
    for token in KB_CITE_RE.findall(answer or ""):
        if token in offered_set and token not in cited:
            cited.append(token)
    return cited


def _uncovered_list(raw: Any) -> list[str]:
    values = raw if isinstance(raw, list) else []
    items: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text[:200])
        if len(items) >= 6:
            break
    return items


async def kb_chat_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """绑定请求、选定本次唯一 profile，并判断空库是否跳过检索。"""

    payload = cast(dict[str, Any], data.input)
    command = ChatKbIn.model_validate(payload["command"])
    knowledge = cast(KnowledgeStore, data.require_resource("knowledge"))
    user_id = str(data.require_resource("kb_user_id"))
    query = command.query.strip()
    (
        profile_id,
        auto_selected,
        empty_reason,
        profile_doc_count,
        other_profile_doc_count,
    ) = await knowledge.bind_workspace_profile(
        user_id, query, command.embedding_profile_id
    )
    await data.async_set_state("query", query, emit=False)
    await data.async_set_state("history", _clip_history(command.history), emit=False)
    await data.async_set_state("embedding_profile_id", profile_id, emit=False)
    await data.async_set_state("auto_selected", auto_selected, emit=False)
    await data.async_set_state("empty_reason", empty_reason, emit=False)
    await data.async_set_state("profile_doc_count", profile_doc_count, emit=False)
    await data.async_set_state(
        "other_profile_doc_count", other_profile_doc_count, emit=False
    )
    await data.async_set_state("skip_retrieve", bool(empty_reason), emit=False)
    await data.async_set_state("limitations", [], emit=False)
    await data.async_set_state("raw_hits", [], emit=False)
    return payload


async def kb_chat_rewrite(data: TriggerFlowRuntimeData) -> str:
    """把用户原话改写成可检索的完整问题。失败则退回原问。"""

    query = str(data.get_state("query") or "")
    if data.get_state("skip_retrieve"):
        await data.async_set_state("rewritten_query", query, emit=False)
        return query
    history = list(cast(list[dict[str, str]], data.get_state("history") or []))
    rewritten = query
    try:
        result = await (
            Agently.create_agent(name="kb-chat-rewrite")
            .input({"query": query, "history": history})
            .instruct(
                [
                    "把用户问题改写成一句完整、可检索的中文问句。",
                    "补全省略主语和指代，不要回答问题，不要编造手册没有的条件。",
                    "先前对话只帮助理解指代。",
                ]
            )
            .output({"rewritten_query": (str, "not_null")}, format="json")
            .async_start()
        )
        text = str(_as_dict(result).get("rewritten_query") or "").strip()
        if text:
            rewritten = text
    except Exception:
        await _note(data, "kb_rewrite_failed")
    await data.async_set_state("rewritten_query", rewritten, emit=False)
    return rewritten


async def kb_chat_split(data: TriggerFlowRuntimeData) -> list[dict[str, str]]:
    """把改写后的问题拆成若干检索问句，供 for_each 并行召回。"""

    rewritten = str(data.get_state("rewritten_query") or data.get_state("query") or "")
    if data.get_state("skip_retrieve"):
        await data.async_set_state("retrieval_queries", [], emit=False)
        return []
    history = list(cast(list[dict[str, str]], data.get_state("history") or []))
    queries = [rewritten]
    try:
        result = await (
            Agently.create_agent(name="kb-chat-split")
            .input(
                {
                    "query": data.get_state("query"),
                    "rewritten_query": rewritten,
                    "history": history,
                }
            )
            .instruct(
                [
                    "把改写后的问题拆成 1 到 4 个需要分别检索手册的问句。",
                    "每个问句只覆盖一个事实点，不要重复，不要回答。",
                    f"retrieval_queries 最多 {MAX_RETRIEVAL_QUERIES} 条。",
                ]
            )
            .output({"retrieval_queries": [str]}, format="json")
            .async_start()
        )
        queries = _normalize_queries(
            _as_dict(result).get("retrieval_queries"), rewritten
        )
    except Exception:
        await _note(data, "kb_split_failed")
        queries = [rewritten]
    await data.async_set_state("retrieval_queries", queries, emit=False)
    return [{"query": item} for item in queries]


async def kb_chat_retrieve(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """for_each 的每一条：按同一 profile hybrid 检索，不跨模型融合。"""

    item = data.input
    query = str(item.get("query") if isinstance(item, dict) else item or "").strip()
    if not query:
        return {"query": "", "hits": []}
    knowledge = cast(KnowledgeStore, data.require_resource("knowledge"))
    user_id = str(data.require_resource("kb_user_id"))
    profile_id = str(data.get_state("embedding_profile_id") or "")
    try:
        retrieved = await knowledge.search(
            user_id,
            SearchKbIn(query=query, embedding_profile_id=profile_id),
        )
    except Exception:
        await data.async_append_state("limitations", "kb_retrieve_failed", emit=False)
        return {"query": query, "hits": []}
    for hit in retrieved.hits:
        payload = hit.model_dump()
        payload["source_query"] = query
        await data.async_append_state("raw_hits", payload, emit=False)
    return {"query": query, "hit_count": len(retrieved.hits)}


async def kb_chat_expand(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """把命中块扩成完整段落（window / 同标题节 / 相邻块），再写入总结上下文。"""

    merged = _merge_hits(list(cast(list[dict[str, Any]], data.get_state("raw_hits") or [])))
    if not merged:
        await data.async_set_state("cards", [], emit=False)
        return []
    knowledge = cast(KnowledgeStore, data.require_resource("knowledge"))
    user_id = str(data.require_resource("kb_user_id"))
    try:
        cards, failed = await _expand_cards(knowledge, user_id, merged)
    except Exception:
        await _note(data, "kb_expand_failed")
        cards, failed = merged, False
    if failed:
        await _note(data, "kb_expand_failed")
    dumped = [item.model_dump(mode="json") for item in cards]
    await data.async_set_state("cards", dumped, emit=False)
    return dumped


async def kb_chat_analyze(data: TriggerFlowRuntimeData) -> list[dict[str, str]]:
    """先把召回切片拆成可引用要点，再交给总结。失败则按每卡一条退回。"""

    stored = list(cast(list[dict[str, Any]], data.get_state("cards") or []))
    cards = [ChatKbHit.model_validate(item) for item in stored]
    offered = [card.kb_id for card in cards]
    if not cards:
        await data.async_set_state("analysis_points", [], emit=False)
        await data.async_set_state("uncovered", [], emit=False)
        return []
    query = str(data.get_state("query") or "")
    rewritten = str(data.get_state("rewritten_query") or query)
    history = list(cast(list[dict[str, str]], data.get_state("history") or []))
    points = _fallback_points(cards)
    uncovered: list[str] = []
    try:
        result = await (
            Agently.create_agent(name="kb-chat-analyze")
            .input(
                {
                    "query": query,
                    "rewritten_query": rewritten,
                    "history": history,
                }
            )
            .info({"offered_kbs": [_offered_for_summarize(card) for card in cards]})
            .instruct(
                [
                    "阅读本次召回的手册切片，拆成能回答用户原问题的要点。",
                    "info.offered_kbs.text 是该 kb_id 的唯一事实来源。",
                    "context 只属于同一张卡的 window，不能把别的切片内容算进这张卡。",
                    "每条 point 只写一条事实，kb_id 必须来自 offered_kbs。",
                    "不同切片的事实必须分给不同 kb_id，禁止把 TEMPR、Cara 等不同段落都标成 k1。",
                    "同一张卡可以有多条 point；不要把多条事实挤进一句。",
                    "用户问到但这些切片没写的，列入 uncovered，不要编造。",
                    f"points 最多 {MAX_ANALYSIS_POINTS} 条。",
                ]
            )
            .output(
                {
                    "points": (
                        [
                            {
                                "point_id": (str, "not_null"),
                                "claim": (str, "not_null"),
                                "kb_id": (str, "not_null"),
                            }
                        ],
                        "not_null",
                    ),
                    "uncovered": [str],
                },
                format="json",
            )
            .async_start()
        )
        payload = _as_dict(result)
        cleaned, extra = _sanitize_points(payload.get("points"), offered)
        if extra:
            for code in extra:
                await _note(data, code)
        if cleaned:
            points = cleaned
        else:
            await _note(data, "kb_analyze_failed")
        uncovered = _uncovered_list(payload.get("uncovered"))
    except Exception:
        await _note(data, "kb_analyze_failed")
        points = _fallback_points(cards)
    await data.async_set_state("analysis_points", points, emit=False)
    await data.async_set_state("uncovered", uncovered, emit=False)
    return points


async def kb_chat_summarize(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """按分析要点写回答。多张切片不得塌缩成同一个 [[kb:k1]]。"""

    stored = list(cast(list[dict[str, Any]], data.get_state("cards") or []))
    cards = (
        [ChatKbHit.model_validate(item) for item in stored]
        if stored
        else _merge_hits(list(cast(list[dict[str, Any]], data.get_state("raw_hits") or [])))
    )
    offered = [card.kb_id for card in cards]
    reason = str(data.get_state("empty_reason") or "")
    if cards:
        reason = ""
    elif reason not in {"library_empty", "no_docs_for_profile"}:
        reason = "no_match"
    limitations = list(
        dict.fromkeys(cast(list[str], data.get_state("limitations") or []))
    )
    query = str(data.get_state("query") or "")
    rewritten = str(data.get_state("rewritten_query") or query)
    retrieval_queries = list(cast(list[str], data.get_state("retrieval_queries") or []))
    history = list(cast(list[dict[str, str]], data.get_state("history") or []))
    points = list(
        cast(list[dict[str, str]], data.get_state("analysis_points") or [])
    )
    if not points and cards:
        points = _fallback_points(cards)
    uncovered = list(cast(list[str], data.get_state("uncovered") or []))
    answer = EMPTY_ANSWER
    cited: list[str] = []
    needed = list(dict.fromkeys(item["kb_id"] for item in points))
    if cards:
        try:
            result = await (
                Agently.create_agent(name="kb-chat-summarize")
                .input(
                    {
                        "query": query,
                        "rewritten_query": rewritten,
                        "retrieval_queries": retrieval_queries,
                        "history": history,
                        "analysis": {"points": points, "uncovered": uncovered},
                    }
                )
                .info({"offered_kbs": [_offered_for_summarize(card) for card in cards]})
                .instruct(
                    [
                        "只根据 input.analysis.points 回答用户原问题，不要抛开要点自由发挥。",
                        "每个 point 至少写成一句，句末紧跟该 point 的 kb_id，例如：句子[[kb:k2]]。",
                        "禁止把不同 kb_id 的要点都标成 [[kb:k1]]。",
                        "不要粘贴手册原文；完整切片在右侧按 k1、k2… 列出。",
                        "uncovered 可在末尾用一句说明手册未覆盖，不要加 kb 引用，不要编造。",
                        "只能引用 analysis.points 和 offered_kbs 里已有的 kb_id，不得占用 [[ref:]]。",
                        "cited_kb_ids 列出正文用到的 kb_id。",
                    ]
                )
                .output(
                    {
                        "answer": (str, "not_null"),
                        "cited_kb_ids": [str],
                    },
                    format="json",
                )
                .async_start()
            )
            payload = _as_dict(result)
            answer, cited, extra = _sanitize_answer(
                str(payload.get("answer") or ""), offered
            )
            limitations.extend(
                item for item in extra if item not in limitations
            )
            for item in payload.get("cited_kb_ids") or []:
                token = str(item)
                if token in offered and token not in cited:
                    cited.append(token)
            if not answer:
                answer = _assemble_from_points(points) or FAILED_ANSWER
                cited = _cited_from_answer(answer, offered)
                if "kb_chat_failed" not in limitations:
                    limitations.append("kb_chat_failed")
            elif len(needed) > 1 and len(set(cited).intersection(needed)) < len(needed):
                rebuilt = _assemble_from_points(points)
                if rebuilt:
                    answer = rebuilt
                    cited = _cited_from_answer(answer, offered)
                    if "collapsed_cite" not in limitations:
                        limitations.append("collapsed_cite")
        except Exception:
            rebuilt = _assemble_from_points(points)
            answer = rebuilt or FAILED_ANSWER
            cited = _cited_from_answer(answer, offered)
            if "kb_chat_failed" not in limitations:
                limitations.append("kb_chat_failed")
    package = ChatKbOut(
        query=query,
        embedding_profile_id=str(data.get_state("embedding_profile_id") or ""),
        answer=answer,
        hits=cards,
        cited_kb_ids=cited,
        rewritten_query=rewritten,
        retrieval_queries=retrieval_queries,
        analysis_points=points,
        uncovered=uncovered,
        empty_reason=reason,
        profile_doc_count=int(data.get_state("profile_doc_count") or 0),
        other_profile_doc_count=int(data.get_state("other_profile_doc_count") or 0),
        auto_selected=bool(data.get_state("auto_selected")),
        limitations=limitations,
    )
    dumped = package.model_dump(mode="json")
    await data.async_set_state("package", dumped, emit=False)
    return dumped
