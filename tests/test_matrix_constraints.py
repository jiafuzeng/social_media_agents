from __future__ import annotations

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.constraints import (
    AhoCorasickMatcher,
    apply_constraint_gate,
    collect_issues,
)
from integrated_agent.runtimes.matrix.analysis.host import (
    BriefValidationError,
    parse_structured,
    sanitize_brief,
    validate_brief,
)
from integrated_agent.runtimes.matrix.analysis.snapshots import (
    SnapshotError,
    bind_snapshot,
)
from integrated_agent.runtimes.matrix.models import (
    ComposeBriefOut,
    Requirement,
    ReviewOut,
    WorkItem,
)


DATA_ROOT = PROJECT_ROOT / "data/matrix"


def test_t01_unknown_platform_key_fails_snapshot() -> None:
    with pytest.raises(SnapshotError, match="unknown platform_key"):
        bind_snapshot(
            data_root=DATA_ROOT,
            account_key="default",
            brand_key="default",
            platform_keys=["not-a-platform"],
            scenario="compose",
        )


@pytest.mark.asyncio
async def test_t02_forbidden_term_does_not_pass() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="compose_post",
        platform_key="x-twitter",
        source_comment_key=None,
        text="这款精华能治愈失眠",
        rationale="演示",
        evidence_ids=[],
        risk_flags=[],
        claim_types=["format"],
        reply_decision=None,
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher(["治愈", "稳赚"]),
        offered_refs=[],
        retrieval_state="empty",
        templates=[{"template_key": "neutral-disclaimer", "text": "请以官方说明为准。", "claim_types": ["efficacy"]}],
    )
    assert gated.degrade_op != "pass"
    assert any(issue.startswith("forbidden_term:治愈") for issue in gated.issues)


@pytest.mark.asyncio
async def test_t03_skip_with_text_is_forced_empty() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="reply_comment",
        platform_key="x-twitter",
        source_comment_key="c2",
        text="请冷静一下我们还是朋友",
        rationale="攻击帖",
        evidence_ids=[],
        risk_flags=["attack"],
        claim_types=["harassment"],
        reply_decision="skip",
        proposed_degrade="skip",
        max_chars=280,
        matcher=AhoCorasickMatcher(["治愈"]),
        offered_refs=[],
        retrieval_state="empty",
        templates=[],
    )
    assert gated.degrade_op == "skip"
    assert gated.text == ""
    assert gated.status == "skipped"


@pytest.mark.asyncio
async def test_t04_unknown_evidence_id_skips_or_fails() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="compose_post",
        platform_key="x-twitter",
        source_comment_key=None,
        text="秋季上新见官方说明。",
        rationale="演示",
        evidence_ids=["no-such-ref"],
        risk_flags=[],
        claim_types=["format"],
        reply_decision=None,
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher(["治愈"]),
        offered_refs=["e12"],
        retrieval_state="hits",
        templates=[],
    )
    assert gated.degrade_op == "skip"
    assert gated.status in {"failed", "skipped"}
    assert "unknown_ref" in gated.issues


@pytest.mark.asyncio
async def test_t05_empty_rag_efficacy_cannot_pass() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="compose_post",
        platform_key="x-twitter",
        source_comment_key=None,
        text="成分温和，日常护理更轻松。",
        rationale="功效主张",
        evidence_ids=[],
        risk_flags=[],
        claim_types=["efficacy"],
        reply_decision=None,
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher(["治愈"]),
        offered_refs=[],
        retrieval_state="empty",
        templates=[],
    )
    assert gated.degrade_op != "pass"
    assert "missing_ref_on_empty_rag" in gated.issues


def test_t06_brief_missing_requirement_fails() -> None:
    snapshot = bind_snapshot(
        data_root=DATA_ROOT,
        account_key="default",
        brand_key="default",
        platform_keys=["x-twitter"],
        scenario="compose",
    )
    brief = ComposeBriefOut(
        normalized_brief="预热",
        requirements=[Requirement(requirement_id="r1", description="矩阵预热")],
        work_items=[
            WorkItem(
                work_item_id="w1",
                kind="compose_post",
                requirement_ids=["r-other"],
                platform_key="x-twitter",
                goal="写稿",
                talking_points=["上新"],
                claim_types=["format"],
            )
        ],
    )
    with pytest.raises(BriefValidationError):
        validate_brief(brief, snapshot=snapshot, expected_kind="compose_post")


def test_sanitize_brief_drops_template_key_from_claim_types() -> None:
    snapshot = bind_snapshot(
        data_root=DATA_ROOT,
        account_key="default",
        brand_key="default",
        platform_keys=["x-twitter"],
        scenario="compose",
    )
    brief = ComposeBriefOut(
        normalized_brief="预热",
        requirements=[Requirement(requirement_id="r1", description="矩阵预热")],
        work_items=[
            WorkItem(
                work_item_id="w1",
                kind="compose_post",
                requirement_ids=["r1"],
                platform_key="x-twitter",
                goal="写稿",
                talking_points=["上新"],
                claim_types=["neutral-disclaimer", "format"],
            )
        ],
    )
    cleaned = sanitize_brief(
        brief, snapshot=snapshot, expected_kind="compose_post"
    )
    validate_brief(cleaned, snapshot=snapshot, expected_kind="compose_post")
    assert cleaned.work_items[0].claim_types == ["format"]


def test_collect_issues_over_limit() -> None:
    issues = collect_issues(
        text="x" * 300,
        kind="compose_post",
        reply_decision=None,
        max_chars=280,
        matcher=AhoCorasickMatcher([]),
        evidence_ids=[],
        offered_refs=[],
        claim_types=[],
        retrieval_state="empty",
    )
    assert "over_limit" in issues


@pytest.mark.asyncio
async def test_skip_decision_is_not_marked_pass() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="reply_comment",
        platform_key="x-twitter",
        source_comment_key="c2",
        text="",
        rationale="人身攻击不回",
        evidence_ids=[],
        risk_flags=["attack"],
        claim_types=["harassment"],
        reply_decision="skip",
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher(["治愈"]),
        offered_refs=[],
        retrieval_state="empty",
        templates=[],
    )
    assert gated.degrade_op == "skip"
    assert gated.status == "skipped"
    assert gated.decision == "skip"
    assert gated.text == ""


def test_parse_structured_strips_unknown_review_fields() -> None:
    review = parse_structured(
        ReviewOut,
        {
            "item_verdicts": [
                {
                    "draft_key": "d-wi2",
                    "verdict": "通过",
                    "notes": "",
                    "reasoning": "keep skip",
                }
            ],
            "package_summary": "口径已对齐。",
            "limitations": "无额外限制",
            "scenario": "reply",
        },
    )
    assert isinstance(review, ReviewOut)
    assert review.item_verdicts[0].verdict == "accept"
    assert review.item_verdicts[0].notes is None
    assert review.limitations == ["无额外限制"]

