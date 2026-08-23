"""改写检索：从用户话里抽 search_query，选最相关推文。"""

from __future__ import annotations

import re
from typing import Any

from integrated_agent.runtimes.matrix.host.models import RouteIntentOut, WriteIntent

_SEARCH_QUERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"查找\s*(.+?)\s*(?:的)?(?:最新动态|最新消息|最新推文|近况|动态)"),
    re.compile(r"搜索\s*(.+?)\s*(?:的)?(?:最新动态|最新消息|最新推文|近况|动态)"),
    re.compile(r"(.+?)\s*(?:的)?(?:最新动态|最新消息|最新推文|近况)"),
)

_REWRITE_META_PHRASES: tuple[str, ...] = (
    "改成我们口吻",
    "改成官方口吻",
    "改写成",
    "改写一下",
    "改写",
    "创作推文",
    "写一条推文",
    "写推文",
    "生成推文",
    "编写推文",
    "帮我写",
    "请写",
    "按照我们口吻",
    "用我们口吻",
    "最新动态推文",
)

_PURE_META_PHRASES: frozenset[str] = frozenset(
    {
        "创作推文",
        "写推文",
        "写一条",
        "改写",
        "改成我们口吻",
        "生成推文",
        "编写推文",
        "改写最新动态推文",
    }
)

_LOOKUP_HINTS: tuple[str, ...] = (
    "最新动态",
    "最新推文",
    "最新消息",
    "搜索",
    "查找",
    "近期",
    "最近",
)

_TOPIC_SCREEN_NAMES: dict[str, frozenset[str]] = {
    "特朗普": frozenset({"realdonaldtrump", "potus", "whitehouse"}),
    "川普": frozenset({"realdonaldtrump", "potus"}),
}

_GOLF_NOISE_TERMS: frozenset[str] = frozenset(
    {"golf", "championship", "course", "tee", "round 1", "scotland"}
)


_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "特朗普": ("trump", "donald trump", "realdonaldtrump"),
    "川普": ("trump", "donald trump", "realdonaldtrump"),
}


def extract_search_query(
    *,
    search_query: str = "",
    user_instruction: str = "",
    request_text: str = "",
) -> str:
    explicit = str(search_query or "").strip()
    if explicit:
        return explicit

    for text in (user_instruction, request_text):
        body = str(text or "").strip()
        if not body:
            continue
        for pattern in _SEARCH_QUERY_PATTERNS:
            match = pattern.search(body)
            if not match:
                continue
            candidate = str(match.group(1) or "").strip(" ，,。.！!？?；;：:")
            if len(candidate) >= 2 and candidate not in _PURE_META_PHRASES:
                return candidate

    return ""


def request_needs_lookup(request_text: str) -> bool:
    text = str(request_text or "")
    return any(hint in text for hint in _LOOKUP_HINTS)


def prefer_search_over_handle(
    *,
    source_kind: str,
    source_anchor: str,
    search_query: str,
) -> bool:
    query = str(search_query or "").strip()
    if not query:
        return False
    kind = str(source_kind or "none").strip().lower()
    if kind in {"paste", "url", "tweet_id"}:
        return False
    if kind == "none":
        return True
    if kind != "handle":
        return False
    anchor = str(source_anchor or "").strip().lstrip("@")
    # 短 handle（如 trump）易撞号，有 search_query 时优先按主题搜
    return len(anchor) <= 8


def normalize_route_intent(
    routed: RouteIntentOut,
    *,
    request_text: str,
) -> RouteIntentOut:
    """主机侧修正路由：补 search_query，消解歧义 handle。"""
    text = str(request_text or "").strip()
    search_query = extract_search_query(
        search_query=routed.search_query,
        user_instruction=routed.user_instruction,
        request_text=text,
    )
    source_kind = routed.source_kind
    source_anchor = str(routed.source_anchor or "").strip().lstrip("@")
    intent: WriteIntent = routed.intent

    needs_lookup = request_needs_lookup(text)
    if intent == "compose" and needs_lookup and search_query:
        intent = "rewrite"

    if (
        prefer_search_over_handle(
            source_kind=source_kind,
            source_anchor=source_anchor,
            search_query=search_query,
        )
        and search_query
    ):
        source_kind = "none"
        source_anchor = ""

    if intent == "rewrite" and needs_lookup and not search_query:
        search_query = extract_search_query(
            search_query="",
            user_instruction=routed.user_instruction,
            request_text=text,
        )

    return routed.model_copy(
        update={
            "intent": intent,
            "source_kind": source_kind,
            "source_anchor": source_anchor,
            "search_query": search_query,
        }
    )


def _query_terms(query: str) -> list[str]:
    q = str(query or "").strip().lower()
    if not q:
        return []
    terms = [q]
    for alias_key, aliases in _TOPIC_ALIASES.items():
        if alias_key in query or alias_key.lower() in q:
            terms.extend(aliases)
            terms.append(alias_key)
    for token in re.split(r"[\s,，、]+", q):
        token = token.strip()
        if len(token) >= 2:
            terms.append(token.lower())
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        lowered = term.lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            out.append(lowered)
    return out


def tweet_relevance_score(tweet: dict[str, Any], query: str) -> int:
    terms = _query_terms(query)
    if not terms:
        return 0
    text = str(tweet.get("text") or "").lower()
    screen = str(tweet.get("screen_name") or "").lstrip("@").lower()
    score = 0
    for term in terms:
        if term in text:
            score += 10
        if term in screen:
            score += 8
        if term.replace(" ", "") in text.replace(" ", ""):
            score += 4

    raw_query = str(query or "").strip()
    for topic, screens in _TOPIC_SCREEN_NAMES.items():
        if topic in raw_query and screen in screens:
            score += 30

    if any(topic in raw_query for topic in _TOPIC_SCREEN_NAMES):
        if any(noise in text for noise in _GOLF_NOISE_TERMS):
            score -= 20
        if "golf" in screen:
            score -= 20

    return score


def pick_primary_tweet(
    tweets: list[dict[str, Any]],
    *,
    search_query: str,
) -> tuple[dict[str, Any] | None, int]:
    if not tweets:
        return None, 0
    query = str(search_query or "").strip()
    if not query:
        return tweets[0], 0
    best_index = 0
    best_score = -1
    for index, tweet in enumerate(tweets):
        score = tweet_relevance_score(tweet, query)
        if score > best_score:
            best_score = score
            best_index = index
    if best_score <= 0:
        return tweets[0], 0
    return tweets[best_index], best_score


__all__ = [
    "extract_search_query",
    "normalize_route_intent",
    "pick_primary_tweet",
    "prefer_search_over_handle",
    "request_needs_lookup",
    "tweet_relevance_score",
]
