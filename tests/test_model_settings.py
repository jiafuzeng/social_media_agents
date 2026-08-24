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
    assert "text-embedding-v3" in Agently.settings.get("model_pool")
    switched = Settings(parent=Agently.settings)
    resolve_model_pool_settings("text-embedding-v3", switched)
    assert switched.get("plugins.ModelRequester.OpenAICompatible.model_type") == "embeddings"
    assert Agently.settings.get("plugins.ModelRequester.OpenAICompatible.model_type") == "chat"
    assert KB_EMBEDDING_AGENTS["text-embedding-v3"]._active_model_key == "text-embedding-v3"


def test_text_embedding_v3_embed_does_not_inherit_chat_request_options() -> None:
    ns = "plugins.ModelRequester.OpenAICompatible"
    chat_options = Agently.settings.get(f"{ns}.request_options") or {}
    assert "temperature" in chat_options
    agent = KB_EMBEDDING_AGENTS["text-embedding-v3"]
    request = agent.create_request()
    resolve_model_pool_settings("text-embedding-v3", request.settings)
    options = request.settings.get(f"{ns}.request_options") or {}
    assert "temperature" not in options
    assert "max_tokens" not in options
    assert "thinking" not in options
    assert request.settings.get(f"{ns}.model") == "text-embedding-v3"
    assert request.settings.get(f"{ns}.model_type") == "embeddings"
    assert request.settings.get(f"{ns}.base_url")
    assert agent._active_model_key == "text-embedding-v3"
