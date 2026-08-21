"""M3+ 尚未接线：分流成功后暂以空草稿包收尾，保留 intent。"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData


async def compose_branch_hold(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    intent = cast(str | None, data.get_state("intent"))
    package = {
        "status": "completed",
        "intent": intent,
        "task_type": "compose_post",
        "summary": "",
        "drafts": [],
        "limitations": list(cast(list[str], data.get_state("limitations") or [])),
    }
    await data.async_set_state("package", package, emit=False)
    return package


__all__ = ["compose_branch_hold"]
