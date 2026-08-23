from integrated_agent.runtimes.matrix.compose.retrieval import (
    extract_search_query,
    normalize_route_intent,
    pick_primary_tweet,
    prefer_search_over_handle,
)
from integrated_agent.runtimes.matrix.host.models import RouteIntentOut


def test_extract_search_query_from_lookup_phrase() -> None:
    assert (
        extract_search_query(
            search_query="",
            user_instruction="改写最新动态推文",
            request_text="搜索特朗普最新动态，创作推文",
        )
        == "特朗普"
    )


def test_extract_search_query_ignores_rewrite_meta_only() -> None:
    assert (
        extract_search_query(
            search_query="",
            user_instruction="改成我们口吻",
            request_text="",
        )
        == ""
    )


def test_prefer_search_over_ambiguous_handle() -> None:
    assert prefer_search_over_handle(
        source_kind="handle",
        source_anchor="trump",
        search_query="特朗普",
    )


def test_normalize_route_clears_ambiguous_handle() -> None:
    routed = RouteIntentOut.model_validate(
        {
            "reason": "test",
            "intent": "rewrite",
            "source_kind": "handle",
            "source_anchor": "trump",
            "user_instruction": "改写最新动态推文",
            "search_query": "特朗普",
            "confidence": "high",
        }
    )
    fixed = normalize_route_intent(
        routed,
        request_text="改写特朗普的最新动态推文",
    )
    assert fixed.source_kind == "none"
    assert fixed.source_anchor == ""
    assert fixed.search_query == "特朗普"


def test_pick_primary_tweet_prefers_trump_topic() -> None:
    tweets = [
        {
            "tweet_id": "1",
            "screen_name": "TrumpGolf",
            "text": "Nexo Championship at Trump Scotland golf course",
        },
        {
            "tweet_id": "2",
            "screen_name": "realDonaldTrump",
            "text": "Great meeting with leaders on border security today.",
        },
    ]
    picked, score = pick_primary_tweet(tweets, search_query="特朗普")
    assert picked["tweet_id"] == "2"
    assert score > 0
