from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Sequence
from typing import Awaitable

from integrated_agent.runtimes.matrix.host.models import (
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
HARD_ISSUE_PREFIXES = (
    "forbidden_term:",
    "missing_ref_on_empty_rag",
    "unknown_https",
    "unknown_media",
    "unknown_cta",
    "growth_engagement",
    "near_duplicate_source",
)
GROWTH_ENGAGEMENT_PHRASES: tuple[str, ...] = (
    "求转",
    "转发抽奖",
    "互关",
    "互粉",
    "你怎么看",
    "你觉得呢",
    "评论区见",
    "来聊聊",
)
CTA_TOKEN_RE = re.compile(r"\[\[cta:(\d+)\]\]")
MEDIA_TOKEN_RE = re.compile(r"\[\[media:([^\]]+)\]\]")
URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"#\w")
URL_CHAR_BUDGET = 23
KB_CITE_RE = re.compile(r"\[\[kb:([^\]]+)\]\]")
REF_CITE_RE = re.compile(r"\[\[ref:([^\]]+)\]\]")


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


def _sanitize_unoffered_cites(
    *,
    text: str,
    evidence_ids: Sequence[str],
    offered_refs: Sequence[str],
    offered_kbs: Sequence[str],
) -> tuple[str, list[str]]:
    """空检索时丢掉编造的 e1/k1；有签发卡时仍把未知 id 留给 unknown_ref / unknown_kb。"""

    offered_ref_set = set(offered_refs)
    offered_kb_set = set(offered_kbs)
    kept: list[str] = []
    for ref_id in evidence_ids:
        if ref_id in offered_ref_set:
            kept.append(ref_id)
            continue
        if ref_id in offered_kb_set:
            continue
        if offered_ref_set:
            kept.append(ref_id)
    cleaned = text or ""
    if not offered_ref_set:
        cleaned = REF_CITE_RE.sub("", cleaned)
    if not offered_kb_set:
        cleaned = KB_CITE_RE.sub("", cleaned)
    return cleaned, kept


def effective_text_length(text: str) -> int:
    """剥 media/cta 占位符；正文 URL 按 X 规则各计 23 字。"""
    body = MEDIA_TOKEN_RE.sub("", text or "")
    body = CTA_TOKEN_RE.sub(" " * URL_CHAR_BUDGET, body)
    for match in URL_RE.finditer(body):
        span = match.span()
        body = body[: span[0]] + (" " * URL_CHAR_BUDGET) + body[span[1] :]
    return len(body)


def _longest_overlap(left: str, right: str, *, min_len: int = 40) -> bool:
    a = re.sub(r"\s+", "", left or "")
    b = re.sub(r"\s+", "", right or "")
    if len(a) < min_len or len(b) < min_len:
        return False
    window = min(len(a), len(b))
    for size in range(window, min_len - 1, -1):
        for start in range(0, len(a) - size + 1):
            chunk = a[start : start + size]
            if chunk and chunk in b:
                return True
    return False


def collect_compose_issues(
    *,
    text: str,
    offered_cta_urls: Sequence[str],
    offered_media_keys: Sequence[str],
    source_text: str = "",
) -> list[str]:
    issues: list[str] = []
    for phrase in GROWTH_ENGAGEMENT_PHRASES:
        if phrase in (text or ""):
            issues.append(f"growth_engagement:{phrase}")
            break
    if len(HASHTAG_RE.findall(text or "")) > 1:
        issues.append("too_many_hashtags")
    allowed_urls = {str(item).strip() for item in offered_cta_urls if str(item).strip()}
    for match in CTA_TOKEN_RE.finditer(text or ""):
        index = int(match.group(1))
        if index >= len(offered_cta_urls):
            issues.append("unknown_cta")
            break
    for token in MEDIA_TOKEN_RE.findall(text or ""):
        if token not in set(offered_media_keys):
            issues.append(f"unknown_media:{token}")
            break
    for url in URL_RE.findall(text or ""):
        if url.rstrip(".,)）]") not in allowed_urls:
            issues.append("unknown_https")
            break
    if source_text.strip() and _longest_overlap(source_text, text):
        issues.append("near_duplicate_source")
    return issues


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
    offered_kbs: Sequence[str] = (),
    offered_cta_urls: Sequence[str] = (),
    offered_media_keys: Sequence[str] = (),
    source_text: str = "",
) -> list[str]:
    issues: list[str] = []
    if reply_decision == "skip" and text.strip():
        issues.append("skip_must_be_empty")
    if kind == "reply_comment" and reply_decision != "skip" and not text.strip():
        issues.append("empty_reply")
    measured = effective_text_length(text) if kind == "compose_post" else len(text)
    if measured > max_chars:
        issues.append("over_limit")
    for term in matcher.find(text):
        issues.append(f"forbidden_term:{term}")
    offered_ref_set = set(offered_refs)
    offered_kb_set = set(offered_kbs)
    case_ids: list[str] = []
    for ref_id in evidence_ids:
        if ref_id in offered_ref_set:
            case_ids.append(ref_id)
            continue
        if ref_id in offered_kb_set:
            continue
        issues.append("unknown_ref")
        break
    cited_kbs = KB_CITE_RE.findall(text or "")
    if any(token not in offered_kb_set for token in cited_kbs):
        issues.append("unknown_kb")
    if requires_citation(claim_types) and not case_ids:
        if retrieval_state == "empty":
            issues.append("missing_ref_on_empty_rag")
    if kind == "compose_post":
        issues.extend(
            collect_compose_issues(
                text=text,
                offered_cta_urls=offered_cta_urls,
                offered_media_keys=offered_media_keys,
                source_text=source_text,
            )
        )
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
    soft = {"over_limit", "too_many_hashtags"}
    return all(issue in soft or issue.startswith("over_limit") for issue in issues)


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
    if ("unknown_ref" in issues or "unknown_kb" in issues) and degrade_op == "skip":
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
    offered_kbs: Sequence[str] = (),
    offered_cta_urls: Sequence[str] = (),
    offered_media_keys: Sequence[str] = (),
    source_text: str = "",
) -> GatedDraft:
    if proposed_degrade not in ALLOWED_DEGRADE_OPS:
        proposed_degrade = None
    text, evidence_ids = _sanitize_unoffered_cites(
        text=text,
        evidence_ids=evidence_ids,
        offered_refs=offered_refs,
        offered_kbs=offered_kbs,
    )
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
        offered_kbs=offered_kbs,
        offered_cta_urls=offered_cta_urls,
        offered_media_keys=offered_media_keys,
        source_text=source_text,
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
            offered_kbs=offered_kbs,
            offered_cta_urls=offered_cta_urls,
            offered_media_keys=offered_media_keys,
            source_text=source_text,
        )

    unknown_cite = "unknown_ref" in issues or "unknown_kb" in issues
    if _has_hard_issue(issues) or unknown_cite:
        template_text = _pick_template(templates, claim_types)
        if template_text and not unknown_cite:
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
        status: DraftStatus = "failed" if unknown_cite else "skipped"
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
            evidence_ids=[] if unknown_cite else list(evidence_ids),
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
