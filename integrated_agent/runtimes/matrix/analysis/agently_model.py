from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar, cast

from agently import Agently
from pydantic import BaseModel

from integrated_agent.config import load_model_settings
from integrated_agent.runtimes.matrix.models import (
    ComposeBriefOut,
    ComposeDraftOut,
    ReplyBriefOut,
    ReplyDraftOut,
    ReviewOut,
)

from .host import parse_structured


PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
TModel = TypeVar("TModel", bound=BaseModel)


class AgentlyMatrixModel:
    """六条 ModelRequest，契约按 Flow 分文件，不共用 Brief schema。"""

    def __init__(self) -> None:
        load_model_settings()

    async def compose_brief(self, *, text: str, info: dict) -> ComposeBriefOut:
        return await _parse(
            ComposeBriefOut,
            "compose/brief.yaml",
            name="matrix-compose-brief",
            mappings={"text": text, "info": info},
        )

    async def compose_draft(
        self,
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ComposeDraftOut:
        return await _parse(
            ComposeDraftOut,
            "compose/draft.yaml",
            name="matrix-compose-draft",
            mappings={
                "work_item": work_item,
                "info": info,
                "repair": repair or {},
            },
        )

    async def compose_review(self, *, package: dict, info: dict) -> ReviewOut:
        return await _parse(
            ReviewOut,
            "compose/review.yaml",
            name="matrix-compose-review",
            mappings={"package": package, "info": info},
        )

    async def reply_brief(self, *, text: str, info: dict) -> ReplyBriefOut:
        return await _parse(
            ReplyBriefOut,
            "reply/brief.yaml",
            name="matrix-reply-brief",
            mappings={"text": text, "info": info},
        )

    async def reply_draft(
        self,
        *,
        work_item: dict,
        info: dict,
        repair: dict | None = None,
    ) -> ReplyDraftOut:
        return await _parse(
            ReplyDraftOut,
            "reply/draft.yaml",
            name="matrix-reply-draft",
            mappings={
                "work_item": work_item,
                "info": info,
                "repair": repair or {},
            },
        )

    async def reply_review(self, *, package: dict, info: dict) -> ReviewOut:
        return await _parse(
            ReviewOut,
            "reply/review.yaml",
            name="matrix-reply-review",
            mappings={"package": package, "info": info},
        )


async def _parse(
    model_cls: type[TModel],
    relative: str,
    *,
    name: str,
    mappings: dict[str, Any],
) -> TModel:
    return cast(TModel, parse_structured(model_cls, await _run(relative, name=name, mappings=mappings)))


async def _run(relative: str, *, name: str, mappings: dict[str, Any]) -> dict[str, Any]:
    agent = Agently.create_agent(name=name)
    agent.load_yaml_prompt(PROMPT_ROOT / relative, mappings=mappings)
    payload = await agent.request.get_result().async_get_data()
    if payload is None:
        raise ValueError(f"{name} returned empty output")
    if isinstance(payload, dict):
        return payload
    return dict(cast(dict[str, Any], payload))
