"""M5：写帖检索 + Draft + ConstraintGate（与回评共用 drafting 骨架）。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.draft_media import (
    resolve_draft_cta,
    resolve_draft_media,
    resolve_draft_refs,
    to_draft_media_cards,
)
from integrated_agent.runtimes.matrix.host.drafting import retrieve_and_gate_draft
from integrated_agent.runtimes.matrix.host.models import (
    ComposeDraftOut,
    DraftMediaCard,
    GatedDraft,
    WorkItem,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog
from integrated_agent.runtimes.matrix.host.progress import publish_progress


def _optional_resource(data: TriggerFlowRuntimeData, key: str) -> Any:
    try:
        return data.require_resource(key)
    except Exception:
        return None


def _work_item_from_payload(work: dict[str, Any]) -> WorkItem:
    formal = work.get("work_item") if isinstance(work.get("work_item"), dict) else {}
    payload = {
        "work_item_id": str(
            formal.get("work_item_id")
            or work.get("work_item_id")
            or work.get("draft_key")
            or "w1"
        ),
        "kind": "compose_post",
        "requirement_ids": list(formal.get("requirement_ids") or ["r1"]),
        "platform_key": str(formal.get("platform_key") or "x-twitter"),
        "source_comment_key": None,
        "goal": str(formal.get("goal") or work.get("goal") or "写出可核验推文"),
        "talking_points": list(
            formal.get("talking_points") or work.get("talking_points") or []
        ),
        "claim_types": list(formal.get("claim_types") or work.get("claim_types") or ["format"]),
    }
    return WorkItem.model_validate(payload)


def _offered_media_keys(offered_media: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("media_key") or "").strip()
        for item in offered_media
        if str(item.get("media_key") or "").strip()
    ]


def _attach_media(gated: GatedDraft, media: list[dict[str, Any]]) -> GatedDraft:
    if not media or gated.degrade_op == "skip" or not gated.text.strip():
        return gated
    cards = [DraftMediaCard.model_validate(item) for item in media]
    return gated.model_copy(update={"media": cards})


async def gate_compose_draft(
    data: TriggerFlowRuntimeData,
    *,
    work: dict[str, Any],
    draft_agent_name: str,
    instruct: list[str],
    source_text: str = "",
    draft_repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """检索案例/手册 → 写稿 → Gate；返回 for_each 载荷。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    work_item = _work_item_from_payload(work)
    draft_key = str(work.get("draft_key") or f"d{work.get('draft_index') or 1}")
    offered_cta_urls = [
        str(item).strip()
        for item in (work.get("offered_cta_urls") or [])
        if str(item).strip()
    ]
    if not offered_cta_urls and snapshot.account is not None:
        offered_cta_urls = list(snapshot.account.offered_cta_urls or [])
    offered_media = [
        item for item in (work.get("offered_media") or []) if isinstance(item, dict)
    ]
    media_catalog = [
        item for item in (work.get("media_catalog") or []) if isinstance(item, dict)
    ]
    media_keys = _offered_media_keys(offered_media)
    user_instruction = str(
        data.get_state("user_instruction")
        or cast(dict[str, Any], data.get_state("request") or {}).get("text")
        or ""
    ).strip()
    extra_info: dict[str, Any] = {
        "intent": str(data.get_state("intent") or "compose"),
        "draft_key": draft_key,
        "draft_index": int(work.get("draft_index") or 1),
        "total_count": int(work.get("total_count") or 1),
        "material_card": work.get("material_card") or {},
        "offered_media": offered_media,
        "media_catalog": media_catalog,
        "offered_cta_urls": offered_cta_urls,
        "brief": data.get_state("brief"),
        "rewrite_plan_card": work.get("rewrite_plan_card") or data.get_state("rewrite_plan_card"),
        "focus_hint": str(work.get("focus_hint") or ""),
        "user_instruction": user_instruction,
        "allocated_source_text": str(
            work.get("allocated_source_text") or source_text or ""
        ).strip(),
        "reference_tweet": work.get("reference_tweet"),
        "cta_url": str(work.get("cta_url") or "").strip(),
    }

    async def draft_once(
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ComposeDraftOut:
        merged = {**info, **extra_info}
        effective_repair = repair if repair is not None else (draft_repair or {})
        result = await (
            Agently.create_agent(name=draft_agent_name)
            .input(
                {
                    "work_item": work_item,
                    "repair": effective_repair,
                    "user_instruction": user_instruction,
                    "source_text": str(
                        merged.get("allocated_source_text") or source_text or ""
                    ).strip(),
                }
            )
            .info(merged)
            .instruct(instruct)
            .output(
                {
                    "work_item_id": (str, "not_null"),
                    "stance_assessment": (str, "not_null"),
                    "claim_types": [str],
                    "risk_flags": [str],
                    "draft_text": str,
                    "rationale": (str, "not_null"),
                    "evidence_ids": [str],
                    "proposed_degrade": str,
                },
                format="json",
            )
            .async_start(max_retries=1)
        )
        if hasattr(result, "model_dump"):
            payload = result.model_dump(mode="json")
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            payload = {}
        payload["work_item_id"] = str(work_item["work_item_id"])
        return ComposeDraftOut.model_validate(payload)

    draft_media: list[dict[str, Any]] = []
    limitations: list[str] = []
    try:
        gated, cards, kb_notes = await retrieve_and_gate_draft(
            work_item=work_item,
            snapshot=snapshot,
            data_root=data.require_resource("data_root"),
            draft_once=draft_once,
            trace=trace,
            kind="compose_post",
            knowledge=_optional_resource(data, "knowledge"),
            user_id=str(_optional_resource(data, "kb_user_id") or "") or None,
            embedding_profile_id=str(_optional_resource(data, "kb_profile_id") or "") or None,
            extra_info=extra_info,
            offered_cta_urls=offered_cta_urls,
            offered_media_keys=media_keys,
            source_text=source_text,
        )
        for note in kb_notes:
            if note not in limitations:
                limitations.append(note)
        if gated.text.strip():
            display_text = resolve_draft_refs(gated.text)
            display_text = resolve_draft_cta(
                display_text,
                offered_cta_urls=offered_cta_urls,
            )
            if media_catalog:
                display_text, attached = resolve_draft_media(
                    display_text,
                    media_catalog=media_catalog,
                    default_reuse=bool(offered_media),
                )
                draft_media = to_draft_media_cards(attached)
            gated = gated.model_copy(update={"text": display_text})
        gated = gated.model_copy(update={"draft_key": draft_key})
        gated = _attach_media(gated, draft_media)
    except Exception as exc:
        error = f"compose_draft_error:{draft_key}:{type(exc).__name__}"
        gated = GatedDraft(
            draft_key=draft_key,
            kind="compose_post",
            platform_key=work_item.platform_key,
            source_comment_key=None,
            degrade_op="skip",
            degrade_trace=[],
            text="",
            rationale=f"写稿失败：{type(exc).__name__}",
            decision="skip",
            evidence_ids=[],
            kb_ids=[],
            risk_flags=[],
            status="failed",
            issues=[error],
        )
        cards = []
        limitations.append(error)

    if limitations:
        state_limits = list(cast(list[str], data.get_state("limitations") or []))
        for note in limitations:
            if note not in state_limits:
                state_limits.append(note)
        await data.async_set_state("limitations", state_limits, emit=False)

    for card in cards:
        await data.async_append_state("evidence_cards", card, emit=False)

    payload = gated.model_dump(mode="json")
    payload.update(
        {
            "draft_index": int(work.get("draft_index") or 1),
            "ok": bool(gated.text.strip()),
            "error": gated.issues[0] if gated.issues else "",
            "material_card": work.get("material_card") or {},
        }
    )
    await publish_progress(
        data,
        "draft.ready",
        {
            "draft_key": gated.draft_key,
            "decision": gated.decision,
            "degrade_op": gated.degrade_op,
        },
    )
    return payload


__all__ = ["gate_compose_draft"]
