from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from agently import TriggerFlow, TriggerFlowRuntimeData

from .models import (
    EvidenceCard,
    GatedDraft,
    MatrixTaskRequest,
    MatrixTaskResult,
    Scenario,
)
from .service import MatrixTaskFailed
from .stores import InMemoryEventStore

AnalyzeFn = Callable[[MatrixTaskRequest], Awaitable[dict[str, Any]]]


@dataclass
class WorkerDependencies:
    analyze_compose: AnalyzeFn
    analyze_reply: AnalyzeFn
    events: InMemoryEventStore


def _deps(data: TriggerFlowRuntimeData) -> WorkerDependencies:
    return cast(
        WorkerDependencies,
        data.execution.require_runtime_resource("worker_dependencies"),
    )


def _request(data: TriggerFlowRuntimeData) -> MatrixTaskRequest:
    return MatrixTaskRequest.model_validate(data.get_state("request"))


async def _stage(
    data: TriggerFlowRuntimeData,
    event_type: str,
    stage: str,
    **values: Any,
) -> None:
    request = _request(data)
    await _deps(data).events.publish(
        request.task_id,
        event_type,
        {"stage": stage, **values},
    )


async def _run_analysis(
    data: TriggerFlowRuntimeData,
    *,
    stage: str,
    analyze: AnalyzeFn,
) -> dict[str, Any]:
    request = MatrixTaskRequest.model_validate(data.input)
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await _stage(data, "stage.started", stage)
    run = await analyze(request)
    await data.async_set_state("matrix_analysis_run", run, emit=False)
    for item in cast(list[dict[str, Any]], (run.get("brief") or {}).get("work_items") or []):
        await _deps(data).events.publish(
            request.task_id,
            "work_item.ready",
            {
                "work_item_id": item.get("work_item_id"),
                "kind": item.get("kind"),
            },
        )
    for draft in cast(list[dict[str, Any]], run.get("drafts") or []):
        await _deps(data).events.publish(
            request.task_id,
            "draft.ready",
            {
                "draft_key": draft.get("draft_key"),
                "decision": draft.get("decision"),
                "degrade_op": draft.get("degrade_op"),
            },
        )
    await _stage(
        data,
        "stage.completed",
        stage,
        pipeline_status=run.get("status"),
        snapshot_id=run.get("snapshot_id"),
    )
    return request.model_dump(mode="json")


async def _publish_package(
    data: TriggerFlowRuntimeData,
    *,
    stage: str,
) -> dict[str, Any]:
    request = _request(data)
    await _stage(data, "stage.started", stage)
    run = cast(dict[str, Any], data.get_state("matrix_analysis_run"))
    if run.get("status") == "failed":
        raise MatrixTaskFailed(str(run.get("summary") or "matrix task failed"))
    drafts = [
        GatedDraft.model_validate(item)
        for item in cast(list[dict[str, Any]], run.get("drafts") or [])
    ]
    evidence = [
        EvidenceCard(
            ref_id=str(item.get("ref_id") or ""),
            title=str(item.get("title") or ""),
            ruling=str(item.get("ruling") or ""),
        )
        for item in cast(list[dict[str, Any]], run.get("evidence") or [])
        if item.get("ref_id")
    ]
    result = MatrixTaskResult(
        task_id=request.task_id,
        snapshot_id=str(run["snapshot_id"]),
        trace_ref=str(run["trace_ref"]),
        status="partial" if run.get("status") == "partial" else "completed",
        task_type=cast(Any, run["task_type"]),
        summary=str(run.get("summary") or ""),
        drafts=drafts,
        evidence=evidence,
        limitations=list(run.get("limitations") or []),
    )
    await data.async_set_state("result", result.model_dump(mode="json"), emit=False)
    await _deps(data).events.publish(
        request.task_id,
        "package.ready",
        {
            "summary": result.summary,
            "draft_count": len(result.drafts),
        },
    )
    await _stage(data, "stage.completed", stage)
    return result.model_dump(mode="json")


compose_service_flow = TriggerFlow(name="matrix-compose-service")
reply_service_flow = TriggerFlow(name="matrix-reply-service")
SERVICE_FLOWS: dict[Scenario, TriggerFlow] = {
    "compose": compose_service_flow,
    "reply": reply_service_flow,
}


@compose_service_flow.chunk
async def analyze_compose(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    return await _run_analysis(
        data,
        stage="analyze_compose",
        analyze=_deps(data).analyze_compose,
    )


@compose_service_flow.chunk
async def publish_compose_package(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    return await _publish_package(data, stage="publish_compose_package")


@reply_service_flow.chunk
async def analyze_reply(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    return await _run_analysis(
        data,
        stage="analyze_reply",
        analyze=_deps(data).analyze_reply,
    )


@reply_service_flow.chunk
async def publish_reply_package(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    return await _publish_package(data, stage="publish_reply_package")


compose_service_flow.to(analyze_compose).to(publish_compose_package)
reply_service_flow.to(analyze_reply).to(publish_reply_package)


class MatrixWorkflowWorker:
    def __init__(self, dependencies: WorkerDependencies) -> None:
        self.dependencies = dependencies

    async def execute_complex_task(
        self,
        request: MatrixTaskRequest,
    ) -> MatrixTaskResult:
        flow = SERVICE_FLOWS[request.scenario]
        execution = flow.create_execution(
            auto_close=False,
            runtime_resources={"worker_dependencies": self.dependencies},
        )
        await execution.async_start(request.model_dump(mode="json"))
        state = await execution.async_close()
        return MatrixTaskResult.model_validate(state["result"])
