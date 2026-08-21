"""M3+ 尚未接线：分流成功后暂以空草稿包收尾，保留 intent。"""

from __future__ import annotations

import json
from typing import Any, cast

from agently import TriggerFlowRuntimeData


def _upstream_tool_cards(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """收集 Intel / Source 写回父图的工具与卡片字段。"""
    keys = (
        "tweet_cards",
        "trend_cards",
        "tool_logs",
        "tool_result_cleaned",
        "source_post",
        "source_media",
        "author_card",
        "related_tweet_cards",
        "source_result",
        "intel_result",
    )
    out: dict[str, Any] = {}
    for key in keys:
        value = data.get_state(key)
        if value is None or value == [] or value == "":
            continue
        out[key] = value
    return out


async def compose_branch_hold(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    intent = cast(str | None, data.get_state("intent"))
    cards = _upstream_tool_cards(data)
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    print(
        "[compose_branch_hold] upstream tool cards\n"
        + json.dumps(
            {
                "intent": intent,
                "limitations": limitations,
                "card_keys": list(cards.keys()),
                "cards": cards,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )
    package = {
        "status": "completed",
        "intent": intent,
        "task_type": "compose_post",
        "summary": "",
        "drafts": [],
        "limitations": limitations,
    }
    await data.async_set_state("package", package, emit=False)
    return package


__all__ = ["compose_branch_hold"]
