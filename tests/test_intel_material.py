from __future__ import annotations

from integrated_agent.runtimes.matrix.compose.intel import (
    _has_url_or_handle_candidate,
    _normalize_material_card,
    _normalize_trending_country,
    _tweet_cards_from_materials,
    confirm_intel_tool,
)


def test_gate_skips_pure_theme_without_trends() -> None:
    assert not _has_url_or_handle_candidate(
        source_kind="none",
        source_anchor="",
        text="为秋季上新写预热稿",
    )


def test_gate_opens_on_handle_or_url() -> None:
    assert _has_url_or_handle_candidate(
        source_kind="handle",
        source_anchor="demo",
        text="",
    )
    assert _has_url_or_handle_candidate(
        source_kind="none",
        source_anchor="",
        text="参考 https://x.com/demo/status/1234567890123456789 自己写",
    )
    assert _has_url_or_handle_candidate(
        source_kind="none",
        source_anchor="",
        text="看看 @matrix_demo 的风格",
    )


def test_confirm_rejects_trending_when_disabled() -> None:
    args, err = confirm_intel_tool(
        "fetch_trending",
        {},
        need_trends=False,
        allowlist={"fetch_trending", "fetch_search_timeline"},
    )
    assert args is None
    assert "trending_disabled" in err


def test_confirm_defaults_trending_country() -> None:
    args, err = confirm_intel_tool(
        "fetch_trending",
        {},
        need_trends=True,
        allowlist={"fetch_trending"},
    )
    assert err == ""
    assert args == {"country": "China"}
    assert _normalize_trending_country("china") == "China"


def test_confirm_search_defaults_latest() -> None:
    args, err = confirm_intel_tool(
        "fetch_search_timeline",
        {"keyword": "秋季上新"},
        need_trends=False,
        allowlist={"fetch_search_timeline"},
    )
    assert err == ""
    assert args is not None
    assert args["search_type"] == "Latest"


def test_normalize_material_card_preserves_media_links() -> None:
    card = _normalize_material_card(
        {
            "kind": "tweet",
            "tweet_id": "99",
            "screen_name": "demo",
            "text": "hello",
            "media_links": ["https://pbs.twimg.com/media/x.jpg"],
        }
    )
    assert card["link"] == ""
    assert len(card["media_links"]) == 1
    assert card["media_links"][0]["preview_url"] == "https://pbs.twimg.com/media/x.jpg"


def test_tweet_cards_from_materials() -> None:
    material_list = [
        {
            "kind": "tweet",
            "tweet_id": "1",
            "screen_name": "a",
            "text": "t1",
            "media": [{"type": "gif", "thumb": "https://example.com/a.gif"}],
        }
    ]
    cards = _tweet_cards_from_materials(material_list)
    assert len(cards) == 1
    assert cards[0]["tweet_id"] == "1"
    assert cards[0]["media"][0]["type"] == "gif"
