from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from integrated_agent.runtimes.matrix.models import (
    BriefOut,
    GatedDraft,
    ReviewOut,
    WorkItemKind,
)

from .snapshots import Snapshot


class BriefValidationError(ValueError):
    pass


def parse_structured(model_cls: type[BaseModel], payload: Any) -> BaseModel:
    """去掉模型多写的字段后再按 extra=forbid 验收，避免整单因多余 key 失败。"""

    return model_cls.model_validate(_strip_to_fields(model_cls, _as_mapping(payload)))


def _as_mapping(payload: Any) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    raise ValueError(f"model output must be an object, got {type(payload).__name__}")


def _strip_to_fields(model_cls: type[BaseModel], payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for name, field in model_cls.model_fields.items():
        if name not in payload:
            continue
        cleaned[name] = _coerce_value(field.annotation, payload[name])
    return cleaned


def _coerce_value(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    args = [item for item in get_args(annotation) if item is not type(None)]
    if origin in {list, tuple}:
        inner = args[0] if args else Any
        if isinstance(value, str):
            return [value] if value.strip() else []
        if not isinstance(value, list):
            value = [] if value in {None, ""} else [value]
        return [_coerce_value(inner, item) for item in value]
    if origin is Union or origin is UnionType:
        for arg in args:
            if _is_model(arg) and isinstance(value, dict):
                return _strip_to_fields(arg, value)
        if value in {"", None}:
            return None
        return value
    if _is_model(annotation) and isinstance(value, dict):
        return _strip_to_fields(annotation, value)
    if value == "" and origin is not list:
        return value
    return value


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def sanitize_brief(
    brief: BriefOut,
    *,
    snapshot: Snapshot,
    expected_kind: WorkItemKind,
) -> BriefOut:
    """丢掉模型误用的枚举（如把 template_key 写进 claim_types），不因此失败整单。"""

    offered_claims = snapshot.offered_claim_types()
    default_claim = "format" if "format" in offered_claims else (
        sorted(offered_claims)[0] if offered_claims else "format"
    )
    items = []
    for item in brief.work_items:
        claims = [claim for claim in item.claim_types if claim in offered_claims]
        source = None if expected_kind == "compose_post" else item.source_comment_key
        items.append(
            item.model_copy(
                update={
                    "kind": expected_kind,
                    "claim_types": claims or [default_claim],
                    "source_comment_key": source or None,
                }
            )
        )
    return brief.model_copy(update={"work_items": items})


def validate_brief(
    brief: BriefOut,
    *,
    snapshot: Snapshot,
    expected_kind: WorkItemKind,
) -> None:
    requirement_ids = [item.requirement_id for item in brief.requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        raise BriefValidationError("requirement_id values must be unique")
    work_item_ids = [item.work_item_id for item in brief.work_items]
    if len(work_item_ids) != len(set(work_item_ids)):
        raise BriefValidationError("work_item_id values must be unique")
    known = set(requirement_ids)
    referenced = {
        requirement_id
        for item in brief.work_items
        for requirement_id in item.requirement_ids
    }
    unknown = referenced - known
    if unknown:
        raise BriefValidationError(
            "work_items reference unknown requirement ids: "
            + ", ".join(sorted(unknown))
        )
    missing = known - referenced
    if missing:
        raise BriefValidationError(
            "requirements are not covered by work_items: "
            + ", ".join(sorted(missing))
        )
    offered_platforms = snapshot.offered_platform_keys()
    offered_comments = snapshot.offered_comment_keys()
    offered_claims = snapshot.offered_claim_types()
    for item in brief.work_items:
        if item.kind != expected_kind:
            raise BriefValidationError(
                f"work_item {item.work_item_id} kind must be {expected_kind}"
            )
        if item.platform_key not in offered_platforms:
            raise BriefValidationError(
                f"unknown platform_key: {item.platform_key}"
            )
        extra_claims = set(item.claim_types) - offered_claims
        if extra_claims:
            raise BriefValidationError(
                "unknown claim_types: " + ", ".join(sorted(extra_claims))
            )
        if expected_kind == "reply_comment":
            if not item.source_comment_key:
                raise BriefValidationError(
                    f"work_item {item.work_item_id} missing source_comment_key"
                )
            if item.source_comment_key not in offered_comments:
                raise BriefValidationError(
                    f"unknown source_comment_key: {item.source_comment_key}"
                )
        elif item.source_comment_key is not None:
            raise BriefValidationError(
                "compose work_item must not set source_comment_key"
            )


def rollup_status(drafts: list[GatedDraft]) -> str:
    if not drafts:
        return "failed"
    ready = sum(item.status == "ready" for item in drafts)
    degraded = sum(item.status == "degraded" for item in drafts)
    skipped = sum(item.status == "skipped" for item in drafts)
    failed = sum(item.status == "failed" for item in drafts)
    if ready == len(drafts):
        return "completed"
    if ready == 0 and degraded == 0:
        return "failed"
    if (ready + degraded) > 0 and (skipped + failed) > 0:
        return "partial"
    return "completed"


async def apply_review(
    drafts: list[GatedDraft],
    review: ReviewOut,
    *,
    re_gate,
) -> tuple[list[GatedDraft], list[str]]:
    by_key = {item.draft_key: item for item in drafts}
    limitations = list(review.limitations)
    for verdict in review.item_verdicts:
        current = by_key.get(verdict.draft_key)
        if current is None:
            limitations.append(f"unknown_draft_key:{verdict.draft_key}")
            continue
        if current.degrade_op in {"skip", "template_fallback"}:
            continue
        if verdict.verdict == "revise" and verdict.revised_text:
            gated = await re_gate(current, verdict.revised_text)
            if gated.status in {"ready", "degraded"}:
                by_key[current.draft_key] = gated
            else:
                limitations.append(f"revise_rejected:{current.draft_key}")
        elif verdict.verdict == "reject" and current.degrade_op == "pass":
            skipped = current.model_copy(
                update={
                    "degrade_op": "skip",
                    "text": "",
                    "decision": "skip",
                    "status": "skipped",
                    "issues": current.issues + ["review_reject"],
                }
            )
            by_key[current.draft_key] = skipped
    ordered = [by_key[item.draft_key] for item in drafts]
    return ordered, limitations
