from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class DecisionValidationError(ValueError):
    """Raised when an AI or local model output is unsafe or malformed."""


@dataclass(frozen=True)
class EmotionDecision:
    user_emotion: str
    robot_emotion: str
    face_id: str
    motion_id: str
    roll_bias: int
    pitch_bias: int
    speed: int
    hold_ms: int
    reply_text: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def validate(
        cls,
        data: dict[str, Any],
        allowed_emotions: list[str],
        allowed_face_ids: list[str],
        allowed_motion_ids: list[str],
    ) -> "EmotionDecision":
        required = [
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
        ]
        missing = [key for key in required if key not in data]
        if missing:
            raise DecisionValidationError(f"Missing keys: {', '.join(missing)}")

        user_emotion = _as_string(data["user_emotion"], "user_emotion")
        robot_emotion = _as_string(data["robot_emotion"], "robot_emotion")
        face_id = _as_string(data["face_id"], "face_id")
        motion_id = _as_string(data["motion_id"], "motion_id")
        reply_text = _as_string(data["reply_text"], "reply_text").strip()

        if user_emotion not in allowed_emotions:
            raise DecisionValidationError(f"Invalid user_emotion: {user_emotion}")
        if robot_emotion not in allowed_emotions:
            raise DecisionValidationError(f"Invalid robot_emotion: {robot_emotion}")
        if face_id not in allowed_face_ids:
            raise DecisionValidationError(f"Invalid face_id: {face_id}")
        if motion_id not in allowed_motion_ids:
            raise DecisionValidationError(f"Invalid motion_id: {motion_id}")
        if not reply_text:
            raise DecisionValidationError("reply_text must not be empty")

        roll_bias = _as_int(data["roll_bias"], "roll_bias")
        pitch_bias = _as_int(data["pitch_bias"], "pitch_bias")
        speed = _as_int(data["speed"], "speed")
        hold_ms = _as_int(data["hold_ms"], "hold_ms")
        confidence = _as_float(data["confidence"], "confidence")

        _check_range("roll_bias", roll_bias, -20, 20)
        _check_range("pitch_bias", pitch_bias, -20, 20)
        _check_range("speed", speed, 1, 100)
        _check_range("hold_ms", hold_ms, 0, 5000)
        if confidence < 0.0 or confidence > 1.0:
            raise DecisionValidationError("confidence out of range 0..1")

        return cls(
            user_emotion=user_emotion,
            robot_emotion=robot_emotion,
            face_id=face_id,
            motion_id=motion_id,
            roll_bias=roll_bias,
            pitch_bias=pitch_bias,
            speed=speed,
            hold_ms=hold_ms,
            reply_text=reply_text,
            confidence=confidence,
        )


def _as_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise DecisionValidationError(f"{name} must be a string")
    return value


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise DecisionValidationError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise DecisionValidationError(f"{name} must be an integer")


def _as_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionValidationError(f"{name} must be a number")
    return float(value)


def _check_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise DecisionValidationError(f"{name} out of range {minimum}..{maximum}: {value}")

