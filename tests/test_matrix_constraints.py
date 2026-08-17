from __future__ import annotations

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.constraints import (
    AhoCorasickMatcher,
    apply_constraint_gate,
    collect_issues,
)
from integrated_agent.runtimes.matrix.analysis.snapshots import (
    SnapshotError,
    bind_snapshot,
    list_account_catalog,
    list_interaction_catalog,
    merged_forbidden_topics,
)


DATA_ROOT = PROJECT_ROOT / "data/matrix"


def test_t01_unknown_account_key_fails_snapshot() -> None:
    with pytest.raises(SnapshotError, match="unknown account_key"):
        bind_snapshot(
            data_root=DATA_ROOT,
            account_key="not-an-account",
            scenario="compose",
        )


def test_unknown_interaction_key_fails_snapshot() -> None:
    with pytest.raises(SnapshotError, match="unknown interaction_key"):
        bind_snapshot(
            data_root=DATA_ROOT,
            interaction_key="not-a-rule",
            scenario="reply",
            thread_key="demo-1",
        )


def test_twitter_platform_caps_posts_at_ten() -> None:
    snapshot = bind_snapshot(
        data_root=DATA_ROOT,
        account_key="default",
        scenario="compose",
    )
    assert snapshot.platform.max_posts == 10
    assert snapshot.platform.max_chars == 280
    assert snapshot.account.background
    assert snapshot.account.goals


def test_account_catalog_has_compose_personas() -> None:
    cards = list_account_catalog(DATA_ROOT)
    assert len(cards) == 8
    keys = {item.account_key for item in cards}
    assert keys == {
        "default",
        "indie-hacker",
        "community-host",
        "science-writer",
        "b2b-pm",
        "neighborhood-host",
        "career-editor",
        "oss-devrel",
    }
    for item in cards:
        assert "reply_stance" not in item.model_dump()


def test_interaction_catalog_is_separate_from_accounts() -> None:
    cards = list_interaction_catalog(DATA_ROOT)
    assert {item.interaction_key for item in cards} == {
        "help-first",
        "cool-down",
        "strict-skip",
        "support-handoff",
    }
    help_first = next(item for item in cards if item.interaction_key == "help-first")
    assert help_first.skip_guidance
    assert help_first.guardrail_keys == ["default"]
    assert help_first.term_list_keys == ["baseline"]


def test_bind_merges_account_guardrails() -> None:
    maker = bind_snapshot(
        data_root=DATA_ROOT,
        account_key="indie-hacker",
        scenario="compose",
    )
    assert maker.interaction is None
    assert [item.guardrail_key for item in maker.guardrails] == ["default", "maker"]
    assert "虚构融资或客户" in merged_forbidden_topics(maker.guardrails)
    assert maker.account.term_list_keys == ["baseline", "finance"]
    assert "财务自由" in maker.policy.terms
    assert "治愈" in maker.policy.terms
    assert maker.policy.term_list_id == "baseline+finance"


def test_bind_reply_uses_interaction_not_account() -> None:
    snapshot = bind_snapshot(
        data_root=DATA_ROOT,
        interaction_key="support-handoff",
        scenario="reply",
        thread_key="demo-1",
    )
    assert snapshot.account is None
    assert snapshot.interaction is not None
    assert snapshot.interaction.display_name == "客服分流"
    assert [item.guardrail_key for item in snapshot.guardrails] == ["default", "support"]
    topics = merged_forbidden_topics(snapshot.guardrails)
    assert "虚假疗效" in topics
    assert "收集密码或验证码" in topics
    assert snapshot.interaction.term_list_keys == ["baseline", "support-hard"]
    assert "把验证码发给我" in snapshot.policy.terms
    assert "把密码发给我" in snapshot.policy.terms
    assert "立即赔偿" in snapshot.policy.terms


def test_unknown_term_list_id_fails_resolve() -> None:
    from integrated_agent.runtimes.matrix.analysis.snapshots import resolve_term_lists

    with pytest.raises(SnapshotError, match="unknown term_list_id"):
        resolve_term_lists(["no-such-pack"], {})


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

