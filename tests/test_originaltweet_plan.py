from integrated_agent.runtimes.matrix.compose.originaltweet import (
    _align_material_cards,
    _compose_media_bundle,
)


def test_align_material_cards_trims_extra_cards() -> None:
    cards = [{"tweet_id": str(i), "text": f"t{i}"} for i in range(1, 5)]
    aligned, mode = _align_material_cards(cards, 2)
    assert mode == "trimmed"
    assert len(aligned) == 2
    assert aligned[0]["tweet_id"] == "1"
    assert aligned[1]["tweet_id"] == "2"


def test_align_material_cards_pads_missing_cards() -> None:
    cards = [
        {
            "tweet_id": "1",
            "text": "a",
            "media_links": [
                {
                    "type": "photo",
                    "thumb": "https://pbs.twimg.com/media/a.jpg",
                    "preview_url": "https://pbs.twimg.com/media/a.jpg",
                }
            ],
        },
        {"tweet_id": "2", "text": "b", "media_links": []},
    ]
    aligned, mode = _align_material_cards(cards, 4)
    assert mode == "padded"
    assert len(aligned) == 4
    assert aligned[2]["tweet_id"] == "1"
    assert aligned[3]["tweet_id"] == "2"
    # 补齐条目不复用配图
    assert aligned[0]["media_links"]
    assert aligned[2]["media_links"] == []
    assert aligned[2]["media"] == []


def test_compose_media_bundle_from_card() -> None:
    offered, catalog = _compose_media_bundle(
        {
            "media_links": [
                {
                    "type": "photo",
                    "thumb": "https://pbs.twimg.com/media/x.jpg",
                    "preview_url": "https://pbs.twimg.com/media/x.jpg",
                }
            ]
        }
    )
    assert offered == [{"media_key": "m1"}]
    assert catalog[0]["preview_url"] == "https://pbs.twimg.com/media/x.jpg"
