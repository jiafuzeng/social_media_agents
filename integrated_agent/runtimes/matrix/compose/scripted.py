"""写帖测试用确定性模型。不混入回评或召回聊天。"""

from __future__ import annotations

from integrated_agent.runtimes.matrix.host.snapshots import TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.models import (
    BriefOut,
    ComposeDraftOut,
    Requirement,
    ReviewItemVerdict,
    ReviewOut,
    WorkItem,
)


class ScriptedComposeModel:
    def __init__(
        self,
        *,
        draft_text_overrides: dict[str, str] | None = None,
        evidence_overrides: dict[str, list[str]] | None = None,
        review_lift_skip: bool = False,
        compose_work_item_count: int = 1,
    ) -> None:
        self.draft_text_overrides = draft_text_overrides or {}
        self.evidence_overrides = evidence_overrides or {}
        self.review_lift_skip = review_lift_skip
        self.compose_work_item_count = compose_work_item_count

    async def compose_brief(self, *, text: str, info: dict) -> BriefOut:
        del info
        req_id = "r-compose"
        count = max(1, self.compose_work_item_count)
        return BriefOut(
            normalized_brief=text.strip(),
            requirements=[Requirement(requirement_id=req_id, description=text)],
            work_items=[
                WorkItem(
                    work_item_id=f"w{index}",
                    kind="compose_post",
                    requirement_ids=[req_id],
                    platform_key=TWITTER_PLATFORM_KEY,
                    source_comment_key=None,
                    goal="写出可核验的预热稿",
                    talking_points=["上新", "官方渠道"],
                    claim_types=["format"],
                )
                for index in range(1, count + 1)
            ],
        )

    async def compose_draft(
        self,
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ComposeDraftOut:
        work_item_id = str(work_item["work_item_id"])
        max_chars = int(info.get("max_chars") or 280)
        offered = [str(card["ref_id"]) for card in info.get("offered_refs") or []]
        text = self.draft_text_overrides.get(work_item_id)
        if text is None:
            text = "秋季上新已公布成分与用法，详情见官方说明。"
        if repair and "over_limit" in (repair.get("issues") or []):
            text = text[:max_chars]
        evidence_ids = self.evidence_overrides.get(work_item_id)
        if evidence_ids is None:
            evidence_ids = offered[:1]
        return ComposeDraftOut(
            work_item_id=work_item_id,
            stance_assessment="same-response",
            claim_types=list(work_item.get("claim_types") or ["format"]),
            risk_flags=[],
            draft_text=text,
            rationale="按平台字数与可核验口径起草，不承诺收益或疗效。",
            evidence_ids=evidence_ids,
            proposed_degrade=None,
        )

    async def compose_review(self, *, package: dict, info: dict) -> ReviewOut:
        del info
        drafts = list(package.get("drafts") or [])
        verdicts = [
            ReviewItemVerdict(
                draft_key=str(item["draft_key"]),
                verdict="accept",
                notes="keep",
            )
            for item in drafts
        ]
        return ReviewOut(
            item_verdicts=verdicts,
            package_summary="矩阵预热稿已过硬门，口径一致。",
            limitations=list(package.get("limitations") or []),
        )
