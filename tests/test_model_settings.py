from __future__ import annotations

from agently import Agently

import integrated_agent.config  # noqa: F401


def test_model_settings_keep_chat_path_mapping() -> None:
    mapping = Agently.settings.get("plugins.ModelRequester.OpenAICompatible.path_mapping")
    assert mapping["chat"] == "/chat/completions"
