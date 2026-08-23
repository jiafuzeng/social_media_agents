"""M4 创作支：compose_brief → collect_compose_work_items。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.compose.material import (
    _align_material_cards,
    _collect_material_cards,
    _compose_media_bundle,
    _draft_angle_hint,
    _focus_hint_for_card,
    _resolve_post_count,
)
from integrated_agent.runtimes.matrix.host.models import BriefOut, WorkItem
from integrated_agent.runtimes.matrix.host.snapshots import (
    OFFERED_CLAIM_TYPES,
    Snapshot,
    TWITTER_PLATFORM_KEY,
    merged_forbidden_topics,
)
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


def _offered_cta_urls(snapshot: Snapshot) -> list[str]:
    account = snapshot.account
    if account is None:
        return []
    return list(account.offered_cta_urls or [])


def _validate_and_fit_brief(
    brief: BriefOut,
    *,
    post_count: int,
    platform_key: str,
) -> tuple[BriefOut, list[str]]:
    limitations: list[str] = []
    requirement_ids = {item.requirement_id for item in brief.requirements}
    kept: list[WorkItem] = []
    for item in brief.work_items:
        if item.kind != "compose_post":
            limitations.append(f"brief_invalid_kind:{item.work_item_id}")
            continue
        if item.platform_key != platform_key:
            limitations.append(f"brief_invalid_platform:{item.work_item_id}")
            continue
        if item.source_comment_key is not None:
            limitations.append(f"brief_has_comment_key:{item.work_item_id}")
            continue
        unknown_req = [req for req in item.requirement_ids if req not in requirement_ids]
        if unknown_req:
            limitations.append(f"brief_unknown_requirement:{item.work_item_id}")
        unknown_claims = [
            claim for claim in item.claim_types if claim not in OFFERED_CLAIM_TYPES
        ]
        if unknown_claims:
            limitations.append(f"brief_unknown_claim:{item.work_item_id}")
            item = item.model_copy(
                update={
                    "claim_types": [
                        claim for claim in item.claim_types if claim in OFFERED_CLAIM_TYPES
                    ]
                    or ["format"]
                }
            )
        kept.append(item)

    if len(kept) > post_count:
        kept = kept[:post_count]
        limitations.append(f"truncated_to_post_count:{post_count}")
    elif kept and len(kept) < post_count:
        seed = kept[0]
        while len(kept) < post_count:
            index = len(kept) + 1
            kept.append(
                seed.model_copy(
                    update={
                        "work_item_id": f"{seed.work_item_id}-v{index}",
                        "talking_points": list(seed.talking_points),
                    }
                )
            )
        limitations.append(f"expanded_to_post_count:{post_count}")
    elif not kept and post_count:
        req_id = brief.requirements[0].requirement_id if brief.requirements else "r1"
        kept = [
            WorkItem(
                work_item_id=f"w{index}",
                kind="compose_post",
                requirement_ids=[req_id],
                platform_key=platform_key,
                source_comment_key=None,
                goal=brief.normalized_brief or "写出可核验的预热稿",
                talking_points=[_draft_angle_hint(index)],
                claim_types=["format"],
            )
            for index in range(1, post_count + 1)
        ]
        limitations.append("brief_empty_work_items_fallback")

    return brief.model_copy(update={"work_items": kept}), limitations


async def compose_brief(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """包级计划：requirements + 正式 WorkItem 清单。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    trace = cast(TraceLog, data.require_resource("trace"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    task_id = str(request.get("task_id") or "")
    post_count = _resolve_post_count(request, snapshot)
    user_instruction = str(data.get_state("user_instruction") or request.get("text") or "").strip()
    material_list = _collect_material_cards(data)
    account = snapshot.account
    platform_key = snapshot.platform.platform_key or TWITTER_PLATFORM_KEY

    info: dict[str, Any] = {
        "post_count": post_count,
        "max_chars": snapshot.platform.max_chars,
        "platform": snapshot.platform.model_dump(mode="json"),
        "offered_claim_types": sorted(OFFERED_CLAIM_TYPES),
        "offered_cta_urls": _offered_cta_urls(snapshot),
        "forbidden_topics": merged_forbidden_topics(snapshot.guardrails),
        "material_list": material_list,
        "tweet_cards": list(data.get_state("tweet_cards") or []),
        "trend_cards": list(data.get_state("trend_cards") or []),
        "plan_summary": str(data.get_state("plan_summary") or ""),
    }
    if account is not None:
        info["account"] = account.model_dump(mode="json")

    limitations = list(cast(list[str], data.get_state("limitations") or []))
    try:
        result = await (
            Agently.create_agent(name="matrix-compose-brief")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input({"text": user_instruction})
            .info(info)
            .instruct(
                [
                    f"必须拆解恰好 {post_count} 条 work_item，对应 {post_count} 条独立推文计划。",
                    "只做包级计划，不写推文正文。",
                    "requirements 写运营目标（如可核验、增长 CTA），不要复述 instruct。",
                    "每条 work_item：kind=compose_post，platform_key 必须是快照平台，source_comment_key 留空。",
                    "goal 写本条要达成的写作目标；talking_points 写 Hook/角度要点（2–3 条）。",
                    "claim_types 只能从 info.offered_claim_types 选取。",
                    "增长 CTA：每条稿结尾只规划一个行动——关注系列/点置顶/去官方渠道；禁止评论区互动话术。",
                    "talking_points 只规划文字 CTA（关注系列/点置顶/去官方渠道）；不要写 [[cta:0]] 或 https。",
                    "可借鉴 info.material_list 的事实点与结构，不要整段抄袭素材正文。",
                    "看不见 source_post；不要发明未签发的链接或 tweet_id。",
                ]
            )
            .output(
                {
                    "normalized_brief": (str, "not_null"),
                    "requirements": (
                        [
                            {
                                "requirement_id": (str, "not_null"),
                                "description": (str, "not_null"),
                            }
                        ],
                        "not_null",
                    ),
                    "work_items": (
                        [
                            {
                                "work_item_id": (str, "not_null"),
                                "kind": (str, "必须是 compose_post", "not_null"),
                                "requirement_ids": ([str], "not_null"),
                                "platform_key": (str, "not_null"),
                                "source_comment_key": str,
                                "goal": (str, "not_null"),
                                "talking_points": [str],
                                "claim_types": [str],
                            }
                        ],
                        "not_null",
                    ),
                },
                format="json",
            )
            .async_start()
        )
        brief = BriefOut.model_validate(result)
        brief, extra = _validate_and_fit_brief(
            brief,
            post_count=post_count,
            platform_key=platform_key,
        )
        for note in extra:
            if note not in limitations:
                limitations.append(note)
    except Exception as exc:
        trace.log(
            layer="business",
            event_type="business.matrix.briefed",
            status="failed",
            subject_id=task_id,
            error=exc,
        )
        raise

    await data.async_set_state("brief", brief.model_dump(mode="json"), emit=False)
    await data.async_set_state("work_items", [item.model_dump(mode="json") for item in brief.work_items], emit=False)
    await data.async_set_state("limitations", limitations, emit=False)
    trace.log(
        layer="business",
        event_type="business.matrix.briefed",
        status="completed",
        subject_id=task_id,
        output=brief.model_dump(mode="json"),
        facts={"work_item_count": len(brief.work_items)},
    )
    return brief.model_dump(mode="json")


async def collect_compose_work_items(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    """合并 brief.work_items 与素材卡，产出 for_each 写稿载荷。"""
    snapshot = cast(Snapshot, data.require_resource("snapshot"))
    request = cast(dict[str, Any], data.get_state("request") or {})
    post_count = _resolve_post_count(request, snapshot)
    brief_raw = cast(dict[str, Any], data.get_state("brief") or {})
    brief = BriefOut.model_validate(brief_raw) if brief_raw else None
    if brief is None or not brief.work_items:
        await data.async_set_state("compose_draft_plan", [], emit=False)
        return []

    source_cards = _collect_material_cards(data)
    aligned_cards, allocation_mode = _align_material_cards(source_cards, post_count)
    offered_cta_urls = _offered_cta_urls(snapshot)
    draft_plan: list[dict[str, Any]] = []

    for index, work_item in enumerate(brief.work_items, start=1):
        material_card = aligned_cards[index - 1] if index - 1 < len(aligned_cards) else {}
        offered_media, media_catalog = _compose_media_bundle(material_card)
        angle_hint = _draft_angle_hint(index)
        talking_hint = "；".join(work_item.talking_points[:3]) or angle_hint
        focus_hint = _focus_hint_for_card(material_card, talking_hint)
        draft_plan.append(
            {
                "draft_key": f"d{index}",
                "draft_index": index,
                "total_count": post_count,
                "work_item": work_item.model_dump(mode="json"),
                "work_item_id": work_item.work_item_id,
                "goal": work_item.goal,
                "talking_points": list(work_item.talking_points),
                "claim_types": list(work_item.claim_types),
                "angle_hint": angle_hint,
                "focus_hint": focus_hint,
                "material_card": material_card,
                "card_allocation": allocation_mode,
                "offered_media": offered_media,
                "media_catalog": media_catalog,
                "offered_cta_urls": offered_cta_urls,
                "cta_index": 0 if offered_cta_urls else None,
            }
        )

    await data.async_set_state("post_count", post_count, emit=False)
    await data.async_set_state("material_list", aligned_cards, emit=False)
    await data.async_set_state("material_allocation", allocation_mode, emit=False)
    await data.async_set_state("compose_draft_plan", draft_plan, emit=False)
    return draft_plan


__all__ = ["collect_compose_work_items", "compose_brief"]
