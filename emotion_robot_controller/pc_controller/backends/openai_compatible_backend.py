from __future__ import annotations

import logging
import os
from typing import Any

from openai import OpenAI

from ..emotion_map import short_emotion_table
from ..models import DecisionValidationError, EmotionDecision
from .base_backend import EmotionBackend, BackendError, load_prompt, retry_count


LOGGER = logging.getLogger(__name__)


class OpenAICompatibleBackend(EmotionBackend):
    """Backend for LM Studio, llama.cpp server, or other /v1/chat/completions servers."""

    def analyze(self, text: str) -> EmotionDecision:
        last_error: Exception | None = None
        for attempt in range(retry_count(self.config) + 1):
            try:
                raw = self._request(text, use_json_object=(attempt == 0))
                return self.parse_and_validate(raw)
            except DecisionValidationError as exc:
                last_error = exc
                LOGGER.warning("OpenAI-compatible backend returned invalid JSON on attempt %s: %s", attempt + 1, exc)
            except Exception as exc:
                if attempt == 0:
                    LOGGER.warning("OpenAI-compatible request failed with JSON mode, retrying plain chat: %s", exc)
                    continue
                return self.fallback(text, f"OpenAI-compatible API failed: {exc}")
        return self.fallback(text, f"OpenAI-compatible JSON validation failed: {last_error}")

    def _request(self, text: str, use_json_object: bool) -> str:
        ai_cfg: dict[str, Any] = self.config.get("ai", {})
        local_cfg: dict[str, Any] = ai_cfg.get("openai_compatible", {})
        base_url = local_cfg.get("base_url", "http://127.0.0.1:1234/v1")
        key_env = local_cfg.get("api_key_env", "LOCAL_API_KEY")
        api_key = os.getenv(key_env) or "local-api-key"

        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=float(ai_cfg.get("request_timeout_sec", 30)),
        )
        model = local_cfg.get("model", "local-model")
        temperature = float(local_cfg.get("temperature", 0.2))
        system_prompt = load_prompt("pc_controller/prompts/local_model_prompt.txt")
        user_prompt = (
            "Allowed emotions and robot mappings:\n"
            f"{short_emotion_table(self.map_data)}\n\n"
            "Choose the best JSON decision for this user text:\n"
            f"{text}"
        )
        kwargs: dict[str, Any] = {}
        if use_json_object:
            kwargs["response_format"] = {"type": "json_object"}

        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            **kwargs,
        )
        content = completion.choices[0].message.content
        if not content:
            raise BackendError("OpenAI-compatible backend returned an empty message")
        return content

