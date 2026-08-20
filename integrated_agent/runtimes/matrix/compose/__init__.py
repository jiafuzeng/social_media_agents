"""推文撰写。独立 TriggerFlow，不经过回评或知识库问答。"""

from __future__ import annotations

from .flow import COMPOSE_FLOW, PIPELINE_VERSION, run_compose

__all__ = ["COMPOSE_FLOW", "PIPELINE_VERSION", "run_compose"]
