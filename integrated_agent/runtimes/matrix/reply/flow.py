"""回评业务 Flow。不包含写帖或趋势节点。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from agently import TriggerFlow

from integrated_agent.runtimes.matrix.host.models import MatrixTaskRequest

from integrated_agent.runtimes.matrix.host.snapshots import bind_snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog, save_run
from .pipeline import (
    reply_brief,
    reply_prelude,
    reply_review,
    retrieve_and_reply_draft,
)


PIPELINE_VERSION = "matrix-reply-v1"

REPLY_FLOW = TriggerFlow(name="matrix-reply-v1")
(
    REPLY_FLOW.to(reply_prelude)
    .to(reply_brief)
    .for_each(concurrency=4)
    .to(retrieve_and_reply_draft)
    .end_for_each()
    .to(reply_review)
)


async def run_reply(
    request: MatrixTaskRequest,
    *,
    data_root: Path,
    output_directory: Path,
    max_concurrency: int = 4,
    knowledge: Any | None = None,
    events: Any | None = None,
) -> dict[str, Any]:
    snapshot = bind_snapshot(
        data_root=data_root,
        interaction_key=request.interaction_key,
        scenario="reply",
        comments=request.comments,
    )
    execution = REPLY_FLOW.create_execution(
        concurrency=max_concurrency,
        runtime_resources={
            "snapshot": snapshot,
            "data_root": data_root,
            "session_id": request.session_id,
            "knowledge": knowledge,
            "kb_user_id": request.user_id or "",
            "events": events,
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
        "task_type": "reply_comment",
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


__all__ = ["PIPELINE_VERSION", "REPLY_FLOW", "run_reply"]
