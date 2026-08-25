"""回评测试用确定性模型。不混入写帖或召回聊天。"""

from __future__ import annotations

from integrated_agent.runtimes.matrix.host.snapshots import TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.host.models import (
    BriefOut,
    ReplyDraftOut,
    Requirement,
    ReviewItemVerdict,
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
        review_revise_pass: bool = False,
    ) -> None:
        self.draft_text_overrides = draft_text_overrides or {}
        self.evidence_overrides = evidence_overrides or {}
        self.review_lift_skip = review_lift_skip
        self.compose_work_item_count = compose_work_item_count
        self.review_revise_pass = review_revise_pass
        self._revise_seen: set[str] = set()

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
        comment = str(info.get("comment") or "")
        max_chars = int(info.get("max_chars") or 280)
        if _is_attack(comment):
            decision = "acknowledge"
            text = self.draft_text_overrides.get(
                work_item_id,
                "这条我们不在评论区展开，有问题请走官方渠道。",
            )
            rationale = "人身攻击不扩大冲突，公开收口。"
        else:
            decision = "reply"
            text = self.draft_text_overrides.get(
                work_item_id,
                "感谢提问，请以官方说明为准，我们不承诺收益或疗效。",
            )
            rationale = "针对提问给出可核验指引，不承诺结果。"
        if repair and "over_limit" in (repair.get("issues") or []):
            text = text[:max_chars]
        if repair and decision != "skip" and (
            repair.get("review_notes") or repair.get("regen_reason") == "review_not_compliant"
        ):
            text = "感谢提问，请以产品页成分说明为准，我们不承诺收益或疗效。"
        evidence_ids = self.evidence_overrides.get(work_item_id) or []
        return ReplyDraftOut(
            work_item_id=work_item_id,
            stance_assessment="same-response",
            reply_decision=decision,  # type: ignore[arg-type]
            claim_types=list(work_item.get("claim_types") or []),
            risk_flags=["attack"] if decision == "acknowledge" and _is_attack(comment) else [],
            draft_text=text,
            rationale=rationale,
            evidence_ids=evidence_ids,
            proposed_degrade=None,
        )

    async def reply_review(self, *, draft: dict, info: dict | None = None) -> ReviewItemVerdict:
        del info
        if not hasattr(self, "_revise_seen"):
            self._revise_seen = set()
        key = str(draft.get("work_item_id") or "")
        if getattr(self, "review_revise_pass", False) and key not in self._revise_seen:
            self._revise_seen.add(key)
            return ReviewItemVerdict(
                draft_key=key,
                verdict="revise",
                notes="语气再克制一些，去掉任何像承诺的措辞。",
            )
        return ReviewItemVerdict(draft_key=key, verdict="accept", notes="keep")
