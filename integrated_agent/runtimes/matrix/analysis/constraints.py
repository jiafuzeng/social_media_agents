from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from typing import Awaitable

from integrated_agent.runtimes.matrix.models import (
    DegradeOp,
    DegradeStep,
    DraftStatus,
    GatedDecision,
    GatedDraft,
    WorkItemKind,
)


CITATION_CLAIM_TYPES = frozenset(
    {
        "guaranteed_return",
        "efficacy",
        "medical",
        "crypto_promotion",
        "superlative",
    }
)
ALLOWED_DEGRADE_OPS: frozenset[str] = frozenset(
    {"pass", "rewrite_safe", "template_fallback", "skip"}
)
HARD_ISSUE_PREFIXES = ("forbidden_term:", "missing_ref_on_empty_rag")


class AhoCorasickMatcher:
    """字符级 Aho-Corasick。中文按子串命中，与 P0 词表扫描一致。"""

    def __init__(self, patterns: Sequence[str]) -> None:
        self._root = _Node()
        for pattern in patterns:
            if not pattern:
                continue
            node = self._root
            for char in pattern:
                node = node.children.setdefault(char, _Node())
            node.outputs.append(pattern)
        self._build_fail()

    def _build_fail(self) -> None:
        queue: deque[_Node] = deque()
        for child in self._root.children.values():
            child.fail = self._root
            queue.append(child)
        while queue:
            node = queue.popleft()
            for char, child in node.children.items():
                fail = node.fail
                while fail is not None and char not in fail.children:
                    fail = fail.fail
                if fail is not None and char in fail.children:
                    child.fail = fail.children[char]
                else:
                    child.fail = self._root
                child.outputs.extend(child.fail.outputs)
                queue.append(child)

    def find(self, text: str) -> list[str]:
        hits: list[str] = []
        seen: set[str] = set()
        node = self._root
        for char in text:
            while node is not self._root and char not in node.children:
                node = node.fail or self._root
            node = node.children.get(char, self._root)
            for pattern in node.outputs:
                if pattern not in seen:
                    seen.add(pattern)
                    hits.append(pattern)
        return hits


class _Node:
    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.fail: _Node | None = None
        self.outputs: list[str] = []


def requires_citation(claim_types: Sequence[str]) -> bool:
    return bool(CITATION_CLAIM_TYPES.intersection(claim_types))


def collect_issues(
    *,
    text: str,
    kind: WorkItemKind,
    reply_decision: str | None,
    max_chars: int,
    matcher: AhoCorasickMatcher,
    evidence_ids: Sequence[str],
    offered_refs: Sequence[str],
    claim_types: Sequence[str],
    retrieval_state: str,
) -> list[str]:
    issues: list[str] = []
    if reply_decision == "skip" and text.strip():
        issues.append("skip_must_be_empty")
    if kind == "reply_comment" and reply_decision != "skip" and not text.strip():
        issues.append("empty_reply")
    if len(text) > max_chars:
        issues.append("over_limit")
    for term in matcher.find(text):
        issues.append(f"forbidden_term:{term}")
    offered = set(offered_refs)
    for ref_id in evidence_ids:
        if ref_id not in offered:
            issues.append("unknown_ref")
            break
    if requires_citation(claim_types) and not evidence_ids:
        if retrieval_state == "empty":
            issues.append("missing_ref_on_empty_rag")
    return issues


def _has_hard_issue(issues: Sequence[str]) -> bool:
    return any(
        issue.startswith(prefix)
        for issue in issues
        for prefix in HARD_ISSUE_PREFIXES
    )


def _only_rewriteable(issues: Sequence[str]) -> bool:
    if not issues:
        return False
    return all(issue == "over_limit" for issue in issues)


def _decision_for(
    *,
    kind: WorkItemKind,
    reply_decision: str | None,
    degrade_op: DegradeOp,
) -> GatedDecision:
    if degrade_op == "skip":
        return "skip"
    if kind == "reply_comment":
        if reply_decision in {"reply", "acknowledge", "skip"}:
            return reply_decision
        return "skip"
    return "publishable"


def _status_for(degrade_op: DegradeOp, issues: Sequence[str]) -> DraftStatus:
    if "unknown_ref" in issues and degrade_op == "skip":
        return "failed"
    if degrade_op == "skip":
        return "skipped"
    if degrade_op in {"rewrite_safe", "template_fallback"}:
        return "degraded"
    return "ready"


async def apply_constraint_gate(
    *,
    work_item_id: str,
    kind: WorkItemKind,
    platform_key: str,
    source_comment_key: str | None,
    text: str,
    rationale: str,
    evidence_ids: Sequence[str],
    risk_flags: Sequence[str],
    claim_types: Sequence[str],
    reply_decision: str | None,
    proposed_degrade: str | None,
    max_chars: int,
    matcher: AhoCorasickMatcher,
    offered_refs: Sequence[str],
    retrieval_state: str,
    templates: Sequence[dict[str, object]],
    rewrite_once: Callable[[list[str]], Awaitable[str]] | None = None,
    attempt: int = 1,
    degrade_trace: list[DegradeStep] | None = None,
) -> GatedDraft:
    if proposed_degrade not in ALLOWED_DEGRADE_OPS:
        proposed_degrade = None
    issues = collect_issues(
        text=text,
        kind=kind,
        reply_decision=reply_decision,
        max_chars=max_chars,
        matcher=matcher,
        evidence_ids=evidence_ids,
        offered_refs=offered_refs,
        claim_types=claim_types,
        retrieval_state=retrieval_state,
    )
    trace = list(degrade_trace or [])

    if not issues:
        op: DegradeOp = "pass"
        if proposed_degrade == "skip" or reply_decision == "skip":
            op = "skip"
            text = ""
            reply_decision = "skip"
        trace.append(DegradeStep(op=op, issues=[], attempt=attempt))
        return GatedDraft(
            draft_key=f"d-{work_item_id}",
            kind=kind,
            platform_key=platform_key,
            source_comment_key=source_comment_key,
            degrade_op=op,
            degrade_trace=trace,
            text=text if op != "skip" else "",
            rationale=rationale,
            decision=_decision_for(
                kind=kind, reply_decision=reply_decision, degrade_op=op
            ),
            evidence_ids=list(evidence_ids),
            risk_flags=list(risk_flags),
            status=_status_for(op, issues),
            issues=[],
        )

    if "skip_must_be_empty" in issues:
        op = "skip"
        trace.append(DegradeStep(op=op, issues=list(issues), attempt=attempt))
        return GatedDraft(
            draft_key=f"d-{work_item_id}",
            kind=kind,
            platform_key=platform_key,
            source_comment_key=source_comment_key,
            degrade_op=op,
            degrade_trace=trace,
            text="",
            rationale=rationale,
            decision="skip",
            evidence_ids=list(evidence_ids),
            risk_flags=list(risk_flags),
            status="skipped",
            issues=list(issues),
        )

    if (
        _only_rewriteable(issues)
        and attempt == 1
        and rewrite_once is not None
    ):
        rewritten = await rewrite_once(list(issues))
        trace.append(
            DegradeStep(op="rewrite_safe", issues=list(issues), attempt=attempt)
        )
        return await apply_constraint_gate(
            work_item_id=work_item_id,
            kind=kind,
            platform_key=platform_key,
            source_comment_key=source_comment_key,
            text=rewritten,
            rationale=rationale,
            evidence_ids=evidence_ids,
            risk_flags=risk_flags,
            claim_types=claim_types,
            reply_decision=reply_decision,
            proposed_degrade=None,
            max_chars=max_chars,
            matcher=matcher,
            offered_refs=offered_refs,
            retrieval_state=retrieval_state,
            templates=templates,
            rewrite_once=None,
            attempt=2,
            degrade_trace=trace,
        )

    if _has_hard_issue(issues) or "unknown_ref" in issues:
        template_text = _pick_template(templates, claim_types)
        if template_text and "unknown_ref" not in issues:
            op = "template_fallback"
            trace.append(DegradeStep(op=op, issues=list(issues), attempt=attempt))
            gated_text = template_text[:max_chars]
            return GatedDraft(
                draft_key=f"d-{work_item_id}",
                kind=kind,
                platform_key=platform_key,
                source_comment_key=source_comment_key,
                degrade_op=op,
                degrade_trace=trace,
                text=gated_text,
                rationale=rationale,
                decision=_decision_for(
                    kind=kind,
                    reply_decision="acknowledge" if kind == "reply_comment" else None,
                    degrade_op=op,
                ),
                evidence_ids=[],
                risk_flags=list(risk_flags),
                status="degraded",
                issues=list(issues),
            )
        op = "skip"
        status: DraftStatus = "failed" if "unknown_ref" in issues else "skipped"
        trace.append(DegradeStep(op=op, issues=list(issues), attempt=attempt))
        return GatedDraft(
            draft_key=f"d-{work_item_id}",
            kind=kind,
            platform_key=platform_key,
            source_comment_key=source_comment_key,
            degrade_op=op,
            degrade_trace=trace,
            text="",
            rationale=rationale,
            decision="skip",
            evidence_ids=list(evidence_ids) if "unknown_ref" not in issues else [],
            risk_flags=list(risk_flags),
            status=status,
            issues=list(issues),
        )

    op = "skip"
    trace.append(DegradeStep(op=op, issues=list(issues), attempt=attempt))
    return GatedDraft(
        draft_key=f"d-{work_item_id}",
        kind=kind,
        platform_key=platform_key,
        source_comment_key=source_comment_key,
        degrade_op=op,
        degrade_trace=trace,
        text="",
        rationale=rationale,
        decision="skip",
        evidence_ids=list(evidence_ids),
        risk_flags=list(risk_flags),
        status="skipped",
        issues=list(issues),
    )


def _pick_template(
    templates: Sequence[dict[str, object]],
    claim_types: Sequence[str],
) -> str | None:
    claims = set(claim_types)
    for item in templates:
        keys = {str(value) for value in item.get("claim_types", []) or []}
        if claims & keys or not claims:
            text = str(item.get("text") or "")
            if text:
                return text
    if templates:
        text = str(templates[0].get("text") or "")
        return text or None
    return None
