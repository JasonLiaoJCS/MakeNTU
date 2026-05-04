from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config_loader import project_path
from .emotion_schema import ALLOWED_FACE_IDS, ALLOWED_MOTION_IDS
from .models import EmotionDecision


class EmotionMapError(RuntimeError):
    """Raised when emotion_map.yaml is missing or inconsistent."""


def load_emotion_map(config: dict[str, Any]) -> dict[str, Any]:
    path = config.get("emotion", {}).get("map_file", "emotion_map.yaml")
    map_path = project_path(path)
    if not map_path.exists():
        raise EmotionMapError(f"Emotion map file not found: {map_path}")

    with map_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    emotions = data.get("emotions")
    if not isinstance(emotions, dict):
        raise EmotionMapError("emotion_map.yaml must contain an 'emotions' mapping")

    allowed = config.get("emotion", {}).get("allowed_emotions", [])
    missing = [emotion for emotion in allowed if emotion not in emotions]
    if missing:
        raise EmotionMapError(f"emotion_map.yaml missing emotions: {', '.join(missing)}")
    return data


def allowed_emotions(config: dict[str, Any]) -> list[str]:
    values = config.get("emotion", {}).get("allowed_emotions", [])
    return list(values)


def allowed_face_ids(map_data: dict[str, Any]) -> list[str]:
    values = {
        str(profile["face_id"])
        for profile in map_data.get("emotions", {}).values()
        if "face_id" in profile
    }
    return sorted(values | set(ALLOWED_FACE_IDS))


def allowed_motion_ids(map_data: dict[str, Any]) -> list[str]:
    values = {
        str(profile["motion_id"])
        for profile in map_data.get("emotions", {}).values()
        if "motion_id" in profile
    }
    return sorted(values | set(ALLOWED_MOTION_IDS))


def profile_for_emotion(emotion: str, map_data: dict[str, Any]) -> dict[str, Any]:
    profiles = map_data.get("emotions", {})
    if emotion not in profiles:
        raise EmotionMapError(f"Unknown emotion: {emotion}")
    return profiles[emotion]


def decision_from_profile(
    emotion: str,
    map_data: dict[str, Any],
    config: dict[str, Any],
    reply_text: str | None = None,
    confidence: float = 0.5,
) -> EmotionDecision:
    profile = profile_for_emotion(emotion, map_data)
    motion_cfg = config.get("motion", {})
    data = {
        "user_emotion": emotion,
        "robot_emotion": profile.get("robot_emotion", emotion),
        "face_id": profile["face_id"],
        "motion_id": profile["motion_id"],
        "roll_bias": int(profile.get("default_roll_bias", 0)),
        "pitch_bias": int(profile.get("default_pitch_bias", 0)),
        "speed": int(profile.get("speed", motion_cfg.get("default_speed", 25))),
        "hold_ms": int(profile.get("hold_ms", motion_cfg.get("default_hold_ms", 1000))),
        "reply_text": reply_text or profile.get("fallback_reply", "我聽見了。"),
        "confidence": confidence,
    }
    return EmotionDecision.validate(
        data,
        allowed_emotions(config),
        allowed_face_ids(map_data),
        allowed_motion_ids(map_data),
    )


def short_emotion_table(map_data: dict[str, Any]) -> str:
    lines: list[str] = []
    for emotion, profile in map_data.get("emotions", {}).items():
        lines.append(
            f"- {emotion}: face_id={profile.get('face_id')}, "
            f"motion_id={profile.get('motion_id')}, "
            f"style={profile.get('reply_style')}"
        )
    return "\n".join(lines)

