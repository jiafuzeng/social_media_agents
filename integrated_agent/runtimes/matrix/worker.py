from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agently import TriggerFlow, TriggerFlowRuntimeData

from .analysis import MatrixAnalysisCapability
from .models import (
    EvidenceCard,
    GatedDraft,
    MatrixTaskRequest,
    MatrixTaskResult,
)
from .service import MatrixTaskFailed
from .stores import InMemoryEventStore


@dataclass
class WorkerDependencies:
    matrix_analysis: MatrixAnalysisCapability
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


matrix_flow = TriggerFlow(name="enterprise-matrix-service")


@matrix_flow.chunk
async def analyze_matrix(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = MatrixTaskRequest.model_validate(data.input)
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await _stage(data, "stage.started", "analyze_matrix")
    run = await _deps(data).matrix_analysis.analyze(request)
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
        "analyze_matrix",
        pipeline_status=run.get("status"),
        snapshot_id=run.get("snapshot_id"),
    )
    return request.model_dump(mode="json")


@matrix_flow.chunk
async def publish_package(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = _request(data)
    await _stage(data, "stage.started", "publish_package")
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
    await _stage(data, "stage.completed", "publish_package")
    return result.model_dump(mode="json")


matrix_flow.to(analyze_matrix).to(publish_package)


class MatrixWorkflowWorker:
    def __init__(self, dependencies: WorkerDependencies) -> None:
        self.dependencies = dependencies

    async def execute_complex_task(
        self,
        request: MatrixTaskRequest,
    ) -> MatrixTaskResult:
        execution = matrix_flow.create_execution(
            auto_close=False,
            runtime_resources={"worker_dependencies": self.dependencies},
        )
        await execution.async_start(request.model_dump(mode="json"))
        state = await execution.async_close()
        return MatrixTaskResult.model_validate(state["result"])
