from __future__ import annotations

from pathlib import Path

import pytest

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.runtimes.matrix.host.task_persistence import load_task_snapshot


def test_load_task_snapshot_from_existing_run_json() -> None:
    task_id = "470db6fbfd0e4bd4823bb340c918eb81"
    logs_root = PROJECT_ROOT / "logs" / "matrix"
    if not (logs_root / task_id / "run.json").is_file():
        pytest.skip("local task log missing")

    snapshot = load_task_snapshot(logs_root, task_id)
    assert snapshot is not None
    assert snapshot.task_id == task_id
    assert snapshot.status == "completed"
    assert snapshot.result is not None
    assert snapshot.result.drafts
