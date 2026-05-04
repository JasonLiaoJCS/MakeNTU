from __future__ import annotations

import logging
import os
from typing import Any

from openai import OpenAI

from ..emotion_map import allowed_emotions, allowed_face_ids, allowed_motion_ids, short_emotion_table
from ..emotion_schema import build_openai_response_format
from ..models import DecisionValidationError, EmotionDecision
from .base_backend import EmotionBackend, BackendError, load_prompt, retry_count


LOGGER = logging.getLogger(__name__)


class OpenAIBackend(EmotionBackend):
    """Cloud OpenAI backend using strict Structured Outputs when supported."""

    def analyze(self, text: str) -> EmotionDecision:
        last_error: Exception | None = None
        for attempt in range(retry_count(self.config) + 1):
            try:
                raw = self._request(text)
                return self.parse_and_validate(raw)
            except DecisionValidationError as exc:
                last_error = exc
                LOGGER.warning("OpenAI returned invalid JSON on attempt %s: %s", attempt + 1, exc)
            except Exception as exc:
                return self.fallback(text, f"OpenAI API failed: {exc}")
        return self.fallback(text, f"OpenAI JSON validation failed: {last_error}")

    def _request(self, text: str) -> str:
        ai_cfg: dict[str, Any] = self.config.get("ai", {})
        openai_cfg: dict[str, Any] = ai_cfg.get("openai", {})
        key_env = openai_cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.getenv(key_env)
        if not api_key:
            raise BackendError(f"Missing API key environment variable: {key_env}")

        client = OpenAI(api_key=api_key, timeout=float(ai_cfg.get("request_timeout_sec", 30)))
        model = openai_cfg.get("model", "gpt-4o-mini")
        temperature = float(openai_cfg.get("temperature", 0.2))
        system_prompt = load_prompt("pc_controller/prompts/emotion_system_prompt.txt")

        user_prompt = (
            "Allowed emotions and robot mappings:\n"
            f"{short_emotion_table(self.map_data)}\n\n"
            f"User text:\n{text}"
        )

        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            response_format=build_openai_response_format(
                allowed_emotions(self.config),
                allowed_face_ids(self.map_data),
                allowed_motion_ids(self.map_data),
            ),
        )
        content = completion.choices[0].message.content
        if not content:
            raise BackendError("OpenAI returned an empty message")
        return content

