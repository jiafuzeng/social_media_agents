"""Compose ConstraintGate 专项：CTA / 媒体 / 增长话术 / 字数 / 近重复。"""

from __future__ import annotations

import pytest

from integrated_agent.runtimes.matrix.host.constraints import (
    AhoCorasickMatcher,
    apply_constraint_gate,
    collect_compose_issues,
    effective_text_length,
)


def test_effective_text_length_strips_media_and_counts_url_budget() -> None:
    text = "开头 [[media:m1]] 中间 https://matrix.demo/official 结尾"
    # media 剥掉；URL 按 23
    assert effective_text_length(text) == len("开头  中间 ") + 23 + len(" 结尾")


def test_collect_compose_rejects_growth_engagement() -> None:
    issues = collect_compose_issues(
        text="大家觉得怎么样，来聊聊",
        offered_cta_urls=[],
        offered_media_keys=[],
    )
    assert any(item.startswith("growth_engagement:") for item in issues)


def test_collect_compose_rejects_unknown_https_and_cta() -> None:
    issues = collect_compose_issues(
        text="详情 https://evil.example/x 或 [[cta:0]]",
        offered_cta_urls=[],
        offered_media_keys=[],
    )
    assert "unknown_https" in issues or "unknown_cta" in issues


def test_collect_compose_allows_offered_cta_token() -> None:
    issues = collect_compose_issues(
        text="关注系列，渠道 [[cta:0]]",
        offered_cta_urls=["https://matrix.demo/official"],
        offered_media_keys=[],
    )
    assert "unknown_cta" not in issues
    assert "unknown_https" not in issues


def test_collect_compose_rejects_unknown_media() -> None:
    issues = collect_compose_issues(
        text="配图 [[media:m9]]",
        offered_cta_urls=[],
        offered_media_keys=["m1"],
    )
    assert any(item.startswith("unknown_media:") for item in issues)


def test_collect_compose_near_duplicate_source() -> None:
    source = "这是一段足够长的原文用于检测近重复改写是否被判定为抄袭。" * 2
    issues = collect_compose_issues(
        text=source,
        offered_cta_urls=[],
        offered_media_keys=[],
        source_text=source,
    )
    assert "near_duplicate_source" in issues


@pytest.mark.asyncio
async def test_apply_constraint_gate_compose_pass() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="compose_post",
        platform_key="x-twitter",
        source_comment_key=None,
        text="秋季上新预热，详情见官方说明。关注后续更新。",
        rationale="ok",
        evidence_ids=["e1"],
        risk_flags=[],
        claim_types=["format"],
        reply_decision=None,
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher(["稳赚", "治愈"]),
        offered_refs=["e1"],
        offered_kbs=[],
        retrieval_state="hits",
        templates=[],
        offered_cta_urls=["https://matrix.demo/official"],
        offered_media_keys=[],
    )
    assert gated.status in {"ready", "degraded"}
    assert gated.degrade_op in {"pass", "rewrite_safe"}


@pytest.mark.asyncio
async def test_apply_constraint_gate_compose_forbidden_term() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="compose_post",
        platform_key="x-twitter",
        source_comment_key=None,
        text="这款产品保证治愈，稳赚不赔。",
        rationale="bad",
        evidence_ids=[],
        risk_flags=[],
        claim_types=["format"],
        reply_decision=None,
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher(["稳赚", "治愈"]),
        offered_refs=[],
        offered_kbs=[],
        retrieval_state="empty",
        templates=[
            {
                "template_key": "neutral-disclaimer",
                "text": "以官方说明为准。",
            }
        ],
        offered_cta_urls=[],
        offered_media_keys=[],
    )
    assert gated.degrade_op in {"template_fallback", "skip"}
    assert any(item.startswith("forbidden_term:") for item in gated.issues)


def test_resolve_draft_cta_expands_offered_url() -> None:
    from integrated_agent.runtimes.matrix.compose.draft_media import resolve_draft_cta

    text = resolve_draft_cta(
        "秋季上新，渠道 [[cta:0]]",
        offered_cta_urls=["https://matrix.demo/official"],
    )
    assert "[[cta:0]]" not in text
    assert "https://matrix.demo/official" in text


def test_resolve_draft_refs_strips_offered_ref_token() -> None:
    from integrated_agent.runtimes.matrix.compose.draft_media import resolve_draft_refs

    text = resolve_draft_refs("秋季上新[[ref:e1]]，详情见官方说明。")
    assert "[[ref:" not in text
    assert "秋季上新，详情见官方说明。" in text


@pytest.mark.asyncio
async def test_gate_strips_ref_tokens_when_offered() -> None:
    gated = await apply_constraint_gate(
        work_item_id="w1",
        kind="compose_post",
        platform_key="x-twitter",
        source_comment_key=None,
        text="秋季上新[[ref:e1]]，详情见官方说明。",
        rationale="ok",
        evidence_ids=["e1"],
        risk_flags=[],
        claim_types=["format"],
        reply_decision=None,
        proposed_degrade=None,
        max_chars=280,
        matcher=AhoCorasickMatcher([]),
        offered_refs=["e1"],
        offered_kbs=[],
        retrieval_state="hits",
        templates=[],
        offered_cta_urls=[],
        offered_media_keys=[],
    )
    assert gated.degrade_op == "pass"
    assert gated.evidence_ids == ["e1"]
    assert "[[ref:" not in gated.text
