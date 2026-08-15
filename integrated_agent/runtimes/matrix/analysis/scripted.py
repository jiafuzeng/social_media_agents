from __future__ import annotations

from typing import Protocol

from integrated_agent.runtimes.matrix.models import (
    ComposeBriefOut,
    ComposeDraftOut,
    ReplyBriefOut,
    ReplyDraftOut,
    Requirement,
    ReviewItemVerdict,
    ReviewOut,
    WorkItem,
)


class MatrixLanguageModel(Protocol):
    async def compose_brief(self, *, text: str, info: dict) -> ComposeBriefOut: ...

    async def compose_draft(
        self,
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ComposeDraftOut: ...

    async def compose_review(self, *, package: dict, info: dict) -> ReviewOut: ...

    async def reply_brief(self, *, text: str, info: dict) -> ReplyBriefOut: ...

    async def reply_draft(
        self,
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ReplyDraftOut: ...

    async def reply_review(self, *, package: dict, info: dict) -> ReviewOut: ...


_ATTACK_MARKERS = ("滚", "骗子", "白痴", "去死")


class ScriptedMatrixModel:
    """确定性模型，供 T07–T15 与 HTTP 回归使用。"""

    def __init__(
        self,
        *,
        draft_text_overrides: dict[str, str] | None = None,
        evidence_overrides: dict[str, list[str]] | None = None,
        review_lift_skip: bool = False,
        omit_requirement: bool = False,
    ) -> None:
        self.draft_text_overrides = draft_text_overrides or {}
        self.evidence_overrides = evidence_overrides or {}
        self.review_lift_skip = review_lift_skip
        self.omit_requirement = omit_requirement

    async def compose_brief(self, *, text: str, info: dict) -> ComposeBriefOut:
        platforms = list(info.get("platforms") or [])
        req_id = "r-compose"
        items: list[WorkItem] = []
        for index, platform in enumerate(platforms, start=1):
            items.append(
                WorkItem(
                    work_item_id=f"w{index}",
                    kind="compose_post",
                    requirement_ids=[] if self.omit_requirement else [req_id],
                    platform_key=str(platform["platform_key"]),
                    source_comment_key=None,
                    goal="写出可核验的预热稿",
                    talking_points=["上新", "官方渠道"],
                    claim_types=["format"],
                )
            )
        requirements = [
            Requirement(requirement_id=req_id, description=text)
        ]
        return ComposeBriefOut(
            normalized_brief=text.strip(),
            requirements=requirements,
            work_items=items,
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

    async def reply_brief(self, *, text: str, info: dict) -> ReplyBriefOut:
        comments = list(info.get("comments") or [])
        platform_key = str(
            (info.get("platforms") or [{}])[0].get("platform_key") or "x-twitter"
        )
        req_id = "r-reply"
        items: list[WorkItem] = []
        for index, comment in enumerate(comments, start=1):
            body = str(comment.get("text") or "")
            claim_types = ["harassment", "reply_risk"] if _is_attack(body) else ["format"]
            items.append(
                WorkItem(
                    work_item_id=f"rw{index}",
                    kind="reply_comment",
                    requirement_ids=[req_id],
                    platform_key=platform_key,
                    source_comment_key=str(comment["comment_key"]),
                    goal="判断该不该回并起草",
                    talking_points=[body[:40]],
                    claim_types=claim_types,
                )
            )
        return ReplyBriefOut(
            normalized_brief=text.strip() or "回复样例线程",
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


def _is_attack(text: str) -> bool:
    return any(marker in text for marker in _ATTACK_MARKERS)
