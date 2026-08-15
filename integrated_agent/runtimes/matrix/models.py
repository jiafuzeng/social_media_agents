from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


DegradeOp = Literal["pass", "rewrite_safe", "template_fallback", "skip"]
ReplyDecision = Literal["reply", "acknowledge", "skip"]
WorkItemKind = Literal["compose_post", "reply_comment"]
Scenario = Literal["compose", "reply"]
TaskType = Literal["compose_post", "reply_comment"]
DraftStatus = Literal["ready", "degraded", "skipped", "failed"]
ItemVerdict = Literal["accept", "revise", "reject"]
CommentRole = Literal["root", "reply"]
GatedDecision = Literal["reply", "acknowledge", "skip", "publishable"]
RetrievalState = Literal["hits", "empty", "failed"]
TaskStatus = Literal["completed", "partial"]


class CommentIn(DomainModel):
    comment_key: str | None = None
    text: str = Field(min_length=1)
    role: CommentRole = "root"
    author_display: str | None = None


class MatrixTaskCreate(DomainModel):
    text: str = Field(min_length=1)
    scenario: Scenario
    platform_keys: list[str] = Field(default_factory=list)
    account_key: str = "default"
    brand_key: str = "default"
    need_trends: bool = False
    thread_key: str | None = None
    comments: list[CommentIn] | None = None
    requester: str = "course-user"
    channel: str = "web"

    @model_validator(mode="after")
    def bind_entry_invariants(self) -> "MatrixTaskCreate":
        if self.scenario == "compose":
            if self.thread_key is not None or self.comments is not None:
                raise ValueError("compose must not include thread_key or comments")
        return self


class MatrixTaskRequest(MatrixTaskCreate):
    task_id: str = Field(min_length=1)


class TaskAccepted(DomainModel):
    task_id: str
    status: Literal["accepted"] = "accepted"
    task_url: str
    events_url: str


class DegradeStep(DomainModel):
    op: DegradeOp
    issues: list[str] = Field(default_factory=list)
    attempt: int = 1


class GatedDraft(DomainModel):
    draft_key: str
    kind: WorkItemKind
    platform_key: str
    source_comment_key: str | None = None
    degrade_op: DegradeOp
    degrade_trace: list[DegradeStep] = Field(default_factory=list)
    text: str = ""
    rationale: str = ""
    decision: GatedDecision
    evidence_ids: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    status: DraftStatus
    issues: list[str] = Field(default_factory=list)


class EvidenceCard(DomainModel):
    ref_id: str
    title: str
    ruling: str


class MatrixTaskResult(DomainModel):
    task_id: str
    snapshot_id: str
    trace_ref: str
    status: TaskStatus
    task_type: TaskType
    summary: str
    drafts: list[GatedDraft] = Field(default_factory=list)
    evidence: list[EvidenceCard] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class TaskSnapshot(DomainModel):
    task_id: str
    status: Literal["accepted", "running", "completed", "failed"]
    result: MatrixTaskResult | None = None
    error: str | None = None


class TaskEvent(DomainModel):
    task_id: str
    sequence: int
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Requirement(DomainModel):
    requirement_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class WorkItem(DomainModel):
    work_item_id: str = Field(min_length=1)
    kind: WorkItemKind
    requirement_ids: list[str] = Field(min_length=1)
    platform_key: str = Field(min_length=1)
    source_comment_key: str | None = None
    goal: str = Field(min_length=1)
    talking_points: list[str] = Field(default_factory=list)
    claim_types: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> object:
        text = str(value or "").strip().lower()
        if text in {"compose", "compose_post", "post", "tweet"}:
            return "compose_post"
        if text in {"reply", "reply_comment", "comment"}:
            return "reply_comment"
        return value

    @field_validator("source_comment_key", mode="before")
    @classmethod
    def empty_comment_key(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class BriefOut(DomainModel):
    normalized_brief: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    work_items: list[WorkItem] = Field(min_length=1)


class ComposeBriefOut(BriefOut):
    pass


class ReplyBriefOut(BriefOut):
    pass


def _map_degrade(value: str | None) -> DegradeOp | None:
    if value is None:
        return None
    if value == "escalate":
        return "skip"
    allowed: set[str] = {"pass", "rewrite_safe", "template_fallback", "skip"}
    if value not in allowed:
        return None
    return value  # type: ignore[return-value]


class ComposeDraftOut(DomainModel):
    work_item_id: str = Field(min_length=1)
    stance_assessment: str = Field(min_length=1)
    claim_types: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    draft_text: str = ""
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    proposed_degrade: DegradeOp | None = None

    @field_validator("proposed_degrade", mode="before")
    @classmethod
    def map_escalate(cls, value: object) -> object:
        if value is None:
            return None
        return _map_degrade(str(value))


class ReplyDraftOut(DomainModel):
    work_item_id: str = Field(min_length=1)
    stance_assessment: str = Field(min_length=1)
    reply_decision: ReplyDecision
    claim_types: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    draft_text: str = ""
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    proposed_degrade: DegradeOp | None = None

    @field_validator("reply_decision", mode="before")
    @classmethod
    def map_escalate_decision(cls, value: object) -> object:
        if value == "escalate":
            return "skip"
        return value

    @field_validator("proposed_degrade", mode="before")
    @classmethod
    def map_escalate(cls, value: object) -> object:
        if value is None:
            return None
        return _map_degrade(str(value))


class ReviewItemVerdict(DomainModel):
    draft_key: str
    verdict: ItemVerdict
    revised_text: str | None = None
    notes: str | None = None

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, value: object) -> object:
        text = str(value or "accept").strip().lower()
        aliases = {
            "accept": "accept",
            "accepted": "accept",
            "keep": "accept",
            "通过": "accept",
            "接受": "accept",
            "revise": "revise",
            "revised": "revise",
            "修改": "revise",
            "修订": "revise",
            "reject": "reject",
            "rejected": "reject",
            "拒绝": "reject",
        }
        return aliases.get(text, "accept")

    @field_validator("revised_text", "notes", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ReviewOut(DomainModel):
    item_verdicts: list[ReviewItemVerdict] = Field(default_factory=list)
    package_summary: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("package_summary", mode="before")
    @classmethod
    def require_summary(cls, value: object) -> object:
        text = str(value or "").strip()
        return text or "草稿包已完成口径复核。"
