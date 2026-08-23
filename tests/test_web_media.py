from integrated_agent.runtimes.matrix.compose.intel import _normalize_material_card
from integrated_agent.runtimes.matrix.compose.web_media import (
    extract_media_urls_from_html,
    is_public_media_url,
    is_public_page_url,
    sanitize_public_media_links,
)


def test_rejects_example_com_urls() -> None:
    assert not is_public_page_url("https://example.com/mooncake")
    assert not is_public_media_url("https://example.com/mooncake_preview.jpg")
    assert sanitize_public_media_links(
        [{"type": "photo", "thumb": "https://example.com/a.jpg", "preview_url": "https://example.com/a.jpg"}]
    ) == []


def test_accepts_real_cdn_urls() -> None:
    url = "https://pbs.twimg.com/media/abc.jpg"
    assert is_public_media_url(url)
    links = sanitize_public_media_links([url])
    assert links[0]["preview_url"] == url


def test_normalize_material_card_strips_placeholder_link() -> None:
    card = _normalize_material_card(
        {
            "kind": "article",
            "title": "月饼",
            "text": "传统月饼",
            "link": "https://example.com/mooncake",
            "media_links": [
                {
                    "type": "photo",
                    "thumb": "https://example.com/mooncake_preview.jpg",
                    "preview_url": "https://example.com/mooncake_preview.jpg",
                }
            ],
        }
    )
    assert card["link"] == ""
    assert card["media_links"] == []


def test_extract_media_urls_from_html_og_image() -> None:
    html = """
    <html><head>
      <meta property="og:image" content="https://cdn.example.org/photo.jpg" />
    </head><body></body></html>
    """
    links = extract_media_urls_from_html(html, "https://news.example.org/post")
    assert links[0]["preview_url"] == "https://cdn.example.org/photo.jpg"
