from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from agently import Agently
from agently.utils.ModelPool import resolve_model_pool_settings
from dotenv import find_dotenv, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 知识库 RecordStore 目录（相对项目根）。该目录即 backend root，库文件为 records.db；
# 不要交给 RecordStore(路径)，否则会套成 .agently/records。
KB_RECORD_ROOT = (PROJECT_ROOT / "workspace" / "rag" / "records").resolve()
KB_FILES_ROOT = (PROJECT_ROOT / "workspace" / "rag" / "files").resolve()
_env_file = PROJECT_ROOT / ".env"
# override=True：以项目 .env 为准。Debug/终端里残留的旧 DEEPSEEK_* 否则会盖住新 key/base_url。
load_dotenv(_env_file if _env_file.is_file() else find_dotenv(usecwd=True), override=True)

Agently.set_settings(
    "model_pool",
    {
        "deepseek": {
            "provider": "OpenAICompatible",
            "model_type": "chat",
            "model": os.environ.get("DEEPSEEK_DEFAULT_MODEL", ""),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", ""),
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "stream": False,
            "request_options": {
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": 8192,
            },
        },
        "text-embedding-v3": {
            "provider": "OpenAICompatible",
            "model_type": "embeddings",
            "model": "text-embedding-v3",
            "base_url": os.environ.get("TEXT_EMBEDDING_V3_BASE_URL", ""),
            "api_key": os.environ.get("TEXT_EMBEDDING_V3_API_KEY", ""),
            "stream": False,
            "path_mapping": {"embeddings": "/embeddings"},
        },
        "bge-m3": {
            "provider": "OpenAICompatible",
            "model_type": "embeddings",
            "model": "bge-m3",
            "base_url": os.environ.get("BGE_M3_EMBEDDING_BASE_URL", ""),
            "auth": "nothing",
            "stream": False,
            "path_mapping": {"embeddings": "/embeddings"},
        },
        "qwen3": {
            "provider": "OpenAICompatible",
            "model_type": "embeddings",
            "model": "qwen3-embedding:0.6b",
            "base_url": os.environ.get("QWEN3_EMBEDDING_BASE_URL", ""),
            "auth": "nothing",
            "stream": False,
            "path_mapping": {"embeddings": "/embeddings"},
        },
    },
)
resolve_model_pool_settings("deepseek", Agently.settings)


def _kb_embedding_agent(profile_id: str):
    """Embedding Agent 不能继承全局 deepseek 的 chat request_options。

    Agently Settings 对 dict 是 merge；profile 里写 request_options: {} 清不掉父级。
    带 thinking/temperature/max_tokens 的 /embeddings 会被网关当 chat 计费。
    """
    agent = Agently.create_agent(f"matrix-kb-embed:{profile_id}")
    snapshot = agent.settings.get()
    agent.settings.parent = None
    if isinstance(snapshot, dict):
        agent.settings.update(snapshot)
    agent.settings.set("plugins.ModelRequester.OpenAICompatible.request_options", {})
    return agent.activate_model(profile_id)


KB_EMBEDDING_PROFILE_IDS: Final[tuple[str, ...]] = (
    "text-embedding-v3",
    "bge-m3",
    "qwen3",
)
KB_DEFAULT_EMBEDDING_PROFILE: Final[str] = "bge-m3"
KB_EMBEDDING_AGENTS = {
    profile_id: _kb_embedding_agent(profile_id) for profile_id in KB_EMBEDDING_PROFILE_IDS
}

__all__ = [
    "PROJECT_ROOT",
    "KB_RECORD_ROOT",
    "KB_FILES_ROOT",
    "KB_EMBEDDING_PROFILE_IDS",
    "KB_DEFAULT_EMBEDDING_PROFILE",
    "KB_EMBEDDING_AGENTS",
]
