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

MIN_COMPOSE_POSTS = 1
MAX_COMPOSE_POSTS = 10


class CommentIn(DomainModel):
    comment_key: str | None = None
    text: str = Field(min_length=1)
    role: CommentRole = "root"
    author_display: str | None = None


class MatrixTaskCreate(DomainModel):
    """矩阵任务入站契约。Web 与 Gateway 最终都落成此对象；禁止 extra 字段，禁止 scenario=auto。"""

    text: str = Field(
        min_length=1,
        description="用户原话：创作是主题/口径；回复有 comments 时是运营指令，否则签发为待回评论",
    )
    scenario: Scenario = Field(description="入口绑定的流程，compose 或 reply，决定跑哪张 Flow")
    account_key: str | None = Field(
        default=None,
        description="写帖获客人设；仅 compose。reply 携带则 422",
    )
    interaction_key: str | None = Field(
        default=None,
        description="回评互动规则；仅 reply。compose 携带则 422",
    )
    need_trends: bool = Field(
        default=False,
        description="是否先抓热帖再写；仅 compose 有效，reply 携带则忽略",
    )
    post_count: int | None = Field(
        default=None,
        ge=MIN_COMPOSE_POSTS,
        le=MAX_COMPOSE_POSTS,
        description="本次要出几条推文；仅 compose，范围 1–10。省略则由模型在平台上限内决定",
    )
    reply_count: int | None = Field(
        default=None,
        ge=MIN_COMPOSE_POSTS,
        le=MAX_COMPOSE_POSTS,
        description="本次要出几条回复草稿；仅 reply，范围 1–10。一条评论时为变体数，多条评论时为覆盖条数。省略则每条评论一条",
    )
    comments: list[CommentIn] | None = Field(
        default=None,
        description="直接提交的评论；仅 reply。省略则用 text 签发一条。compose 携带则 422",
    )
    requester: str = Field(default="course-user", description="提交者，仅审计/日志，不参与拆解")
    channel: str = Field(default="web", description="来源通道 web 或 gateway，不改流程")

    @model_validator(mode="after")
    def bind_entry_invariants(self) -> "MatrixTaskCreate":
        if self.scenario == "compose":
            if self.comments is not None:
                raise ValueError("compose must not include comments")
            if self.interaction_key is not None:
                raise ValueError("compose must not include interaction_key")
            if self.reply_count is not None:
                raise ValueError("compose must not include reply_count")
            if not self.account_key:
                self.account_key = "default"
        if self.scenario == "reply":
            if self.post_count is not None:
                raise ValueError("reply must not include post_count")
            if self.account_key is not None:
                raise ValueError("reply must not include account_key")
            if not self.interaction_key:
                self.interaction_key = "help-first"
            if not self.comments:
                self.comments = [CommentIn(text=self.text, role="root")]
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

