"""回评测试用确定性模型。不混入写帖或召回聊天。"""

from __future__ import annotations

from integrated_agent.runtimes.matrix.host.snapshots import TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.host.models import (
    BriefOut,
    ReplyDraftOut,
    Requirement,
    ReviewItemVerdict,
    ReviewOut,
    WorkItem,
)


_ATTACK_MARKERS = ("滚", "骗子", "白痴", "去死")


def _is_attack(text: str) -> bool:
    return any(marker in text for marker in _ATTACK_MARKERS)


class ScriptedReplyModel:
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

    async def reply_brief(self, *, text: str, info: dict) -> BriefOut:
        comments = list(info.get("comments") or [])
        req_id = "r-reply"
        items: list[WorkItem] = []
        target = int(info.get("reply_count") or 0) or len(comments)
        if len(comments) == 1:
            comment = comments[0]
            body = str(comment.get("text") or "")
            claim_types = ["harassment", "reply_risk"] if _is_attack(body) else ["format"]
            key = str(comment["comment_key"])
            for index in range(1, max(target, 1) + 1):
                items.append(
                    WorkItem(
                        work_item_id=f"rw{index}",
                        kind="reply_comment",
                        requirement_ids=[req_id],
                        platform_key=TWITTER_PLATFORM_KEY,
                        source_comment_key=key,
                        goal="判断该不该回并起草",
                        talking_points=[body[:40]],
                        claim_types=claim_types,
                    )
                )
        else:
            for index, comment in enumerate(comments, start=1):
                body = str(comment.get("text") or "")
                claim_types = ["harassment", "reply_risk"] if _is_attack(body) else ["format"]
                items.append(
                    WorkItem(
                        work_item_id=f"rw{index}",
                        kind="reply_comment",
                        requirement_ids=[req_id],
                        platform_key=TWITTER_PLATFORM_KEY,
                        source_comment_key=str(comment["comment_key"]),
                        goal="判断该不该回并起草",
                        talking_points=[body[:40]],
                        claim_types=claim_types,
                    )
                )
        return BriefOut(
            normalized_brief=text.strip() or "回复已签发评论",
            requirements=[
                Requirement(requirement_id=req_id, description="逐条评理并起草")
            ],
            work_items=items,
        )

    async def reply_draft(
        self,
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ReplyDraftOut:
        work_item_id = str(work_item["work_item_id"])
        comment = str((info.get("comment") or {}).get("text") or "")
        max_chars = int(info.get("max_chars") or 280)
        offered = [str(card["ref_id"]) for card in info.get("offered_refs") or []]
        if _is_attack(comment):
            decision = "skip"
            text = self.draft_text_overrides.get(work_item_id, "")
            rationale = "人身攻击不扩大冲突，跳过回复。"
        else:
            decision = "reply"
            text = self.draft_text_overrides.get(
                work_item_id,
                "感谢提问，请以官方说明为准，我们不承诺收益或疗效。",
            )
            rationale = "针对提问给出可核验指引，不承诺结果。"
        if repair and "over_limit" in (repair.get("issues") or []):
            text = text[:max_chars]
        evidence_ids = self.evidence_overrides.get(work_item_id)
        if evidence_ids is None:
            evidence_ids = offered[:1]
        return ReplyDraftOut(
            work_item_id=work_item_id,
            stance_assessment="same-response",
            reply_decision=decision,  # type: ignore[arg-type]
            claim_types=list(work_item.get("claim_types") or []),
            risk_flags=["attack"] if decision == "skip" else [],
            draft_text=text,
            rationale=rationale,
            evidence_ids=evidence_ids if decision != "skip" else [],
            proposed_degrade="skip" if decision == "skip" else None,
        )

    async def reply_review(self, *, package: dict, info: dict) -> ReviewOut:
        del info
        drafts = list(package.get("drafts") or [])
        verdicts: list[ReviewItemVerdict] = []
        for item in drafts:
            if self.review_lift_skip and item.get("degrade_op") == "skip":
                verdicts.append(
                    ReviewItemVerdict(
                        draft_key=str(item["draft_key"]),
                        verdict="revise",
                        revised_text="我们理解你的心情，欢迎继续交流。",
                        notes="try lift skip",
                    )
                )
            else:
                verdicts.append(
                    ReviewItemVerdict(
                        draft_key=str(item["draft_key"]),
                        verdict="accept",
                        notes="keep",
                    )
                )
        return ReviewOut(
            item_verdicts=verdicts,
            package_summary="评论回复包已过硬门，攻击项保持 skip。",
            limitations=list(package.get("limitations") or []),
        )
