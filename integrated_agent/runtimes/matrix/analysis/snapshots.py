from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from integrated_agent.runtimes.matrix.models import CommentIn, DomainModel


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


class BrandCard(DomainModel):
    brand_key: str
    forbidden_topics: list[str] = Field(default_factory=list)
    template_keys: list[str] = Field(default_factory=list)


class PlatformCard(DomainModel):
    platform_key: str
    max_chars: int
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
    brand: BrandCard
    platforms: list[PlatformCard]
    policy: PolicyCard
    comments: list[CommentCard] = Field(default_factory=list)
    templates: list[TemplateCard] = Field(default_factory=list)
    trend_cards: list[TrendCard] = Field(default_factory=list)

    def platform(self, platform_key: str) -> PlatformCard:
        for item in self.platforms:
            if item.platform_key == platform_key:
                return item
        raise SnapshotError(f"unknown platform_key: {platform_key}")


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
    brand_key: str,
    platform_keys: list[str],
    scenario: str,
    thread_key: str | None = None,
    comments: list[CommentIn] | None = None,
) -> Snapshot:
    accounts_doc = _load_yaml(data_root / "accounts.yaml")
    platforms_doc = _load_yaml(data_root / "platforms.yaml")
    policy_doc = _load_yaml(data_root / "policy_terms.yaml")
    templates_doc = _load_yaml(data_root / "templates.yaml")

    accounts = accounts_doc.get("accounts") or {}
    brands = accounts_doc.get("brands") or {}
    if account_key not in accounts:
        raise SnapshotError(f"unknown account_key: {account_key}")
    if brand_key not in brands:
        raise SnapshotError(f"unknown brand_key: {brand_key}")

    account_raw = accounts[account_key]
    brand_raw = brands[brand_key]
    account = AccountCard(
        account_key=str(account_raw["account_key"]),
        display_name=str(account_raw["display_name"]),
        voice_summary=str(account_raw["voice_summary"]),
    )
    brand = BrandCard(
        brand_key=str(brand_raw["brand_key"]),
        forbidden_topics=list(brand_raw.get("forbidden_topics") or []),
        template_keys=list(brand_raw.get("template_keys") or []),
    )

    catalog = platforms_doc.get("platforms") or {}
    requested = list(platform_keys)
    thread_platform_key: str | None = None
    comment_cards: list[CommentCard] = []

    if scenario == "reply":
        if comments:
            comment_cards = _issue_comment_keys(comments)
        elif thread_key:
            threads = _load_json(data_root / "sample_threads.json").get("threads") or {}
            if thread_key not in threads:
                raise SnapshotError(f"unknown thread_key: {thread_key}")
            thread = threads[thread_key]
            thread_platform_key = str(thread.get("platform_key") or "")
            raw_comments = [
                CommentIn.model_validate(item) for item in thread.get("comments") or []
            ]
            comment_cards = _issue_comment_keys(raw_comments)
        else:
            raise SnapshotError("reply requires thread_key or comments")
        if not requested:
            if thread_platform_key:
                requested = [thread_platform_key]
            else:
                requested = list(account_raw.get("default_platform_keys") or [])
    else:
        if not requested:
            requested = list(account_raw.get("default_platform_keys") or [])

    if not requested:
        raise SnapshotError("no platform_keys offered")

    platforms: list[PlatformCard] = []
    seen: set[str] = set()
    for key in requested:
        if key in seen:
            continue
        seen.add(key)
        if key not in catalog:
            raise SnapshotError(f"unknown platform_key: {key}")
        raw = catalog[key]
        platforms.append(
            PlatformCard(
                platform_key=str(raw["platform_key"]),
                max_chars=int(raw["max_chars"]),
                mention_rules=str(raw.get("mention_rules") or ""),
            )
        )

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
    allowed_keys = set(brand.template_keys)
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
        brand=brand,
        platforms=platforms,
        policy=policy,
        comments=comment_cards,
        templates=templates,
        trend_cards=[],
    )

