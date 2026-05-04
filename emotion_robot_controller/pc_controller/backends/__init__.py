from __future__ import annotations

from typing import Any

from .base_backend import EmotionBackend
from .ollama_backend import OllamaBackend
from .openai_backend import OpenAIBackend
from .openai_compatible_backend import OpenAICompatibleBackend
from .rule_based_backend import RuleBasedBackend


def create_backend(
    config: dict[str, Any],
    map_data: dict[str, Any],
    backend_name: str | None = None,
) -> EmotionBackend:
    name = backend_name or config.get("ai", {}).get("backend", "rule_based")
    name = str(name).lower()
    if name == "openai":
        return OpenAIBackend(config, map_data)
    if name == "ollama":
        return OllamaBackend(config, map_data)
    if name == "openai_compatible":
        return OpenAICompatibleBackend(config, map_data)
    if name == "rule_based":
        return RuleBasedBackend(config, map_data)
    raise ValueError(f"Unsupported AI backend: {name}")

