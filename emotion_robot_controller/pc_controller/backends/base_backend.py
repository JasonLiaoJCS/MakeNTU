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
    text = strip_model_wrappers(raw_text)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = decode_first_json_object(text)

    if not isinstance(value, dict):
        raise DecisionValidationError("Model output must be a JSON object")
    return value


def strip_model_wrappers(raw_text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", str(raw_text or ""), flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^\s*/?no_think\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def decode_first_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        candidate = text[match.start() :]
        try:
            value, _end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            repaired = repair_truncated_json(candidate)
            if not repaired:
                continue
            try:
                value = json.loads(repaired)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict):
            return value
    raise DecisionValidationError("No JSON object found in model output")


def repair_truncated_json(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    candidate = text[start:].strip()
    stack: list[str] = []
    in_string = False
    escaped = False
    last_index = -1

    for index, char in enumerate(candidate):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append("}" if char == "{" else "]")
            last_index = index
        elif char in "}]":
            if not stack or stack[-1] != char:
                break
            stack.pop()
            last_index = index
            if not stack:
                return candidate[: index + 1]
        elif stack:
            last_index = index

    if last_index < 0:
        return ""
    repaired = candidate[: last_index + 1].rstrip()
    if in_string:
        repaired += '"'
    repaired += "".join(reversed(stack))
    return repaired


def retry_count(config: dict[str, Any]) -> int:
    return int(config.get("ai", {}).get("retry_invalid_json_count", 1))
