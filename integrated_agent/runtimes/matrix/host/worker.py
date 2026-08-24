from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from .models import (
    EvidenceCard,
    GatedDraft,
    MatrixTaskRequest,
    MatrixTaskResult,
    TaskStatus,
    coerce_media_links,
)
from .stores import InMemoryEventStore

AnalyzeFn = Callable[[MatrixTaskRequest], Awaitable[dict[str, Any]]]


def _rollup_task_status(raw: Any) -> TaskStatus:
    status = str(raw or "").strip().lower()
    if status in {"completed", "partial", "failed"}:
        return cast(TaskStatus, status)
    return "failed"


@dataclass
class WorkerDependencies:
    analyze_compose: AnalyzeFn
    analyze_reply: AnalyzeFn
    events: InMemoryEventStore


def result_from_run(run: dict[str, Any], *, task_id: str) -> MatrixTaskResult:
    drafts = [
        GatedDraft.model_validate(item)
        for item in cast(list[dict[str, Any]], run.get("drafts") or [])
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
        for item in cast(list[dict[str, Any]], run.get("evidence") or [])
        if item.get("ref_id")
    ]
    return MatrixTaskResult(
        task_id=task_id,
        snapshot_id=str(run.get("snapshot_id") or ""),
        trace_ref=str(run.get("trace_ref") or ""),
        status=_rollup_task_status(run.get("status")),
        task_type=cast(Any, run.get("task_type") or "compose_post"),
        summary=str(run.get("summary") or ""),
        drafts=drafts,
        evidence=evidence,
        limitations=list(run.get("limitations") or []),
    )


class MatrixWorkflowWorker:
    def __init__(self, dependencies: WorkerDependencies) -> None:
        self.dependencies = dependencies

    async def execute_complex_task(
        self,
        request: MatrixTaskRequest,
    ) -> MatrixTaskResult:
        if request.scenario == "compose":
            run = await self.dependencies.analyze_compose(request)
        elif request.scenario == "reply":
            run = await self.dependencies.analyze_reply(request)
        else:
            raise ValueError(f"unsupported scenario: {request.scenario}")
        result = result_from_run(run, task_id=request.task_id)
        await self.dependencies.events.publish(
            request.task_id,
            "package.ready",
            {
                "summary": result.summary,
                "draft_count": len(result.drafts),
            },
        )
        return result
