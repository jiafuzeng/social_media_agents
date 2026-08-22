"""M3 Intel 子图：prelude → plan_material → for_each(intel_reason) → merge_material。"""

from __future__ import annotations

import os
from typing import Any, cast

from agently import Agently, TriggerFlow, TriggerFlowRuntimeData
from agently.builtins.actions import Browse, Search
from agently.types.trigger_flow.trigger_flow import (
    TriggerFlowSubFlowCapture,
    TriggerFlowSubFlowWriteBack,
)

from integrated_agent.runtimes.matrix.host.trace_log import TraceLog

MAX_PLAN_TASKS = 4
MAX_ACTION_ROUNDS = 5


def _search_browse_actions() -> list[Any]:
    return [
        Search(
            proxy=os.environ.get("http_proxy", ""),
            timeout=20,
            backend="yahoo",
            max_attempts=1,
            region="cn-zh",
        ),
        Browse(
            proxy=os.environ.get("http_proxy", ""),
            timeout=20,
            fallback_order=("bs4",),
            enable_playwright=False,
            enable_bs4=True,
            max_content_length=12_000,
        ),
    ]


def _intel_context(data: TriggerFlowRuntimeData, state: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = state or cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    request = cast(dict[str, Any], data.get_state("request") or {})
    user_instruction = str(
        payload.get("user_instruction")
        or data.get_state("user_instruction")
        or request.get("text")
        or ""
    ).strip()
    return {
        "user_instruction": user_instruction,
        "intent": str(payload.get("intent") or data.get_state("intent") or "compose"),
        "source_kind": str(payload.get("source_kind") or data.get_state("source_kind") or "none"),
        "source_anchor": str(payload.get("source_anchor") or data.get_state("source_anchor") or "").strip(),
        "need_trends": bool(payload.get("need_trends") or data.get_state("need_trends")),
        "task_id": str(payload.get("task_id") or data.get_state("task_id") or ""),
    }


def _tasks_from_plan(raw_tasks: Any) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw_tasks if isinstance(raw_tasks, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal") or "").strip()
        if not goal:
            continue
        tasks.append(
            {
                "task_id": str(item.get("task_id") or f"m{index}"),
                "goal": goal,
            }
        )
        if len(tasks) >= MAX_PLAN_TASKS:
            break
    return tasks


def _dedupe_materials(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("link") or item.get("title") or item.get("text") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


async def intel_prelude(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """接收父图 capture，初始化本单 state。"""
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

    return {
        "intent": intent,
        "source_kind": source_kind,
        "source_anchor": source_anchor,
        "user_instruction": user_instruction,
        "need_trends": need_trends,
        "task_id": task_id,
    }


async def plan_material(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """按用户意图拆解为若干可并行执行的素材采集子任务。"""
    ctx = _intel_context(data)
    user_instruction = ctx["user_instruction"]
    plan_summary = ""
    tasks: list[dict[str, Any]] = []
    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-intel-plan")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input(user_instruction)
            .info(ctx)
            .instruct(
                [
                    "根据用户创作意图，拆解 1 到 4 个素材采集子任务，供下游并行执行。",
                    "每个子任务只覆盖一个检索角度，goal 写清楚要采集什么。",
                    "不要写推文正文，只输出任务计划。",
                ]
            )
            .output(
                {
                    "plan_summary": (str, "整体拆解说明", "not_null"),
                    "tasks": (
                        [
                            {
                                "task_id": (str, "子任务 id，如 m1", "not_null"),
                                "goal": (str, "该子任务目标", "not_null"),
                            }
                        ],
                        "not_null",
                    ),
                },
                format="json",
            )
            .async_start()
        )
        if isinstance(raw, dict):
            plan_summary = str(raw.get("plan_summary") or "")
            tasks = _tasks_from_plan(raw.get("tasks"))
        if not tasks:
            tasks = [{"task_id": "m1", "goal": user_instruction or "创作素材"}]
    except Exception as exc:
        limitations = list(cast(list[str], data.get_state("limitations") or []))
        code = f"intel_plan_error:{type(exc).__name__}"
        if code not in limitations:
            limitations.append(code)
        await data.async_set_state("limitations", limitations, emit=False)
        tasks = [{"task_id": "m1", "goal": user_instruction or "创作素材"}]

    await data.async_set_state("material_plan", tasks, emit=False)
    await data.async_set_state("plan_summary", plan_summary, emit=False)
    return tasks


async def intel_reason(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """执行单个子任务：Search/Browse 采集素材卡。"""
    task = cast(dict[str, Any], data.input if isinstance(data.input, dict) else {})
    ctx = _intel_context(data)
    goal = str(task.get("goal") or "").strip()
    task_id = str(task.get("task_id") or "")

    try:
        raw = await (
            Agently.create_agent(name="matrix-compose-intel-task")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input({"goal": goal})
            .info({**ctx, "task": task})
            .use_actions(_search_browse_actions())
            .set_action_loop(max_rounds=MAX_ACTION_ROUNDS)
            .instruct(
                [
                    "只完成当前子任务，整理素材卡；不要写推文正文。",
                    "最多 Search 1 次、Browse 2 个链接；完成后立刻输出。",
                    "每条素材：kind（tweet/article/trend）、title、text、link、media_links（可空）。",
                    "搜不到配图时 media_links 留空，不要反复搜索。",
                ]
            )
            .output(
                {
                    "answer": (str, "该子任务采集结论", "not_null"),
                    "material_list": (
                        list,
                        "[{kind, title, text, link, media_links:[{type, thumb, video_url}]}]",
                    ),
                },
                format="json",
            )
            .async_start()
        )
    except Exception as exc:
        return {
            "task_id": task_id,
            "goal": goal,
            "ok": False,
            "error": str(exc),
            "answer": "",
            "material_list": [],
        }

    if not isinstance(raw, dict):
        raw = {}
    material_list = raw.get("material_list") or []
    if not isinstance(material_list, list):
        material_list = []
    cleaned = [item for item in material_list if isinstance(item, dict)]
    for item in cleaned:
        item.setdefault("source_task_id", task_id)
        item.setdefault("source_goal", goal)
    return {
        "task_id": task_id,
        "goal": goal,
        "ok": True,
        "answer": str(raw.get("answer") or ""),
        "material_list": cleaned,
    }


async def merge_material(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """汇总 for_each 子任务结果，写回父图所需 state。"""
    ctx = _intel_context(data)
    task_results = [
        item for item in cast(list[Any], data.input or []) if isinstance(item, dict)
    ]
    merged: list[dict[str, Any]] = []
    answers: list[str] = []
    limitations = list(cast(list[str], data.get_state("limitations") or []))

    for result in task_results:
        answer = str(result.get("answer") or "").strip()
        if answer:
            answers.append(answer)
        if not result.get("ok", True):
            code = f"intel_task_failed:{result.get('task_id') or 'unknown'}"
            if code not in limitations:
                limitations.append(code)
        merged.extend(
            item
            for item in cast(list[Any], result.get("material_list") or [])
            if isinstance(item, dict)
        )

    material_list = _dedupe_materials(merged)
    plan_summary = str(data.get_state("plan_summary") or "").strip()
    if plan_summary:
        answers.insert(0, plan_summary)
    if not material_list and not any(
        str(item.get("error") or "") for item in task_results if isinstance(item, dict)
    ):
        limitations.append("intel_empty")

    intel_result = "；".join(part for part in answers if part) or (
        f"共采集 {len(material_list)} 条素材" if material_list else "未采集到可用素材"
    )

    await data.async_set_state("material_list", material_list, emit=False)
    await data.async_set_state("intel_result", intel_result, emit=False)
    await data.async_set_state("tool_logs", task_results, emit=False)
    await data.async_set_state("tweet_cards", [], emit=False)
    await data.async_set_state("trend_cards", [], emit=False)
    await data.async_set_state("limitations", limitations, emit=False)

    trace = cast(TraceLog, data.require_resource("trace"))
    trace.log(
        layer="business",
        event_type="business.matrix.intel",
        status="completed",
        subject_id=ctx["task_id"],
        facts={
            "plan_tasks": len(task_results),
            "material_list": len(material_list),
            "limitations": limitations,
        },
    )
    return {
        "material_list": material_list,
        "intel_result": intel_result,
        "tool_logs": task_results,
        "limitations": limitations,
    }


def build_intel_subflow() -> TriggerFlow:
    flow = TriggerFlow(name="matrix-compose-intel-v1")
    (
        flow.to(intel_prelude)
        .to(plan_material)
        .for_each(concurrency=4)
        .to(intel_reason)
        .end_for_each()
        .to(merge_material)
    )
    return flow


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
        "material_list": "result.material_list",
        "tool_logs": "result.tool_logs",
        "intel_result": "result.intel_result",
        "limitations": "result.limitations",
    },
}


__all__ = [
    "INTEL_SUBFLOW_CAPTURE",
    "INTEL_SUBFLOW_WRITE_BACK",
    "build_intel_subflow",
    "intel_prelude",
    "intel_reason",
    "merge_material",
    "plan_material",
]
