from __future__ import annotations

from integrated_agent.runtimes.matrix.compose.brief import _validate_and_fit_brief
from integrated_agent.runtimes.matrix.compose.rewrite_plan import (
    build_rewrite_plan_card,
    build_rewrite_work_item,
)
from integrated_agent.runtimes.matrix.host.models import BriefOut, Requirement, WorkItem
from integrated_agent.runtimes.matrix.host.snapshots import TWITTER_PLATFORM_KEY


def test_validate_and_fit_brief_expands_to_post_count() -> None:
    brief = BriefOut(
        normalized_brief="秋季上新",
        requirements=[Requirement(requirement_id="r1", description="预热")],
        work_items=[
            WorkItem(
                work_item_id="w1",
                kind="compose_post",
                requirement_ids=["r1"],
                platform_key=TWITTER_PLATFORM_KEY,
                source_comment_key=None,
                goal="写预热稿",
                talking_points=["上新"],
                claim_types=["format"],
            )
        ],
    )
    fitted, limitations = _validate_and_fit_brief(
        brief,
        post_count=3,
        platform_key=TWITTER_PLATFORM_KEY,
    )
    assert len(fitted.work_items) == 3
    assert "expanded_to_post_count:3" in limitations


def test_build_rewrite_plan_card_reuses_media() -> None:
    plan = build_rewrite_plan_card(
        source_media=[{"type": "photo", "thumb": "https://pic.example/x.jpg"}],
        source_post={"text": "原文"},
        offered_cta_urls=["https://matrix.demo/official"],
        user_instruction="改成我们口吻",
        limitations=[],
    )
    assert plan["media_choice"] == "reuse_source_media"
    assert plan["cta_url"] == ""


def test_build_rewrite_plan_card_sets_cta_when_requested() -> None:
    plan = build_rewrite_plan_card(
        source_media=[],
        source_post={"text": "原文"},
        offered_cta_urls=["https://matrix.demo/official"],
        user_instruction="改成我们口吻，带上官方渠道链接",
        limitations=[],
    )
    assert plan["media_choice"] == "none"
    assert plan["cta_url"] == "https://matrix.demo/official"


def test_build_rewrite_work_item_shape() -> None:
    item = build_rewrite_work_item(
        user_instruction="改写",
        source_text="第一句。第二句。",
        platform_key=TWITTER_PLATFORM_KEY,
        source_issues=[],
    )
    assert item.work_item_id == "rw1"
    assert item.kind == "compose_post"
    assert item.talking_points
