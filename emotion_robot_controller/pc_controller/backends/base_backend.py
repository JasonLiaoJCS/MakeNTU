from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..config_loader import PROJECT_ROOT
from ..emotion_map import allowed_emotions, allowed_face_ids, allowed_motion_ids
from ..fallback_rules import classify_rule_based
from ..models import DecisionValidationError, EmotionDecision


LOGGER = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """Raised when a backend cannot return a usable model response."""


class EmotionBackend(ABC):
    def __init__(self, config: dict[str, Any], map_data: dict[str, Any]) -> None:
        self.config = config
        self.map_data = map_data

    @abstractmethod
    def analyze(self, text: str) -> EmotionDecision:
        raise NotImplementedError

    def fallback(self, text: str, reason: str) -> EmotionDecision:
        LOGGER.warning("Using rule-based fallback: %s", reason)
        return classify_rule_based(text, self.map_data, self.config)

    def validate_payload(self, payload: dict[str, Any]) -> EmotionDecision:
        return EmotionDecision.validate(
            payload,
            allowed_emotions(self.config),
            allowed_face_ids(self.map_data),
            allowed_motion_ids(self.map_data),
        )

    def parse_and_validate(self, raw_text: str) -> EmotionDecision:
        payload = parse_json_object(raw_text)
        return self.validate_payload(payload)


def load_prompt(relative_path: str) -> str:
    path = PROJECT_ROOT / relative_path
    return path.read_text(encoding="utf-8")


def parse_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise DecisionValidationError("No JSON object found in model output")
        value = json.loads(match.group(0))

    if not isinstance(value, dict):
        raise DecisionValidationError("Model output must be a JSON object")
    return value


def retry_count(config: dict[str, Any]) -> int:
    return int(config.get("ai", {}).get("retry_invalid_json_count", 1))

