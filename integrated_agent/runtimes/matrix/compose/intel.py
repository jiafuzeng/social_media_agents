"""M3 Intel 子图：prelude → Reason ↔ Act 信号环（s06）。

- intel_reason：对照可用工具与任务目标，只决策 name+args（pending_tools）
- intel_act：执行 pending_tools，结果写入 history，再回到 Reason
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.host.tikhubtools import (
    TWITTER_WEB_TOOLS,
    fetch_trending,
)
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_STEPS = 8

# 写帖常用子集（与 COMPOSE_TOOL_FUNCS 对齐）
_COMPOSE_TOOL_NAMES = (
    "fetch_tweet_detail",
    "fetch_user_profile",
    "fetch_user_post_tweet",
    "fetch_user_media",
    "fetch_search_timeline",
    "fetch_trending",
)


def _compose_tools(*, need_trends: bool) -> dict[str, dict[str, Any]]:
    names = list(_COMPOSE_TOOL_NAMES)
    if not need_trends:
        names = [n for n in names if n != "fetch_trending"]
    return {name: TWITTER_WEB_TOOLS[name] for name in names if name in TWITTER_WEB_TOOLS}


async def _run_one_tool(name: str, args: dict[str, Any], tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    try:
        if name not in tools:
            result: Any = {"error": f"未知工具: {name}"}
        else:
            result = await asyncio.to_thread(tools[name]["func"], **(args or {}))
    except Exception as exc:
        result = {"error": str(exc)}
    return {"tool": name, "args": args or {}, "result": result}


async def intel_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收父图 capture；need_trends 时 emit Reason 进入信号环。"""
    payload = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    intent = str(data.get_state("intent") or payload.get("intent") or "compose")
    source_kind = str(data.get_state("source_kind") or "none")
    source_anchor = str(data.get_state("source_anchor") or "").strip()
    user_instruction = str(
        data.get_state("user_instruction") or request.get("text") or ""
    )
    limitations = list(cast(list[str], data.get_state("limitations") or []))
    need_trends = bool(request.get("need_trends"))
    task_id = str(request.get("task_id") or "")

    await data.async_set_state("task_id", task_id, emit=False)
    await data.async_set_state("intent", intent, emit=False)
    await data.async_set_state("source_kind", source_kind, emit=False)
    await data.async_set_state("source_anchor", source_anchor, emit=False)
    await data.async_set_state("user_instruction", user_instruction, emit=False)
    await data.async_set_state("need_trends", need_trends, emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    base = {
        "intent": intent,
        "source_kind": source_kind,
        "source_anchor": source_anchor,
        "user_instruction": user_instruction,
        "need_trends": need_trends,
        "task_id": task_id,
    }

    if not need_trends:
        await data.async_set_state("tweet_cards", [], emit=False)
        await data.async_set_state("trend_cards", [], emit=False)
        await data.async_set_state("tool_logs", [], emit=False)
        await data.async_set_state("intel_result", "", emit=False)
        trace = cast(TraceLog, data.require_resource("trace"))
        trace.log(
            layer="business",
            event_type="business.matrix.intel",
            status="completed",
            subject_id=task_id,
            facts={"skipped_react": True, "need_trends": False},
        )
        return base

    data.emit_nowait(
        "Reason",
        {
            "question": user_instruction,
            "history": [],
            "step": 0,
            "need_trends": True,
            "task_id": task_id,
        },
    )
    return base


async def intel_reason(data: TriggerFlowRuntimeData) -> None:
    """根据任务目标与可用工具，决定调用哪些函数并填写入参；不执行。

    - type=tool → emit Act，载荷含 pending_tools=[{name, args}, ...]
    - type=final / 预算耗尽 → 收尾写 state，不再 emit
    """
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    step = int(state.get("step") or 0) + 1
    budget_left = MAX_STEPS - step
    need_trends = bool(state.get("need_trends"))
    tools = _compose_tools(need_trends=need_trends)
    tools_schema = [
        {"name": k, "desc": v["desc"], "args": v["args"]} for k, v in tools.items()
    ]
    session_id = str(data.require_resource("session_id"))
    limitations = list(cast(list[str], data.get_state("limitations") or []))

    extra: list[str] = []
    if budget_left <= 1:
        extra.append(
            f"步骤预算只剩 {budget_left} 步，请直接基于现有观察给出结论（type=final），不要再声明工具"
        )

    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-tikhub-react")
            .activate_session(session_id=session_id)
            .input(state.get("question") or "")
            .info(
                {
                    "job": "为创作采集对标/热搜材料；够用就 final。不要写正文。",
                    "branch": "intel",
                    "可用工具": tools_schema,
                    "已完成步骤": (state.get("history") or [])[-6:],
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
                    "对照「可用工具」与「已完成步骤」，判断还缺什么材料。",
                    "若需采集：type=tool，在 tool_calls 中列出要调用的函数名与完整入参（可一次多个）。",
                    "name 必须来自可用工具；args 只填该工具声明的字段。",
                    "材料已够：type=final，填写 answer 简短结论；tool_calls 置空。",
                    *extra,
                ]
            )
            .output(
                {
                    "type": (str, "tool 或 final", "not_null"),
                    "reasoning": (str, "推理：目标还缺什么、为何选这些工具", "not_null"),
                    "tool_calls": (
                        list,
                        "type==tool 时填 [{name: 工具名, args: 入参字典}]；否则 []",
                    ),
                    "answer": (str, "type==final 时填最终结论，否则空串"),
                },
                format="json",
            )
            .async_start()
        )
    except Exception as exc:
        code = f"tikhub_react_error:{type(exc).__name__}"
        if code not in limitations:
            limitations.append(code)
        await _finalize_intel(
            data,
            history=list(state.get("history") or []),
            answer="",
            limitations=limitations,
            need_trends=need_trends,
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
    new_state = {**state, "step": step}

    if budget_left <= 0 or decision == "final" or not pending_tools:
        await _finalize_intel(
            data,
            history=list(state.get("history") or []),
            answer=answer or ("已达到最大步骤数" if budget_left <= 0 else ""),
            limitations=limitations,
            need_trends=need_trends,
            task_id=str(state.get("task_id") or ""),
            step=step,
        )
        return

    # 只把「函数名 + 入参」交给 Act 执行
    data.emit_nowait("Act", {**new_state, "pending_tools": pending_tools})


async def intel_act(data: TriggerFlowRuntimeData) -> None:
    """执行 Reason 声明的 pending_tools，把结果追加进 history，再 emit Reason。"""
    state = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    pending = state.get("pending_tools") or []
    need_trends = bool(state.get("need_trends"))
    tools = _compose_tools(need_trends=need_trends)
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
    data.emit_nowait(
        "Reason",
        {
            **state,
            "history": list(state.get("history") or []) + list(results),
        },
    )


async def _finalize_intel(
    data: TriggerFlowRuntimeData,
    *,
    history: list[dict[str, Any]],
    answer: str,
    limitations: list[str],
    need_trends: bool,
    task_id: str,
    step: int,
) -> None:
    tool_logs = list(history)
    used = {
        str(item.get("tool") or "").strip()
        for item in tool_logs
        if str(item.get("tool") or "").strip()
    }
    if need_trends and "fetch_trending" not in used:
        try:
            trend_result = await fetch_trending(country="China")
            tool_logs.append(
                {
                    "tool": "fetch_trending",
                    "args": {"country": "China"},
                    "result": trend_result,
                    "source": "host_ensure",
                }
            )
            used.add("fetch_trending")
        except Exception as exc:
            code = f"trending_ensure_error:{type(exc).__name__}"
            if code not in limitations:
                limitations.append(code)

    await data.async_set_state("tool_logs", tool_logs, emit=False)
    await data.async_set_state("intel_result", answer, emit=False)
    await data.async_set_state("tweet_cards", [], emit=False)
    await data.async_set_state("trend_cards", [], emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.intel",
        status="completed",
        subject_id=task_id,
        facts={
            "need_trends": need_trends,
            "tool_calls": len(tool_logs),
            "tool_names": sorted(used),
            "react_steps": step,
            "limitations": limitations,
        },
    )


def build_intel_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-intel-v1")
    flow.to(intel_prelude)
    flow.when("Act").to(intel_act)
    flow.when("Reason").to(intel_reason)
    return flow


# 父 when("compose") → 本 subflow 的数据桥
INTEL_SUBFLOW_CAPTURE: TriggerFlowSubFlowCapture = {
    "input": "value",
    "runtime_data": {
        "request": "runtime_data.request",
        "intent": "runtime_data.intent",
        "source_kind": "runtime_data.source_kind",
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

INTEL_SUBFLOW_WRITE_BACK: TriggerFlowSubFlowWriteBack = {
    "runtime_data": {
        "tweet_cards": "result.tweet_cards",
        "trend_cards": "result.trend_cards",
        "tool_logs": "result.tool_logs",
        "intel_result": "result.intel_result",
        "limitations": "result.limitations",
    },
}


__all__ = [
    "INTEL_SUBFLOW_CAPTURE",
    "INTEL_SUBFLOW_WRITE_BACK",
    "build_intel_subflow",
    "intel_act",
    "intel_prelude",
    "intel_reason",
]
