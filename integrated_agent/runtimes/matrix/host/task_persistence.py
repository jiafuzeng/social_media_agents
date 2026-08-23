"""任务快照磁盘持久化：服务重启后仍可从 logs/matrix/<task_id> 恢复。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from .models import (
    EvidenceCard,
    GatedDraft,
    MatrixTaskResult,
    TaskSnapshot,
    TaskStatus,
    coerce_media_links,
)


def _rollup_task_status(raw: Any) -> TaskStatus:
    status = str(raw or "").strip().lower()
    if status in {"completed", "partial", "failed"}:
        return status  # type: ignore[return-value]
    return "failed"


def _snapshot_status(raw: Any) -> Literal["completed", "failed"]:
    status = _rollup_task_status(raw)
    return "failed" if status == "failed" else "completed"


def task_snapshot_from_run(
    task_id: str,
    run: dict[str, Any],
    *,
    logs_root: Path,
) -> TaskSnapshot | None:
    if not run:
        return None
    raw_status = str(run.get("status") or "").strip().lower()
    if raw_status not in {"completed", "partial", "failed"}:
        return None

    trace_ref = str(run.get("trace_ref") or "").strip()
    if not trace_ref:
        trace_ref = (logs_root / task_id / "run.json").resolve().as_uri()

    drafts = [
        GatedDraft.model_validate(item)
        for item in run.get("drafts") or []
        if isinstance(item, dict)
    ]
    evidence = [
        EvidenceCard(
            ref_id=str(item.get("ref_id") or ""),
            title=str(item.get("title") or ""),
            ruling=str(item.get("ruling") or item.get("text") or ""),
            kind=str(item.get("kind") or ""),
            link=str(item.get("link") or ""),
            media_links=coerce_media_links(item.get("media_links")),
        )
        for item in run.get("evidence") or []
        if isinstance(item, dict) and item.get("ref_id")
    ]
    result = MatrixTaskResult(
        task_id=task_id,
        snapshot_id=str(run.get("snapshot_id") or ""),
        trace_ref=trace_ref,
        status=_rollup_task_status(run.get("status")),
        task_type=run.get("task_type") or "compose_post",
        summary=str(run.get("summary") or ""),
        drafts=drafts,
        evidence=evidence,
        limitations=list(run.get("limitations") or []),
    )
    return TaskSnapshot(
        task_id=task_id,
        status=_snapshot_status(run.get("status")),
        result=result,
        error=None if raw_status != "failed" else str(run.get("summary") or "task failed"),
    )


def persist_task_snapshot(logs_root: Path, snapshot: TaskSnapshot) -> None:
    task_dir = logs_root / snapshot.task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "snapshot.json").write_text(
        snapshot.model_dump_json(indent=2),
        encoding="utf-8",
    )


def load_task_snapshot(logs_root: Path, task_id: str) -> TaskSnapshot | None:
    task_dir = logs_root / task_id
    snapshot_path = task_dir / "snapshot.json"
    if snapshot_path.is_file():
        try:
            return TaskSnapshot.model_validate_json(
                snapshot_path.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    run_path = task_dir / "run.json"
    if not run_path.is_file():
        return None
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(run, dict):
        return None
    return task_snapshot_from_run(task_id, run, logs_root=logs_root)


__all__ = [
    "load_task_snapshot",
    "persist_task_snapshot",
    "task_snapshot_from_run",
]
