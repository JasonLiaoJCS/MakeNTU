from __future__ import annotations

import logging
from typing import Any

import requests

from ..emotion_map import short_emotion_table
from ..models import DecisionValidationError, EmotionDecision
from .base_backend import EmotionBackend, BackendError, load_prompt, retry_count


LOGGER = logging.getLogger(__name__)


class OllamaBackend(EmotionBackend):
    """Backend for Ollama native /api/chat or /api/generate."""

    def analyze(self, text: str) -> EmotionDecision:
        last_error: Exception | None = None
        for attempt in range(retry_count(self.config) + 1):
            try:
                raw = self._request(text)
                return self.parse_and_validate(raw)
            except DecisionValidationError as exc:
                last_error = exc
                LOGGER.warning("Ollama returned invalid JSON on attempt %s: %s", attempt + 1, exc)
            except Exception as exc:
                return self.fallback(text, f"Ollama API failed: {exc}")
        return self.fallback(text, f"Ollama JSON validation failed: {last_error}")

    def _request(self, text: str) -> str:
        ai_cfg: dict[str, Any] = self.config.get("ai", {})
        ollama_cfg: dict[str, Any] = ai_cfg.get("ollama", {})
        base_url = str(ollama_cfg.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
        model = ollama_cfg.get("model", "llama3.1:8b")
        api = str(ollama_cfg.get("api", "chat")).lower()
        stream = bool(ollama_cfg.get("stream", False))
        think = bool(ollama_cfg.get("think", False))
        timeout = float(ai_cfg.get("request_timeout_sec", 30))
        system_prompt = load_prompt("pc_controller/prompts/local_model_prompt.txt")
        table = short_emotion_table(self.map_data)

        if api == "generate":
            url = f"{base_url}/api/generate"
            payload = {
                "model": model,
                "prompt": (
                    f"{system_prompt}\n\nAllowed emotions and robot mappings:\n{table}\n\n"
                    f"User text:\n{text}"
                ),
                "format": "json",
                "stream": stream,
                "think": think,
            }
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            content = data.get("response")
        else:
            url = f"{base_url}/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Allowed emotions and robot mappings:\n{table}\n\n"
                            f"User text:\n{text}"
                        ),
                    },
                ],
                "format": "json",
                "stream": stream,
                "think": think,
            }
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            content = (data.get("message") or {}).get("content")

        if not content:
            raise BackendError("Ollama returned an empty response")
        return str(content)
