from integrated_agent.runtimes.matrix.compose.intel import (
    _normalize_material_card,
    _pad_plan_tasks,
    _tasks_from_plan,
    _tweet_cards_from_materials,
)


def test_tasks_from_plan_respects_post_count() -> None:
    raw = [{"task_id": f"m{i}", "goal": f"主题{i}"} for i in range(1, 6)]
    tasks = _tasks_from_plan(raw, max_tasks=3)
    assert len(tasks) == 3
    assert tasks[0]["goal"] == "主题1"


def test_pad_plan_tasks_fills_to_post_count() -> None:
    tasks = _pad_plan_tasks([{"task_id": "m1", "goal": "已有任务"}], post_count=3, user_instruction="秋季上新")
    assert len(tasks) == 3
    assert tasks[0]["goal"] == "已有任务"
    assert "角度 3" in tasks[2]["goal"]


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
