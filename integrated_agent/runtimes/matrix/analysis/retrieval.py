from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from integrated_agent.runtimes.matrix.models import DomainModel


class RetrieveQuery(DomainModel):
    work_item_id: str
    platform_key: str
    claim_types: list[str] = Field(default_factory=list)
    goal: str = ""


class CaseCard(DomainModel):
    ref_id: str
    title: str
    ruling: str
    allowed: str = ""
    forbidden: str = ""
    quote: str = ""
    claim_types: list[str] = Field(default_factory=list)


class RetrieveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["hits", "empty", "failed"]
    cards: list[CaseCard] = Field(default_factory=list)


def retrieve_cases(query: RetrieveQuery, *, data_root: Path, limit: int = 4) -> RetrieveResult:
    cases_dir = data_root / "cases"
    path = cases_dir / f"{query.platform_key}.json"
    if not path.is_file():
        matches = sorted(cases_dir.glob("*.json")) if cases_dir.is_dir() else []
        if not matches:
            return RetrieveResult(state="empty", cards=[])
        path = matches[0]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RetrieveResult(state="failed", cards=[])

    wanted = set(query.claim_types)
    cards: list[CaseCard] = []
    for item in payload.get("cases") or []:
        platforms = {str(value) for value in item.get("platform_keys") or []}
        if query.platform_key not in platforms:
            continue
        claims = [str(value) for value in item.get("claim_types") or []]
        if wanted and not wanted.intersection(claims):
            continue
        cards.append(
            CaseCard(
                ref_id=str(item.get("ref_id") or item.get("case_id") or ""),
                title=str(item.get("title") or ""),
                ruling=str(item.get("ruling") or ""),
                allowed=str(item.get("allowed") or ""),
                forbidden=str(item.get("forbidden") or ""),
                quote=str(item.get("quote") or ""),
                claim_types=claims,
            )
        )
        if len(cards) >= limit:
            break
    if not cards:
        return RetrieveResult(state="empty", cards=[])
    return RetrieveResult(state="hits", cards=cards)
