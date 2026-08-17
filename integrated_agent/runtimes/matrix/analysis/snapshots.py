from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from integrated_agent.runtimes.matrix.models import (
    MAX_COMPOSE_POSTS,
    MIN_COMPOSE_POSTS,
    CommentIn,
    DomainModel,
)


TWITTER_PLATFORM_KEY = "x-twitter"

OFFERED_CLAIM_TYPES = frozenset(
    {
        "guaranteed_return",
        "investment",
        "crypto_promotion",
        "paid_partnership",
        "efficacy",
        "medical",
        "superlative",
        "unsubstantiated",
        "testimonial",
        "social_proof",
        "engagement_bait",
        "spam",
        "impersonation",
        "identity",
        "harassment",
        "reply_risk",
        "civic",
        "misinformation",
        "advertising",
        "pii",
        "doxxing",
        "format",
    }
)


class SnapshotError(ValueError):
    pass


class AccountCard(DomainModel):
    account_key: str
    display_name: str
    voice_summary: str
    handle: str = ""
    one_liner: str = ""
    background: str = ""
    goals: list[str] = Field(default_factory=list)
    audience: str = ""
    content_pillars: list[str] = Field(default_factory=list)
    must_do: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    reply_stance: str = ""
    guardrail_keys: list[str] = Field(min_length=1)


class GuardrailCard(DomainModel):
    guardrail_key: str
    forbidden_topics: list[str] = Field(default_factory=list)
    template_keys: list[str] = Field(default_factory=list)


class PlatformCard(DomainModel):
    platform_key: str
    max_chars: int
    max_posts: int = Field(
        default=MAX_COMPOSE_POSTS,
        ge=MIN_COMPOSE_POSTS,
        le=MAX_COMPOSE_POSTS,
    )
    mention_rules: str = ""


class PolicyCard(DomainModel):
    term_list_id: str
    ac_ready: bool
    terms: list[str] = Field(default_factory=list)


class CommentCard(DomainModel):
    comment_key: str
    text: str
    role: str = "root"
    author_display: str | None = None


class TemplateCard(DomainModel):
    template_key: str
    text: str
    claim_types: list[str] = Field(default_factory=list)


class TrendCard(DomainModel):
    trend_key: str
    title: str
    summary: str


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    account: AccountCard
    guardrails: list[GuardrailCard] = Field(min_length=1)
    platform: PlatformCard
    policy: PolicyCard
    comments: list[CommentCard] = Field(default_factory=list)
    templates: list[TemplateCard] = Field(default_factory=list)
    trend_cards: list[TrendCard] = Field(default_factory=list)


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in (value or []) if str(item).strip()]


def _unique_str(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def parse_guardrail_card(raw: dict[str, Any], *, catalog_key: str) -> GuardrailCard:
    key = str(raw.get("guardrail_key") or catalog_key)
    if key != str(catalog_key):
        raise SnapshotError(f"guardrail_key mismatch: {catalog_key}")
    return GuardrailCard(
        guardrail_key=key,
        forbidden_topics=_str_list(raw.get("forbidden_topics")),
        template_keys=_str_list(raw.get("template_keys")),
    )


def resolve_guardrails(
    keys: list[str],
    catalog: dict[str, Any],
) -> list[GuardrailCard]:
    if not keys:
        raise SnapshotError("account requires guardrail_keys")
    cards: list[GuardrailCard] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        raw = catalog.get(key)
        if not isinstance(raw, dict):
            raise SnapshotError(f"unknown guardrail_key: {key}")
        cards.append(parse_guardrail_card(raw, catalog_key=key))
    return cards


def merged_forbidden_topics(cards: list[GuardrailCard]) -> list[str]:
    topics: list[str] = []
    for card in cards:
        topics.extend(card.forbidden_topics)
    return _unique_str(topics)


def merged_template_keys(cards: list[GuardrailCard]) -> list[str]:
    keys: list[str] = []
    for card in cards:
        keys.extend(card.template_keys)
    return _unique_str(keys)


def parse_account_card(raw: dict[str, Any]) -> AccountCard:
    return AccountCard(
        account_key=str(raw["account_key"]),
        display_name=str(raw["display_name"]),
        voice_summary=str(raw["voice_summary"]),
        handle=str(raw.get("handle") or ""),
        one_liner=str(raw.get("one_liner") or ""),
        background=str(raw.get("background") or "").strip(),
        goals=_str_list(raw.get("goals")),
        audience=str(raw.get("audience") or ""),
        content_pillars=_str_list(raw.get("content_pillars")),
        must_do=_str_list(raw.get("must_do")),
        must_not=_str_list(raw.get("must_not")),
        reply_stance=str(raw.get("reply_stance") or ""),
        guardrail_keys=_unique_str(_str_list(raw.get("guardrail_keys"))),
    )


def list_account_catalog(data_root: Path) -> list[AccountCard]:
    doc = _load_yaml(data_root / "accounts.yaml")
    accounts = doc.get("accounts") or {}
    catalog = doc.get("guardrails") or {}
    cards: list[AccountCard] = []
    for key, raw in accounts.items():
        if not isinstance(raw, dict):
            raise SnapshotError(f"invalid account: {key}")
        card = parse_account_card(raw)
        if card.account_key != str(key):
            raise SnapshotError(f"account_key mismatch: {key}")
        resolve_guardrails(card.guardrail_keys, catalog)
        cards.append(card)
    return cards


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise SnapshotError(f"missing snapshot file: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> Any:
    import json

    if not path.is_file():
        raise SnapshotError(f"missing snapshot file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_bytes(payload: Any) -> bytes:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def snapshot_id_for(data_root: Path) -> str:
    files = [
        data_root / "accounts.yaml",
        data_root / "platforms.yaml",
        data_root / "policy_terms.yaml",
        data_root / "templates.yaml",
        data_root / "sample_threads.json",
    ]
    hasher = sha256()
    for path in files:
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(_canonical_bytes(_load_yaml(path) if path.suffix == ".yaml" else _load_json(path)))
        hasher.update(b"\n")
    return hasher.hexdigest()[:16]


def _issue_comment_keys(comments: list[CommentIn]) -> list[CommentCard]:
    cards: list[CommentCard] = []
    used: set[str] = set()
    next_index = 1
    for item in comments:
        key = item.comment_key
        if not key:
            while f"c{next_index}" in used:
                next_index += 1
            key = f"c{next_index}"
            next_index += 1
        if key in used:
            raise SnapshotError(f"duplicate comment_key: {key}")
        used.add(key)
        cards.append(
            CommentCard(
                comment_key=key,
                text=item.text,
                role=item.role,
                author_display=item.author_display,
            )
        )
    return cards


def bind_snapshot(
    *,
    data_root: Path,
    account_key: str,
    scenario: str,
    thread_key: str | None = None,
    comments: list[CommentIn] | None = None,
) -> Snapshot:
    accounts_doc = _load_yaml(data_root / "accounts.yaml")
    platforms_doc = _load_yaml(data_root / "platforms.yaml")
    policy_doc = _load_yaml(data_root / "policy_terms.yaml")
    templates_doc = _load_yaml(data_root / "templates.yaml")

    accounts = accounts_doc.get("accounts") or {}
    guardrail_catalog = accounts_doc.get("guardrails") or {}
    if account_key not in accounts:
        raise SnapshotError(f"unknown account_key: {account_key}")

    account_raw = accounts[account_key]
    if not isinstance(account_raw, dict):
        raise SnapshotError(f"invalid account: {account_key}")
    account = parse_account_card(account_raw)
    guardrails = resolve_guardrails(account.guardrail_keys, guardrail_catalog)

    platform_catalog = platforms_doc.get("platforms") or {}
    raw = platform_catalog.get(TWITTER_PLATFORM_KEY)
    if not raw:
        raise SnapshotError(f"missing platform: {TWITTER_PLATFORM_KEY}")
    platform = PlatformCard(
        platform_key=str(raw["platform_key"]),
        max_chars=int(raw["max_chars"]),
        max_posts=min(
            max(int(raw.get("max_posts") or MAX_COMPOSE_POSTS), MIN_COMPOSE_POSTS),
            MAX_COMPOSE_POSTS,
        ),
        mention_rules=str(raw.get("mention_rules") or ""),
    )

    comment_cards: list[CommentCard] = []
    if scenario == "reply":
        if comments:
            comment_cards = _issue_comment_keys(comments)
        elif thread_key:
            threads = _load_json(data_root / "sample_threads.json").get("threads") or {}
            if thread_key not in threads:
                raise SnapshotError(f"unknown thread_key: {thread_key}")
            thread = threads[thread_key]
            raw_comments = [
                CommentIn.model_validate(item) for item in thread.get("comments") or []
            ]
            comment_cards = _issue_comment_keys(raw_comments)
        else:
            raise SnapshotError("reply requires thread_key or comments")

    terms = [str(item) for item in policy_doc.get("terms") or [] if str(item)]
    term_list_id = str(policy_doc.get("term_list_id") or "").strip()
    if not term_list_id or not terms:
        raise SnapshotError("policy terms are required")
    policy = PolicyCard(
        term_list_id=term_list_id,
        ac_ready=True,
        terms=terms,
    )

    templates: list[TemplateCard] = []
    allowed_keys = set(merged_template_keys(guardrails))
    for item in templates_doc.get("templates") or []:
        key = str(item.get("template_key") or "")
        if allowed_keys and key not in allowed_keys:
            continue
        templates.append(
            TemplateCard(
                template_key=key,
                text=str(item.get("text") or ""),
                claim_types=list(item.get("claim_types") or []),
            )
        )

    return Snapshot(
        snapshot_id=snapshot_id_for(data_root),
        account=account,
        guardrails=guardrails,
        platform=platform,
        policy=policy,
        comments=comment_cards,
        templates=templates,
        trend_cards=[],
    )

