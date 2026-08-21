"""写帖终包：落 package state，供 run_compose 收成。"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData


async def compose_package(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    existing = data.get_state("package")
    if isinstance(existing, dict):
        package = existing
    else:
        package = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    await data.async_set_state("package", package, emit=False)
    return package


__all__ = ["compose_package"]
