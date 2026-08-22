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
    assert any(name == "matrix-compose-route-intent" for name, _ in model.agent_sessions)


def test_accounts_default_snapshot_binds() -> None:
    snapshot = bind_snapshot(
        data_root=DATA_ROOT, account_key="default", scenario="compose"
    )
    assert snapshot.account is not None
    assert snapshot.platform.platform_key == "x-twitter"
