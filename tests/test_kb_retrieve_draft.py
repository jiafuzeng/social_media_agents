from __future__ import annotations

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.compose.draft_gate import retrieve_and_gate_compose_draft
from integrated_agent.runtimes.matrix.host.snapshots import (
    TWITTER_PLATFORM_KEY,
    bind_snapshot,
)
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog
from integrated_agent.runtimes.matrix.host.models import ComposeDraftOut, WorkItem


DATA_ROOT = PROJECT_ROOT / "data/matrix"


class _EmptyKb:
    async def retrieve_draft_cards(self, *args, **kwargs):
        return []


class _HitKb:
    async def retrieve_draft_cards(self, *args, **kwargs):
        return [
            {
                "kb_id": "k1",
                "chunk_id": "c1",
                "doc_id": "d1",
                "text": "七天无理由退款需提供凭证。",
                "window": None,
                "header_path": None,
                "embedding_profile_id": "bge-m3",
            }
        ]


class _BoomKb:
    async def retrieve_draft_cards(self, *args, **kwargs):
        raise RuntimeError("embed down")


def _item(*, claim_types: list[str]) -> WorkItem:
    return WorkItem(
        work_item_id="w1",
        kind="compose_post",
        requirement_ids=["r1"],
        platform_key=TWITTER_PLATFORM_KEY,
        goal="写预热稿",
        talking_points=["退款"],
        claim_types=claim_types,
    )


def _draft(*, text: str, claim_types: list[str], evidence_ids: list[str] | None = None):
    async def draft_once(*, work_item: dict, info: dict, repair: dict | None = None):
        del work_item, repair
        assert "offered_kbs" in info
        return ComposeDraftOut(
            work_item_id="w1",
            stance_assessment="same-response",
            claim_types=claim_types,
            risk_flags=[],
            draft_text=text,
            rationale="测试写稿",
            evidence_ids=list(evidence_ids or []),
        )

    return draft_once


@pytest.mark.asyncio
async def test_empty_kb_still_writes() -> None:
    snapshot = bind_snapshot(data_root=DATA_ROOT, account_key="default", scenario="compose")
    gated, cards, notes = await retrieve_and_gate_compose_draft(
        work_item=_item(claim_types=["format"]),
        snapshot=snapshot,
        draft_once=_draft(text="秋季上新已公布成分。", claim_types=["format"]),
        trace=TraceLog("t-empty-kb", "exec"),
        knowledge=_EmptyKb(),
        user_id="user-a",
        embedding_profile_id="bge-m3",
    )
    assert cards == []
    assert notes == []
    assert gated.status == "ready"
    assert gated.degrade_op == "pass"


@pytest.mark.asyncio
async def test_kb_retrieve_failed_does_not_skip() -> None:
    snapshot = bind_snapshot(data_root=DATA_ROOT, account_key="default", scenario="compose")
    gated, _cards, notes = await retrieve_and_gate_compose_draft(
        work_item=_item(claim_types=["format"]),
        snapshot=snapshot,
        draft_once=_draft(text="秋季上新已公布成分。", claim_types=["format"]),
        trace=TraceLog("t-kb-fail", "exec"),
        knowledge=_BoomKb(),
        user_id="user-a",
        embedding_profile_id="bge-m3",
    )
    assert notes == ["kb_retrieve_failed"]
    assert gated.status == "ready"


@pytest.mark.asyncio
async def test_kb_only_hit_cannot_pass_efficacy_empty_cases() -> None:
    snapshot = bind_snapshot(data_root=DATA_ROOT, account_key="default", scenario="compose")
    gated, _cards, notes = await retrieve_and_gate_compose_draft(
        work_item=_item(claim_types=["efficacy"]),
        snapshot=snapshot,
        draft_once=_draft(
            text="根据手册[[kb:k1]]，成分温和。",
            claim_types=["efficacy"],
            evidence_ids=["k1"],
        ),
        trace=TraceLog("t-kb-only", "exec"),
        knowledge=_HitKb(),
        user_id="user-a",
        embedding_profile_id="bge-m3",
    )
    assert notes == []
    assert gated.degrade_op != "pass"
    assert "missing_ref_on_empty_rag" in gated.issues
    assert gated.kb_ids == ["k1"]
