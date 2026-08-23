from __future__ import annotations

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.compose import run_compose
from integrated_agent.runtimes.matrix.host.models import MatrixTaskRequest
from integrated_agent.runtimes.matrix.host.snapshots import bind_snapshot
from tests.fakes import ScriptedMatrixModel, install_compose_ask


DATA_ROOT = PROJECT_ROOT / "data/matrix"
STATUS_A = "https://x.com/demo/status/1234567890123456789"


def _request(task_id: str, text: str, **kwargs) -> MatrixTaskRequest:
    return MatrixTaskRequest(
        task_id=task_id,
        text=text,
        session_id="flow-session",
        scenario="compose",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_m1_unknown_account_fails(tmp_path, monkeypatch) -> None:
    install_compose_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_compose(
        _request("bad-acc", "写帖", account_key="not-an-account"),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "bad-acc",
    )
    assert run["status"] == "failed"
    assert run["drafts"] == []


@pytest.mark.asyncio
async def test_m2_theme_uses_route_intent(tmp_path, monkeypatch) -> None:
    model = ScriptedMatrixModel()
    install_compose_ask(monkeypatch, model)
    run = await run_compose(
        _request("m2-theme", "为秋季上新写预热稿"),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "m2-theme",
    )
    assert run["status"] == "completed"
    assert run["intent"] == "compose"
    assert run["source_kind"] == "none"
    assert len(run["drafts"]) == 1
    assert any(name == "matrix-compose-route-intent" for name, _ in model.agent_sessions)


@pytest.mark.asyncio
async def test_compose_post_count_fans_out_drafts(tmp_path, monkeypatch) -> None:
    model = ScriptedMatrixModel()
    install_compose_ask(monkeypatch, model)
    run = await run_compose(
        _request("m2-multi", "为七夕写三条推文", post_count=3),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "m2-multi",
    )
    assert run["status"] == "completed"
    assert len(run["drafts"]) == 3
    assert {item["draft_key"] for item in run["drafts"]} == {"d1", "d2", "d3"}
    assert sum(1 for name, _ in model.agent_sessions if name == "matrix-compose-original-draft") == 3


@pytest.mark.asyncio
async def test_compose_intel_collects_media_and_evidence(tmp_path, monkeypatch) -> None:
    model = ScriptedMatrixModel()
    install_compose_ask(monkeypatch, model)

    async def _skip_host_fetch(**_kwargs):
        return None

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.compose.intel._host_fetch_tweet_material",
        _skip_host_fetch,
    )
    run = await run_compose(
        _request("m2-intel-media", "写两条推文", post_count=2),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "m2-intel-media",
    )
    assert run["status"] == "completed"
    material_cards = [item for item in run.get("material_cards") or [] if isinstance(item, dict)]
    assert material_cards
    assert any(item.get("media_links") for item in material_cards)
    evidence = [item for item in run.get("evidence") or [] if isinstance(item, dict)]
    assert any(item.get("media_links") for item in evidence)
    assert len(material_cards) == 2
    assert sum(1 for name, _ in model.agent_sessions if name == "matrix-compose-intel-task") == 2


def test_rewrite_plan_splits_long_source_across_drafts() -> None:
    from integrated_agent.runtimes.matrix.compose.rewritetweet import (
        _plan_rewrite_work_items,
        _split_source_for_drafts,
    )

    source = "第一句。第二句！第三句？第四句。"
    slices, mode = _split_source_for_drafts(source, 2)
    assert mode == "split"
    assert len(slices) == 2
    assert slices[0]
    assert slices[1]

    work_items = _plan_rewrite_work_items(
        post_count=2,
        source_text=source,
        source_post={"tweet_id": "1234567890123456789", "text": source},
        related_tweet_cards=[
            {
                "tweet_id": "9876543210987654321",
                "screen_name": "ref",
                "text": "参考推文",
                "media": [
                    {
                        "type": "photo",
                        "thumb": "https://pic.example.com/ref.jpg",
                        "width": 800,
                        "height": 600,
                    }
                ],
            }
        ],
        offered_media=[{"media_key": "m1", "kind": "photo"}],
        media_catalog=[
            {
                "media_key": "m1",
                "kind": "photo",
                "preview_url": "https://pic.example.com/src.jpg",
            }
        ],
    )
    assert len(work_items) == 2
    assert work_items[0]["allocated_source_text"]
    assert work_items[1]["allocated_source_text"]
    assert work_items[0]["allocated_source_text"] != work_items[1]["allocated_source_text"]
    assert work_items[0]["reuse_media"] is True
    assert work_items[0]["media_catalog"][0]["preview_url"] == "https://pic.example.com/src.jpg"
    assert work_items[0]["reference_tweet"]["offered_media"]
    assert work_items[1]["reference_tweet"]["media_catalog"][0]["preview_url"] == "https://pic.example.com/ref.jpg"
    assert work_items[1]["reuse_media"] is True


def test_reference_tweet_from_card_includes_media() -> None:
    from integrated_agent.runtimes.matrix.compose.rewritetweet import _reference_tweet_from_card

    ref = _reference_tweet_from_card(
        {
            "tweet_id": "9876543210987654321",
            "screen_name": "ref",
            "text": "带图参考",
            "media": [{"type": "photo", "thumb": "https://pic.example.com/ref.jpg"}],
        }
    )
    assert ref["offered_media"] == [{"media_key": "m1", "kind": "photo"}]
    assert ref["media_catalog"][0]["preview_url"] == "https://pic.example.com/ref.jpg"


def test_rewrite_plan_pads_short_source_for_multi_drafts() -> None:
    from integrated_agent.runtimes.matrix.compose.rewritetweet import _split_source_for_drafts

    slices, mode = _split_source_for_drafts("一句短文", 3)
    assert mode == "full"
    assert slices == ["一句短文", "一句短文", "一句短文"]


@pytest.mark.asyncio
async def test_rewrite_hydrates_source_post_from_cleaned_tweets() -> None:
    from integrated_agent.runtimes.matrix.compose.rewritetweet import (
        _hydrate_rewrite_upstream,
        _rewrite_has_source_card,
    )

    upstream = {
        "tool_result_cleaned": [
            {
                "kind": "tweet",
                "tweet_id": "1234567890123456789",
                "text": "原帖正文",
                "screen_name": "demo",
                "ok": True,
            }
        ]
    }
    hydrated = _hydrate_rewrite_upstream(upstream)
    assert hydrated["source_post"]["tweet_id"] == "1234567890123456789"
    assert hydrated["source_post"]["text"] == "原帖正文"
    rewrite_ctx = {
        "source_post": hydrated["source_post"],
        "tool_result_cleaned": hydrated["tool_result_cleaned"],
    }
    assert _rewrite_has_source_card(rewrite_ctx)


@pytest.mark.asyncio
async def test_m2_rewrite_from_route_intent(tmp_path, monkeypatch) -> None:
    model = ScriptedMatrixModel(
        route_intent_out={
            "reason": "有帖链接且要求改口吻。",
            "intent": "rewrite",
            "source_kind": "url",
            "source_anchor": "1234567890123456789",
            "user_instruction": "改成我们口吻",
            "confidence": "high",
        }
    )
    install_compose_ask(monkeypatch, model)
    run = await run_compose(
        _request("m2-mix", f"改成我们口吻 {STATUS_A}"),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "m2-mix",
    )
    assert run["intent"] == "rewrite"
    assert run["source_anchor"] == "1234567890123456789"
    assert len(run["drafts"]) == 1
    assert run["drafts"][0]["text"]
    assert run["drafts"][0]["media"][0]["preview_url"] == "https://pic.example.com/source.jpg"
    assert run["evidence"][0]["media_links"][0]["preview_url"] == "https://pic.example.com/source.jpg"
    assert any(name == "matrix-compose-route-intent" for name, _ in model.agent_sessions)
    assert any(name == "matrix-compose-rewrite-draft" for name, _ in model.agent_sessions)


@pytest.mark.asyncio
async def test_rewrite_post_count_fans_out_drafts(tmp_path, monkeypatch) -> None:
    model = ScriptedMatrixModel(
        route_intent_out={
            "reason": "有帖链接且要求改口吻。",
            "intent": "rewrite",
            "source_kind": "url",
            "source_anchor": "1234567890123456789",
            "user_instruction": "改成我们口吻",
            "confidence": "high",
        }
    )
    install_compose_ask(monkeypatch, model)
    run = await run_compose(
        _request(
            "m2-rewrite-multi",
            f"改成我们口吻 {STATUS_A}",
            post_count=2,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "m2-rewrite-multi",
    )
    assert run["intent"] == "rewrite"
    assert len(run["drafts"]) == 2
    assert {item["draft_key"] for item in run["drafts"]} == {"d1", "d2"}
    assert sum(1 for name, _ in model.agent_sessions if name == "matrix-compose-rewrite-draft") == 2


@pytest.mark.asyncio
async def test_rewrite_preserves_source_media_in_draft(tmp_path, monkeypatch) -> None:
    from integrated_agent.runtimes.matrix.compose.rewritetweet import (
        _build_rewrite_media_catalog,
        _resolve_draft_media,
    )

    offered, catalog, _ = _build_rewrite_media_catalog(
        [
            {
                "kind": "photo",
                "preview_url": "https://pic.example.com/a.jpg",
                "width": 1200,
                "height": 675,
            }
        ],
        None,
    )
    assert offered == [{"media_key": "m1", "kind": "photo", "width": 1200, "height": 675}]
    text, media = _resolve_draft_media(
        "我们口吻版 [[media:m1]]",
        media_catalog=catalog,
        default_reuse=True,
    )
    assert text == "我们口吻版"
    assert media[0]["preview_url"] == "https://pic.example.com/a.jpg"


def test_rewrite_draft_media_cards_match_gated_draft() -> None:
    from integrated_agent.runtimes.matrix.compose.rewritetweet import (
        _normalize_rewrite_draft,
    )
    from integrated_agent.runtimes.matrix.host.models import GatedDraft

    draft = _normalize_rewrite_draft(
        draft_key="d1",
        draft_text="hello",
        rationale="r",
        platform_key="x-twitter",
        media=[
            {
                "media_key": "m1",
                "kind": "photo",
                "thumb": "https://pic.example.com/a.jpg",
            }
        ],
    )
    gated = GatedDraft.model_validate(draft)
    assert gated.media[0].preview_url == "https://pic.example.com/a.jpg"


def test_append_evidence_includes_ruling_and_media_links() -> None:
    from integrated_agent.runtimes.matrix.compose.branch_hold import (
        _append_evidence,
        _media_links_from_raw,
    )

    cards: list[dict] = []
    _append_evidence(
        cards,
        ref_id="e1",
        kind="source_post",
        title="demo",
        text="body",
        link="https://x.com/demo/status/1",
        branch="rewrite",
        media_links=_media_links_from_raw(
            [{"type": "photo", "thumb": "https://pic.example.com/x.jpg"}]
        ),
    )
    assert cards[0]["ruling"] == "body"
    assert cards[0]["media_links"][0]["preview_url"] == "https://pic.example.com/x.jpg"


def test_accounts_default_snapshot_binds() -> None:
    snapshot = bind_snapshot(
        data_root=DATA_ROOT, account_key="default", scenario="compose"
    )
    assert snapshot.account is not None
    assert snapshot.platform.platform_key == "x-twitter"
