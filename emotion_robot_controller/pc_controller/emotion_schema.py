from __future__ import annotations

from copy import deepcopy
from typing import Any


ALLOWED_EMOTIONS = [
    "neutral",
    "happy",
    "excited",
    "sad",
    "tired",
    "angry",
    "surprised",
    "curious",
    "confused",
    "thinking",
    "concerned",
    "sleepy",
]

ALLOWED_FACE_IDS = [
    "FACE_NEUTRAL",
    "FACE_HAPPY",
    "FACE_EXCITED",
    "FACE_SAD",
    "FACE_TIRED",
    "FACE_ANGRY",
    "FACE_SURPRISED",
    "FACE_CURIOUS",
    "FACE_CONFUSED",
    "FACE_THINKING",
    "FACE_CONCERNED",
    "FACE_SLEEPY",
]

ALLOWED_MOTION_IDS = [
    "CENTER",
    "HAPPY_NOD_SWAY",
    "EXCITED_FAST_NOD",
    "SAD_LOWER_HEAD",
    "TIRED_DROOP",
    "ANGRY_SHORT_SHAKE",
    "SURPRISED_POP_UP",
    "CURIOUS_TILT",
    "CONFUSED_DOUBLE_TILT",
    "THINKING_LOOK_DOWN_UP",
    "CONCERNED_SOFT_NOD",
    "SLEEPY_BREATH",
    "ROLL_LEFT",
    "ROLL_RIGHT",
    "PITCH_UP",
    "PITCH_DOWN",
]


def build_json_schema(
    allowed_emotions: list[str] | None = None,
    allowed_face_ids: list[str] | None = None,
    allowed_motion_ids: list[str] | None = None,
) -> dict[str, Any]:
    emotions = allowed_emotions or ALLOWED_EMOTIONS
    face_ids = allowed_face_ids or ALLOWED_FACE_IDS
    motion_ids = allowed_motion_ids or ALLOWED_MOTION_IDS

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "user_emotion",
            "robot_emotion",
            "face_id",
            "motion_id",
            "roll_bias",
            "pitch_bias",
            "speed",
            "hold_ms",
            "reply_text",
            "confidence",
        ],
        "properties": {
            "user_emotion": {"type": "string", "enum": emotions},
            "robot_emotion": {"type": "string", "enum": emotions},
            "face_id": {"type": "string", "enum": face_ids},
            "motion_id": {"type": "string", "enum": motion_ids},
            "roll_bias": {"type": "integer", "minimum": -20, "maximum": 20},
            "pitch_bias": {"type": "integer", "minimum": -20, "maximum": 20},
            "speed": {"type": "integer", "minimum": 1, "maximum": 100},
            "hold_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
            "reply_text": {"type": "string", "minLength": 1, "maxLength": 160},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
    }


def build_openai_response_format(
    allowed_emotions: list[str] | None = None,
    allowed_face_ids: list[str] | None = None,
    allowed_motion_ids: list[str] | None = None,
) -> dict[str, Any]:
    schema = build_json_schema(allowed_emotions, allowed_face_ids, allowed_motion_ids)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "robot_emotion_decision",
            "strict": True,
            "schema": deepcopy(schema),
        },
    }

