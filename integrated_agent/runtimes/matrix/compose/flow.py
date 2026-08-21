"""写帖业务 Flow：M1 Snapshot → M2 Route → compose|rewrite|PACKAGE。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agently import TriggerFlow

from integrated_agent.runtimes.matrix.host.models import MatrixTaskRequest
from integrated_agent.runtimes.matrix.host.snapshots import SnapshotError, bind_snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog, save_run
from .branch_hold import compose_branch_hold
from .init import compose_init
from .package import compose_package
from .route import compose_route


PIPELINE_VERSION = "matrix-compose-v1"

COMPOSE_FLOW = TriggerFlow(name="matrix-compose-v1")
COMPOSE_FLOW.to(compose_init).to(compose_route)
COMPOSE_FLOW.when("compose").to(compose_branch_hold)
COMPOSE_FLOW.when("rewrite").to(compose_branch_hold)
COMPOSE_FLOW.when("PACKAGE").to(compose_package)


def _failed_run(
    request: MatrixTaskRequest,
    *,
    summary: str,
    limitations: list[str],
    snapshot_id: str = "",
    execution_id: str = "",
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": request.task_id,
        "execution_id": execution_id,
        "status": "failed",
        "intent": None,
        "task_type": "compose_post",
        "snapshot_id": snapshot_id,
        "pipeline_version": PIPELINE_VERSION,
        "brief": None,
        "drafts": [],
        "summary": summary,
        "limitations": limitations,
        "evidence": [],
        "events": events or [],
    }


async def run_compose(
    request: MatrixTaskRequest,
    *,
    data_root: Path,
    output_directory: Path,
    max_concurrency: int = 10,
    knowledge: Any | None = None,
    fetch_tweets: Any | None = None,
) -> dict[str, Any]:
    del knowledge, fetch_tweets
    try:
        snapshot = bind_snapshot(
            data_root=data_root,
            account_key=request.account_key,
            scenario="compose",
        )
    except SnapshotError as exc:
        run = _failed_run(
            request,
            summary="未知人设或快照无效，无法写帖。",
            limitations=[str(exc)],
        )
        save_run(run, output_directory)
        return run

    execution = COMPOSE_FLOW.create_execution(
        concurrency=max_concurrency,
        runtime_resources={
            "snapshot": snapshot,
            "data_root": data_root,
            "session_id": request.session_id,
        },
        auto_close=False,
    )
    trace = TraceLog(request.task_id, execution.id)
    execution.update_runtime_resources({"trace": trace})
    try:
        await execution.async_start({"request": request.model_dump(mode="json")})
        state = await execution.async_close()
    except BaseException:
        output_directory.mkdir(parents=True, exist_ok=True)
        (output_directory / "events.jsonl").write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in trace.events)
            + ("\n" if trace.events else ""),
            encoding="utf-8",
        )
        raise

    package = state.get("package")
    if not isinstance(package, dict):
        run = _failed_run(
            request,
            summary="写帖未产出草稿包。",
            limitations=["missing_package"],
            snapshot_id=snapshot.snapshot_id,
            execution_id=execution.id,
            events=trace.events,
        )
        save_run(run, output_directory)
        return run

    run = {
        "task_id": request.task_id,
        "execution_id": execution.id,
        "status": package.get("status") or "failed",
        "intent": package.get("intent") or state.get("intent"),
        "task_type": "compose_post",
        "snapshot_id": snapshot.snapshot_id,
        "pipeline_version": PIPELINE_VERSION,
        "brief": state.get("brief"),
        "work_items": state.get("work_items") or [],
        "drafts": package.get("drafts") or [],
        "summary": package.get("summary") or "",
        "source_kind": state.get("source_kind"),
        "source_anchor": state.get("source_anchor"),
        "user_instruction": state.get("user_instruction"),
        "candidates": state.get("candidates"),
        "limitations": package.get("limitations") or state.get("limitations") or [],
        "evidence": [],
        "events": trace.events,
    }
    save_run(run, output_directory)
    return run


__all__ = ["COMPOSE_FLOW", "PIPELINE_VERSION", "run_compose"]
