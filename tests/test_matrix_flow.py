from __future__ import annotations

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from integrated_agent.runtimes.matrix.analysis.workflows.compose_flow import run_compose
from integrated_agent.runtimes.matrix.analysis.workflows.reply_flow import run_reply
from integrated_agent.runtimes.matrix.models import MatrixTaskRequest
from tests.fakes import DEMO_REPLY_COMMENTS, install_scripted_ask


DATA_ROOT = PROJECT_ROOT / "data/matrix"


@pytest.mark.asyncio
async def test_t07_compose_yields_one_twitter_draft(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_compose(
        MatrixTaskRequest(
            task_id="t07",
            text="为秋季上新写预热稿",
            scenario="compose",
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t07",
    )
    assert len(run["drafts"]) == 1
    assert run["drafts"][0]["platform_key"] == "x-twitter"
    assert run["task_type"] == "compose_post"


@pytest.mark.asyncio
async def test_compose_caps_work_items_at_platform_max_posts(tmp_path, monkeypatch) -> None:
    install_scripted_ask(
        monkeypatch, ScriptedMatrixModel(compose_work_item_count=11)
    )
    run = await run_compose(
        MatrixTaskRequest(
            task_id="t07-cap",
            text="为秋季上新写一组预热推文",
            scenario="compose",
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t07-cap",
    )
    assert len(run["drafts"]) == 10
    assert "truncated_to_max_posts:10" in run["limitations"]


@pytest.mark.asyncio
async def test_compose_honors_requested_post_count(tmp_path, monkeypatch) -> None:
    install_scripted_ask(
        monkeypatch, ScriptedMatrixModel(compose_work_item_count=8)
    )
    run = await run_compose(
        MatrixTaskRequest(
            task_id="t07-count",
            text="为秋季上新写一组预热推文",
            scenario="compose",
            post_count=3,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t07-count",
    )
    assert len(run["drafts"]) == 3
    assert "truncated_to_max_posts:3" in run["limitations"]


@pytest.mark.asyncio
async def test_t08_attack_comment_is_skipped_empty(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t08",
            text="处理这组评论",
            scenario="reply",
            comments=DEMO_REPLY_COMMENTS,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t08",
    )
    skipped = [item for item in run["drafts"] if item["degrade_op"] == "skip"]
    assert len(run["drafts"]) == 3
    assert skipped
    assert all(item["text"] == "" for item in skipped)
    assert run["task_type"] == "reply_comment"


@pytest.mark.asyncio
async def test_reply_honors_requested_reply_count(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t08-count",
            text="处理这组评论",
            scenario="reply",
            comments=DEMO_REPLY_COMMENTS,
            reply_count=1,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t08-count",
    )
    assert len(run["drafts"]) == 1
    assert run["drafts"][0]["source_comment_key"] in {"c1", "c2", "c3"}
    assert "truncated_to_reply_count:1" in run["limitations"]


@pytest.mark.asyncio
async def test_reply_without_thread_replies_to_user_text(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t08-text",
            text="成分表在哪看？有没有官方说明？",
            scenario="reply",
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t08-text",
    )
    assert len(run["drafts"]) == 1
    assert run["drafts"][0]["source_comment_key"] == "c1"
    assert run["drafts"][0]["degrade_op"] != "skip"
    assert run["drafts"][0]["text"]


@pytest.mark.asyncio
async def test_reply_count_on_single_comment_yields_variants(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t08-variants",
            text="成分表在哪看？有没有官方说明？",
            scenario="reply",
            reply_count=3,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t08-variants",
    )
    assert len(run["drafts"]) == 3
    assert {item["source_comment_key"] for item in run["drafts"]} == {"c1"}
    assert all(item["text"] for item in run["drafts"])


@pytest.mark.asyncio
async def test_t09_one_gate_failure_keeps_successful_item(tmp_path, monkeypatch) -> None:
    install_scripted_ask(
        monkeypatch, ScriptedMatrixModel(evidence_overrides={"rw1": ["no-such-ref"]})
    )
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t09",
            text="处理这组评论",
            scenario="reply",
            comments=DEMO_REPLY_COMMENTS,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t09",
    )
    assert run["status"] == "partial"
    statuses = {item["status"] for item in run["drafts"]}
    assert "ready" in statuses or "degraded" in statuses
    assert "failed" in statuses or "skipped" in statuses


@pytest.mark.asyncio
async def test_t10_review_cannot_lift_skip(tmp_path, monkeypatch) -> None:
    install_scripted_ask(
        monkeypatch, ScriptedMatrixModel(review_lift_skip=True)
    )
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t10",
            text="处理这组评论",
            scenario="reply",
            comments=DEMO_REPLY_COMMENTS,
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t10",
    )
    skipped = [item for item in run["drafts"] if item["decision"] == "skip"]
    assert skipped
    assert all(item["text"] == "" for item in skipped)
    assert all(item["degrade_op"] == "skip" for item in skipped)
