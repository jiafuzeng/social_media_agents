from __future__ import annotations

from integrated_agent.runtimes.matrix.analysis.snapshots import TWITTER_PLATFORM_KEY
from integrated_agent.runtimes.matrix.models import (
    BriefOut,
    ComposeDraftOut,
    ReplyDraftOut,
    Requirement,
    ReviewItemVerdict,
    ReviewOut,
    WorkItem,
)


_ATTACK_MARKERS = ("滚", "骗子", "白痴", "去死")


class ScriptedMatrixModel:
    """确定性模型，供 T07–T15 与 HTTP 回归使用。"""

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

    async def kb_chat_rewrite(
        self, *, query: str, history: list | None = None
    ) -> dict:
        del history
        return {"rewritten_query": str(query or "").strip()}

    async def kb_chat_split(
        self, *, rewritten_query: str, history: list | None = None
    ) -> dict:
        del history
        text = str(rewritten_query or "").strip()
        return {"retrieval_queries": [text] if text else []}

    async def kb_chat(
        self, *, query: str, info: dict, history: list | None = None
    ) -> dict:
        del query, history
        offered = list(info.get("offered_kbs") or [])
        if not offered:
            return {
                "answer": "当前模型下没有检索到可用手册段落，无法根据知识库作答。",
                "cited_kb_ids": [],
            }
        card = offered[0]
        kb_id = str(card.get("kb_id") or "k1")
        return {
            "answer": f"手册规定相关条款按检索结果办理[[kb:{kb_id}]]。",
            "cited_kb_ids": [kb_id],
        }


def _is_attack(text: str) -> bool:
    return any(marker in text for marker in _ATTACK_MARKERS)
