from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from agently import Agently
from dotenv import find_dotenv, load_dotenv


PACKAGE_ROOT = Path(__file__).parent
PROJECT_ROOT = PACKAGE_ROOT.parent
_SETTINGS_NS = "plugins.ModelRequester.OpenAICompatible"


def load_model_settings() -> None:
    project_env = PROJECT_ROOT / ".env"
    load_dotenv(
        project_env if project_env.is_file() else find_dotenv(usecwd=True)
    )
    Agently.load_settings(
        "yaml_file",
        str(PACKAGE_ROOT / "model_settings.yaml"),
        auto_load_env=True,
        raise_empty=True,
    )
    base_url = str(Agently.settings.get(f"{_SETTINGS_NS}.base_url") or "")
    model = Agently.settings.get(f"{_SETTINGS_NS}.model")
    auth = Agently.settings.get(f"{_SETTINGS_NS}.auth")
    if not model or not auth or not base_url:
        raise RuntimeError(
            "模型配置未生效：请确认 .env 中的 DEEPSEEK_BASE_URL、"
            "DEEPSEEK_DEFAULT_MODEL、DEEPSEEK_API_KEY 均已填写。"
        )
    host = urlparse(base_url).netloc or base_url
    Agently.logger.info(
        "model settings loaded: host=%s model=%s stream=%s thinking=%s",
        host,
        model,
        Agently.settings.get(f"{_SETTINGS_NS}.stream"),
        Agently.settings.get(f"{_SETTINGS_NS}.request_options.thinking"),
    )

