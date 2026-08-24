"""M4 改写支：接收 Source 原文包，按 post_count 并发生成改写推文草稿。"""

from __future__ import annotations

from typing import Any, cast

import re

from agently import TriggerFlow, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.branch_hold import (
    _collect_upstream,
    _normalize_branch_context,
)
from integrated_agent.runtimes.matrix.compose.draft_media import (
    media_kind as _media_kind,
    to_draft_media_cards as _to_draft_media_cards,
)
from integrated_agent.runtimes.matrix.compose.source import (
    _materialize_source_package,
    _source_media_entries,
    _tweet_status_url,
)
from integrated_agent.runtimes.matrix.compose.draft_gate import gate_compose_draft
from integrated_agent.runtimes.matrix.compose.review import (
    _coerce_gated_draft,
    review_compose_draft_item,
)
from integrated_agent.runtimes.matrix.compose.rewrite_plan import (
    build_rewrite_plan_card,
    build_rewrite_work_item,
)
from integrated_agent.runtimes.matrix.compose.material import (
    _draft_angle_hint,
    _resolve_post_count,
)
from integrated_agent.runtimes.matrix.compose.originaltweet import (
    MAX_DRAFT_CONCURRENCY,
    MAX_DRAFT_REGEN_ATTEMPTS,
    _draft_payload_from_gated,
    _is_publishable_compose_draft,
    _normalize_draft,
    _regen_repair,
)
from integrated_agent.runtimes.matrix.compose.subflow import (
    DRAFT_RESOURCES,
    ROUTE_STATE,
    SubFlow,
    capture_state,
    write_back_result,
)
from integrated_agent.runtimes.matrix.host.drafting import rollup_status
from integrated_agent.runtimes.matrix.host.models import WorkItem
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


async def rewrite_tweet_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """归一化 Source 原文包，准备改写上下文。"""
    ctx = _rewrite_context(data)
    request = cast(dict[str, Any], data.get_state("request") or {})
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    events = data.get_resource("events")
    task_id = str(request.get("task_id") or "")
    if events is not None and task_id:
        await events.publish(task_id, "stage.started", {"stage": "rewrite_tweet"})

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


def _offered_cta_urls(snapshot: Snapshot) -> list[str]:
    account = snapshot.account
    if account is None:
        return []
    return list(account.offered_cta_urls or [])


async def host_rewrite_plan(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """M4：宿主拼 rewrite_plan_card + 正式 WorkItem，并签发改写媒体。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    task_id = str(request.get("task_id") or "")
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    branch_context = _as_dict(data.get_state("branch_context"))
    rewrite_ctx = _as_dict(branch_context.get("rewrite"))
    user_instruction = str(data.get_state("user_instruction") or request.get("text") or "").strip()
    request_text = str(request.get("text") or "")
    source_text = str(data.get_state("source_text") or "").strip()
    if not source_text:
        source_text = _extract_source_text(
            rewrite_ctx=rewrite_ctx,
            user_instruction=user_instruction,
            request_text=request_text,
        )

    if not _rewrite_has_source_card(rewrite_ctx):
        if "rewrite_missing_source_card" not in limitations:
            limitations.append("rewrite_missing_source_card")
        await data.async_set_state("limitations", limitations, emit=False)
        await data.async_set_state("rewrite_plan_card", None, emit=False)
        await data.async_set_state("work_items", [], emit=False)
        return {"rewrite_plan_card": None, "work_items": []}

    source_post = rewrite_ctx.get("source_post")
    source_post_dict = source_post if isinstance(source_post, dict) else None
    source_media = [
        item for item in _as_list(data.get_state("source_media")) if isinstance(item, dict)
    ]
    offered_cta_urls = _offered_cta_urls(snapshot)
    offered_media, media_catalog, media_limits = _build_rewrite_media_catalog(
        source_media,
        source_post_dict,
    )
    for note in media_limits:
        if note not in limitations:
            limitations.append(note)

    plan_card = build_rewrite_plan_card(
        source_media=source_media,
        source_post=source_post_dict,
        offered_cta_urls=offered_cta_urls,
        user_instruction=user_instruction,
        limitations=limitations,
    )
    if plan_card["media_choice"] == "none":
        offered_media = []
        media_catalog = []

    platform_key = snapshot.platform.platform_key or TWITTER_PLATFORM_KEY
    work_item = build_rewrite_work_item(
        user_instruction=user_instruction,
        source_text=source_text,
        platform_key=platform_key,
        source_issues=list(plan_card.get("source_issues") or []),
    )

    await data.async_set_state("source_text", source_text, emit=False)
    await data.async_set_state("rewrite_plan_card", plan_card, emit=False)
    await data.async_set_state("work_items", [work_item.model_dump(mode="json")], emit=False)
    await data.async_set_state("offered_media", offered_media, emit=False)
    await data.async_set_state("rewrite_media_catalog", media_catalog, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    trace.log(
        layer="business",
        event_type="business.matrix.rewrite_planned",
        status="completed",
        subject_id=task_id,
        facts={
            "media_choice": plan_card.get("media_choice"),
            "has_cta_url": bool(plan_card.get("cta_url")),
        },
    )
    return {
        "rewrite_plan_card": plan_card,
        "work_items": [work_item.model_dump(mode="json")],
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
    plan_card = _as_dict(data.get_state("rewrite_plan_card"))
    formal_items = [
        WorkItem.model_validate(item)
        for item in _as_list(data.get_state("work_items"))
        if isinstance(item, dict)
    ]
    formal_item = formal_items[0] if formal_items else None
    offered_cta_urls = _offered_cta_urls(snapshot)
    cta_url = str(plan_card.get("cta_url") or "").strip()
    reuse_media = plan_card.get("media_choice") == "reuse_source_media"

    work_items = _plan_rewrite_work_items(
        post_count=post_count,
        source_text=source_text,
        source_post=source_post,
        related_tweet_cards=related_tweet_cards,
        offered_media=offered_media if reuse_media else [],
        media_catalog=media_catalog if reuse_media else [],
    )
    for item in work_items:
        if formal_item is not None:
            item["work_item"] = formal_item.model_dump(mode="json")
            item["work_item_id"] = formal_item.work_item_id
            item["goal"] = formal_item.goal
            item["talking_points"] = list(formal_item.talking_points)
        item["rewrite_plan_card"] = plan_card
        item["offered_cta_urls"] = offered_cta_urls
        item["cta_url"] = cta_url
        item["reuse_media"] = reuse_media and bool(item.get("media_catalog"))
    await data.async_set_state("post_count", post_count, emit=False)
    await data.async_set_state("rewrite_draft_plan", work_items, emit=False)
    events = data.get_resource("events")
    task_id = str(request.get("task_id") or "")
    for item in work_items:
        if events is not None and task_id:
            await events.publish(
                task_id,
                "work_item.ready",
                {
                    "work_item_id": str(
                        item.get("work_item_id") or item.get("draft_key") or ""
                    ),
                    "kind": "compose_post",
                },
            )
    return work_items


def _rewrite_draft_instruct(
    *,
    total_count: int,
    draft_index: int,
    draft_key: str,
    focus_hint: str,
) -> list[str]:
    return [
        f"本次共需生成 {total_count} 条改写推文，你负责第 {draft_index} 条（draft_key={draft_key}）。",
        f"写法角度：{focus_hint}。与其他条目的开头、结构、落脚点要有明显区分，禁止复读同一句。",
        "优先遵循 work_item.goal 与 talking_points；它们是包级计划，不要偏离。",
        "只改写 input.source_text 这一段，并结合 input.user_instruction。",
        "source_text 是原文事实；user_instruction 只是口吻/写法要求，不得当正文主题。",
        "必须原创表述，禁止整段照抄原文；可保留事实点，但句式与结构要改写。",
        "遵守 info.account 的 voice、pillars、must_do、must_not。",
        "正文不超过 info.max_chars 字。",
        "结尾只给一个增长 CTA：关注系列/点置顶/去官方渠道；禁止评论区互动话术。",
        "结尾只用文字 CTA；不要写 [[cta:0]] 或任意 https。",
        "若 info.offered_media 非空且计划复用媒体，draft_text 用 [[media:m1]] 占位。",
        "不要输出 hashtags 堆砌；不要编造原文中没有的事实。",
        "证据只能引用 info.offered_refs 的 ref_id，填 evidence_ids；正文不要写 [[ref:]]。offered_refs 为空时 evidence_ids 必须是 []。",
    ]


def _rewrite_skip_payload(
    *,
    draft_key: str,
    draft_index: int,
    error: str,
    rationale: str,
) -> dict[str, Any]:
    return {
        "draft_key": draft_key,
        "draft_index": draft_index,
        "kind": "compose_post",
        "platform_key": TWITTER_PLATFORM_KEY,
        "degrade_op": "skip",
        "text": "",
        "rationale": rationale,
        "decision": "skip",
        "status": "skipped",
        "issues": [error],
        "ok": False,
        "error": error,
    }


async def rewrite_tweet_draft_with_review(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """改写写稿 + Gate + Review；不合格则带 repair 再生成（至多 MAX_DRAFT_REGEN_ATTEMPTS 次）。"""
    work = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    draft_index = int(work.get("draft_index") or 1)
    total_count = int(work.get("total_count") or 1)
    draft_key = str(work.get("draft_key") or "d1")
    allocated_source_text = str(
        work.get("allocated_source_text") or data.get_state("source_text") or ""
    ).strip()
    focus_hint = str(work.get("focus_hint") or _draft_angle_hint(draft_index)).strip()
    if not allocated_source_text:
        return _rewrite_skip_payload(
            draft_key=draft_key,
            draft_index=draft_index,
            error="rewrite_missing_source",
            rationale="缺少可改写的原文片段。",
        )

    instruct = _rewrite_draft_instruct(
        total_count=total_count,
        draft_index=draft_index,
        draft_key=draft_key,
        focus_hint=focus_hint,
    )
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    repair: dict[str, Any] | None = None
    last_payload: dict[str, Any] = {}

    for attempt in range(1, MAX_DRAFT_REGEN_ATTEMPTS + 1):
        attempt_instruct = list(instruct)
        if repair:
            attempt_instruct.append(
                "上一稿未通过 Gate/Review，请按 repair.issues、review_notes 重写，"
                "相对 source_text 与 previous_text 都要明显改写，不要复读。"
            )
        last_payload = await gate_compose_draft(
            data,
            work=work,
            draft_agent_name="matrix-compose-draft",
            source_text=allocated_source_text,
            instruct=attempt_instruct,
            draft_repair=repair,
        )
        gated = _coerce_gated_draft(last_payload)

        if not gated.text.strip() or gated.degrade_op == "skip":
            if attempt < MAX_DRAFT_REGEN_ATTEMPTS:
                note = f"rewrite_draft_regen:{draft_key}:gate:{attempt}"
                if note not in limitations:
                    limitations.append(note)
                repair = _regen_repair(
                    gated=gated,
                    attempt=attempt,
                    reason="gate_skip_or_empty",
                )
                continue
            break

        try:
            reviewed, review_notes = await review_compose_draft_item(
                data,
                gated,
                limitations=limitations,
            )
        except Exception as exc:
            note = f"rewrite_review_error:{draft_key}:{type(exc).__name__}"
            if note not in limitations:
                limitations.append(note)
            if _is_publishable_compose_draft(gated):
                last_payload = _draft_payload_from_gated(
                    gated,
                    work=work,
                    base=last_payload,
                )
                break
            if attempt < MAX_DRAFT_REGEN_ATTEMPTS:
                repair = _regen_repair(
                    gated=gated,
                    attempt=attempt,
                    reason="review_agent_error",
                    review_notes=str(exc)[:200],
                )
                continue
            break
        for note in review_notes:
            if note not in limitations:
                limitations.append(note)

        last_payload = _draft_payload_from_gated(reviewed, work=work, base=last_payload)
        if _is_publishable_compose_draft(reviewed):
            break

        if attempt < MAX_DRAFT_REGEN_ATTEMPTS:
            note = f"rewrite_draft_regen:{draft_key}:review:{attempt}"
            if note not in limitations:
                limitations.append(note)
            repair = _regen_repair(
                gated=reviewed,
                attempt=attempt,
                reason="review_not_publishable",
                review_notes="；".join(
                    part
                    for part in (
                        reviewed.issues[0] if reviewed.issues else "",
                        str(repair.get("review_notes") or "") if repair else "",
                    )
                    if part
                ),
            )
            continue
        break

    if limitations:
        await data.async_set_state("limitations", limitations, emit=False)
    return last_payload


async def normalized_output_rewrite(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """汇总 review 后的 Gate 草稿，归一化为 package / drafts 结构。"""
    ctx = _rewrite_context(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    evidence_cards = [
        item for item in _as_list(data.get_state("evidence_cards")) if isinstance(item, dict)
    ]
    branch_context = _as_dict(data.get_state("branch_context"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    post_count = _resolve_post_count(request, snapshot)

    review_payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    raw_input = data.input
    if isinstance(raw_input, list):
        drafts_raw = [item for item in raw_input if isinstance(item, dict)]
    else:
        drafts_raw = list(
            cast(
                list[Any],
                data.get_state("drafts") or review_payload.get("drafts") or [],
            )
        )
    if not drafts_raw:
        drafts_raw = list(cast(list[Any], data.get_state("drafts") or []))
    summary = str(
        review_payload.get("review_summary")
        or data.get_state("review_summary")
        or ""
    ).strip()

    drafts: list[dict[str, Any]] = []
    for index, item in enumerate(drafts_raw, start=1):
        if not isinstance(item, dict):
            continue
        gated = _coerce_gated_draft(item)
        draft = gated.model_dump(mode="json")
        if not draft.get("draft_key"):
            draft["draft_key"] = f"d{index}"
        drafts.append(draft)

    status = str(
        review_payload.get("rollup_status")
        or data.get_state("rollup_status")
        or rollup_status([_coerce_gated_draft(item) for item in drafts_raw if isinstance(item, dict)])
    )
    if not summary:
        ready = sum(1 for item in drafts if str(item.get("text") or "").strip())
        summary = (
            f"已生成 {ready} 条改写推文草稿"
            if ready
            else "未生成改写推文草稿"
        )

    package = {
        "status": status,
        "intent": "rewrite",
        "task_type": "compose_post",
        "summary": summary,
        "material_cards": evidence_cards,
        "drafts": drafts,
        "limitations": limitations,
        "branch_context": branch_context,
    }

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
        "rewrite_plan_card": data.get_state("rewrite_plan_card"),
        "work_items": data.get_state("work_items") or [],
    }


def build_rewrite_tweet_subflow() -> SubFlow:
    flow = TriggerFlow(name="matrix-compose-rewrite-tweet-v1")
    (
        flow.to(rewrite_tweet_prelude)
        .to(host_rewrite_plan)
        .to(plan_rewrite_drafts)
        .for_each(concurrency=MAX_DRAFT_CONCURRENCY)
        .to(rewrite_tweet_draft_with_review)
        .end_for_each()
        .to(normalized_output_rewrite)
    )
    return SubFlow(
        flow,
        capture_state(
            *ROUTE_STATE,
            "source_text",
            "search_query",
            "tool_logs",
            "tool_result_cleaned",
            "source_result",
            "source_post",
            "source_media",
            "author_card",
            "related_tweet_cards",
            resources=DRAFT_RESOURCES,
        ),
        write_back_result(
            "package",
            "drafts",
            "limitations",
            "material_list",
            "evidence_cards",
            "branch_context",
            "rewrite_plan_card",
            "work_items",
        ),
    )


__all__ = [
    "build_rewrite_tweet_subflow",
    "host_rewrite_plan",
    "normalized_output_rewrite",
    "plan_rewrite_drafts",
    "rewrite_tweet_draft_with_review",
    "rewrite_tweet_prelude",
]
