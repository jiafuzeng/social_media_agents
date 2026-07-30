"""问数任务领域模型与 API DTO。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    """禁止多余字段的领域基类。"""

    model_config = ConfigDict(extra="forbid")


class TaskCreate(DomainModel):
    """创建任务的入站命令。"""

    question: str = Field(min_length=1)
    requester: str = "course-user"
    channel: str = "web"


class TaskRequest(TaskCreate):
    """进入队列后的完整任务请求（含 task_id）。"""

    task_id: str = Field(min_length=1)


class TaskAccepted(DomainModel):
    """任务受理回执（HTTP 202）。"""

    task_id: str
    status: Literal["accepted"] = "accepted"
    task_url: str
    events_url: str


class Claim(DomainModel):
    """最终答案中的一条可追溯断言。"""

    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRef(DomainModel):
    """指向一次查询证据的稳定引用。"""

    evidence_id: str
    query_id: str
    result_ref: str
    summary: str


class ChartSeries(DomainModel):
    """图表中的一条序列。"""

    name: str
    values: list[float]


class ChartSpec(DomainModel):
    """前端可直接渲染的图表规格。"""

    chart_id: str
    title: str
    chart_type: Literal["bar", "line"]
    unit: str
    categories: list[str]
    series: list[ChartSeries]
    evidence_ids: list[str] = Field(default_factory=list)


class TaskResult(DomainModel):
    """问数任务成功完成后的结构化结果。"""

    task_id: str
    status: Literal["completed", "partial"]
    answer: str
    claims: list[Claim]
    evidence: list[EvidenceRef]
    evidence_refs: list[str]
    charts: list[ChartSpec] = Field(default_factory=list)
    data_snapshot_id: str
    trace_ref: str


class TaskSnapshot(DomainModel):
    """任务当前状态快照。"""

    task_id: str
    status: Literal["accepted", "running", "completed", "failed"]
    result: TaskResult | None = None
    error: str | None = None


class TaskEvent(DomainModel):
    """任务生命周期与阶段事件（供 SSE 推送）。"""

    task_id: str
    sequence: int
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
