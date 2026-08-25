"""M5：写帖检索 + Draft + ConstraintGate。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.config import KB_DEFAULT_EMBEDDING_PROFILE
from integrated_agent.runtimes.matrix.compose.draft_media import (
    resolve_draft_cta,
    resolve_draft_media,
    resolve_draft_refs,
    to_draft_media_cards,
)
from integrated_agent.runtimes.matrix.host.constraints import (
    AhoCorasickMatcher,
    KB_CITE_RE,
    apply_constraint_gate,
)
from integrated_agent.runtimes.matrix.host.models import (
    ComposeDraftOut,
    DraftMediaCard,
    GatedDraft,
    WorkItem,
)
from integrated_agent.runtimes.matrix.host.snapshots import Snapshot, merged_forbidden_topics
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


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


async def retrieve_and_gate_compose_draft(
    *,
    work_item: WorkItem,
    snapshot: Snapshot,
    draft_once: Callable[..., Awaitable[ComposeDraftOut]],
    trace: TraceLog,
    knowledge: Any | None = None,
    user_id: str | None = None,
    embedding_profile_id: str | None = None,
    extra_info: dict[str, Any] | None = None,
    offered_cta_urls: list[str] | None = None,
    offered_media_keys: list[str] | None = None,
    source_text: str = "",
    repair: dict[str, Any] | None = None,
) -> tuple[GatedDraft, list[dict[str, Any]], list[str]]:
    kb_cards: list[dict[str, Any]] = []
    limitations: list[str] = []
    profile_id = (embedding_profile_id or KB_DEFAULT_EMBEDDING_PROFILE).strip()
    if knowledge is not None and user_id:
        kb_query = " ".join(
            part for part in [work_item.goal, *work_item.talking_points] if part
        ).strip() or work_item.goal
        try:
            kb_cards = await knowledge.retrieve_draft_cards(
                user_id,
                kb_query,
                profile_id,
            )
            trace.log(
                layer="business",
                event_type="business.matrix.kb_retrieved",
                status="completed",
                subject_id=work_item.work_item_id,
                output={"card_count": len(kb_cards), "embedding_profile_id": profile_id},
                facts={"card_count": len(kb_cards), "embedding_profile_id": profile_id},
            )
        except Exception as exc:
            limitations.append("kb_retrieve_failed")
            trace.log(
                layer="business",
                event_type="business.matrix.kb_retrieved",
                status="failed",
                subject_id=work_item.work_item_id,
                error=exc,
                facts={"embedding_profile_id": profile_id},
            )

    platform = snapshot.platform
    matcher = AhoCorasickMatcher(snapshot.policy.terms)
    offered_kb_ids = [str(card.get("kb_id") or "") for card in kb_cards]
    info: dict[str, Any] = {
        "guardrails": [item.model_dump(mode="json") for item in snapshot.guardrails],
        "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
        "max_chars": platform.max_chars,
        "mention_rules": platform.mention_rules,
        "offered_refs": [],
        "offered_kbs": kb_cards,
        "embedding_profile_id": profile_id,
        "policy": {
            "term_list_id": snapshot.policy.term_list_id,
            "ac_ready": snapshot.policy.ac_ready,
        },
    }
    if snapshot.account is not None:
        info["account"] = snapshot.account.model_dump(mode="json")
    if extra_info:
        info.update(extra_info)
    draft = await draft_once(
        work_item=work_item.model_dump(mode="json"),
        info=info,
        repair=repair,
    )
    text = draft.draft_text
    evidence_ids = draft.evidence_ids
    trace.log(
        layer="business",
        event_type="business.matrix.drafted",
        status="completed",
        subject_id=work_item.work_item_id,
        output={"work_item_id": work_item.work_item_id},
        facts={"reply_decision": None},
    )

    async def rewrite_once(issues: list[str]) -> str:
        repaired = await draft_once(
            work_item=work_item.model_dump(mode="json"),
            info=info,
            repair={"issues": issues, "previous_text": text},
        )
        return repaired.draft_text

    gated = await apply_constraint_gate(
        work_item_id=work_item.work_item_id,
        kind="compose_post",
        platform_key=work_item.platform_key,
        source_comment_key=work_item.source_comment_key,
        text=text,
        rationale=draft.rationale,
        evidence_ids=evidence_ids,
        risk_flags=draft.risk_flags,
        claim_types=draft.claim_types or work_item.claim_types,
        reply_decision=None,
        proposed_degrade=draft.proposed_degrade,
        max_chars=platform.max_chars,
        matcher=matcher,
        offered_refs=[],
        offered_kbs=offered_kb_ids,
        retrieval_state="empty",
        templates=[item.model_dump(mode="json") for item in snapshot.templates],
        rewrite_once=rewrite_once,
        offered_cta_urls=offered_cta_urls or [],
        offered_media_keys=offered_media_keys or [],
        source_text=source_text,
    )
    cited = [
        token
        for token in KB_CITE_RE.findall(gated.text or text)
        if token in offered_kb_ids
    ]
    for item in evidence_ids:
        if item in offered_kb_ids and item not in cited:
            cited.append(item)
    gated = gated.model_copy(update={"kb_ids": cited})
    trace.log(
        layer="business",
        event_type="business.matrix.gated",
        status="completed",
        subject_id=gated.draft_key,
        output=gated.model_dump(mode="json"),
        facts={"degrade_op": gated.degrade_op, "issues": gated.issues},
    )
    return gated, [], limitations


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
        gated, cards, kb_notes = await retrieve_and_gate_compose_draft(
            work_item=work_item,
            snapshot=snapshot,
            draft_once=draft_once,
            trace=trace,
            knowledge=_optional_resource(data, "knowledge"),
            user_id=str(_optional_resource(data, "kb_user_id") or "") or None,
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
    events = data.get_resource("events")
    request = data.get_state("request")
    task_id = str(request.get("task_id") or "") if isinstance(request, dict) else ""
    if events is not None and task_id:
        await events.publish(
            task_id,
            "draft.ready",
            {
                "draft_key": gated.draft_key,
                "decision": gated.decision,
                "degrade_op": gated.degrade_op,
            },
        )
    return payload


__all__ = ["gate_compose_draft", "retrieve_and_gate_compose_draft"]
