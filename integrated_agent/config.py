from __future__ import annotations

import os
from pathlib import Path

from agently import Agently
from agently.utils.ModelPool import resolve_model_pool_settings
from dotenv import find_dotenv, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 知识库 RecordStore 目录（相对项目根）。该目录即 backend root，库文件为 records.db；
# 不要交给 RecordStore(路径)，否则会套成 .agently/records。
KB_RECORD_ROOT = (PROJECT_ROOT / "workspace" / "kb" / "records").resolve()
_env_file = PROJECT_ROOT / ".env"
load_dotenv(_env_file if _env_file.is_file() else find_dotenv(usecwd=True))

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
        "openai-small": {
            "provider": "OpenAICompatible",
            "model_type": "embeddings",
            "model": "text-embedding-3-small",
            "base_url": os.environ.get("EMBEDDING_OPENAI_BASE_URL", ""),
            "api_key": os.environ.get("EMBEDDING_OPENAI_API_KEY", ""),
            "stream": False,
            "path_mapping": {"embeddings": "/embeddings"},
        },
        "bge-m3": {
            "provider": "OpenAICompatible",
            "model_type": "embeddings",
            "model": "bge-m3",
            "base_url": os.environ.get("EMBEDDING_BGE_BASE_URL", ""),
            "auth": os.environ.get("EMBEDDING_BGE_AUTH", "nothing"),
            "stream": False,
            "path_mapping": {"embeddings": "/embeddings"},
        },
        "qwen3": {
            "provider": "OpenAICompatible",
            "model_type": "embeddings",
            "model": "qwen3-embedding:0.6b",
            "base_url": os.environ.get("EMBEDDING_QWEN_BASE_URL", ""),
            "auth": os.environ.get("EMBEDDING_QWEN_AUTH", "nothing"),
            "stream": False,
            "path_mapping": {"embeddings": "/embeddings"},
        },
    },
)
resolve_model_pool_settings("deepseek", Agently.settings)

KB_EMBEDDING_AGENTS = {
    profile_id: Agently.create_agent(f"matrix-kb-embed:{profile_id}").activate_model(profile_id)
    for profile_id in ("openai-small", "bge-m3", "qwen3")
}
