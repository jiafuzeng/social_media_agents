from __future__ import annotations

import os
from pathlib import Path

from agently import Agently
from dotenv import find_dotenv, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_env_file = PROJECT_ROOT / ".env"
load_dotenv(_env_file if _env_file.is_file() else find_dotenv(usecwd=True))

Agently.set_settings(
    "OpenAICompatible",
    {
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", ""),
        "model": os.environ.get("DEEPSEEK_DEFAULT_MODEL", ""),
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model_type": "chat",
        "stream": False,
        "request_options": {
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 8192,
        },
    },
)
