from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from agently import TriggerFlow

from integrated_agent.runtimes.matrix.models import MatrixTaskRequest

from ..snapshots import bind_snapshot
from ..trace_log import TraceLog, save_run
from .chunks.compose.pipeline import (
    compose_brief,
    compose_prelude,
    compose_review,
    retrieve_and_compose_draft,
)


PIPELINE_VERSION = "matrix-compose-v1"

COMPOSE_FLOW = TriggerFlow(name="matrix-compose-v1")
(
    COMPOSE_FLOW.to(compose_prelude)
    .to(compose_brief)
    .for_each(concurrency=10)
    .to(retrieve_and_compose_draft)
    .end_for_each()
    .to(compose_review)
)


async def run_compose(
    request: MatrixTaskRequest,
    *,
    data_root: Path,
    output_directory: Path,
    max_concurrency: int = 10,
    knowledge: Any | None = None,
) -> dict[str, Any]:
    snapshot = bind_snapshot(
        data_root=data_root,
        account_key=request.account_key,
        scenario="compose",
    )
    execution = COMPOSE_FLOW.create_execution(
        concurrency=max_concurrency,
        runtime_resources={
            "snapshot": snapshot,
            "data_root": data_root,
            "session_id": request.session_id,
            "knowledge": knowledge,
            "kb_user_id": request.user_id or "",
            "kb_profile_id": request.embedding_profile_id or "",
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
    package = cast(dict[str, Any], state["package"])
    run = {
        "task_id": request.task_id,
        "execution_id": execution.id,
        "status": package["status"],
        "task_type": "compose_post",
        "snapshot_id": snapshot.snapshot_id,
        "pipeline_version": PIPELINE_VERSION,
        "brief": state.get("brief"),
        "drafts": package["drafts"],
        "summary": package["summary"],
        "limitations": package.get("limitations") or [],
        "evidence": _unique_cards(state.get("evidence_cards") or []),
        "events": trace.events,
    }
    save_run(run, output_directory)
    return run


def _unique_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for card in cards:
        ref_id = str(card.get("ref_id") or "")
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        unique.append(card)
    return unique


__all__ = ["COMPOSE_FLOW", "PIPELINE_VERSION", "run_compose"]
