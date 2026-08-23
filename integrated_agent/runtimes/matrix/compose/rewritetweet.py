"""M4 改写支：接收 Source 原文包，按 post_count 并发生成改写推文草稿。"""

from __future__ import annotations

from typing import Any, cast

import re

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.compose.branch_hold import (
    _collect_upstream,
    _normalize_branch_context,
)
from integrated_agent.runtimes.matrix.compose.draft_media import (
    media_kind as _media_kind,
    resolve_draft_media as _resolve_draft_media,
    to_draft_media_cards as _to_draft_media_cards,
)
from integrated_agent.runtimes.matrix.compose.source import (
    _materialize_source_package,
    _source_media_entries,
    _tweet_status_url,
)
from integrated_agent.runtimes.matrix.compose.originaltweet import (
    MAX_DRAFT_CONCURRENCY,
    _draft_angle_hint,
    _normalize_draft,
    _resolve_post_count,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot, TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rewrite_context(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = cast(dict[str, Any], data.get_state("request") or {})
    user_instruction = str(data.get_state("user_instruction") or request.get("text") or "").strip()
    return {
        "user_instruction": user_instruction,
        "intent": "rewrite",
        "source_kind": str(data.get_state("source_kind") or ""),
        "source_anchor": str(data.get_state("source_anchor") or "").strip(),
        "task_id": str(request.get("task_id") or data.get_state("task_id") or ""),
    }


def _extract_source_text(
    *,
    rewrite_ctx: dict[str, Any],
    user_instruction: str,
    request_text: str,
) -> str:
    source_post = rewrite_ctx.get("source_post")
    if isinstance(source_post, dict):
        text = str(source_post.get("text") or "").strip()
        if text:
            return text

    for item in _as_list(rewrite_ctx.get("tool_result_cleaned")):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("title") or "").strip()
        if text:
            return text

    source_result = str(rewrite_ctx.get("source_result") or "").strip()
    if source_result:
        return source_result

    pasted = str(rewrite_ctx.get("source_text") or "").strip()
    if pasted:
        return pasted

    return str(request_text or "").strip()


def _hydrate_rewrite_upstream(upstream: dict[str, Any]) -> dict[str, Any]:
    """若 Source 只写回 tool_result_cleaned，从推文卡补全 source_post 等字段。"""
    hydrated = dict(upstream)
    search_query = str(upstream.get("search_query") or "").strip()
    if not isinstance(hydrated.get("source_post"), dict):
        package = _materialize_source_package(
            _as_list(upstream.get("tool_result_cleaned")),
            search_query=search_query,
        )
        if not package["source_post"]:
            return upstream
        hydrated["source_post"] = package["source_post"]
        if package["source_media"]:
            hydrated["source_media"] = package["source_media"]
        if package["related_tweet_cards"]:
            hydrated["related_tweet_cards"] = package["related_tweet_cards"]
        return hydrated

    if not _as_list(hydrated.get("source_media")):
        post_media = hydrated["source_post"].get("media")
        if isinstance(post_media, list) and post_media:
            hydrated["source_media"] = _source_media_entries(post_media)
    return hydrated


def _rewrite_has_source_card(rewrite_ctx: dict[str, Any]) -> bool:
    if isinstance(rewrite_ctx.get("source_post"), dict):
        return bool(str(rewrite_ctx["source_post"].get("text") or "").strip())
    for item in _as_list(rewrite_ctx.get("tool_result_cleaned")):
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip().lower() != "tweet":
            continue
        if str(item.get("tweet_id") or "").strip():
            return True
    return False


def _account_rewrite_hint(account: Any) -> dict[str, Any]:
    if account is None:
        return {}
    return {
        "voice_summary": str(getattr(account, "voice_summary", "") or ""),
        "content_pillars": list(getattr(account, "content_pillars", []) or []),
        "must_do": list(getattr(account, "must_do", []) or []),
        "must_not": list(getattr(account, "must_not", []) or []),
    }


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _split_source_for_drafts(source_text: str, post_count: int) -> tuple[list[str], str]:
    """把原文拆到每条草稿；句子多则切分（多退），句子少则共用并补全（少补）。"""
    text = str(source_text or "").strip()
    count = max(int(post_count), 1)
    if not text:
        return [""] * count, "empty"
    if count == 1:
        return [text], "full"

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text] * count, "full"

    if len(sentences) >= count:
        slices: list[str] = []
        base = len(sentences) // count
        extra = len(sentences) % count
        cursor = 0
        for index in range(count):
            take = base + (1 if index < extra else 0)
            chunk = "".join(sentences[cursor : cursor + take]).strip()
            slices.append(chunk or text)
            cursor += take
        return slices, "split"

    slices = [""] * count
    for index, sentence in enumerate(sentences):
        slot = index % count
        slices[slot] = f"{slices[slot]}{sentence}".strip()
    for index, chunk in enumerate(slices):
        if not chunk:
            slices[index] = text
    return slices, "padded"


def _raw_media_from_tweet(card: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("media", "source_media"):
        raw = card.get(key)
        if isinstance(raw, list):
            items = [item for item in raw if isinstance(item, dict)]
            if items:
                return items
    return []


def _reference_tweet_from_card(card: dict[str, Any]) -> dict[str, Any]:
    screen = str(card.get("screen_name") or "").lstrip("@")
    tid = str(card.get("tweet_id") or "").strip()
    raw_media = _raw_media_from_tweet(card)
    offered, catalog, _ = _build_rewrite_media_catalog(raw_media, None)
    url = str(card.get("url") or "").strip() or _tweet_status_url(screen, tid)
    return {
        "tweet_id": tid,
        "screen_name": screen,
        "text": str(card.get("text") or "").strip()[:240],
        "url": url,
        "media": raw_media,
        "offered_media": offered,
        "media_catalog": catalog,
    }


def _reference_tweet_for_model(reference_tweet: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(reference_tweet, dict):
        return None
    offered = [
        item
        for item in _as_list(reference_tweet.get("offered_media"))
        if isinstance(item, dict)
    ]
    return {
        "tweet_id": reference_tweet.get("tweet_id"),
        "screen_name": reference_tweet.get("screen_name"),
        "text": reference_tweet.get("text"),
        "url": reference_tweet.get("url"),
        "offered_media": offered,
        "media": offered,
    }


def _work_item_media_bundle(
    *,
    draft_index: int,
    source_offered: list[dict[str, Any]],
    source_catalog: list[dict[str, Any]],
    reference_tweet: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if draft_index == 1 and source_catalog:
        return list(source_offered), list(source_catalog)
    if isinstance(reference_tweet, dict):
        ref_catalog = [
            item
            for item in _as_list(reference_tweet.get("media_catalog"))
            if isinstance(item, dict)
        ]
        if ref_catalog:
            ref_offered = [
                item
                for item in _as_list(reference_tweet.get("offered_media"))
                if isinstance(item, dict)
            ]
            return ref_offered, ref_catalog
    if draft_index == 1 and source_catalog:
        return list(source_offered), list(source_catalog)
    return [], []


def _allocate_related_tweets(
    related_tweet_cards: list[dict[str, Any]], post_count: int
) -> list[dict[str, Any] | None]:
    slots: list[dict[str, Any] | None] = [None] * max(int(post_count), 1)
    candidates: list[dict[str, Any]] = []
    for item in related_tweet_cards:
        if not isinstance(item, dict):
            continue
        body = str(item.get("text") or "").strip()
        if not body:
            continue
        candidates.append(_reference_tweet_from_card(item))
    if not candidates:
        return slots
    for index in range(len(slots)):
        slots[index] = candidates[index % len(candidates)]
    return slots


def _plan_rewrite_work_items(
    *,
    post_count: int,
    source_text: str,
    source_post: dict[str, Any],
    related_tweet_cards: list[dict[str, Any]],
    offered_media: list[dict[str, Any]],
    media_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    slices, allocation_mode = _split_source_for_drafts(source_text, post_count)
    related_slots = _allocate_related_tweets(related_tweet_cards, post_count)
    source_tweet_id = str(source_post.get("tweet_id") or "").strip()
    work_items: list[dict[str, Any]] = []
    for index in range(1, post_count + 1):
        angle_hint = _draft_angle_hint(index)
        reference_tweet = related_slots[index - 1]
        focus_hint = angle_hint
        if reference_tweet:
            screen = str(reference_tweet.get("screen_name") or "").strip()
            if screen:
                focus_hint = f"{angle_hint}；可参考 @{screen.lstrip('@')} 的结构"
            if reference_tweet.get("offered_media"):
                focus_hint = f"{focus_hint}；参考推文含配图可参考 info.reference_tweet.offered_media"
        item_offered, item_catalog = _work_item_media_bundle(
            draft_index=index,
            source_offered=offered_media,
            source_catalog=media_catalog,
            reference_tweet=reference_tweet,
        )
        reuse_media = bool(item_catalog)
        work_items.append(
            {
                "draft_key": f"d{index}",
                "draft_index": index,
                "total_count": post_count,
                "angle_hint": angle_hint,
                "source_tweet_id": source_tweet_id,
                "allocated_source_text": slices[index - 1],
                "allocation_mode": allocation_mode,
                "focus_hint": focus_hint,
                "reference_tweet": reference_tweet,
                "reuse_media": reuse_media,
                "offered_media": item_offered,
                "media_catalog": item_catalog,
            }
        )
    return work_items


def _build_rewrite_media_catalog(
    source_media: list[dict[str, Any]],
    source_post: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """签发改写媒体：模型只见 key/kind/尺寸，包内带 preview_url。"""
    limitations: list[str] = []
    raw_items: list[dict[str, Any]] = list(source_media)
    if not raw_items and isinstance(source_post, dict):
        post_media = source_post.get("media")
        if isinstance(post_media, list):
            raw_items = [item for item in post_media if isinstance(item, dict)]

    if len(raw_items) > 1:
        limitations.append("media_truncated")
        raw_items = raw_items[:1]

    offered: list[dict[str, Any]] = []
    catalog: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        media_key = f"m{index}"
        kind = _media_kind(str(item.get("kind") or item.get("type") or "photo"))
        preview_url = str(
            item.get("preview_url")
            or item.get("thumb")
            or item.get("media_url_https")
            or ""
        ).strip()
        file_url = str(item.get("file_url") or item.get("video_url") or "").strip()
        width = item.get("width")
        height = item.get("height")

        package_item: dict[str, Any] = {
            "media_key": media_key,
            "kind": kind,
            "preview_url": preview_url,
        }
        if file_url:
            package_item["file_url"] = file_url
        if width is not None:
            package_item["width"] = width
        if height is not None:
            package_item["height"] = height

        model_item: dict[str, Any] = {"media_key": media_key, "kind": kind}
        if width is not None:
            model_item["width"] = width
        if height is not None:
            model_item["height"] = height

        offered.append(model_item)
        catalog.append(package_item)

    if raw_items and not any(str(item.get("preview_url") or "").strip() for item in catalog):
        limitations.append("source_media_unavailable")

    return offered, catalog, limitations


def _normalize_rewrite_draft(
    *,
    draft_key: str,
    draft_text: str,
    rationale: str,
    platform_key: str,
    media: list[dict[str, Any]],
) -> dict[str, Any]:
    draft = _normalize_draft(
        draft_key=draft_key,
        draft_text=draft_text,
        rationale=rationale,
        platform_key=platform_key,
    )
    if media:
        draft["media"] = _to_draft_media_cards(media)
    return draft


def _normalize_rewrite_package(
    *,
    drafts: list[dict[str, Any]],
    material_cards: list[dict[str, Any]],
    evidence_cards: list[dict[str, Any]],
    branch_context: dict[str, Any],
    limitations: list[str],
    post_count: int,
) -> dict[str, Any]:
    ready_count = sum(1 for item in drafts if str(item.get("text") or "").strip())
    if ready_count == 0:
        summary = "未生成改写推文草稿"
        status = "partial"
    elif ready_count < post_count:
        summary = f"已生成 {ready_count}/{post_count} 条改写推文草稿"
        status = "partial"
    else:
        summary = f"已生成 {ready_count} 条改写推文草稿"
        status = "completed"
    return {
        "status": status,
        "intent": "rewrite",
        "task_type": "compose_post",
        "summary": summary,
        "material_cards": material_cards,
        "evidence_cards": evidence_cards,
        "branch_context": branch_context,
        "drafts": drafts,
        "limitations": limitations,
    }


async def rewrite_tweet_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """归一化 Source 原文包，准备改写上下文。"""
    ctx = _rewrite_context(data)
    request = cast(dict[str, Any], data.get_state("request") or {})
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))

    upstream = _hydrate_rewrite_upstream(_collect_upstream(data))
    branch_context, evidence_cards = _normalize_branch_context(
        intent="rewrite",
        upstream=upstream,
        user_instruction=ctx["user_instruction"],
    )
    rewrite_ctx = _as_dict(branch_context.get("rewrite"))
    source_text = _extract_source_text(
        rewrite_ctx=rewrite_ctx,
        user_instruction=ctx["user_instruction"],
        request_text=str(request.get("text") or ""),
    )
    source_media = [
        item for item in _as_list(rewrite_ctx.get("source_media")) if isinstance(item, dict)
    ]
    offered_media, media_catalog, media_limitations = _build_rewrite_media_catalog(
        source_media,
        rewrite_ctx.get("source_post"),
    )
    for code in media_limitations:
        if code not in limitations:
            limitations.append(code)
    if not source_text and "rewrite_missing_source" not in limitations:
        limitations.append("rewrite_missing_source")

    await data.async_set_state("branch_context", branch_context, emit=False)
    await data.async_set_state("evidence_cards", evidence_cards, emit=False)
    await data.async_set_state("material_list", list(evidence_cards), emit=False)
    await data.async_set_state("source_text", source_text, emit=False)
    await data.async_set_state("offered_media", offered_media, emit=False)
    await data.async_set_state("rewrite_media_catalog", media_catalog, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    account = snapshot.account
    return {
        **ctx,
        "limitations": limitations,
        "source_text": source_text,
        "branch_context": branch_context,
        "evidence_cards": evidence_cards,
        "rewrite_ctx": rewrite_ctx,
        "offered_media": offered_media,
        "media_catalog": media_catalog,
        "platform_key": snapshot.platform.platform_key,
        "max_chars": snapshot.platform.max_chars,
        "voice_summary": account.voice_summary if account else "",
        "content_pillars": list(account.content_pillars) if account else [],
    }


async def plan_rewrite_drafts(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """计划阶段拆解原文并分配到每条并行写稿任务（多退少补）。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    branch_context = _as_dict(data.get_state("branch_context"))
    rewrite_ctx = _as_dict(branch_context.get("rewrite"))
    source_text = str(data.get_state("source_text") or "").strip()
    offered_media = [
        item for item in _as_list(data.get_state("offered_media")) if isinstance(item, dict)
    ]
    media_catalog = [
        item
        for item in _as_list(data.get_state("rewrite_media_catalog"))
        if isinstance(item, dict)
    ]
    related_tweet_cards = [
        item
        for item in _as_list(rewrite_ctx.get("related_tweet_cards"))
        if isinstance(item, dict)
    ]

    if not _rewrite_has_source_card(rewrite_ctx):
        if "rewrite_missing_source_card" not in limitations:
            limitations.append("rewrite_missing_source_card")
        await data.async_set_state("limitations", limitations, emit=False)
        await data.async_set_state("post_count", 0, emit=False)
        await data.async_set_state("rewrite_draft_plan", [], emit=False)
        return []

    post_count = _resolve_post_count(request, snapshot)
    source_post = _as_dict(rewrite_ctx.get("source_post"))
    work_items = _plan_rewrite_work_items(
        post_count=post_count,
        source_text=source_text,
        source_post=source_post,
        related_tweet_cards=related_tweet_cards,
        offered_media=offered_media,
        media_catalog=media_catalog,
    )
    await data.async_set_state("post_count", post_count, emit=False)
    await data.async_set_state("rewrite_draft_plan", work_items, emit=False)
    return work_items


async def rewrite_tweet_reason(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """基于计划阶段分配的原文片段与人设，生成单条改写推文草稿。"""
    work = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    draft_key = str(work.get("draft_key") or "d1")
    draft_index = int(work.get("draft_index") or 1)
    total_count = int(work.get("total_count") or 1)
    angle_hint = str(work.get("angle_hint") or _draft_angle_hint(draft_index))
    allocated_source_text = str(
        work.get("allocated_source_text") or data.get_state("source_text") or ""
    ).strip()
    focus_hint = str(work.get("focus_hint") or angle_hint).strip()
    reference_tweet = work.get("reference_tweet")
    reference_tweet_dict = _reference_tweet_for_model(
        reference_tweet if isinstance(reference_tweet, dict) else None
    )

    ctx = _rewrite_context(data)
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    account = snapshot.account
    user_instruction = str(ctx["user_instruction"])
    offered_media = [
        item for item in _as_list(work.get("offered_media")) if isinstance(item, dict)
    ]
    media_catalog = [
        item for item in _as_list(work.get("media_catalog")) if isinstance(item, dict)
    ]
    if not media_catalog:
        media_catalog = [
            item
            for item in _as_list(data.get_state("rewrite_media_catalog"))
            if isinstance(item, dict)
        ]

    model_input = {
        "user_instruction": user_instruction,
        "source_text": allocated_source_text,
    }
    info: dict[str, Any] = {
        "intent": "rewrite",
        "work_item": work,
        "user_instruction": user_instruction,
        "source_text": allocated_source_text,
        "focus_hint": focus_hint,
        "angle_hint": angle_hint,
        "allocation_mode": str(work.get("allocation_mode") or ""),
        "reference_tweet": reference_tweet_dict,
        "offered_media": offered_media,
        "max_chars": snapshot.platform.max_chars,
        "draft_index": draft_index,
        "total_count": total_count,
        "draft_key": draft_key,
        "account": _account_rewrite_hint(account),
    }

    draft_text = ""
    rationale = ""
    draft_media: list[dict[str, Any]] = []
    error = ""
    if not allocated_source_text:
        error = "rewrite_missing_source"
    else:
        try:
            raw = await (
                Agently.create_agent(name="matrix-compose-rewrite-draft")
                .input(model_input)
                .info(info)
                .instruct(
                    [
                        f"本次共需生成 {total_count} 条改写推文，你负责第 {draft_index} 条（draft_key={draft_key}）。",
                        f"写法角度：{focus_hint}。与其他条目的开头、结构、落脚点要有明显区分，禁止复读同一句。",
                        "你是改写支写稿模型：只改写 input.source_text 这一段，并结合 input.user_instruction。",
                        "input.source_text 是检索到的原文事实；input.user_instruction 只是口吻/写法要求，不得当正文主题。",
                        "推文主题、人物、事件必须与 input.source_text 一致，不要引入原文没有的新话题。",
                        "只输出一个 JSON 对象，不要 markdown 代码块，不要额外说明。",
                        "必须原创表述，禁止整段照抄原文；可保留事实点，但句式与结构要改写。",
                        "遵守 info.account 的 voice_summary、content_pillars、must_do、must_not。",
                        f"draft_text 不超过 info.max_chars 字。",
                        "若 info.offered_media 非空，默认保留配图：draft_text 用 [[media:m1]] 占位（仅写已签发的 media_key），不要把图片/视频链接写进正文。",
                        "info.reference_tweet.offered_media 展示参考推文配图信息，可决定是否沿用同样配图策略。",
                        "无 offered_media 时不要编造 [[media:]]；不要输出 hashtags 堆砌；不要编造原文中没有的事实。",
                        "info.reference_tweet 只作结构参考，不得把参考推文的话题混入正文。",
                        "rationale 用一句话说明写法，不要复述原文。",
                    ]
                )
                .output(
                    {
                        "draft_text": (str, "推文正文", "not_null"),
                        "rationale": (str, "写法说明", "not_null"),
                    },
                    format="json",
                )
                .async_start()
            )
            if isinstance(raw, dict):
                raw_text = str(raw.get("draft_text") or "").strip()
                rationale = str(raw.get("rationale") or "").strip()
                draft_text, draft_media = _resolve_draft_media(
                    raw_text,
                    media_catalog=media_catalog,
                    default_reuse=bool(work.get("reuse_media")) and bool(media_catalog),
                )
        except Exception as exc:
            error = f"rewrite_tweet_error:{draft_key}:{type(exc).__name__}"

    if (
        not draft_media
        and bool(work.get("reuse_media"))
        and media_catalog
    ):
        _, draft_media = _resolve_draft_media(
            draft_text,
            media_catalog=media_catalog,
            default_reuse=True,
        )

    return {
        "draft_key": draft_key,
        "draft_index": draft_index,
        "draft_text": draft_text,
        "rationale": rationale,
        "media": draft_media,
        "ok": bool(draft_text),
        "error": error,
    }


async def normalized_output_rewrite(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """汇总 for_each 改写写稿结果，归一化为 package / drafts 结构。"""
    ctx = _rewrite_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    evidence_cards = [
        item for item in _as_list(data.get_state("evidence_cards")) if isinstance(item, dict)
    ]
    branch_context = _as_dict(data.get_state("branch_context"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    post_count = _resolve_post_count(request, snapshot)
    platform_key = snapshot.platform.platform_key or TWITTER_PLATFORM_KEY

    task_results = [
        item for item in _as_list(data.input) if isinstance(item, dict)
    ]
    task_results.sort(key=lambda item: int(item.get("draft_index") or 0))

    plan_by_key = {
        str(item.get("draft_key") or ""): item
        for item in _as_list(data.get_state("rewrite_draft_plan"))
        if isinstance(item, dict)
    }

    drafts: list[dict[str, Any]] = []
    for result in task_results:
        error = str(result.get("error") or "").strip()
        if error and error not in limitations:
            limitations.append(error)
        if not result.get("ok", True) and not error:
            code = f"rewrite_tweet_failed:{result.get('draft_key') or 'unknown'}"
            if code not in limitations:
                limitations.append(code)

        draft_media = [
            item for item in _as_list(result.get("media")) if isinstance(item, dict)
        ]
        if not draft_media:
            work = plan_by_key.get(str(result.get("draft_key") or ""), {})
            catalog = [
                item
                for item in _as_list(work.get("media_catalog"))
                if isinstance(item, dict)
            ]
            if work.get("reuse_media") and catalog:
                _, draft_media = _resolve_draft_media(
                    str(result.get("draft_text") or ""),
                    media_catalog=catalog,
                    default_reuse=True,
                )
        draft = _normalize_rewrite_draft(
            draft_key=str(result.get("draft_key") or f"d{len(drafts) + 1}"),
            draft_text=str(result.get("draft_text") or "").strip(),
            rationale=str(result.get("rationale") or "").strip(),
            platform_key=platform_key,
            media=draft_media,
        )
        drafts.append(draft)

    package = _normalize_rewrite_package(
        drafts=drafts,
        material_cards=evidence_cards,
        evidence_cards=evidence_cards,
        branch_context=branch_context,
        limitations=limitations,
        post_count=post_count,
    )

    await data.async_set_state("drafts", drafts, emit=False)
    await data.async_set_state("package", package, emit=False)
    await data.async_set_state("material_list", evidence_cards, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.rewrite_tweet",
        status="completed" if any(d["text"] for d in drafts) else "failed",
        subject_id=ctx["task_id"],
        facts={
            "evidence_cards": len(evidence_cards),
            "post_count": post_count,
            "draft_count": len([d for d in drafts if d["text"]]),
            "limitations": limitations,
        },
    )
    return {
        "package": package,
        "drafts": drafts,
        "limitations": limitations,
        "material_list": evidence_cards,
        "evidence_cards": evidence_cards,
        "branch_context": branch_context,
    }


def build_rewrite_tweet_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-rewrite-tweet-v1")
    (
        flow.to(rewrite_tweet_prelude)
        .to(plan_rewrite_drafts)
        .for_each(concurrency=MAX_DRAFT_CONCURRENCY)
        .to(rewrite_tweet_reason)
        .end_for_each()
        .to(normalized_output_rewrite)
    )
    return flow


REWRITE_TWEET_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
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
        "tool_logs": "runtime_data.tool_logs",
        "tool_result_cleaned": "runtime_data.tool_result_cleaned",
        "source_result": "runtime_data.source_result",
        "source_post": "runtime_data.source_post",
        "source_media": "runtime_data.source_media",
        "author_card": "runtime_data.author_card",
        "related_tweet_cards": "runtime_data.related_tweet_cards",
    },
    "resources": {
        "trace": "resources.trace",
        "session_id": "resources.session_id",
        "snapshot": "resources.snapshot",
    },
}

REWRITE_TWEET_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "package": "result.package",
        "drafts": "result.drafts",
        "limitations": "result.limitations",
        "material_list": "result.material_list",
        "evidence_cards": "result.evidence_cards",
        "branch_context": "result.branch_context",
    },
}


__all__ = [
    "REWRITE_TWEET_SUBFLOW_CAPTURE",
    "REWRITE_TWEET_SUBFLOW_WRITE_BACK",
    "build_rewrite_tweet_subflow",
    "normalized_output_rewrite",
    "plan_rewrite_drafts",
    "rewrite_tweet_prelude",
    "rewrite_tweet_reason",
]
