from integrated_agent.runtimes.matrix.host.models import coerce_media_links, media_links_as_dicts


def test_coerce_media_links_accepts_url_strings() -> None:
    links = coerce_media_links(["https://example.com/a.jpg"])
    assert len(links) == 1
    assert links[0].preview_url == "https://example.com/a.jpg"


def test_media_links_as_dicts_normalizes_mixed_items() -> None:
    out = media_links_as_dicts(
        [
            "https://example.com/b.jpg",
            {"type": "video", "thumb": "https://example.com/thumb.jpg", "video_url": "https://example.com/v.mp4"},
        ]
    )
    assert len(out) == 2
    assert out[0]["preview_url"] == "https://example.com/b.jpg"
    assert out[1]["type"] == "video"
