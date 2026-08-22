"""M3 Source 子图（改写）：prelude → Reason ↔ Act → materialize → Reason。

- source_reason：对照可用工具与任务目标，只决策 name+args
- source_act：执行 pending_tools，本批结果交给 clean
- clean_tool_result：materialize 确定性物化工具返回为素材卡，再参与 Reason

改写不看 need_trends。tweet_id / keyword 由 Reason 根据 source_anchor 与指令自行填参。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.compose.materialize import materialize_tool_batch
from integrated_agent.runtimes.matrix.host.tikhubtools import source_tools_list
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_STEPS = 3
# Reason：放大 session 窗口与输出额度，容纳累计 tool_result_cleaned
_REACT_SESSION_MAX_LENGTH = 64_000
_REACT_MAX_TOKENS = 8_192



async def _run_one_tool(
    name: str, args: dict[str, Any], tools: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    try:
        if name not in tools:
            result: Any = {"error": f"未知工具: {name}"}
        else:
            result = await asyncio.to_thread(tools[name]["func"], **(args or {}))
    except Exception as exc:
        result = {"error": str(exc)}
    return {"tool": name, "args": args or {}, "result": result}


async def source_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收父图 capture；emit Reason 进入信号环。"""
    payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    intent = str(data.get_state("intent") or payload.get("intent") or "rewrite")
    source_anchor = str(data.get_state("source_anchor") or "").strip()
    user_instruction = str(
        data.get_state("user_instruction") or request.get("text") or ""
    )
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    task_id = str(request.get("task_id") or "")
    text = str(request.get("text") or "")

    await data.async_set_state("task_id", task_id, emit=False)
    await data.async_set_state("intent", intent, emit=False)
    await data.async_set_state("source_anchor", source_anchor, emit=False)
    await data.async_set_state("user_instruction", user_instruction, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    await data.async_set_state("tool_result_cleaned", [], emit=False)

    base = {
        "intent": intent,
        "source_anchor": source_anchor,
        "user_instruction": user_instruction,
        "task_id": task_id,
    }

    data.emit_nowait(
        "Reason",
        {
            "question": user_instruction or text,
            "tool_result_cleaned": [],
            "step": 0,
            "source_anchor": source_anchor,
            "task_id": task_id,
        },
    )
    return base


async def source_reason(data: TriggerFlowRuntimeData) -> None:
    """根据任务目标与可用工具，决定调用哪些函数并填写入参；不执行。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    step = int(state.get("step") or 0) + 1
    budget_left = MAX_STEPS - step
    tools = source_tools_list
    tools_schema = [
        {"name": k, "desc": v["desc"], "args": v["args"]} for k, v in tools.items()
    ]
    session_id = str(data.require_resource("session_id"))
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    tool_result_cleaned = list(cast(list[Any], data.get_state("tool_result_cleaned") or []))

    if budget_left <= 1:
        extra = [
            f"步骤预算只剩 {budget_left} 步，请直接基于现有观察给出结论（type=final），不要再声明工具"
        ]
    else:
        extra = [
            "可从 source_anchor / 用户指令中自行识别 tweet_id 或搜索关键字；"
            "有 status id 则优先 fetch_tweet_detail；否则可用 fetch_search_timeline。"
            "禁止 fetch_user_media / fetch_trending。"
        ]

    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-source-react").
            activate_session(session_id=session_id)
            .input(state.get("question") or "")
            .info(
                {
                    "job": "为改写组装原文包；够用就 final。不要写改写正文。",
                    "branch": "source",
                    "source_anchor": state.get("source_anchor"),
                    "tools": tools_schema,
                    "已完成步骤": tool_result_cleaned,
                    "budget": {
                        "step": step,
                        "max_steps": MAX_STEPS,
                        "budget_left": budget_left,
                    },
                }
            )
            .instruct(
                [
                    "你只做决策，不执行工具。",
                    "对照「可用工具」与「已完成步骤」，判断还缺什么原文材料。",
                    "若需采集:在 tools 中列出要调用的函数名与完整入参（可一次多个）。",
                    "name 必须来自 tools; args 只填该工具声明的字段。",
                    "材料已够（至少有原文正文或可改粘贴）:填写 answer; tools 置空。",
                    *extra,
                ]
            )
            .output(
                {
                    "type": (str, "tool 或 final", "not_null"),
                    "reasoning": (str, "推理：目标还缺什么、为何选这些工具", "not_null"),
                    "tool_calls": (
                        list,
                        "tools 时填 [{name: 工具名, args: 入参字典}]；否则 []",
                    ),
                    "answer": (str, "final 时填最终结论，否则空串"),
                },
                format="json",
            )
            .async_start()
        )
    except Exception as exc:
        code = f"source_reason_error:{type(exc).__name__}"
        if code not in limitations:
            limitations.append(code)
        await _finalize_source(
            data,
            tool_result=tool_result_cleaned,
            answer="",
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
        )
        return

    if not isinstance(raw, dict):
        raw = {}
    decision = str(raw.get("type") or "final").strip().lower()
    if decision not in {"tool", "final"}:
        decision = "final"

    pending_tools: list[dict[str, Any]] = []
    for item in raw.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        if not name:
            continue
        pending_tools.append({"name": name, "args": cast(dict[str, Any], args)})

    answer = str(raw.get("answer") or "")
    new_state = {
        **state,
        "step": step,
        "tool_result_cleaned": tool_result_cleaned,
    }

    if budget_left <= 0 or decision == "final" or not pending_tools:
        await _finalize_source(
            data,
            tool_result=tool_result_cleaned,
            answer=answer or ("已达到最大步骤数" if budget_left <= 0 else ""),
            limitations=limitations,
            task_id=str(state.get("task_id") or ""),
            step=step,
        )
        return

    data.emit_nowait("Act", {**new_state, "pending_tools": pending_tools})


async def source_act(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """执行 pending_tools；本批原始结果交给 clean。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    pending = state.get("pending_tools") or []
    tools = source_tools_list
    results = await asyncio.gather(
        *[
            _run_one_tool(
                str(tc.get("name") or ""),
                cast(dict[str, Any], tc.get("args") or {}),
                tools,
            )
            for tc in pending
            if isinstance(tc, dict)
        ]
    )
    return {**state, "tool_batch": list(results)}


async def clean_tool_result(data: TriggerFlowRuntimeData) -> None:
    """用 materialize 确定性物化 tool_batch → 素材卡；追加进 tool_result_cleaned 后 emit Reason。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    batch = [
        item
        for item in cast(list[Any], state.get("tool_batch") or [])
        if isinstance(item, dict)
    ]
    cleaned = materialize_tool_batch(batch) if batch else []
    prior = list(cast(list[Any], data.get_state("tool_result_cleaned") or []))
    tool_result_cleaned = prior + cleaned
    await data.async_set_state("tool_result_cleaned", tool_result_cleaned, emit=False)
    next_state = {
        k: v for k, v in state.items() if k not in {"tool_batch", "pending_tools"}
    }
    data.emit_nowait(
        "Reason", {**next_state, "tool_result_cleaned": tool_result_cleaned}
    )


async def _finalize_source(
    data: TriggerFlowRuntimeData,
    *,
    tool_result: list[dict[str, Any]],
    answer: str,
    limitations: list[str],
    task_id: str,
    step: int,
) -> None:
    tool_logs = list(tool_result)
    used = {
        str(item.get("tool") or "").strip()
        for item in tool_logs
        if str(item.get("tool") or "").strip()
    }
    await data.async_set_state("tool_logs", tool_logs, emit=False)
    await data.async_set_state("tool_result_cleaned", tool_logs, emit=False)
    await data.async_set_state("source_result", answer, emit=False)
    await data.async_set_state("source_post", None, emit=False)
    await data.async_set_state("source_media", [], emit=False)
    await data.async_set_state("author_card", None, emit=False)
    await data.async_set_state("related_tweet_cards", [], emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.source",
        status="completed",
        subject_id=task_id,
        facts={
            "tool_calls": len(tool_logs),
            "tool_names": sorted(used),
            "react_steps": step,
            "limitations": limitations,
        },
    )


def build_source_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-source-v1")
    flow.to(source_prelude)
    flow.when("Act").to(source_act).to(clean_tool_result)
    flow.when("Reason").to(source_reason)
    return flow


SOURCE_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "request": "runtime_data.request",
        "intent": "runtime_data.intent",
        "source_anchor": "runtime_data.source_anchor",
        "user_instruction": "runtime_data.user_instruction",
        "limitations": "runtime_data.limitations",
    },
    "resources": {
        "trace": "resources.trace",
        "session_id": "resources.session_id",
        "snapshot": "resources.snapshot",
    },
}

SOURCE_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "tool_logs": "result.tool_logs",
        "tool_result_cleaned": "result.tool_result_cleaned",
        "source_result": "result.source_result",
        "source_post": "result.source_post",
        "source_media": "result.source_media",
        "author_card": "result.author_card",
        "related_tweet_cards": "result.related_tweet_cards",
        "limitations": "result.limitations",
    },
}


__all__ = [
    "SOURCE_SUBFLOW_CAPTURE",
    "SOURCE_SUBFLOW_WRITE_BACK",
    "build_source_subflow",
    "clean_tool_result",
    "source_act",
    "source_prelude",
    "source_reason",
]
