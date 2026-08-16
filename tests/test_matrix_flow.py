from __future__ import annotations

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from integrated_agent.runtimes.matrix.analysis.workflows.compose_flow import run_compose
from integrated_agent.runtimes.matrix.analysis.workflows.reply_flow import run_reply
from integrated_agent.runtimes.matrix.models import MatrixTaskRequest
from tests.fakes import install_scripted_ask


DATA_ROOT = PROJECT_ROOT / "data/matrix"


@pytest.mark.asyncio
async def test_t07_two_platform_compose_yields_two_drafts(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_compose(
        MatrixTaskRequest(
            task_id="t07",
            text="为秋季上新写预热稿",
            scenario="compose",
            platform_keys=["x-twitter", "weibo"],
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t07",
    )
    assert len(run["drafts"]) == 2
    assert {item["platform_key"] for item in run["drafts"]} == {"x-twitter", "weibo"}
    assert run["task_type"] == "compose_post"


@pytest.mark.asyncio
async def test_t08_attack_comment_is_skipped_empty(tmp_path, monkeypatch) -> None:
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    run = await run_reply(
        MatrixTaskRequest(
            task_id="t08",
            text="处理 demo 线程",
            scenario="reply",
            thread_key="demo-1",
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
async def test_t09_one_gate_failure_keeps_successful_item(tmp_path, monkeypatch) -> None:
    install_scripted_ask(
        monkeypatch, ScriptedMatrixModel(evidence_overrides={"w1": ["no-such-ref"]})
    )
    run = await run_compose(
        MatrixTaskRequest(
            task_id="t09",
            text="两平台预热",
            scenario="compose",
            platform_keys=["x-twitter", "weibo"],
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
            text="处理 demo 线程",
            scenario="reply",
            thread_key="demo-1",
        ),
        data_root=DATA_ROOT,
        output_directory=tmp_path / "t10",
    )
    skipped = [item for item in run["drafts"] if item["decision"] == "skip"]
    assert skipped
    assert all(item["text"] == "" for item in skipped)
    assert all(item["degrade_op"] == "skip" for item in skipped)
