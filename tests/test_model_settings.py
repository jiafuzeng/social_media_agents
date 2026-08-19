from __future__ import annotations

from agently import Agently
from agently.utils.ModelPool import resolve_model_pool_settings
from agently.utils.Settings import Settings

from integrated_agent.config import KB_EMBEDDING_AGENTS


def test_model_settings_keep_chat_path_mapping() -> None:
    mapping = Agently.settings.get("plugins.ModelRequester.OpenAICompatible.path_mapping")
    assert mapping["chat"] == "/chat/completions"


def test_kb_embed_profiles_switch_via_global_model_pool() -> None:
    assert Agently.settings.get("plugins.ModelRequester.OpenAICompatible.model_type") == "chat"
    assert "openai-small" in Agently.settings.get("model_pool")
    switched = Settings(parent=Agently.settings)
    resolve_model_pool_settings("openai-small", switched)
    assert switched.get("plugins.ModelRequester.OpenAICompatible.model_type") == "embeddings"
    assert Agently.settings.get("plugins.ModelRequester.OpenAICompatible.model_type") == "chat"
    assert KB_EMBEDDING_AGENTS["openai-small"]._active_model_key == "openai-small"
