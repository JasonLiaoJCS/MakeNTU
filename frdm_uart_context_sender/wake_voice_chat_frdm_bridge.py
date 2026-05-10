#!/usr/bin/env python3
"""
Wake-word voice chat -> Windows AI -> FRDM UART bridge.

This is the hands-free version of voice_chat_frdm_uart_bridge.py. It keeps the
Jetson microphone open, waits for a wake word, records until the voice volume
has stayed low for a short time, then sends the captured WAV to the existing
Windows /voice-chat endpoint. The returned reply/emotion is handled by the
same FRDM UART and Piper TTS flow as the Enter-based bridge.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import queue
import random
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tempfile
import urllib.error
import urllib.request
import urllib.parse
import uuid
import wave
from typing import Any, Callable

import numpy as np


os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
VOICE_DIR = PROJECT_ROOT / "emotion_robot_controller" / "voice_stt_remote"
VISION_DIR = PROJECT_ROOT / "vision"
MUSIC_DIR = PROJECT_ROOT / "music_web_player"
DEFAULT_FOCUS_SCRIPT = THIS_DIR / "focus_work_mode.py"

if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))
if str(VISION_DIR) not in sys.path:
    sys.path.insert(0, str(VISION_DIR))
if str(MUSIC_DIR) not in sys.path:
    sys.path.insert(0, str(MUSIC_DIR))

import voice_chat_frdm_uart_bridge as bridge  # noqa: E402
import jetson_fast_voice_chat as voice_chat  # noqa: E402
try:
    import music_web_player as music_tool  # noqa: E402
except Exception:
    music_tool = None  # type: ignore[assignment]
try:
    import esp32s3_ble_fan_led_controller as esp32_ble  # noqa: E402
except Exception as exc:
    esp32_ble = None  # type: ignore[assignment]
    ESP32_BLE_IMPORT_ERROR = exc
else:
    ESP32_BLE_IMPORT_ERROR = None


CLIENT_VERSION = "wake_voice_chat_frdm_bridge_vision_conversation_motor_natural_v6"
DEFAULT_INSTANCE_LOCK = "/tmp/wake_voice_chat_frdm_bridge.lock"
DEFAULT_MUSIC_TOOL_URL = os.getenv("MUSIC_TOOL_URL", "http://127.0.0.1:8788/music")
DEFAULT_WEATHER_TOOL_URL = os.getenv("WEATHER_TOOL_URL", "http://127.0.0.1:8788/weather")
DEFAULT_WEATHER_LOCATION = os.getenv("WEATHER_DEFAULT_LOCATION", "Taipei")
DEFAULT_ESP32_TEMPERATURE_PATH = os.getenv("ESP32_TEMPERATURE_PATH", "/temperature")
DEFAULT_TODO_LIST_PATH = THIS_DIR / "logs" / "todo_list.json"
DEFAULT_AI_TRACE_PATH = THIS_DIR / "logs" / "ai_trace.jsonl"
DEFAULT_WAKE_STATUS_PATH = THIS_DIR / "logs" / "wake_status.json"
DEFAULT_ESP32_DASHBOARD_HOST = os.getenv("ESP32_DASHBOARD_HOST", "127.0.0.1")
DEFAULT_ESP32_DASHBOARD_PORT = 8791
DEFAULT_FAN_DASHBOARD_URL = os.getenv("FAN_DASHBOARD_URL", "http://127.0.0.1:8789/api/devices/{device_id}/set")
DEFAULT_DISCORD_WEBHOOK_FILE = Path(os.getenv("DISCORD_WEBHOOK_FILE", "~/.config/makentu/discord_webhook_url")).expanduser()
PET_IDLE_SILENCE_TOKEN = "PET_IDLE_SILENCE"
SESSION_END_KEYWORDS = (
    "結束對話",
    "退出對話模式",
    "結束",
    "不用了",
    "不用聽了",
    "先這樣",
    "沒事了",
    "掰掰",
    "拜拜",
    "再見",
    "再會",
    "休息",
    "你可以睡了",
    "bye bye",
    "byebye",
    "bye",
    "go to sleep",
    "stop listening",
    "goodbye",
    "good bye",
    "by by",
    "buy buy",
)
SESSION_END_REGEXES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bye-zh-variant", re.compile(r"(掰|拜){2,}")),
    ("bye-asr-white-white", re.compile(r"白白(了|啦|囉|啰|喔|哦|唷|呀|啊|吧)?$")),
    ("bye-asr-eight-eight", re.compile(r"八八(了|啦|囉|啰|喔|哦|唷|呀|啊|吧)?$")),
    ("bye-en-variant", re.compile(r"(bye|by|buy){2,}|good(bye|by)")),
    ("goodbye-zh", re.compile(r"再[見见會会]")),
)
DEMO_STALE_PROCESS_PATTERNS = (
    "wake_voice_chat_frdm_bridge.py",
    "camera_ollama_status.py",
    "cameraTest.py",
    "latest_frame_camera.py",
    "camera_object_comment.py",
    "camera_gemini.py",
    "arecord",
    "aplay",
    "mpv",
    "ffplay",
    "paplay",
)
AUDIO_STALE_PROCESS_NAMES = {"arecord", "aplay", "mpv", "ffplay", "paplay"}
DEVICE_OWNER_ALLOW_PATTERNS = (
    "jetson_piper_tts.server",
    "pulseaudio",
    "pipewire",
    "wireplumber",
)
UART_DEVICE_GLOB_PATTERNS = ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*")
UART_DEVICE_DESCRIPTION = "FRDM UART (/dev/serial/by-id/*, /dev/ttyACM*, /dev/ttyUSB*)"
DEMO_PREFLIGHT_SKIP_ARGS = (
    "--self-test",
    "--device-preflight-only",
    "--help",
    "--list-mics",
    "--list-uarts",
    "--test-beep",
)

CORE_SCREEN_COMMANDS = {"Sleep", "Normal", "Thinking", "Speaking", "Music", "Focus"}
MOTOR_COMMANDS = {"MotorPitch", "MotorYaw", "MotorYawPitch"}
UTILITY_COMMANDS = {"ShowNum"}
WEATHER_COMMANDS = {"Weather"}
TIME_COMMANDS = {"Time"}
DATA_COMMANDS = {"Todo", "TodoItem", "TodoEnd", "Health", "Device", "TempRoom"}
ALLOWED_UART_COMMANDS = CORE_SCREEN_COMMANDS | MOTOR_COMMANDS | UTILITY_COMMANDS | WEATHER_COMMANDS | TIME_COMMANDS | DATA_COMMANDS
SINGLE_ARG_UART_COMMANDS = (MOTOR_COMMANDS - {"MotorYawPitch"}) | {"Speaking", "TempRoom"}

VALID_PERSISTENT_STATES = {"normal", "sleep", "unchanged"}
VALID_SCREEN_MODES = {"unchanged", "normal", "sleep", "thinking", "music", "focus"}
VALID_EMOTIONS = {"neutral", "concerned", "angry", "sad", "happy", "curious", "excited", "confused", "sleepy"}
VALID_HEAD_MOTIONS = {
    "none",
    "nod",
    "double_nod",
    "look_around",
    "shake",
    "gentle_nod",
    "sleepy_drop",
    "happy_bounce",
    "excited_bounce",
    "curious_peek",
    "concerned_tilt",
    "sad_droop",
    "confused_tilt",
    "firm_shake",
}
SCREEN_STATE_DEDUPE_SEC = 1.5

SCREEN_MODE_TO_COMMAND = {
    "normal": "Normal",
    "sleep": "Sleep",
    "thinking": "Thinking",
    "music": "Music",
    "focus": "Focus",
}

# FRDM SpeakingGui expects a single emotion argument:
# 0=neutral, 1=concerned, 2=angry, 3=sad, 4=happy, 5=confused.
EMOTION_TO_SPEAKING_CODE = {
    "neutral": 0,
    "concerned": 1,
    "angry": 2,
    "sad": 3,
    "happy": 4,
    "curious": 5,
    "excited": 4,
    "confused": 5,
    "sleepy": 3,
}

EMOTION_TO_HEAD_MOTION = {
    "neutral": "gentle_nod",
    "concerned": "concerned_tilt",
    "angry": "firm_shake",
    "sad": "sad_droop",
    "happy": "happy_bounce",
    "curious": "curious_peek",
    "excited": "excited_bounce",
    "confused": "confused_tilt",
    "sleepy": "sleepy_drop",
}

EMOTION_ALIASES = {
    "calm": "neutral",
    "normal": "neutral",
    "中性": "neutral",
    "開心": "happy",
    "开心": "happy",
    "joy": "happy",
    "joyful": "happy",
    "positive": "happy",
    "interested": "curious",
    "thinking": "curious",
    "questioning": "curious",
    "好奇": "curious",
    "surprised": "excited",
    "surprise": "excited",
    "energetic": "excited",
    "amazed": "excited",
    "興奮": "excited",
    "兴奋": "excited",
    "unsure": "confused",
    "uncertain": "confused",
    "puzzled": "confused",
    "confusing": "confused",
    "困惑": "confused",
    "angry": "angry",
    "mad": "angry",
    "furious": "angry",
    "rage": "angry",
    "生氣": "angry",
    "生气": "angry",
    "火大": "angry",
    "憤怒": "angry",
    "愤怒": "angry",
    "sad": "sad",
    "down": "sad",
    "depressed": "sad",
    "難過": "sad",
    "难过": "sad",
    "沮喪": "sad",
    "沮丧": "sad",
    "anxious": "concerned",
    "worried": "concerned",
    "frustrated": "concerned",
    "upset": "concerned",
    "急": "concerned",
    "急躁": "concerned",
    "擔心": "concerned",
    "担心": "concerned",
    "焦慮": "concerned",
    "焦虑": "concerned",
    "tired": "sleepy",
    "drowsy": "sleepy",
    "sleep": "sleepy",
    "asleep": "sleepy",
    "睏": "sleepy",
    "困": "sleepy",
    "疲累": "sleepy",
}

# FRDM head motors use absolute servo angles:
# MotorPitch 65=down limit, 90=center, 115=up limit.
# MotorYaw 0=right limit, 90=center, 180=left limit.
# Single-axis motor UART wire format is one argument: "MotorPitch 90".
# Combined motor UART wire format is two arguments: "MotorYawPitch 120 90".
MOTOR_PITCH_MIN = 65
MOTOR_PITCH_CENTER = 90
MOTOR_PITCH_MAX = 115
MOTOR_YAW_MIN = 0
MOTOR_YAW_CENTER = 90
MOTOR_YAW_MAX = 180
PITCH_DOWN_LIMIT = MOTOR_PITCH_MIN
PITCH_DOWN_STRONG = 65
PITCH_DOWN = 72
PITCH_DOWN_SOFT = 80
PITCH_DROWSY = 76
PITCH_CENTER = MOTOR_PITCH_CENTER
PITCH_ATTENTIVE = 102
PITCH_UP_SOFT = 100
PITCH_UP = 108
PITCH_UP_STRONG = 115
PITCH_UP_LIMIT = MOTOR_PITCH_MAX
YAW_RIGHT_LIMIT = MOTOR_YAW_MIN
YAW_RIGHT = MOTOR_YAW_MIN
YAW_RIGHT_SOFT = 25
YAW_RIGHT_SMALL = 55
YAW_RIGHT_TINY = 72
YAW_CENTER = MOTOR_YAW_CENTER
YAW_LEFT_TINY = 108
YAW_LEFT_SMALL = 125
YAW_LEFT_SOFT = 155
YAW_LEFT = MOTOR_YAW_MAX
YAW_LEFT_LIMIT = MOTOR_YAW_MAX
MOTOR_STEP_DELAY_SEC = 0.55
MOTOR_LIVE_MIN_STEP_DELAY_SEC = 0.25
MOTOR_SMOOTH_STEP_DEG = 120
MOTOR_SPEAKING_STEP_DELAY_SEC = 0.72
MOTOR_SPEAKING_SMOOTH_STEP_DEG = 120
MOTOR_STOP_TIMEOUT_SEC = 6.0
MOTOR_RESET_REPEATS = 1
MOTOR_RESET_DELAY_SEC = 0.35
MOTOR_LIVE_MIN_RESET_DELAY_SEC = 0.20
MOTOR_READ_MS = 35
MOTOR_JOIN_TIMEOUT_SEC = 6.0
MOTOR_ACK_RE = re.compile(r"\bMotor\s+(Pitch|Yaw)\s*=\s*(-?\d+)\b", re.IGNORECASE)
MOTOR_YAWPITCH_ACK_RE = re.compile(
    r"\bMotor\s+YawPitch\s*=\s*yaw\s*:?\s*(-?\d+)\s+pitch\s*:?\s*(-?\d+)\b",
    re.IGNORECASE,
)
MotorStep = tuple[str, int, int]


def pitch(angle: int) -> MotorStep:
    return ("MotorPitch", angle, 0)


def yaw(angle: int) -> MotorStep:
    return ("MotorYaw", angle, 0)


def yaw_pitch(yaw_angle: int, pitch_angle: int) -> MotorStep:
    return ("MotorYawPitch", yaw_angle, pitch_angle)


def hold_step(step: MotorStep, count: int = 1) -> list[MotorStep]:
    """Keep call sites readable; actual hold time is handled by per-pose delays."""
    _ = count
    return [step]


def center_head_steps() -> list[MotorStep]:
    return [yaw_pitch(YAW_CENTER, PITCH_CENTER)]


def format_motor_sequence(steps: list[MotorStep]) -> str:
    chunks = []
    for command, value, value2 in steps:
        if command == "MotorYawPitch":
            chunks.append(f"{command}:yaw={value},pitch={value2}")
        else:
            chunks.append(f"{command}:{value}")
    return " -> ".join(chunks)


def format_uart_wire_command(command: str, v1: int, v2: int) -> str:
    if command == "MotorYawPitch":
        return f"{command} {v1} {v2}"
    if command in SINGLE_ARG_UART_COMMANDS:
        return f"{command} {v1}"
    return f"{command} {v1} {v2}"


def motor_command_limits(command: str) -> tuple[int, int]:
    if command == "MotorPitch":
        return MOTOR_PITCH_MIN, MOTOR_PITCH_MAX
    if command == "MotorYaw":
        return MOTOR_YAW_MIN, MOTOR_YAW_MAX
    return -999999, 999999


def motor_ack_problem(command: str, expected_value: int, rx_lines: list[str], expected_value2: int = 0) -> str:
    if command == "MotorYawPitch":
        for line in rx_lines:
            match = MOTOR_YAWPITCH_ACK_RE.search(line)
            if not match:
                continue
            reported_yaw = int(match.group(1))
            reported_pitch = int(match.group(2))
            yaw_ok = MOTOR_YAW_MIN <= reported_yaw <= MOTOR_YAW_MAX
            pitch_ok = MOTOR_PITCH_MIN <= reported_pitch <= MOTOR_PITCH_MAX
            if yaw_ok and pitch_ok:
                return ""
            return (
                f"FRDM MotorYawPitch ACK out of range after MotorYawPitch {expected_value} {expected_value2}: {line!r}. "
                f"Expected yaw {MOTOR_YAW_MIN}..{MOTOR_YAW_MAX}, pitch {MOTOR_PITCH_MIN}..{MOTOR_PITCH_MAX}."
            )
        return ""

    if command not in {"MotorPitch", "MotorYaw"}:
        return ""

    low, high = motor_command_limits(command)
    expected_axis = "Pitch" if command == "MotorPitch" else "Yaw"
    for line in rx_lines:
        match = MOTOR_ACK_RE.search(line)
        if not match:
            continue
        axis = match.group(1).title()
        if axis != expected_axis:
            continue
        reported = int(match.group(2))
        if low <= reported <= high:
            return ""

        pointer_hint = ""
        if 0x20000000 <= reported <= 0x3FFFFFFF:
            pointer_hint = " The value looks like a Cortex-M RAM pointer, so FRDM likely used char* pValue as an int instead of atoi(pValue)."
        return (
            f"FRDM motor ACK out of range after {command} {expected_value}: {line!r}. "
            f"Expected {low}..{high}.{pointer_hint}"
        )
    return ""


def clamp_motor_value(command: str, value: int) -> int:
    if command == "MotorPitch":
        return clamp_int(value, MOTOR_PITCH_MIN, MOTOR_PITCH_MAX)
    if command == "MotorYaw":
        return clamp_int(value, MOTOR_YAW_MIN, MOTOR_YAW_MAX)
    return int(value)


def clamp_motor_step(command: str, v1: int, v2: int = 0) -> MotorStep:
    if command == "MotorYawPitch":
        return (
            "MotorYawPitch",
            clamp_int(v1, MOTOR_YAW_MIN, MOTOR_YAW_MAX),
            clamp_int(v2, MOTOR_PITCH_MIN, MOTOR_PITCH_MAX),
        )
    if command == "MotorPitch":
        return ("MotorPitch", clamp_int(v1, MOTOR_PITCH_MIN, MOTOR_PITCH_MAX), 0)
    if command == "MotorYaw":
        return ("MotorYaw", clamp_int(v1, MOTOR_YAW_MIN, MOTOR_YAW_MAX), 0)
    return (command, int(v1), int(v2))


def smoothstep_fraction(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def interpolation_segments(max_delta: int, step_deg: int) -> int:
    if max_delta <= 0:
        return 1
    return max(1, int(np.ceil(max_delta / max(1, step_deg))))


def crosses_yaw_center(current_yaw: int, target_yaw: int, *, min_offset: int = 12) -> bool:
    current_offset = int(current_yaw) - YAW_CENTER
    target_offset = int(target_yaw) - YAW_CENTER
    return (
        current_offset * target_offset < 0
        and abs(current_offset) >= min_offset
        and abs(target_offset) >= min_offset
    )


def yaw_pitch_interpolation_segments(
    current_yaw: int,
    current_pitch: int,
    target_yaw: int,
    target_pitch: int,
    step_deg: int,
) -> int:
    # A cross-center yaw turn should be one continuous servo move. Inserting
    # an intermediate center command makes the head pause in the middle.
    if crosses_yaw_center(current_yaw, target_yaw):
        return 1
    return interpolation_segments(max(abs(target_yaw - current_yaw), abs(target_pitch - current_pitch)), step_deg)


def smooth_motor_sequence(keyframes: list[MotorStep], max_step_deg: int) -> list[MotorStep]:
    """Expand only very large jumps; keyframes carry the intended held poses."""
    step_deg = max(1, int(max_step_deg or MOTOR_SMOOTH_STEP_DEG))
    expanded: list[MotorStep] = []
    current_by_command: dict[str, int] = {}
    current_yaw: int | None = None
    current_pitch: int | None = None
    for command, raw_value, _unused in keyframes:
        if command == "MotorYawPitch":
            _name, target_yaw, target_pitch = clamp_motor_step(command, int(raw_value), int(_unused))
            if current_yaw is None or current_pitch is None:
                step = yaw_pitch(target_yaw, target_pitch)
                if not expanded or expanded[-1] != step:
                    expanded.append(step)
                current_yaw = target_yaw
                current_pitch = target_pitch
                continue

            delta_yaw = target_yaw - current_yaw
            delta_pitch = target_pitch - current_pitch
            if delta_yaw == 0 and delta_pitch == 0:
                expanded.append(yaw_pitch(target_yaw, target_pitch))
                continue

            segments = yaw_pitch_interpolation_segments(current_yaw, current_pitch, target_yaw, target_pitch, step_deg)
            for index in range(1, segments + 1):
                fraction = index / segments
                interpolated_yaw = clamp_int(int(round(current_yaw + (delta_yaw * fraction))), MOTOR_YAW_MIN, MOTOR_YAW_MAX)
                interpolated_pitch = clamp_int(int(round(current_pitch + (delta_pitch * fraction))), MOTOR_PITCH_MIN, MOTOR_PITCH_MAX)
                step = yaw_pitch(interpolated_yaw, interpolated_pitch)
                if expanded and expanded[-1] == step:
                    continue
                expanded.append(step)
            current_yaw = target_yaw
            current_pitch = target_pitch
            continue

        command, value, _unused = clamp_motor_step(command, int(raw_value), int(_unused))
        if command not in MOTOR_COMMANDS:
            expanded.append((command, value, _unused))
            continue

        previous = current_by_command.get(command)
        if previous is None:
            expanded.append((command, value, 0))
            current_by_command[command] = value
            if command == "MotorYaw":
                current_yaw = value
            elif command == "MotorPitch":
                current_pitch = value
            continue

        delta = value - previous
        if delta == 0:
            expanded.append((command, value, 0))
            continue

        segments = interpolation_segments(abs(delta), step_deg)
        for index in range(1, segments + 1):
            fraction = index / segments
            interpolated = int(round(previous + (delta * fraction)))
            interpolated = clamp_motor_value(command, interpolated)
            step = (command, interpolated, 0)
            if expanded and expanded[-1] == step:
                continue
            expanded.append(step)
        current_by_command[command] = value
        if command == "MotorYaw":
            current_yaw = value
        elif command == "MotorPitch":
            current_pitch = value
    return expanded


def natural_motor_delays(sequence: list[MotorStep], base_delay: float, *, speaking: bool = False) -> list[float]:
    """Hold expressive poses longer than transit points."""
    base = max(0.01, float(base_delay or 0.01))
    delays: list[float] = []
    previous: MotorStep | None = None
    for index, step in enumerate(sequence):
        command, v1, v2 = step
        multiplier = 0.82 + (0.10 * (index % 3))
        if previous == step:
            multiplier = 2.45 if not speaking else 2.10
        elif command == "MotorYawPitch":
            yaw_offset = abs(v1 - YAW_CENTER)
            pitch_offset = abs(v2 - PITCH_CENTER)
            if yaw_offset <= 2 and pitch_offset <= 2:
                multiplier = max(multiplier, 1.15)
            if yaw_offset >= 70 or pitch_offset >= 20:
                multiplier = max(multiplier, 1.75 if speaking else 1.95)
            elif yaw_offset >= 35 or pitch_offset >= 12:
                multiplier = max(multiplier, 1.35 if speaking else 1.50)
            if previous is not None and previous[0] == "MotorYawPitch":
                prev_yaw, prev_pitch = previous[1], previous[2]
                near_target = abs(v1 - prev_yaw) <= 5 and abs(v2 - prev_pitch) <= 5
                if near_target:
                    multiplier = max(multiplier, 2.55 if not speaking else 2.15)
        delay = base * multiplier
        delays.append(max(0.12, min(delay, 1.35 if speaking else 1.55)))
        previous = step
    return delays


def sleep_interruptible(duration_sec: float, stop_event: threading.Event | None = None) -> bool:
    """Sleep up to duration_sec. Return False if stop_event was set."""
    duration = max(0.0, float(duration_sec or 0.0))
    if duration <= 0.0:
        return stop_event is None or not stop_event.is_set()
    if stop_event is None:
        time.sleep(duration)
        return True
    return not stop_event.wait(duration)


HEAD_MOTION_SEQUENCES = {
    "none": center_head_steps(),
    "nod": [
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP_SOFT),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_STRONG)),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "double_nod": [
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP_SOFT),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_STRONG)),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP_SOFT),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN)),
        yaw_pitch(YAW_CENTER, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "look_around": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_ATTENTIVE),
        *hold_step(yaw_pitch(YAW_RIGHT, PITCH_ATTENTIVE)),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_ATTENTIVE),
        *hold_step(yaw_pitch(YAW_LEFT, PITCH_ATTENTIVE)),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "shake": [
        *hold_step(yaw_pitch(YAW_RIGHT_SOFT, PITCH_CENTER)),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_CENTER),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "gentle_nod": [
        yaw_pitch(YAW_RIGHT_TINY, PITCH_ATTENTIVE),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_SOFT)),
        yaw_pitch(YAW_CENTER, PITCH_UP_SOFT),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "sleepy_drop": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DROWSY),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN),
        *hold_step(yaw_pitch(YAW_RIGHT_SOFT, PITCH_DOWN_STRONG)),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DROWSY),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "happy_bounce": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP_STRONG),
        yaw_pitch(YAW_CENTER, PITCH_UP),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_UP),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "excited_bounce": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_UP_STRONG),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_ATTENTIVE),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_UP_STRONG),
        yaw_pitch(YAW_LEFT_TINY, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP),
        yaw_pitch(YAW_CENTER, PITCH_UP_STRONG),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "curious_peek": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT, PITCH_UP),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_ATTENTIVE),
        yaw_pitch(YAW_LEFT, PITCH_UP),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_UP_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "concerned_tilt": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_ATTENTIVE),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "sad_droop": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_STRONG),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_STRONG),
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_DOWN_STRONG),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "confused_tilt": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_DOWN),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
    "firm_shake": [
        yaw_pitch(YAW_RIGHT, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT, PITCH_UP_SOFT),
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_CENTER),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_CENTER),
        yaw_pitch(YAW_CENTER, PITCH_CENTER),
    ],
}

SPEAKING_HEAD_MOTION_LOOPS = {
    "none": center_head_steps(),
    "nod": [
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP_SOFT),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN)),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
    ],
    "double_nod": [
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP_SOFT),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN)),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_SOFT),
    ],
    "look_around": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_ATTENTIVE),
        *hold_step(yaw_pitch(YAW_RIGHT, PITCH_ATTENTIVE)),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_ATTENTIVE),
        *hold_step(yaw_pitch(YAW_LEFT, PITCH_ATTENTIVE)),
    ],
    "shake": [
        *hold_step(yaw_pitch(YAW_RIGHT_SOFT, PITCH_CENTER)),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_CENTER),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP_SOFT),
    ],
    "gentle_nod": [
        yaw_pitch(YAW_RIGHT_TINY, PITCH_ATTENTIVE),
        *hold_step(yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_SOFT)),
        yaw_pitch(YAW_CENTER, PITCH_UP_SOFT),
    ],
    "sleepy_drop": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DROWSY),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN),
        *hold_step(yaw_pitch(YAW_RIGHT_SOFT, PITCH_DOWN_STRONG)),
    ],
    "happy_bounce": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP),
        yaw_pitch(YAW_CENTER, PITCH_UP_STRONG),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP_STRONG),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_UP),
    ],
    "excited_bounce": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_UP_STRONG),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_ATTENTIVE),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_UP_STRONG),
        yaw_pitch(YAW_LEFT_TINY, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_UP),
    ],
    "curious_peek": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT, PITCH_UP),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_ATTENTIVE),
        yaw_pitch(YAW_LEFT, PITCH_UP),
    ],
    "concerned_tilt": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_RIGHT_TINY, PITCH_DOWN_SOFT),
    ],
    "sad_droop": [
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_DOWN),
        yaw_pitch(YAW_CENTER, PITCH_DOWN_STRONG),
        yaw_pitch(YAW_LEFT_TINY, PITCH_DOWN_STRONG),
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_DOWN_STRONG),
    ],
    "confused_tilt": [
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_CENTER, PITCH_ATTENTIVE),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP),
        yaw_pitch(YAW_LEFT_SMALL, PITCH_DOWN),
    ],
    "firm_shake": [
        yaw_pitch(YAW_RIGHT, PITCH_UP_SOFT),
        yaw_pitch(YAW_LEFT, PITCH_UP_SOFT),
        yaw_pitch(YAW_RIGHT_SOFT, PITCH_CENTER),
        yaw_pitch(YAW_LEFT_SOFT, PITCH_DOWN_SOFT),
        yaw_pitch(YAW_RIGHT_SMALL, PITCH_UP_SOFT),
    ],
}

SLEEP_INTENT_KEYWORDS = (
    "去睡覺",
    "去睡觉",
    "睡覺吧",
    "睡觉吧",
    "睡一下",
    "想睡",
    "先睡",
    "休息一下",
    "休眠",
    "休息模式",
    "晚安",
    "進入睡眠模式",
    "进入睡眠模式",
    "安靜一下",
    "安静一下",
    "安靜模式",
    "安静模式",
    "不要吵我",
    "先不要聽",
    "先不要听",
    "sleep",
    "go to sleep",
    "standby",
    "quiet mode",
)

WAKE_INTENT_KEYWORDS = (
    "起床",
    "醒來",
    "醒来",
    "回來",
    "回来",
    "回來了",
    "回来了",
    "回來工作",
    "回来工作",
    "繼續工作",
    "继续工作",
    "開始工作",
    "开始工作",
    "回到正常",
    "正常模式",
    "一般模式",
    "不要睡了",
    "回來陪我",
    "回来陪我",
    "wake up",
    "come back",
    "normal",
    "back to work",
    "don't sleep",
    "do not sleep",
)

FOCUS_START_INTENT_KEYWORDS = (
    "專注",
    "专注",
    "專心",
    "专心",
    "開始工作",
    "开始工作",
    "開始專注",
    "开始专注",
    "開始專心",
    "开始专心",
    "專心工作",
    "专心工作",
    "進入工作模式",
    "进入工作模式",
    "工作模式",
    "專心模式",
    "专心模式",
    "番茄鐘",
    "番茄钟",
    "我要工作",
    "我要開始工作",
    "我要开始工作",
    "start work",
    "work mode",
    "focus mode",
    "start focus",
    "pomodoro",
)

FOCUS_STOP_INTENT_KEYWORDS = (
    "結束工作",
    "结束工作",
    "停止工作",
    "結束專心",
    "结束专心",
    "結束專注",
    "结束专注",
    "停止專心",
    "停止专心",
    "停止專注",
    "停止专注",
    "退出工作模式",
    "離開工作模式",
    "离开工作模式",
    "下班",
    "我完成了",
    "完成工作",
    "stop work",
    "end work",
    "stop focus",
    "end focus",
    "exit focus",
)
TODO_MARKER_KEYWORDS = (
    "待辦",
    "待办",
    "代辦",
    "代办",
    "todo",
    "to do",
    "to-do",
    "任務清單",
    "任务清单",
    "工作清單",
    "工作清单",
)
TODO_ADD_INTENT_KEYWORDS = (
    "新增待辦",
    "新增待办",
    "新增代辦",
    "加入待辦",
    "加入待办",
    "加一個待辦",
    "加一个待办",
    "記一個待辦",
    "记一个待办",
    "幫我記待辦",
    "帮我记待办",
    "幫我記一個待辦",
    "帮我记一个待办",
    "todo add",
    "add todo",
    "new todo",
)
TODO_LIST_INTENT_KEYWORDS = (
    "列出待辦",
    "列出待办",
    "查看待辦",
    "查看待办",
    "看待辦",
    "看待办",
    "我的待辦",
    "我的待办",
    "待辦清單",
    "待办清单",
    "還有哪些待辦",
    "还有哪些待办",
    "todo list",
    "list todo",
    "show todo",
)
TODO_DONE_INTENT_KEYWORDS = (
    "完成待辦",
    "完成待办",
    "完成代辦",
    "完成代办",
    "做完待辦",
    "做完待办",
    "勾掉待辦",
    "勾掉待办",
    "刪除待辦",
    "删除待办",
    "移除待辦",
    "移除待办",
    "todo done",
    "done todo",
    "finish todo",
)
TODO_CLEAR_COMPLETED_INTENT_KEYWORDS = (
    "清除已完成待辦",
    "清除已完成待办",
    "刪除已完成待辦",
    "删除已完成待办",
    "清理已完成待辦",
    "清理已完成待办",
    "clear completed todo",
)
TODO_CLEAR_ALL_INTENT_KEYWORDS = (
    "清空待辦",
    "清空待办",
    "清除所有待辦",
    "清除所有待办",
    "刪除所有待辦",
    "删除所有待办",
    "clear all todo",
)
CAMERA_CAPTURE_HELPER = r"""
import glob
import os
import re
import sys
import time

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2
try:
    cv2.setLogLevel(2)
except Exception:
    pass


def parse_camera_id(raw):
    if raw in ("", "auto", "None"):
        return "auto"
    try:
        return int(raw)
    except ValueError:
        return raw


def camera_candidates(raw):
    camera_id = parse_camera_id(raw)
    if camera_id != "auto":
        return [camera_id]
    result = []
    for path in glob.glob("/dev/video*"):
        match = re.search(r"\d+$", path)
        if match:
            result.append(int(match.group()))
    return sorted(result)


def open_capture(camera_id):
    if hasattr(cv2, "CAP_V4L2"):
        cap = cv2.VideoCapture(camera_id, cv2.CAP_V4L2)
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(camera_id)


def main():
    camera_id = sys.argv[1]
    width = int(sys.argv[2])
    height = int(sys.argv[3])
    max_side = int(sys.argv[4])
    quality = int(sys.argv[5])
    warmup_frames = int(sys.argv[6])

    errors = []
    for candidate in camera_candidates(camera_id):
        cap = open_capture(candidate)
        if not cap.isOpened():
            errors.append(f"{candidate}: open failed")
            cap.release()
            continue
        try:
            if hasattr(cv2, "CAP_PROP_FOURCC"):
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if hasattr(cv2, "CAP_PROP_FPS"):
                cap.set(cv2.CAP_PROP_FPS, 10)

            frame = None
            for _ in range(max(1, warmup_frames)):
                ok, maybe_frame = cap.read()
                if ok and maybe_frame is not None:
                    frame = maybe_frame
            if frame is None:
                errors.append(f"{candidate}: read failed")
                continue

            h, w = frame.shape[:2]
            largest_side = max(w, h)
            if max_side > 0 and largest_side > max_side:
                scale = max_side / float(largest_side)
                frame = cv2.resize(
                    frame,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )

            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                errors.append(f"{candidate}: encode failed")
                continue
            sys.stdout.buffer.write(encoded.tobytes())
            sys.stderr.write(f"captured camera={candidate} bytes={len(encoded)}\n")
            return 0
        finally:
            cap.release()

    sys.stderr.write("; ".join(errors) or "no camera candidates")
    return 2


raise SystemExit(main())
"""


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def short_preview(text: Any, limit: int = 100) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "..."


def read_secret_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        print(f"WARNING: could not read secret file {path}: {exc}")
        return ""


def default_discord_webhook_url() -> str:
    return (
        os.getenv("FOCUS_DISCORD_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        or read_secret_file(DEFAULT_DISCORD_WEBHOOK_FILE)
    )


def resample_float(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    if len(audio) == 0:
        return np.asarray(audio, dtype=np.float32)
    duration = len(audio) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def to_int16(audio_float: np.ndarray) -> np.ndarray:
    clipped = np.clip(audio_float, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)


def int16_volume(audio_int16: np.ndarray) -> int:
    if len(audio_int16) == 0:
        return 0
    return int(np.abs(audio_int16.astype(np.int32)).mean())


def percentile_int(values: list[int], percentile: float, *, fallback: int = 0) -> int:
    cleaned = [int(value) for value in values if int(value) >= 0]
    if not cleaned:
        return int(fallback)
    return int(np.percentile(np.asarray(cleaned, dtype=np.float32), float(percentile)))


def adaptive_recording_thresholds(args: argparse.Namespace, ambient_volumes: list[int], *, fallback_volume: int) -> tuple[int, int, int]:
    """Return ambient floor, speech-start threshold, and base silence threshold."""
    noise_floor = percentile_int(
        ambient_volumes,
        getattr(args, "noise_floor_percentile", 75.0),
        fallback=fallback_volume,
    )
    speech_start_threshold = int(getattr(args, "volume_min", 700))
    silence_base_threshold = speech_start_threshold
    if not getattr(args, "no_adaptive_volume", False):
        speech_start_ratio = max(0.0, float(getattr(args, "speech_start_ratio", 1.25) or 0.0))
        silence_noise_ratio = max(0.0, float(getattr(args, "silence_noise_ratio", 1.15) or 0.0))
        speech_start_threshold = max(
            speech_start_threshold,
            noise_floor + int(getattr(args, "speech_start_margin", 350)),
            int(round(noise_floor * speech_start_ratio)),
        )
        silence_base_threshold = max(
            int(getattr(args, "volume_min", 700)),
            noise_floor + int(getattr(args, "silence_margin", 500)),
            int(round(noise_floor * silence_noise_ratio)),
        )
    return noise_floor, speech_start_threshold, silence_base_threshold


def adaptive_wake_volume_threshold(args: argparse.Namespace, ambient_volumes: list[int], *, fallback_volume: int) -> tuple[int, int]:
    """Return ambient floor and dynamic volume needed to accept a wake score."""
    noise_floor = percentile_int(
        ambient_volumes,
        getattr(args, "noise_floor_percentile", 75.0),
        fallback=fallback_volume,
    )
    wake_volume_threshold = int(getattr(args, "wake_volume_min", 350))
    if not getattr(args, "no_adaptive_volume", False):
        wake_volume_ratio = max(0.0, float(getattr(args, "wake_volume_ratio", 1.15) or 0.0))
        wake_volume_threshold = max(
            wake_volume_threshold,
            noise_floor + int(getattr(args, "wake_volume_margin", 0)),
            int(round(noise_floor * wake_volume_ratio)),
        )
    return noise_floor, wake_volume_threshold


def adaptive_silence_threshold(args: argparse.Namespace, silence_base_threshold: int, peak_volume: int) -> int:
    if getattr(args, "no_adaptive_volume", False):
        return int(getattr(args, "volume_min", 700))
    peak_ratio = float(getattr(args, "silence_peak_ratio", 0.35))
    return max(int(silence_base_threshold), int(round(max(0, peak_volume) * peak_ratio)))


def normalize_session_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    return re.sub(r"[\s，。！？!?、,.：:；;「」『』\"'`~\-_/]+", "", lowered)


def end_session_keyword(transcript: str) -> str | None:
    cleaned = normalize_session_text(transcript)
    for label, pattern in SESSION_END_REGEXES:
        if pattern.search(cleaned):
            return label
    for keyword in SESSION_END_KEYWORDS:
        normalized_keyword = normalize_session_text(keyword)
        if normalized_keyword and normalized_keyword in cleaned:
            return keyword
    return None


def should_end_conversation_session(transcript: str) -> bool:
    return end_session_keyword(transcript) is not None


def find_device_by_keyword(keyword: str) -> int | None:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Missing dependency: sounddevice. Install it in the voice venv.") from exc

    devices = sd.query_devices()
    for index, device in enumerate(devices):
        name = str(device.get("name", ""))
        if keyword.lower() in name.lower() and int(device.get("max_input_channels", 0)) > 0:
            return index
    return None


def find_output_device_by_keyword(keyword: str) -> int | None:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Missing dependency: sounddevice. Install it in the voice venv.") from exc

    devices = sd.query_devices()
    for index, device in enumerate(devices):
        name = str(device.get("name", ""))
        if keyword.lower() in name.lower() and int(device.get("max_output_channels", 0)) > 0:
            return index
    return None


def refresh_sounddevice_backend(*, label: str = "") -> bool:
    """Refresh PortAudio's device view after USB audio re-enumerates."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Missing dependency: sounddevice. Install it in the voice venv.") from exc

    terminate = getattr(sd, "_terminate", None)
    initialize = getattr(sd, "_initialize", None)
    if not callable(terminate) or not callable(initialize):
        return False
    try:
        terminate()
        initialize()
        if label:
            print(f"Device preflight: refreshed sounddevice backend while waiting for {label}.")
        return True
    except Exception as exc:
        if label:
            print(f"WARNING: sounddevice backend refresh failed while waiting for {label}: {exc}")
        return False


def wait_for_sounddevice_keyword(keyword: str, *, output: bool, timeout_sec: float, label: str) -> int | None:
    keyword = str(keyword or "").strip()
    if not keyword:
        return None
    finder = find_output_device_by_keyword if output else find_device_by_keyword
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_report_at = 0.0
    last_refresh_at = 0.0
    refresh_label = f"{label} keyword {keyword!r}"
    while True:
        selected = finder(keyword)
        if selected is not None:
            print(f"Device ready: selected {label} device {selected} by keyword {keyword!r}.")
            return selected
        now = time.monotonic()
        if now >= deadline:
            return None
        if now - last_report_at >= 2.0:
            print(f"Device preflight: waiting for sounddevice {label} keyword {keyword!r}...")
            last_report_at = now
        if now - last_refresh_at >= 2.0:
            if refresh_sounddevice_backend(label=refresh_label):
                selected = finder(keyword)
                if selected is not None:
                    print(f"Device ready: selected {label} device {selected} by keyword {keyword!r}.")
                    return selected
            last_refresh_at = now
        time.sleep(0.5)


def wait_for_path_candidates(description: str, glob_pattern: str, *, timeout_sec: float) -> list[str]:
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_report_at = 0.0
    while True:
        matches = sorted(glob.glob(glob_pattern))
        if matches:
            print(f"Device ready: {description}: {', '.join(matches)}")
            return matches
        now = time.monotonic()
        if now >= deadline:
            return []
        if now - last_report_at >= 2.0:
            print(f"Device preflight: waiting for {description} ({glob_pattern})...")
            last_report_at = now
        time.sleep(0.5)


def wait_for_uart_candidates(*, timeout_sec: float) -> list[str]:
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_report_at = 0.0
    patterns = ", ".join(UART_DEVICE_GLOB_PATTERNS)
    while True:
        matches = [str(path) for path in discover_demo_uart_paths()]
        if matches:
            print(f"Device ready: FRDM UART nodes: {', '.join(matches)}")
            return matches
        now = time.monotonic()
        if now >= deadline:
            return []
        if now - last_report_at >= 2.0:
            print(f"Device preflight: waiting for FRDM UART nodes ({patterns})...")
            last_report_at = now
        time.sleep(0.5)


def output_device_info(device_index: int | None) -> dict[str, Any] | None:
    if device_index is None:
        return None
    try:
        import sounddevice as sd

        info = sd.query_devices(device_index, "output")
    except Exception:
        return None
    return info if isinstance(info, dict) else None


def beep_player_requires_sounddevice_output(args: argparse.Namespace) -> bool:
    return str(getattr(args, "beep_player", "auto") or "auto").strip().lower() == "sounddevice"


def list_sounddevice_inputs() -> None:
    import sounddevice as sd

    print("sounddevice input devices:")
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        print(
            f"[{index:2d}] inputs={device.get('max_input_channels')} "
            f"default_sr={device.get('default_samplerate')} name={device.get('name')}"
        )


def list_sounddevice_outputs() -> None:
    import sounddevice as sd

    print("sounddevice output devices:")
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_output_channels", 0)) <= 0:
            continue
        print(
            f"[{index:2d}] outputs={device.get('max_output_channels')} "
            f"default_sr={device.get('default_samplerate')} name={device.get('name')}"
        )


class TimingLogger:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.previous = self.started

    def mark(self, label: str) -> None:
        now = time.perf_counter()
        print(
            f"[timing] {label}: +{int((now - self.previous) * 1000)} ms "
            f"(total {int((now - self.started) * 1000)} ms)"
        )
        self.previous = now


class InstanceLock:
    def __init__(self, path: str, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.handle: Any | None = None

    def acquire(self) -> bool:
        if not self.enabled:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            old_pid = handle.read().strip() or "unknown"
            print()
            print(f"ERROR: another wake bridge is already running (pid={old_pid}).")
            print("Stop it with:")
            print("  pkill -9 -f wake_voice_chat_frdm_bridge.py")
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self.handle = handle
        return True

    def release(self) -> None:
        handle = self.handle
        self.handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass


def process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    text = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    if text:
        return text
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def iter_process_cmdlines() -> list[tuple[int, str]]:
    processes: list[tuple[int, str]] = []
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        pid = int(item.name)
        if pid == os.getpid():
            continue
        cmdline = process_cmdline(pid)
        if cmdline:
            processes.append((pid, cmdline))
    return processes


def command_matches(cmdline: str, patterns: tuple[str, ...] | list[str]) -> bool:
    lowered = cmdline.lower()
    return any(pattern.lower() in lowered for pattern in patterns if pattern)


def command_matches_stale_demo_process(cmdline: str, patterns: tuple[str, ...] | list[str]) -> bool:
    lowered = cmdline.lower()
    try:
        tokens = shlex.split(cmdline)
    except ValueError:
        tokens = cmdline.split()
    executable_name = Path(tokens[0]).name.lower() if tokens else ""
    for pattern in patterns:
        normalized = str(pattern or "").strip().lower()
        if not normalized:
            continue
        if normalized in AUDIO_STALE_PROCESS_NAMES:
            if normalized == executable_name:
                return True
            continue
        if normalized in lowered:
            return True
    return False


def is_protected_process(cmdline: str, args: argparse.Namespace) -> bool:
    if getattr(args, "kill_audio_servers", False):
        protected = tuple(pattern for pattern in DEVICE_OWNER_ALLOW_PATTERNS if pattern not in {"pulseaudio", "pipewire", "wireplumber"})
    else:
        protected = DEVICE_OWNER_ALLOW_PATTERNS
    return command_matches(cmdline, protected)


def terminate_pids(pid_reasons: dict[int, str], *, dry_run: bool, grace_sec: float = 0.8) -> None:
    if not pid_reasons:
        return
    for pid, reason in sorted(pid_reasons.items()):
        cmdline = process_cmdline(pid)
        if dry_run:
            print(f"Device preflight dry-run: would stop pid={pid} ({reason}) cmd={cmdline}")
            continue
        try:
            print(f"Device preflight: stopping pid={pid} ({reason}) cmd={cmdline}")
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            print(f"WARNING: cannot stop pid={pid} ({reason}): {exc}")

    if dry_run:
        return

    deadline = time.monotonic() + max(0.1, grace_sec)
    while time.monotonic() < deadline:
        alive = [pid for pid in pid_reasons if Path(f"/proc/{pid}").exists()]
        if not alive:
            return
        time.sleep(0.05)

    for pid, reason in sorted(pid_reasons.items()):
        if not Path(f"/proc/{pid}").exists():
            continue
        try:
            print(f"Device preflight: force-killing pid={pid} ({reason})")
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            print(f"WARNING: cannot force-kill pid={pid} ({reason}): {exc}")


def uacdemo_audio_device_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        cards_text = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return paths
    cards: list[str] = []
    for line in cards_text.splitlines():
        match = re.match(r"\s*(\d+)\s+\[[^\]]+\]\s*:\s*.*UACDemo", line)
        if match:
            cards.append(match.group(1))
    for card in cards:
        for path in glob.glob(f"/dev/snd/pcmC{card}D*"):
            paths.append(Path(path))
    return paths


def uart_hardware_enabled(args: argparse.Namespace) -> bool:
    return not bool(getattr(args, "no_uart", False)) and not bool(getattr(args, "uart_dry_run", False))


def auto_uart_requested(args: argparse.Namespace) -> bool:
    return str(getattr(args, "uart_port", "auto") or "auto").strip().lower() == "auto"


def discover_demo_uart_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    for port in bridge.discover_uart_ports():
        device = str(port.get("device", "") or "")
        if not device.startswith("/dev/"):
            continue
        path = Path(device)
        if not path.exists():
            continue
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = device
        if resolved in seen:
            continue
        paths.append(path)
        seen.add(resolved)

    for pattern in UART_DEVICE_GLOB_PATTERNS:
        for device in sorted(glob.glob(pattern)):
            path = Path(device)
            if not path.exists():
                continue
            try:
                resolved = str(path.resolve())
            except OSError:
                resolved = device
            if resolved in seen:
                continue
            paths.append(path)
            seen.add(resolved)

    return sorted(paths, key=lambda path: str(path))


def demo_device_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if not getattr(args, "no_camera", False) and not getattr(args, "no_vision", False):
        paths.extend(Path(path) for path in glob.glob("/dev/video*"))
    if uart_hardware_enabled(args) and auto_uart_requested(args):
        paths.extend(discover_demo_uart_paths())
    else:
        uart_path = Path(str(args.uart_port))
        if uart_hardware_enabled(args) and str(uart_path).startswith("/dev/"):
            paths.append(uart_path)
    paths.extend(uacdemo_audio_device_paths())
    existing: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved not in seen and path.exists():
            existing.append(path)
            seen.add(resolved)
    return existing


def missing_demo_devices(args: argparse.Namespace) -> list[str]:
    missing: list[str] = []
    try:
        asound_cards = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
    except OSError:
        asound_cards = ""
    if str(getattr(args, "mic_keyword", "") or "").strip().lower() == "uacdemo" and "UACDemo" not in asound_cards:
        missing.append("UACDemo audio")
    if not getattr(args, "no_camera", False) and not getattr(args, "no_vision", False) and not glob.glob("/dev/video*"):
        missing.append("camera /dev/video*")
    if (
        uart_hardware_enabled(args)
        and auto_uart_requested(args)
        and not discover_demo_uart_paths()
    ):
        missing.append(UART_DEVICE_DESCRIPTION)
    return missing


def reset_usb_host_if_missing(args: argparse.Namespace) -> None:
    missing = missing_demo_devices(args)
    if not missing:
        return
    if getattr(args, "no_usb_reset_if_missing", False):
        print(f"WARNING: missing demo devices ({', '.join(missing)}), but USB reset is disabled.")
        return
    controller = str(getattr(args, "usb_controller", "3610000.usb") or "3610000.usb")
    command = (
        f"echo {controller} > /sys/bus/platform/drivers/tegra-xusb/unbind; "
        "sleep 2; "
        f"echo {controller} > /sys/bus/platform/drivers/tegra-xusb/bind"
    )
    if getattr(args, "device_preflight_dry_run", False):
        print(f"Device preflight dry-run: would reset USB host because missing: {', '.join(missing)}")
        print(f"  sudo sh -c {command!r}")
        return

    print(f"Device preflight: missing {', '.join(missing)}; resetting Jetson USB host {controller}.")
    print("Device preflight: sudo may ask for your password in this terminal.")
    try:
        result = subprocess.run(["sudo", "sh", "-c", command], check=False, timeout=20)
    except subprocess.TimeoutExpired:
        print("WARNING: USB host reset timed out.")
        return
    except Exception as exc:
        print(f"WARNING: USB host reset failed: {exc}")
        return
    if result.returncode != 0:
        print(f"WARNING: USB host reset returned exit code {result.returncode}.")
        return
    wait_sec = max(0.0, float(getattr(args, "usb_reset_wait", 6.0)))
    print(f"Device preflight: waiting {wait_sec:.1f}s for USB devices to re-enumerate.")
    time.sleep(wait_sec)
    still_missing = missing_demo_devices(args)
    if still_missing:
        print(f"WARNING: still missing after USB reset: {', '.join(still_missing)}")
    else:
        print("Device preflight: USB devices re-enumerated.")


def collect_device_owner_pids(paths: list[Path], args: argparse.Namespace) -> dict[int, str]:
    resolved_paths: dict[str, str] = {}
    for path in paths:
        try:
            resolved_paths[str(path.resolve())] = str(path)
        except OSError:
            pass
    if not resolved_paths:
        return {}

    owners: dict[int, str] = {}
    for pid, cmdline in iter_process_cmdlines():
        if is_protected_process(cmdline, args):
            continue
        fd_dir = Path(f"/proc/{pid}/fd")
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        matched: list[str] = []
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if not target.startswith("/dev/"):
                continue
            target = target.removesuffix(" (deleted)")
            try:
                resolved = str(Path(target).resolve())
            except OSError:
                resolved = target
            if resolved in resolved_paths:
                matched.append(resolved_paths[resolved])
        if matched:
            owners[pid] = "owns " + ", ".join(sorted(set(matched)))
    return owners


def kill_stale_demo_processes(args: argparse.Namespace) -> None:
    patterns = DEMO_STALE_PROCESS_PATTERNS
    if getattr(args, "device_preflight_keep_music", False):
        patterns = tuple(pattern for pattern in patterns if pattern not in {"mpv", "ffplay"})
    pid_reasons: dict[int, str] = {}
    for pid, cmdline in iter_process_cmdlines():
        if is_protected_process(cmdline, args):
            continue
        if command_matches(cmdline, DEMO_PREFLIGHT_SKIP_ARGS):
            continue
        if command_matches_stale_demo_process(cmdline, patterns):
            pid_reasons[pid] = "stale demo/audio process"
    terminate_pids(pid_reasons, dry_run=args.device_preflight_dry_run, grace_sec=args.device_preflight_grace)


def wait_for_demo_devices_ready(args: argparse.Namespace) -> bool:
    if getattr(args, "device_preflight_dry_run", False):
        return True
    timeout_sec = float(getattr(args, "device_ready_timeout", 12.0) or 0.0)
    if timeout_sec <= 0:
        return True

    if not getattr(args, "no_camera", False) and not getattr(args, "no_vision", False):
        wait_for_path_candidates("camera nodes", "/dev/video*", timeout_sec=timeout_sec)
    if uart_hardware_enabled(args) and auto_uart_requested(args):
        uart_matches = wait_for_uart_candidates(timeout_sec=timeout_sec)
        setattr(args, "_frdm_uart_available_after_preflight", bool(uart_matches))
        if not uart_matches:
            message = (
                f"FRDM UART did not appear after {timeout_sec:g}s. "
                "Plug/replug the FRDM debug USB cable and run "
                "`python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts`."
            )
            if getattr(args, "require_uart", False):
                print(f"ERROR: {message}")
                return False
            print(f"WARNING: {message}")
            print("WARNING: FRDM UART will stay in auto-recovery mode; plug it in and this run will resume UART without restart.")
            setattr(args, "_frdm_uart_startup_missing", True)

    manual_input = bool(getattr(args, "_manual_input_device", getattr(args, "device", None) is not None))
    if not manual_input:
        wait_for_sounddevice_keyword(
            str(getattr(args, "mic_keyword", "") or ""),
            output=False,
            timeout_sec=timeout_sec,
            label="input",
        )

    manual_beep = bool(getattr(args, "_manual_beep_device", getattr(args, "beep_device", None) is not None))
    beep_keyword = str(getattr(args, "beep_keyword", "") or "")
    if not getattr(args, "no_beep", False) and not manual_beep and beep_player_requires_sounddevice_output(args):
        wait_for_sounddevice_keyword(
            beep_keyword,
            output=True,
            timeout_sec=timeout_sec,
            label="output",
        )
    elif (
        not getattr(args, "no_beep", False)
        and not manual_beep
        and beep_keyword.strip()
        and getattr(args, "device_preflight_verbose", False)
    ):
        beep_player = str(getattr(args, "beep_player", "auto") or "auto").strip().lower()
        print(
            "Device preflight: skipping sounddevice output keyword wait "
            f"for beep player {beep_player!r}."
        )
    return True


def device_preflight(args: argparse.Namespace) -> bool:
    if getattr(args, "no_device_preflight", False):
        print("Device preflight skipped by --no-device-preflight.")
        return True
    print("Device preflight: releasing stale demo device owners.")
    kill_stale_demo_processes(args)
    reset_usb_host_if_missing(args)
    paths = demo_device_paths(args)
    if getattr(args, "device_preflight_verbose", False):
        if paths:
            print("Device preflight: target device nodes:")
            for path in paths:
                print(f"  {path}")
        else:
            print("Device preflight: no current target device nodes found yet.")
    owners = collect_device_owner_pids(paths, args)
    terminate_pids(owners, dry_run=args.device_preflight_dry_run, grace_sec=args.device_preflight_grace)
    if not owners and not getattr(args, "device_preflight_dry_run", False):
        print("Device preflight: target devices look free.")
    time.sleep(max(0.0, float(getattr(args, "device_preflight_settle", 0.8))))
    return wait_for_demo_devices_ready(args)


def clamp_int(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def normalize_intent_text(text: str) -> str:
    return re.sub(r"[\s，。！？!?、,.：:；;「」『』\"'`]+", "", str(text or "").lower())


def contains_intent(text: str, keywords: tuple[str, ...]) -> bool:
    compact = normalize_intent_text(text)
    lowered = str(text or "").lower()
    return any(normalize_intent_text(keyword) in compact or keyword.lower() in lowered for keyword in keywords)


def detect_persistent_state_intent(transcript: str) -> str | None:
    if contains_intent(transcript, WAKE_INTENT_KEYWORDS):
        return "normal"
    if contains_intent(transcript, SLEEP_INTENT_KEYWORDS):
        return "sleep"
    return None


def detect_focus_mode_intent(transcript: str) -> str | None:
    if contains_intent(transcript, FOCUS_STOP_INTENT_KEYWORDS):
        return "stop"
    if contains_intent(transcript, FOCUS_START_INTENT_KEYWORDS):
        return "start"
    return None


def should_end_conversation_after_focus_turn(
    args: argparse.Namespace,
    *,
    focus_intent: str | None,
    focus_was_running: bool,
    focus_is_running: bool,
) -> bool:
    if not getattr(args, "conversation_mode", False):
        return False
    return focus_intent is not None or focus_was_running or focus_is_running


def pet_idle_reflection_enabled(args: argparse.Namespace) -> bool:
    return not bool(getattr(args, "no_pet_idle_reflection", False))


def pet_idle_next_delay(args: argparse.Namespace) -> float:
    interval = max(1.0, float(getattr(args, "pet_idle_interval_sec", 30.0) or 30.0))
    jitter = max(0.0, float(getattr(args, "pet_idle_jitter_sec", 0.0) or 0.0))
    jitter = min(jitter, interval * 0.8)
    return max(1.0, interval + random.uniform(-jitter, jitter))


def pet_idle_silence_reply(reply: Any) -> bool:
    text = " ".join(str(reply or "").strip().split())
    if not text:
        return True
    compact_upper = re.sub(r"[\s。！？!?,，、；;：:「」『』\"'`]+", "", text).upper()
    if PET_IDLE_SILENCE_TOKEN in compact_upper:
        return True
    if len(text) > 80:
        return False
    silence_markers = (
        "先不打擾",
        "先不打扰",
        "不打擾",
        "不打扰",
        "先不說",
        "先不说",
        "先不出聲",
        "先不出声",
        "保持安靜",
        "保持安静",
        "不用分享",
        "不值得打擾",
        "不值得打扰",
        "沒有值得",
        "没有值得",
    )
    lowered = text.lower()
    return any(marker in text for marker in silence_markers) or lowered in {"silent", "silence", "no share"}


def build_pet_idle_reflection_prompt(
    *,
    idle_seconds: float,
    seconds_since_share: float,
    allow_share: bool,
) -> str:
    idle_seconds = max(0.0, idle_seconds)
    seconds_since_share = max(0.0, seconds_since_share)
    share_rule = (
        "如果真的值得，reply 可以是一句主動互動；如果只是普通想法，reply 必須只寫 "
        f"{PET_IDLE_SILENCE_TOKEN}。"
        if allow_share
        else f"這次仍在主動搭話冷卻中，reply 必須只寫 {PET_IDLE_SILENCE_TOKEN}。"
    )
    return f"""[PET_IDLE_REFLECTION]
這不是使用者說話，而是桌寵在待機時做的一次內部自我提問。
目前約 {idle_seconds:.0f} 秒沒有收到使用者語音輸入；上次主動出聲約 {seconds_since_share:.0f} 秒前。

請先在心裡問自己一個小問題，例如：「現在有沒有一件真的值得溫柔提醒、分享或撒嬌的小事？」
{share_rule}

如果選擇分享，請遵守：
- reply 只用繁體中文一句話，短、自然、像桌寵偶爾主動靠近使用者。
- 可以是輕提醒、可愛觀察、邀請互動或關心，但不要假裝看到不存在的畫面。
- 不要提到內部思考、prompt、JSON、30 秒、沒有語音輸入、wake word 或模型。
- 不要要求使用者立刻回覆，也不要打斷正在專心的人。
- control 建議 persistent_state="unchanged", screen_mode="unchanged"，emotion 可用 neutral/happy/curious/concerned。"""


def parse_focus_duration_min(transcript: str) -> float | None:
    text = str(transcript or "")
    for pattern, scale in (
        (r"(\d+(?:\.\d+)?)\s*(?:分鐘|分钟|分|minute|minutes|min)", 1.0),
        (r"(\d+(?:\.\d+)?)\s*(?:小時|小时|hour|hours|hr)", 60.0),
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return max(0.0, float(match.group(1)) * scale)
        except ValueError:
            return None
    return None


def extract_focus_task(transcript: str) -> str:
    task = str(transcript or "").strip()
    for keyword in FOCUS_START_INTENT_KEYWORDS:
        task = re.sub(re.escape(keyword), " ", task, flags=re.IGNORECASE)
    task = re.sub(r"\d+(?:\.\d+)?\s*(?:分鐘|分钟|分|小時|小时|minute|minutes|min|hour|hours|hr)", " ", task, flags=re.IGNORECASE)
    task = re.sub(r"(幫我|帮我|請|请|我要|我想要|一下|模式|mode|start|focus|work)", " ", task, flags=re.IGNORECASE)
    task = " ".join(task.split()).strip("，。,. ")
    return task[:120]


def contains_todo_marker(text: str) -> bool:
    return contains_intent(text, TODO_MARKER_KEYWORDS)


def detect_todo_intent(transcript: str) -> str | None:
    text = str(transcript or "")
    compact = normalize_intent_text(text)
    lowered = text.lower()

    if contains_intent(text, TODO_CLEAR_ALL_INTENT_KEYWORDS):
        return "clear_all"
    if contains_intent(text, TODO_CLEAR_COMPLETED_INTENT_KEYWORDS):
        return "clear_completed"
    if contains_intent(text, TODO_LIST_INTENT_KEYWORDS):
        return "list"
    if contains_intent(text, TODO_DONE_INTENT_KEYWORDS):
        return "done"
    if contains_intent(text, TODO_ADD_INTENT_KEYWORDS):
        return "add"

    marker = contains_todo_marker(text)
    if marker and any(word in compact for word in ("有哪些", "還有什麼", "还有什么", "列出", "查看", "看看", "清單", "清单")):
        return "list"
    if marker and any(word in compact for word in ("完成", "做完", "勾掉", "刪除", "删除", "移除")):
        return "done"
    if marker and any(word in compact for word in ("新增", "增加", "加入", "添加", "記", "记", "建立")):
        return "add"
    if re.search(r"\b(todo|to-do)\s+(add|new|create)\b", lowered):
        return "add"
    if re.search(r"\b(todo|to-do)\s+(done|finish|complete|remove|delete)\b", lowered):
        return "done"
    if re.search(r"\b(todo|to-do)\s+(list|show)\b", lowered):
        return "list"
    return None


def clean_todo_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^[：:，,。.\s]+", "", cleaned)
    cleaned = re.sub(r"[。.!！?？\s]+$", "", cleaned)
    cleaned = re.sub(r"^(幫我|帮我|請|请|麻煩|麻烦|我要|我想要)\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:160]


def extract_todo_add_text(transcript: str) -> str:
    text = str(transcript or "").strip()
    patterns = (
        r"^(?:幫我|帮我|請|请|麻煩|麻烦)?\s*"
        r"(?:新增|增加|加入|添加|建立|記下|记下|記住|记住|記|记|幫我記|帮我记|提醒我)"
        r"(?:一個|一个|一項|一项|新的)?"
        r"(?:待辦|待办|代辦|代办|todo|to do|to-do|任務|任务|事項|事项)?"
        r"(?:清單|清单)?[：:\s,，]*(?P<item>.+)$",
        r"^(?:待辦|待办|代辦|代办|todo|to do|to-do)\s*(?:新增|增加|加入|add|new|create)?[：:\s,，]*(?P<item>.+)$",
        r"^(?:把|將|将)?\s*(?P<item>.+?)(?:加入|新增到|加到|放進|放进|放到)(?:我的)?(?:待辦|待办|代辦|代办|todo|清單|清单)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        item = clean_todo_text(match.group("item"))
        if item and not contains_intent(item, TODO_MARKER_KEYWORDS):
            return item
        if item and len(normalize_intent_text(item)) >= 2:
            return item
    return ""


CHINESE_NUMBER_VALUES = {
    "零": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def parse_chinese_number_token(token: str) -> int | None:
    raw = str(token or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        number = int(raw)
        return number if number > 0 else None
    if raw in CHINESE_NUMBER_VALUES:
        number = CHINESE_NUMBER_VALUES[raw]
        return number if number > 0 else None
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = CHINESE_NUMBER_VALUES.get(left, 1 if left == "" else 0)
        ones = CHINESE_NUMBER_VALUES.get(right, 0 if right == "" else -1)
        if tens <= 0 or ones < 0:
            return None
        number = tens * 10 + ones
        return number if number > 0 else None
    return None


def extract_todo_done_number(transcript: str) -> int | None:
    text = str(transcript or "")
    patterns = (
        r"(?:完成|做完|勾掉|刪除|删除|移除|done|finish|complete|remove|delete)"
        r"(?:\s*(?:待辦|待办|代辦|代办|todo|to-do))?\s*(?:第)?(?P<num>\d+)\s*(?:項|项|個|个|件|筆|笔|號|号)?",
        r"(?:第)(?P<num>[一二兩两三四五六七八九十]+)\s*(?:項|项|個|个|件|筆|笔)",
        r"(?:完成|做完|勾掉|刪除|删除|移除)\s*(?:第)?(?P<num>[一二兩两三四五六七八九十]+)\s*(?:項|项|個|个|件|筆|笔)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        number = parse_chinese_number_token(match.group("num"))
        if number is not None:
            return number
    return None


def extract_todo_done_text(transcript: str) -> str:
    text = str(transcript or "")
    text = re.sub(
        r"(?:完成|做完|勾掉|刪除|删除|移除|done|finish|complete|remove|delete)"
        r"(?:\s*(?:待辦|待办|代辦|代办|todo|to-do))?",
        " ",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?:第)?\d+\s*(?:項|项|個|个|件|筆|笔|號|号)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:第)?[一二兩两三四五六七八九十]+\s*(?:項|项|個|个|件|筆|笔)?", " ", text)
    for keyword in TODO_MARKER_KEYWORDS:
        text = re.sub(re.escape(keyword), " ", text, flags=re.IGNORECASE)
    return clean_todo_text(text)


def todo_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_wake_status(args: argparse.Namespace, **fields: Any) -> None:
    if getattr(args, "no_wake_status_log", False):
        return
    path = Path(getattr(args, "wake_status_path", "") or DEFAULT_WAKE_STATUS_PATH).expanduser()
    record = {
        "updated_at": todo_timestamp(),
        "listening": True,
        **fields,
    }
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        if getattr(args, "debug", False):
            print(f"WARN: failed to write wake status {path}: {exc}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


def emotion_from_transcript_keywords(transcript: str) -> str:
    text = str(transcript or "").lower()
    if any(word in text for word in ("操你媽", "操你妈", "幹你娘", "干你娘", "媽的", "妈的", "靠北", "靠邀", "fuck", "shit")):
        return "concerned"
    if any(word in text for word in ("生氣", "生气", "很氣", "很气", "氣死", "气死", "火大", "憤怒", "愤怒", "不爽")):
        return "concerned"
    if any(word in text for word in ("難過", "难过", "傷心", "伤心", "沮喪", "沮丧", "失落")):
        return "concerned"
    if any(word in text for word in ("擔心", "担心", "焦慮", "焦虑", "怕", "緊張", "紧张")):
        return "concerned"
    if any(word in text for word in ("開心", "开心", "太好了", "讚", "赞", "棒")):
        return "happy"
    if any(word in text for word in ("看不懂", "不懂", "怪怪", "奇怪", "搞不懂")):
        return "confused"
    return "neutral"


def direct_head_motion_from_transcript(transcript: str) -> str | None:
    text = str(transcript or "").lower()
    compact = re.sub(r"[\s，。！？!?、,.：:；;「」『』\"'`]+", "", text)
    if any(word in compact for word in ("點頭", "点头", "點個頭", "点个头")) or re.search(r"\bnod\b|\bnod your head\b", text):
        return "nod"
    if any(word in compact for word in ("搖頭", "摇头", "不要的動作", "不要的动作")) or re.search(r"\bshake your head\b", text):
        return "shake"
    if any(word in compact for word in ("歪頭", "歪头", "困惑動作", "困惑动作")) or re.search(r"\btilt your head\b", text):
        return "confused_tilt"
    if any(word in compact for word in ("賣萌", "卖萌", "可愛一點", "可爱一点", "開心動作", "开心动作")):
        return "happy_bounce"
    if any(word in compact for word in ("左右看", "看左右", "轉頭", "转头", "四處看", "四处看")) or re.search(r"\blook around\b", text):
        return "curious_peek"
    return None


def local_control_from_transcript(transcript: str, response: dict[str, Any] | None = None) -> dict[str, str]:
    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        return {
            "persistent_state": "sleep",
            "screen_mode": "sleep",
            "emotion": "sleepy",
            "head_motion": "sleepy_drop",
            "reason": "sleep intent",
        }
    if state_intent == "normal":
        return {
            "persistent_state": "normal",
            "screen_mode": "normal",
            "emotion": "happy",
            "head_motion": "happy_bounce",
            "reason": "wake/normal intent",
        }

    keyword_emotion = emotion_from_transcript_keywords(transcript)
    emotion = keyword_emotion
    if response is not None:
        raw_emotion = response.get("emotion")
        if isinstance(raw_emotion, dict):
            emotion = normalize_emotion_name(raw_emotion.get("primary", emotion), default=emotion)
        elif isinstance(raw_emotion, str):
            emotion = normalize_emotion_name(raw_emotion, default=emotion)
    emotion = normalize_emotion_name(emotion, default="neutral")
    return {
        "persistent_state": "unchanged",
        "screen_mode": "unchanged",
        "emotion": emotion,
        "head_motion": direct_head_motion_from_transcript(transcript) or EMOTION_TO_HEAD_MOTION.get(emotion, "none"),
        "reason": "local fallback",
    }


def head_motion_for_emotion(emotion: str, requested_head_motion: str = "") -> str:
    normalized_emotion = normalize_emotion_name(emotion, default="neutral")
    requested = str(requested_head_motion or "").strip().lower()
    if requested in VALID_HEAD_MOTIONS and requested != "none":
        return requested
    return EMOTION_TO_HEAD_MOTION.get(normalized_emotion, "none")


def speaking_code_for_emotion(emotion: str) -> int:
    normalized_emotion = normalize_emotion_name(emotion, default="neutral")
    return EMOTION_TO_SPEAKING_CODE.get(normalized_emotion, 0)


def normalize_emotion_name(value: Any, *, default: str = "neutral") -> str:
    raw = str(value or "").strip().lower()
    normalized = EMOTION_ALIASES.get(raw, raw)
    if normalized in VALID_EMOTIONS:
        return normalized
    return default if default in VALID_EMOTIONS else "neutral"


def normalize_control(response: dict[str, Any]) -> dict[str, str]:
    transcript = str(response.get("transcript", "")).strip()
    fallback = local_control_from_transcript(transcript, response)

    raw_control = response.get("control")
    if not isinstance(raw_control, dict):
        raw_uart = response.get("uart")
        raw_control = raw_uart if isinstance(raw_uart, dict) else None

    # Backstop for server/debug paths that accidentally return the structured
    # object in reply. This keeps TTS from reading JSON aloud.
    reply_text = str(response.get("reply", "")).strip()
    if raw_control is None and reply_text:
        parsed = extract_json_object(reply_text)
        if parsed is not None:
            raw_control = parsed.get("control") if isinstance(parsed.get("control"), dict) else parsed.get("uart")
            parsed_reply = str(parsed.get("reply", "")).strip()
            if parsed_reply:
                response["reply"] = parsed_reply
                print("JSON parse fallback: extracted reply/control from response reply field.")

    source = raw_control if isinstance(raw_control, dict) else {}
    persistent_state = str(source.get("persistent_state", fallback["persistent_state"])).strip().lower()
    if persistent_state not in VALID_PERSISTENT_STATES:
        persistent_state = fallback["persistent_state"]

    screen_mode = str(source.get("screen_mode", fallback["screen_mode"])).strip().lower()
    if screen_mode not in VALID_SCREEN_MODES:
        screen_mode = fallback["screen_mode"]

    emotion = normalize_emotion_name(source.get("emotion", fallback["emotion"]), default=fallback["emotion"])
    direct_head_motion = direct_head_motion_from_transcript(transcript)
    head_motion = direct_head_motion or head_motion_for_emotion(emotion, str(source.get("head_motion", "") or ""))

    reason = str(source.get("reason", fallback["reason"])).strip() or fallback["reason"]
    if direct_head_motion:
        reason = f"direct head motion intent: {direct_head_motion}"

    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        persistent_state = "sleep"
        screen_mode = "sleep"
        emotion = "sleepy"
        head_motion = "sleepy_drop"
        reason = "sleep intent"
    elif state_intent == "normal":
        persistent_state = "normal"
        screen_mode = "normal"
        if emotion in {"sleepy", "concerned", "confused"}:
            emotion = "happy"
        if head_motion in {"sleepy_drop", "shake"}:
            head_motion = "happy_bounce"
        reason = "wake/normal intent"
    elif persistent_state in {"normal", "sleep"} and screen_mode == "unchanged":
        screen_mode = persistent_state

    return {
        "persistent_state": persistent_state,
        "screen_mode": screen_mode,
        "emotion": emotion,
        "head_motion": head_motion,
        "reason": reason,
    }


def sanitize_reply(response: dict[str, Any]) -> str:
    reply = str(response.get("reply", "")).strip()
    parsed = extract_json_object(reply)
    if parsed is not None:
        if "control" not in response and isinstance(parsed.get("control"), dict):
            response["control"] = parsed["control"]
        elif "control" not in response and isinstance(parsed.get("uart"), dict):
            response["control"] = parsed["uart"]
        parsed_reply = str(parsed.get("reply", "")).strip()
        if parsed_reply:
            print("JSON parse fallback: reply contained JSON; using parsed reply only.")
            response["reply"] = parsed_reply
            return parsed_reply
        print("JSON parse fallback: reply looked internal; using safe fallback reply.")
        reply = ""

    lowered = reply.lower()
    internal_markers = (
        "persistent_state",
        "screen_mode",
        "head_motion",
        "motorpitch",
        "motoryaw",
        '"control"',
        '"reply"',
        "uart",
        "json",
        "內部理由",
        "内部理由",
        "控制欄位",
        "控制字段",
        "emotion 是",
        "emotion is",
        "head motion",
        "persistent state",
    )
    if any(marker in lowered for marker in internal_markers):
        print("JSON parse fallback: stripped internal control text from reply.")
        reply = ""

    if not reply:
        reply = "我剛剛有收到，但這次回覆有點不穩，我先保持待命。"
        response["reply"] = reply
    return reply


def is_user_visible_transcript(transcript: str) -> bool:
    text = str(transcript or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered == "pet_idle_reflection" or text.startswith("[PET_IDLE_REFLECTION]"):
        return False
    return True


def append_ai_trace(
    response: dict[str, Any],
    args: argparse.Namespace,
    *,
    turn_source: str = "",
    recording_meta: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if getattr(args, "no_ai_trace_log", False):
        return
    transcript = str(response.get("transcript", "") or "").strip()
    if not is_user_visible_transcript(transcript):
        return
    path = Path(getattr(args, "ai_trace_path", "") or DEFAULT_AI_TRACE_PATH).expanduser()
    debug = response.get("debug") if isinstance(response.get("debug"), dict) else {}
    control = normalize_control(response)
    record = {
        "timestamp": todo_timestamp(),
        "request_id": str(response.get("request_id") or debug.get("request_id") or ""),
        "turn_source": turn_source,
        "model": str(debug.get("ollama_model") or response.get("vision_model") or ""),
        "input": transcript,
        "output": sanitize_reply(response),
        "raw_output": str(debug.get("ollama_content_preview") or ""),
        "parse_status": str(debug.get("parse_status") or ""),
        "emotion": control.get("emotion", ""),
        "screen_mode": control.get("screen_mode", ""),
        "head_motion": control.get("head_motion", ""),
        "ok": bool(debug.get("ok", response.get("ok", True))),
    }
    if isinstance(recording_meta, dict):
        for key in (
            "wake_score",
            "peak_volume",
            "noise_floor",
            "speech_start_threshold",
            "silence_base_threshold",
            "duration_sec",
            "reason",
        ):
            if key in recording_meta:
                record[key] = recording_meta.get(key)
    if isinstance(metadata, dict):
        record["metadata"] = {
            key: metadata.get(key)
            for key in ("turn_source", "conversation_mode", "conversation_session_id", "conversation_turn_index", "latency_profile")
            if key in metadata
        }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        if getattr(args, "debug", False):
            print(f"WARN: failed to append AI trace {path}: {exc}")


def normalize_local_tool_url(raw_url: str, *, default_url: str, endpoint: str) -> str:
    value = str(raw_url or default_url).strip() or default_url
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme:
        value = "http://" + value
        parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/") or endpoint
    if path == "":
        path = endpoint
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_music_url(raw_url: str) -> str:
    value = normalize_local_tool_url(raw_url, default_url=DEFAULT_MUSIC_TOOL_URL, endpoint="/music")
    parsed = urllib.parse.urlsplit(value)
    if parsed.path.rstrip("/") in {"", "/", "/weather"}:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/music", "", ""))
    return value


def normalize_weather_url(raw_url: str) -> str:
    value = normalize_local_tool_url(raw_url, default_url=DEFAULT_WEATHER_TOOL_URL, endpoint="/weather")
    parsed = urllib.parse.urlsplit(value)
    if parsed.path.rstrip("/") in {"", "/", "/music"}:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/weather", "", ""))
    return value


def local_tool_health_url(tool_url: str) -> str:
    parsed = urllib.parse.urlsplit(tool_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def fallback_detect_music_intent(text: str) -> dict[str, Any]:
    normalized = re.sub(r"[，。！？、；：,.!?;:()\[\]{}\"'`《》〈〉「」『』]+", " ", str(text or ""))
    normalized = re.sub(r"\bhey\s+jarvis\b|\bjarvis\b|嘿\s*jarvis|嗨\s*jarvis|賈維斯|贾维斯", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    lowered = normalized.lower()
    if not normalized:
        return {"intent": False, "action": "none", "query": "", "reason": "empty", "normalized_text": ""}
    if re.search(r"停止|不要播|別播|别播|停歌|\bstop\b", lowered, flags=re.IGNORECASE):
        return {"intent": True, "action": "stop", "query": "", "reason": "fallback_stop", "normalized_text": normalized}
    if re.search(r"暫停|暂停|\bpause\b", lowered, flags=re.IGNORECASE):
        return {"intent": True, "action": "pause", "query": "", "reason": "fallback_pause", "normalized_text": normalized}
    if re.search(r"(繼續|继续|接著|接着|恢復|恢复).{0,4}(播放|播|放|音樂|音乐|歌曲|歌)|\b(resume|unpause)\b|\bcontinue\s+(the\s+)?(music|song|audio|playback)\b", lowered, flags=re.IGNORECASE):
        return {"intent": True, "action": "resume", "query": "", "reason": "fallback_resume", "normalized_text": normalized}
    volume_context_words = (
        "音量", "聲音", "声音", "喇叭", "揚聲器", "扬声器", "音響", "音响",
        "音樂", "音乐", "歌曲", "speaker", "volume", "music", "song", "audio",
    )
    if any(word in lowered for word in volume_context_words):
        set_match = re.search(
            r"(?:音量|volume).{0,8}?(?:到|成|設成|设成|設定|设置|調到|调到|調成|调成|to)?\s*(?P<value>\d{1,3})\s*%?",
            normalized,
            flags=re.IGNORECASE,
        )
        if set_match:
            return {"intent": True, "action": "volume", "query": set_match.group("value"), "reason": "fallback_volume_set", "normalized_text": normalized}
        if any(word in lowered for word in ("調大", "调大", "大聲", "大声", "提高", "升高", "加大", "加十", "加 10", "往上", "太小", "聽不到", "听不到", "turn up", "volume up", "louder", "increase", "raise")):
            return {"intent": True, "action": "volume", "query": "+10", "reason": "fallback_volume_up10", "normalized_text": normalized}
        if any(word in lowered for word in ("調小", "调小", "小聲一點", "小声一点", "降低", "降下", "減小", "减小", "減十", "减十", "減 10", "减 10", "往下", "太大", "太吵", "破音", "爆音", "turn down", "volume down", "quieter", "decrease", "lower")):
            return {"intent": True, "action": "volume", "query": "-10", "reason": "fallback_volume_down10", "normalized_text": normalized}
    audio_complaint_words = (
        "沒聲音",
        "没声音",
        "沒有聲音",
        "没有声音",
        "聲音太小",
        "声音太小",
        "聲音很小",
        "声音很小",
        "聲音超小",
        "声音超小",
        "小聲",
        "小声",
        "音量",
        "聽不到",
        "听不到",
        "聽到聲音",
        "听到声音",
    )
    explicit_music_words = (
        "播放音樂",
        "播放音乐",
        "播放歌曲",
        "播音樂",
        "播音乐",
        "播歌",
        "放音樂",
        "放音乐",
        "放歌",
        "聽歌",
        "听歌",
        "點歌",
        "点歌",
        "play music",
        "play song",
    )
    if any(word in lowered for word in audio_complaint_words) and not any(word in lowered for word in explicit_music_words):
        return {"intent": False, "action": "none", "query": "", "reason": "fallback_audio_complaint_not_music", "normalized_text": normalized}
    play_pattern = re.compile(
        r"(?:播放|播一下|播|波一下|波|放一下|放|換成|换成|換一首|换一首|改播|切到|我想要聽|我想要听|想要聽|想要听|我想聽|我想听|想聽|想听|我要聽|我要听|聽一下|听一下|play|listen to)\s*(?P<query>.+)",
        flags=re.IGNORECASE,
    )
    match = play_pattern.search(normalized)
    if match:
        query = re.sub(r"\s*(?:這首歌|这首歌|這首|这首|音樂|音乐|歌曲|謝謝|谢谢|please)\s*$", "", match.group("query"), flags=re.IGNORECASE)
        query = re.sub(r"\s+", " ", query).strip()
        return {"intent": True, "action": "play", "query": query or "music", "reason": "fallback_play", "normalized_text": normalized}
    if any(word in lowered for word in ("音樂", "音乐", "歌曲", "聽歌", "听歌", "music", "song")) and any(
        word in lowered for word in ("播放", "播", "波", "放", "聽", "听", "play", "listen")
    ):
        return {"intent": True, "action": "play", "query": "music", "reason": "fallback_implicit_music", "normalized_text": normalized}
    return {"intent": False, "action": "none", "query": "", "reason": "fallback_no_music_intent", "normalized_text": normalized}


def detect_music_route(response: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    transcript = str(response.get("transcript", "") or "").strip()
    route: dict[str, Any] = {
        "enabled": not bool(getattr(args, "no_music", False)),
        "intent": False,
        "action": "none",
        "query": "",
        "reason": "disabled" if getattr(args, "no_music", False) else "empty",
        "transcript": transcript,
        "should_call": False,
    }
    if getattr(args, "no_music", False) or not transcript:
        response["music"] = route
        return route

    try:
        if music_tool is not None:
            intent_obj = music_tool.detect_music_intent(transcript)
            route.update(
                {
                    "intent": bool(getattr(intent_obj, "intent", False)),
                    "action": str(getattr(intent_obj, "action", "none") or "none"),
                    "query": str(getattr(intent_obj, "query", "") or ""),
                    "reason": str(getattr(intent_obj, "reason", "") or ""),
                    "normalized_text": str(getattr(intent_obj, "normalized_text", "") or ""),
                }
            )
        else:
            route.update(fallback_detect_music_intent(transcript))
    except Exception as exc:
        route.update(fallback_detect_music_intent(transcript))
        route["warning"] = f"music intent fallback used: {exc}"

    route["should_call"] = bool(route.get("intent") or getattr(args, "music_always_call", False))
    response["music"] = route
    if route["should_call"] or getattr(args, "music_debug", False):
        print()
        print("Music routing:")
        print(f"  transcript : {transcript or '(empty)'}")
        print(f"  intent     : {route.get('intent')}")
        print(f"  action     : {route.get('action')}")
        print(f"  query      : {route.get('query') or '(none)'}")
        print(f"  reason     : {route.get('reason')}")
        print(f"  url        : {args.music_url}")
    return route


def fallback_detect_weather_intent(text: str, *, default_location: str = DEFAULT_WEATHER_LOCATION) -> dict[str, Any]:
    normalized = re.sub(r"[，。！？、；：,.!?;:()\[\]{}\"'`《》〈〉「」『』]+", " ", str(text or ""))
    normalized = re.sub(r"\bhey\s+jarvis\b|\bjarvis\b|嘿\s*jarvis|嗨\s*jarvis|賈維斯|贾维斯", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    lowered = normalized.lower()
    if not normalized:
        return {"intent": False, "action": "none", "location": "", "reason": "empty", "normalized_text": ""}
    weather_words = (
        "天氣",
        "天气",
        "氣溫",
        "气温",
        "溫度",
        "温度",
        "幾度",
        "几度",
        "下雨",
        "降雨",
        "雨傘",
        "雨伞",
        "帶傘",
        "带伞",
        "冷不冷",
        "熱不熱",
        "热不热",
        "weather",
        "forecast",
        "temperature",
        "rain",
        "umbrella",
    )
    if any(word in lowered for word in weather_words) or re.search(r"(今天|明天|後天|后天|今晚|明早).{0,8}(冷|熱|热|雨|傘|伞|幾度|几度)", normalized):
        location = default_location
        for alias, canonical in (
            ("台北", "Taipei"),
            ("臺北", "Taipei"),
            ("新竹", "Hsinchu"),
            ("台中", "Taichung"),
            ("臺中", "Taichung"),
            ("台南", "Tainan"),
            ("臺南", "Tainan"),
            ("高雄", "Kaohsiung"),
            ("東京", "Tokyo"),
            ("大阪", "Osaka"),
            ("首爾", "Seoul"),
            ("首尔", "Seoul"),
        ):
            if alias.lower() in lowered:
                location = canonical
                break
        english_match = re.search(r"\b(?:in|for|at)\s+(?P<location>[A-Za-z][A-Za-z\s.-]{1,40})", normalized, flags=re.IGNORECASE)
        if english_match:
            location = re.sub(r"\b(today|tomorrow|weather|forecast|temperature|rain)\b", " ", english_match.group("location"), flags=re.IGNORECASE)
            location = re.sub(r"\s+", " ", location).strip() or default_location
        return {"intent": True, "action": "weather", "location": location, "reason": "fallback_weather_keyword", "normalized_text": normalized}
    return {"intent": False, "action": "none", "location": "", "reason": "fallback_no_weather_intent", "normalized_text": normalized}


def detect_weather_route(response: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    transcript = str(response.get("transcript", "") or "").strip()
    route: dict[str, Any] = {
        "enabled": not bool(getattr(args, "no_weather", False)),
        "intent": False,
        "action": "none",
        "location": "",
        "reason": "disabled" if getattr(args, "no_weather", False) else "empty",
        "transcript": transcript,
        "should_call": False,
    }
    if getattr(args, "no_weather", False) or not transcript:
        response["weather_route"] = route
        return route

    try:
        if music_tool is not None and hasattr(music_tool, "detect_weather_intent"):
            intent_obj = music_tool.detect_weather_intent(transcript, default_location=args.weather_default_location)
            route.update(
                {
                    "intent": bool(getattr(intent_obj, "intent", False)),
                    "action": "weather" if bool(getattr(intent_obj, "intent", False)) else "none",
                    "location": str(getattr(intent_obj, "location", "") or ""),
                    "reason": str(getattr(intent_obj, "reason", "") or ""),
                    "normalized_text": str(getattr(intent_obj, "normalized_text", "") or ""),
                }
            )
        else:
            route.update(fallback_detect_weather_intent(transcript, default_location=args.weather_default_location))
    except Exception as exc:
        route.update(fallback_detect_weather_intent(transcript, default_location=args.weather_default_location))
        route["warning"] = f"weather intent fallback used: {exc}"

    route["should_call"] = bool(route.get("intent") or getattr(args, "weather_always_call", False))
    response["weather_route"] = route
    if route["should_call"] or getattr(args, "weather_debug", False):
        print()
        print("Weather routing:")
        print(f"  transcript : {transcript or '(empty)'}")
        print(f"  intent     : {route.get('intent')}")
        print(f"  location   : {route.get('location') or args.weather_default_location}")
        print(f"  reason     : {route.get('reason')}")
        print(f"  url        : {args.weather_url}")
    return route


def music_health_url(music_url: str) -> str:
    return local_tool_health_url(music_url)


def music_playback_active(args: argparse.Namespace, *, timeout_sec: float = 0.2) -> bool:
    if getattr(args, "no_music", False):
        return False
    try:
        health = voice_chat.get_json(music_health_url(args.music_url), timeout_sec=max(0.05, timeout_sec))
    except Exception:
        return False
    return bool(health.get("active") and not health.get("paused"))


def resolve_music_backend(args: argparse.Namespace) -> str:
    backend = str(getattr(args, "music_backend", "auto") or "auto").strip().lower()
    if backend in {"browser", "mpv"}:
        return backend
    env_backend = os.getenv("MUSIC_PLAYER_BACKEND", "").strip().lower()
    if env_backend in {"browser", "mpv"}:
        return env_backend
    if shutil.which("mpv") and (shutil.which("yt-dlp") or shutil.which("youtube-dl")):
        return "mpv"
    return "browser"


def maybe_autostart_music_tool(args: argparse.Namespace, *, tool_url: str | None = None) -> bool:
    if getattr(args, "no_music_autostart", False):
        return False
    target_url = tool_url or args.music_url
    parsed = urllib.parse.urlsplit(target_url)
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost"}:
        print(f"Music tool autostart skipped: non-local host {host!r}.")
        return False
    script = MUSIC_DIR / "music_web_player.py"
    if not script.exists():
        print(f"Music tool autostart skipped: missing {script}.")
        return False
    backend = resolve_music_backend(args)
    port = int(parsed.port or 8788)
    command = [
        sys.executable,
        str(script),
        "--server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--backend",
        backend,
        "--weather-default-location",
        str(getattr(args, "weather_default_location", DEFAULT_WEATHER_LOCATION) or DEFAULT_WEATHER_LOCATION),
        "--weather-timeout",
        str(getattr(args, "weather_api_timeout", 4.5) or 4.5),
    ]
    if backend == "mpv":
        command.extend(["--mpv-audio-device", str(getattr(args, "music_mpv_audio_device", "auto") or "auto")])
        command.extend(["--mpv-audio-keyword", str(getattr(args, "music_mpv_audio_keyword", "UACDemo") or "UACDemo")])
        command.extend(["--mpv-volume", str(getattr(args, "music_mpv_volume", 150))])
        command.extend(["--mpv-volume-max", str(getattr(args, "music_mpv_volume_max", 200))])
        command.extend(["--mpv-ready-timeout", str(getattr(args, "music_mpv_ready_timeout", 1.5))])
        cookies_path = str(getattr(args, "music_mpv_ytdl_cookies", "") or "").strip()
        if cookies_path:
            command.extend(["--mpv-ytdl-cookies", cookies_path])
        cookies_browser = str(getattr(args, "music_mpv_ytdl_cookies_from_browser", "") or "").strip()
        if cookies_browser:
            command.extend(["--mpv-ytdl-cookies-from-browser", cookies_browser])
    if getattr(args, "music_dry_run", False):
        command.append("--dry-run")
    print(f"Music tool autostart: {' '.join(command)}")
    try:
        subprocess.Popen(command, cwd=str(MUSIC_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"Music tool autostart failed: {exc}")
        return False

    deadline = time.monotonic() + 3.0
    health_url = local_tool_health_url(target_url)
    while time.monotonic() < deadline:
        try:
            health = voice_chat.get_json(health_url, timeout_sec=0.5)
            if health.get("ok"):
                print(f"Music tool autostart ready: {health_url}")
                return True
        except Exception:
            time.sleep(0.2)
    print("Music tool autostart did not become ready in time.")
    return False


def pause_music_for_wake(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "no_music", False) or getattr(args, "no_music_pause_on_wake", False):
        return None
    payload: dict[str, Any] = {
        "action": "pause",
        "source": "wake_voice_chat_frdm_bridge",
        "phase": "wake_pause",
    }
    try:
        started = time.monotonic()
        result = voice_chat.post_json(
            args.music_url,
            payload,
            timeout_sec=float(getattr(args, "music_wake_pause_timeout", 0.6) or 0.6),
        )
        result["post_ms"] = int((time.monotonic() - started) * 1000)
        if result.get("stopped") or result.get("paused") or getattr(args, "music_debug", False):
            print(
                "Music wake pause: "
                f"ok={result.get('ok')} action={result.get('action')} "
                f"paused={result.get('paused')} stopped={result.get('stopped')} "
                f"post_ms={result.get('post_ms')}"
            )
        return result
    except Exception as exc:
        if getattr(args, "music_debug", False):
            print(f"Music wake pause skipped: {exc}")
        return {"ok": False, "action": "pause", "error": str(exc)}


def settle_after_music_wake_pause(args: argparse.Namespace, pause_result: dict[str, Any] | None) -> None:
    if not isinstance(pause_result, dict):
        return
    if not (pause_result.get("paused") or pause_result.get("stopped")):
        return
    settle_sec = max(0.0, float(getattr(args, "music_wake_beep_settle", 0.18) or 0.0))
    if settle_sec <= 0.0:
        return
    if getattr(args, "music_debug", False):
        print(f"Music wake pause settled for {settle_sec:.2f}s before beep.")
    time.sleep(settle_sec)


def post_music_standby_cooldown(args: argparse.Namespace, action: str) -> None:
    if action not in {"play", "resume"}:
        return
    cooldown_sec = max(0.0, float(getattr(args, "post_music_standby_cooldown", 0.8) or 0.0))
    if cooldown_sec <= 0.0:
        return
    print(f"Post-music standby cooldown: {cooldown_sec:.1f}s before listening for Hey Jarvis again.")
    time.sleep(cooldown_sec)


def execute_music_route(route: dict[str, Any], args: argparse.Namespace, response: dict[str, Any], *, phase: str) -> dict[str, Any] | None:
    if not route.get("should_call"):
        return None

    payload: dict[str, Any] = {
        "text": route.get("transcript", ""),
        "action": route.get("action", "none"),
        "source": "wake_voice_chat_frdm_bridge",
        "phase": phase,
    }
    if route.get("query"):
        payload["query"] = route.get("query")
    payload["backend"] = resolve_music_backend(args)
    if getattr(args, "music_dry_run", False):
        payload["dry_run"] = True

    try:
        started = time.monotonic()
        result = voice_chat.post_json(args.music_url, payload, timeout_sec=float(getattr(args, "music_timeout", 3.0)))
        result["post_ms"] = int((time.monotonic() - started) * 1000)
        result["phase"] = phase
    except Exception as exc:
        first_error = str(exc)
        if route.get("action") in {"pause", "stop", "resume"}:
            result = {
                "ok": route.get("action") in {"pause", "stop"},
                "handled": route.get("action") in {"pause", "stop"},
                "phase": phase,
                "action": route.get("action", "none"),
                "query": route.get("query", ""),
                "message": "music sidecar unavailable; nothing to pause/stop" if route.get("action") in {"pause", "stop"} else "music sidecar unavailable; cannot resume",
                "warning": first_error,
            }
        elif maybe_autostart_music_tool(args):
            try:
                started = time.monotonic()
                result = voice_chat.post_json(args.music_url, payload, timeout_sec=float(getattr(args, "music_timeout", 3.0)))
                result["post_ms"] = int((time.monotonic() - started) * 1000)
                result["phase"] = phase
                result["autostarted"] = True
            except Exception as retry_exc:
                result = {
                    "ok": False,
                    "handled": False,
                    "phase": phase,
                    "action": route.get("action", "none"),
                    "query": route.get("query", ""),
                    "error": str(retry_exc),
                    "first_error": first_error,
                }
        else:
            result = {
                "ok": False,
                "handled": False,
                "phase": phase,
                "action": route.get("action", "none"),
                "query": route.get("query", ""),
                "error": first_error,
            }

    response["music"] = {**route, **result}
    print()
    print("Music tool:")
    print(f"  phase   : {phase}")
    print(f"  ok      : {result.get('ok')}")
    print(f"  handled : {result.get('handled', result.get('ok', False))}")
    print(f"  action  : {result.get('action', route.get('action'))}")
    print(f"  query   : {result.get('query', route.get('query', '')) or '(none)'}")
    if result.get("backend"):
        print(f"  backend : {result.get('backend')}")
    if result.get("autostarted"):
        print("  autostarted: True")
    if result.get("url"):
        print(f"  url     : {result.get('url')}")
    if result.get("target"):
        print(f"  target  : {result.get('target')}")
    if result.get("audio_device"):
        print(f"  audio   : {result.get('audio_device')}")
    if result.get("cookies_configured"):
        print(f"  cookies : {result.get('cookies')}")
    elif result.get("cookies_from_browser"):
        print(f"  cookies : browser:{result.get('cookies_from_browser')}")
    if "playback_ready" in result:
        print(f"  playback_ready: {result.get('playback_ready')}")
    if result.get("audio_out"):
        print(f"  audio_out: {result.get('audio_out')}")
    if "paused" in result:
        print(f"  paused  : {result.get('paused')}")
    if "resumed" in result:
        print(f"  resumed : {result.get('resumed')}")
    if "stopped" in result:
        print(f"  stopped : {result.get('stopped')}")
    if "volume_percent" in result or "volume" in result:
        print(f"  volume  : {result.get('volume_percent', result.get('volume'))}")
    if "volume_delta" in result:
        print(f"  delta   : {result.get('volume_delta')}")
    if "ipc_ready" in result:
        print(f"  ipc_ready: {result.get('ipc_ready')}")
    if result.get("error"):
        print(f"  error   : {result.get('error')}")
        print("  hint    : start Terminal 4 music_web_player, or use --no-music.")
    return result


def execute_weather_route(route: dict[str, Any], args: argparse.Namespace, response: dict[str, Any], *, phase: str) -> dict[str, Any] | None:
    if not route.get("should_call"):
        return None

    payload: dict[str, Any] = {
        "text": route.get("transcript", ""),
        "action": "weather",
        "source": "wake_voice_chat_frdm_bridge",
        "phase": phase,
        "default_location": getattr(args, "weather_default_location", DEFAULT_WEATHER_LOCATION),
        "timeout_sec": float(getattr(args, "weather_api_timeout", 5.0) or 5.0),
    }
    if route.get("location"):
        payload["location"] = route.get("location")

    try:
        started = time.monotonic()
        result = voice_chat.post_json(args.weather_url, payload, timeout_sec=float(getattr(args, "weather_timeout", 6.0)))
        result["post_ms"] = int((time.monotonic() - started) * 1000)
        result["phase"] = phase
    except Exception as exc:
        first_error = str(exc)
        if maybe_autostart_music_tool(args, tool_url=args.weather_url):
            try:
                started = time.monotonic()
                result = voice_chat.post_json(args.weather_url, payload, timeout_sec=float(getattr(args, "weather_timeout", 6.0)))
                result["post_ms"] = int((time.monotonic() - started) * 1000)
                result["phase"] = phase
                result["autostarted"] = True
            except Exception as retry_exc:
                result = {
                    "ok": False,
                    "handled": False,
                    "phase": phase,
                    "action": "weather",
                    "location": route.get("location", ""),
                    "error": str(retry_exc),
                    "first_error": first_error,
                }
        else:
            result = {
                "ok": False,
                "handled": False,
                "phase": phase,
                "action": "weather",
                "location": route.get("location", ""),
                "error": first_error,
            }

    response["weather"] = {**route, **result}
    print()
    print("Weather tool:")
    print(f"  phase    : {phase}")
    print(f"  ok       : {result.get('ok')}")
    print(f"  handled  : {result.get('handled', result.get('ok', False))}")
    print(f"  location : {result.get('location', route.get('location', '')) or args.weather_default_location}")
    if result.get("target"):
        print(f"  target   : {result.get('target')}")
    if result.get("source"):
        print(f"  source   : {result.get('source')}")
    if result.get("reply"):
        print(f"  reply    : {result.get('reply')}")
    if result.get("error"):
        print(f"  error    : {result.get('error')}")
        print("  hint     : check network, Open-Meteo reachability, or use --no-weather.")
    return result


def maybe_apply_weather_response(response: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    weather_route = detect_weather_route(response, args)
    if not weather_route.get("should_call"):
        return None

    result = execute_weather_route(weather_route, args, response, phase="before_tts")
    if not result:
        return None

    if result.get("ok") and result.get("handled") and str(result.get("reply", "")).strip():
        response["reply"] = str(result["reply"]).strip()
        response["control"] = {
            "persistent_state": "unchanged",
            "emotion": "curious",
            "head_motion": "gentle_nod",
            "reason": "local weather API answer",
        }
        response["emotion"] = emotion_summary_from_control(response["control"])
        response.setdefault("debug", {})
        if isinstance(response["debug"], dict):
            response["debug"]["local_weather_used"] = True
            response["debug"]["local_weather_source"] = result.get("source", "open-meteo")
        return result

    response["reply"] = "我有聽到你在問天氣，但我剛剛連不到本地天氣工具或天氣資料來源。你可以稍後再問我一次。"
    response["control"] = {
        "persistent_state": "unchanged",
        "emotion": "confused",
        "head_motion": "gentle_nod",
        "reason": "local weather API failed",
    }
    response["emotion"] = emotion_summary_from_control(response["control"])
    return result


def normalize_temperature_path(raw_path: str) -> str:
    path = str(raw_path or DEFAULT_ESP32_TEMPERATURE_PATH).strip()
    if not path:
        path = DEFAULT_ESP32_TEMPERATURE_PATH
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        path = parsed.path or DEFAULT_ESP32_TEMPERATURE_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/temperature"


def _coerce_temperature_c(value: Any) -> float | None:
    try:
        temp_c = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if temp_c < -55.0 or temp_c > 125.0:
        return None
    return temp_c


def extract_temperature_c(payload: Any) -> float | None:
    if isinstance(payload, dict):
        if "ok" in payload and not bool(payload.get("ok")):
            return None
        for key in ("temperature_c", "temp_c", "temperatureC", "temperature", "temp", "value"):
            if key not in payload:
                continue
            value = payload.get(key)
            if isinstance(value, dict):
                nested = extract_temperature_c(value)
                if nested is not None:
                    return nested
                continue
            temp_c = _coerce_temperature_c(value)
            if temp_c is not None:
                return temp_c
        return None
    if isinstance(payload, (list, tuple)):
        for item in payload:
            temp_c = extract_temperature_c(item)
            if temp_c is not None:
                return temp_c
        return None
    if isinstance(payload, (int, float)):
        return _coerce_temperature_c(payload)
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            return extract_temperature_c(json.loads(text))
        except json.JSONDecodeError:
            return _coerce_temperature_c(text)
    return None


def temperature_c_to_uart_x10(temp_c: float) -> int:
    return clamp_int(int(round(temp_c * 10)), -550, 1250)


def format_temperature_uart_field(temp_c: float | None) -> str | None:
    if temp_c is None:
        return None
    coerced = _coerce_temperature_c(temp_c)
    if coerced is None:
        return None
    return str(temperature_c_to_uart_x10(coerced))


def format_temp_room_uart_payload(temp_c: float | None) -> str | None:
    return format_temperature_uart_field(temp_c)


def send_temp_room_uart_update(
    args: argparse.Namespace,
    robot: "RobotUartController",
    reading: dict[str, Any] | None,
    *,
    reason: str,
) -> str | None:
    if reading is None:
        return None
    payload = format_temp_room_uart_payload(reading.get("temperature_c"))
    if payload is None:
        return None
    ok = robot.send_uart_raw_line(f"TempRoom {payload}", reason=reason, read_ms=60)
    if ok:
        age = reading.get("age_sec")
        age_text = f", age={float(age):.1f}s" if isinstance(age, (int, float)) else ""
        print(f"TempRoom UART sent: TempRoom {payload} ({float(reading['temperature_c']):.1f} C{age_text})")
    else:
        print("WARNING: TempRoom UART was not sent.")
    return payload


class FrdmRoomTemperaturePublisher:
    def __init__(
        self,
        args: argparse.Namespace,
        robot: "RobotUartController",
        receiver: "Esp32TemperatureReceiver | None",
    ) -> None:
        self.args = args
        self.robot = robot
        self.receiver = receiver
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enabled(self) -> bool:
        if getattr(self.args, "no_uart", False) or getattr(self.args, "no_temp_room_uart", False):
            return False
        if self.receiver is not None:
            return self.interval_sec() > 0.0
        mode = str(getattr(self.args, "esp32_temperature_mode", "disabled") or "disabled").strip().lower()
        url = str(getattr(self.args, "esp32_temperature_url", "") or "").strip()
        return self.interval_sec() > 0.0 and mode in {"pull", "both"} and bool(url)

    def interval_sec(self) -> float:
        return max(0.0, float(getattr(self.args, "temp_room_uart_interval_sec", 10.0) or 0.0))

    def max_age_sec(self) -> float:
        return max(0.0, float(getattr(self.args, "temp_room_uart_max_age_sec", 30.0) or 0.0))

    def start(self) -> bool:
        if not self.enabled():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="frdm-temp-room-publisher", daemon=True)
        self._thread.start()
        print(
            "FRDM TempRoom UART: "
            f"enabled, interval={self.interval_sec():g}s, max_age={self.max_age_sec():g}s."
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _latest_reading(self) -> dict[str, Any] | None:
        receiver = self.receiver
        if receiver is None:
            return get_local_temperature_reading(self.args)
        return receiver.latest(max_age_sec=self.max_age_sec())

    def _run(self) -> None:
        interval_sec = self.interval_sec()
        next_send_at = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_send_at:
                reading = self._latest_reading()
                if reading is None:
                    wait_sec = min(1.0, interval_sec) if self.receiver is not None and interval_sec > 0 else max(1.0, interval_sec)
                    self._stop.wait(wait_sec)
                    continue
                send_temp_room_uart_update(
                    self.args,
                    self.robot,
                    reading,
                    reason="ESP32 BLE room temperature update",
                )
                next_send_at = time.monotonic() + interval_sec
            wait_sec = max(0.1, min(1.0, next_send_at - time.monotonic()))
            self._stop.wait(wait_sec)


class Esp32TemperatureReceiver:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.host = str(getattr(args, "esp32_temperature_host", "0.0.0.0") or "0.0.0.0")
        self.port = int(getattr(args, "esp32_temperature_port", 8790) or 8790)
        self.path = normalize_temperature_path(str(getattr(args, "esp32_temperature_path", DEFAULT_ESP32_TEMPERATURE_PATH)))
        self.debug = bool(getattr(args, "esp32_temperature_debug", False))
        self._lock = threading.RLock()
        self._latest: dict[str, Any] | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def update(self, temp_c: float, *, source: str, remote: str = "") -> dict[str, Any]:
        reading = {
            "temperature_c": temp_c,
            "source": source,
            "remote": remote,
            "received_at": time.monotonic(),
            "received_at_iso": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._latest = reading
        if self.debug:
            print(f"ESP32 temperature update: {temp_c:.1f} C from {source}" + (f" ({remote})" if remote else ""))
        return reading

    def latest(self, *, max_age_sec: float) -> dict[str, Any] | None:
        with self._lock:
            latest = dict(self._latest) if self._latest is not None else None
        if latest is None:
            return None
        age_sec = max(0.0, time.monotonic() - float(latest.get("received_at", 0.0) or 0.0))
        if max_age_sec > 0 and age_sec > max_age_sec:
            return None
        latest["age_sec"] = age_sec
        return latest

    def start(self) -> bool:
        if self._server is not None:
            return True
        receiver = self

        class TemperatureHandler(BaseHTTPRequestHandler):
            server_version = "MakeNTUTemperatureHTTP/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                if receiver.debug:
                    print("ESP32 temperature HTTP: " + (fmt % args))

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _path_ok(self) -> bool:
                parsed = urllib.parse.urlsplit(self.path)
                return normalize_temperature_path(parsed.path) == receiver.path

            def do_GET(self) -> None:
                if not self._path_ok():
                    self._send_json(404, {"ok": False, "error": "not_found", "path": receiver.path})
                    return
                parsed = urllib.parse.urlsplit(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                temp_c = extract_temperature_c({key: values[0] for key, values in params.items() if values})
                if temp_c is not None:
                    reading = receiver.update(temp_c, source="esp32-http-get", remote=str(self.client_address[0]))
                    self._send_json(200, {"ok": True, "temperature_c": reading["temperature_c"]})
                    return
                latest = receiver.latest(max_age_sec=float(getattr(receiver.args, "esp32_temperature_max_age_sec", 120.0) or 120.0))
                if latest is None:
                    self._send_json(503, {"ok": False, "error": "no_temperature"})
                else:
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "temperature_c": latest["temperature_c"],
                            "age_sec": round(float(latest.get("age_sec", 0.0)), 2),
                            "source": latest.get("source", ""),
                        },
                    )

            def do_POST(self) -> None:
                if not self._path_ok():
                    self._send_json(404, {"ok": False, "error": "not_found", "path": receiver.path})
                    return
                try:
                    length = min(max(0, int(self.headers.get("Content-Length", "0") or "0")), 4096)
                except ValueError:
                    length = 0
                raw = self.rfile.read(length).decode("utf-8", errors="replace").strip()
                content_type = str(self.headers.get("Content-Type", "") or "").lower()
                payload: Any
                if "application/x-www-form-urlencoded" in content_type:
                    payload = {key: values[0] for key, values in urllib.parse.parse_qs(raw).items() if values}
                else:
                    payload = raw
                temp_c = extract_temperature_c(payload)
                if temp_c is None:
                    self._send_json(400, {"ok": False, "error": "invalid_temperature"})
                    return
                reading = receiver.update(temp_c, source="esp32-http-post", remote=str(self.client_address[0]))
                self._send_json(200, {"ok": True, "temperature_c": reading["temperature_c"]})

        class ReusableThreadingHTTPServer(ThreadingHTTPServer):
            allow_reuse_address = True

        try:
            self._server = ReusableThreadingHTTPServer((self.host, self.port), TemperatureHandler)
        except OSError as exc:
            print(f"WARNING: ESP32 temperature receiver could not listen on {self.host}:{self.port}: {exc}")
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, name="esp32-temperature-http", daemon=True)
        self._thread.start()
        print(f"ESP32 temperature receiver: http://{self.host}:{self.port}{self.path}")
        return True

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)


def maybe_start_esp32_temperature_receiver(args: argparse.Namespace) -> Esp32TemperatureReceiver | None:
    mode = str(getattr(args, "esp32_temperature_mode", "disabled") or "disabled").strip().lower()
    if mode not in {"push", "both"} or getattr(args, "no_weather_local_temperature", False):
        return None
    receiver = Esp32TemperatureReceiver(args)
    if not receiver.start():
        return None
    setattr(args, "_esp32_temperature_receiver", receiver)
    return receiver


def fetch_esp32_temperature(args: argparse.Namespace) -> dict[str, Any] | None:
    url = str(getattr(args, "esp32_temperature_url", "") or "").strip()
    if not url:
        return None
    timeout_sec = max(0.05, float(getattr(args, "esp32_temperature_timeout", 0.6) or 0.6))
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
        temp_c = extract_temperature_c(raw)
        if temp_c is None:
            return None
        return {"temperature_c": temp_c, "source": "esp32-http-get", "url": url, "age_sec": 0.0}
    except Exception as exc:
        if getattr(args, "esp32_temperature_debug", False):
            print(f"ESP32 temperature fetch failed: {exc}")
        return None


def get_local_temperature_reading(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "no_weather_local_temperature", False):
        return None
    mode = str(getattr(args, "esp32_temperature_mode", "disabled") or "disabled").strip().lower()
    ble_temperature_enabled = bool(getattr(args, "esp32_ble", False)) and not bool(
        getattr(args, "_esp32_ble_runtime_unavailable", False)
    )
    if mode not in {"push", "pull", "both"} and not ble_temperature_enabled:
        return None
    max_age_sec = max(0.0, float(getattr(args, "esp32_temperature_max_age_sec", 120.0) or 120.0))
    receiver = getattr(args, "_esp32_temperature_receiver", None)
    if (mode in {"push", "both"} or ble_temperature_enabled) and isinstance(receiver, Esp32TemperatureReceiver):
        latest = receiver.latest(max_age_sec=max_age_sec)
        if latest is not None:
            return latest
    if mode in {"pull", "both"}:
        fetched = fetch_esp32_temperature(args)
        if fetched is not None:
            if isinstance(receiver, Esp32TemperatureReceiver):
                receiver.update(float(fetched["temperature_c"]), source=str(fetched.get("source", "esp32-http-get")))
            return fetched
    return None


def attach_local_temperature_to_weather_result(args: argparse.Namespace, weather_result: dict[str, Any]) -> dict[str, Any] | None:
    reading = get_local_temperature_reading(args)
    if reading is None:
        return None
    weather_result["local_temperature"] = reading
    weather_result["local_temperature_c"] = reading["temperature_c"]
    return reading


def esp32_status_fan_is_off(status: Any | None) -> bool:
    if status is None:
        return False
    fan = str(getattr(status, "fan", "") or "").strip().upper()
    if fan == "OFF":
        return True
    if fan == "ON":
        return False
    speed = getattr(status, "speed", None)
    if speed is None:
        return False
    try:
        return int(speed) <= 0
    except (TypeError, ValueError):
        return False


def esp32_commands_are_fan_off_only(commands: list[str]) -> bool:
    upper = [str(command or "").strip().upper() for command in commands if str(command or "").strip()]
    return bool(upper) and all(command == "FAN_OFF" for command in upper)


class Esp32BleBridgeManager:
    """Background BLE bridge from the Wake Bridge to ESP32-S3 fan/LED/temp."""

    def __init__(self, args: argparse.Namespace, temperature_receiver: Esp32TemperatureReceiver | None = None) -> None:
        self.args = args
        self.temperature_receiver = temperature_receiver
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._controller: Any | None = None
        self._thread: threading.Thread | None = None
        self._pending_commands: list[str] = []
        self._latest_status: Any | None = None
        self._started = False
        self._last_reconnect_notice_at = 0.0
        self._unavailable_notice_printed = False
        self._dropped_pending_commands = 0

    def is_enabled(self) -> bool:
        return bool(getattr(self.args, "esp32_ble", False)) and not bool(
            getattr(self.args, "_esp32_ble_runtime_unavailable", False)
        )

    def unavailable_reason(self) -> str:
        return str(getattr(self.args, "_esp32_ble_runtime_unavailable_reason", "") or "").strip()

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def is_connected(self) -> bool:
        controller = self._controller
        try:
            return bool(controller is not None and controller.connected.is_set())
        except Exception:
            return False

    def latest_status(self) -> Any | None:
        with self._lock:
            return self._latest_status

    def queue_stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued_pending": len(self._pending_commands),
                "dropped_pending": self._dropped_pending_commands,
            }

    def start(self) -> bool:
        if not self.is_enabled():
            reason = self.unavailable_reason()
            if bool(getattr(self.args, "esp32_ble", False)) and reason and not self._unavailable_notice_printed:
                self._unavailable_notice_printed = True
                print(f"WARNING: ESP32 BLE reconnect loop not started ({reason}); other bridge features continue.")
            return False
        if esp32_ble is None:
            print(f"WARNING: ESP32 BLE disabled because helper import failed: {ESP32_BLE_IMPORT_ERROR}")
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        if self._thread is not None:
            self._thread = None
        self._thread = threading.Thread(target=self._run_reconnect_loop, name="esp32s3-ble-reconnect-loop", daemon=True)
        self._thread.start()
        self._started = True
        return True

    def ensure_reconnect_loop(self) -> bool:
        if not self.is_enabled():
            return False
        started = self.start()
        now = time.monotonic()
        if started and not self.is_connected() and now - self._last_reconnect_notice_at >= 10.0:
            self._last_reconnect_notice_at = now
            print("ESP32 BLE is not connected; reconnect loop is active.")
        return started

    def stop(self) -> None:
        loop: asyncio.AbstractEventLoop | None
        controller: Any | None
        with self._lock:
            loop = self._loop
            controller = self._controller
        if loop is not None and controller is not None:
            def request_stop() -> None:
                controller.stop.set()
                try:
                    controller.command_queue.put_nowait(None)
                except Exception:
                    pass

            try:
                loop.call_soon_threadsafe(request_stop)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _controller_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            name=str(getattr(self.args, "esp32_ble_name", esp32_ble.DEVICE_NAME) or esp32_ble.DEVICE_NAME),
            address=str(getattr(self.args, "esp32_ble_address", "") or ""),
            adapter=str(getattr(self.args, "esp32_ble_adapter", "") or ""),
            scan_mode=str(getattr(self.args, "esp32_ble_scan_mode", "active") or "active"),
            scan_duplicates=bool(getattr(self.args, "esp32_ble_scan_duplicates", False)),
            scan_filter_service=bool(getattr(self.args, "esp32_ble_scan_filter_service", False)),
            scan_sort="name",
            min_rssi=None,
            scan_timeout=max(0.1, float(getattr(self.args, "esp32_ble_scan_timeout", 8.0) or 8.0)),
            connect_timeout=max(0.1, float(getattr(self.args, "esp32_ble_connect_timeout", 12.0) or 12.0)),
            reconnect_sec=max(0.1, float(getattr(self.args, "esp32_ble_reconnect_sec", 3.0) or 3.0)),
            write_with_response=bool(getattr(self.args, "esp32_ble_write_with_response", False)),
            write_response_auto=bool(getattr(self.args, "esp32_ble_write_response_auto", True)),
            read_status_on_connect=bool(getattr(self.args, "esp32_ble_read_status_on_connect", True)),
            no_passive_reminder=bool(getattr(self.args, "no_esp32_ble_passive_reminder", False)),
            passive_threshold=float(getattr(self.args, "esp32_ble_passive_threshold", 25.0) or 25.0),
            passive_cooldown_sec=max(0.0, float(getattr(self.args, "esp32_ble_passive_cooldown_sec", 120.0) or 120.0)),
            passive_message=str(
                getattr(
                    self.args,
                    "esp32_ble_passive_message",
                    "現在溫度 {temp:.1f} 度，有點熱，要不要幫你開風扇？",
                )
                or "現在溫度 {temp:.1f} 度，有點熱，要不要幫你開風扇？"
            ),
            no_tts_reminder=bool(getattr(self.args, "no_tts", False) or getattr(self.args, "no_esp32_ble_tts_reminder", False)),
            tts_url=str(getattr(self.args, "tts_url", "") or ""),
            tts_timeout=max(0.1, float(getattr(self.args, "esp32_ble_tts_timeout", getattr(self.args, "tts_timeout", 1.5)) or 1.5)),
            voice_speed_step=max(1, int(getattr(self.args, "esp32_ble_voice_speed_step", 32) or 32)),
        )

    def _pending_command_limit(self) -> int:
        return max(1, int(getattr(self.args, "esp32_ble_command_queue_max", 64) or 64))

    def _queue_pending_locked(self, command: str) -> bool:
        limit = self._pending_command_limit()
        if len(self._pending_commands) >= limit:
            self._pending_commands.pop(0)
            self._dropped_pending_commands += 1
        self._pending_commands.append(command)
        return True

    def _run_reconnect_loop(self) -> None:
        if esp32_ble is None:
            return
        manager = self
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        class WakeBridgeEsp32BleController(esp32_ble.Esp32BleController):  # type: ignore[misc, union-attr]
            def _accept_status_bytes(self, data: bytes) -> None:
                super()._accept_status_bytes(data)
                status = self.latest_status
                if status is not None:
                    manager._handle_status(status)

        controller = WakeBridgeEsp32BleController(self._controller_args())
        with self._lock:
            self._loop = loop
            self._controller = controller
            pending = list(self._pending_commands)
            self._pending_commands.clear()
        for command in pending:
            controller.command_queue.put_nowait(command)
        try:
            loop.run_until_complete(controller.run_ble_forever())
        except Exception as exc:
            if self.is_enabled():
                print(f"WARNING: ESP32 BLE bridge stopped: {exc}")
        finally:
            with self._lock:
                self._controller = None
                self._loop = None
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    def _handle_status(self, status: Any) -> None:
        with self._lock:
            self._latest_status = status
        temp_c = getattr(status, "temp_c", None)
        if temp_c is None:
            return
        receiver = self.temperature_receiver or getattr(self.args, "_esp32_temperature_receiver", None)
        if isinstance(receiver, Esp32TemperatureReceiver):
            receiver.update(float(temp_c), source="esp32-ble-notify")

    def send_command(self, command: str, *, source: str = "") -> bool:
        command = str(command or "").strip()
        if not command or not self.is_enabled():
            return False
        if esp32_ble is None:
            return False
        with self._lock:
            loop = self._loop
            controller = self._controller
            if loop is None or controller is None:
                if self._started:
                    return self._queue_pending_locked(command)
                return False
        try:
            async def put_bounded() -> None:
                limit = self._pending_command_limit()
                try:
                    while controller.command_queue.qsize() >= limit:
                        controller.command_queue.get_nowait()
                        with self._lock:
                            self._dropped_pending_commands += 1
                except asyncio.QueueEmpty:
                    pass
                await controller.command_queue.put(command)

            asyncio.run_coroutine_threadsafe(put_bounded(), loop)
            if source:
                print(f"ESP32 BLE queued ({source}): {command}")
            return True
        except Exception as exc:
            print(f"WARNING: ESP32 BLE queue failed for {command!r}: {exc}")
            return False

    def send_commands(self, commands: list[str], *, source: str = "") -> dict[str, Any]:
        cleaned = [str(command or "").strip() for command in commands if str(command or "").strip()]
        connected_before = self.is_connected()
        reconnect_requested = False
        if cleaned and not connected_before:
            reconnect_requested = self.ensure_reconnect_loop()
        ok_count = 0
        for command in cleaned:
            if self.send_command(command, source=source):
                ok_count += 1
        return {
            "ok": bool(cleaned) and ok_count == len(cleaned),
            "queued": ok_count,
            "commands": cleaned,
            "connected": self.is_connected(),
            "was_connected": connected_before,
            "reconnect_requested": reconnect_requested,
            "source": source,
        }

    def handle_frdm_fan_event(self, event: dict[str, Any]) -> bool:
        if not self.is_enabled() or getattr(self.args, "no_esp32_ble_frdm_control", False):
            return False
        percent = clamp_int(int(event.get("percent", 0) or 0), 0, 100)
        if not bool(event.get("power", percent > 0)):
            commands = ["FAN_OFF"]
        else:
            commands = ["FAN_ON", f"FAN_SPEED:{esp32_ble.percent_to_pwm(percent)}"]  # type: ignore[union-attr]
        result = self.send_commands(commands, source="frdm_uart")
        print(
            "ESP32 BLE fan relay: "
            f"percent={percent} commands={','.join(commands)} "
            f"queued={result['queued']}/{len(commands)} connected={result['connected']}"
        )
        return bool(result.get("ok"))

    def handle_voice_transcript(self, transcript: str) -> dict[str, Any] | None:
        if not self.is_enabled() or getattr(self.args, "no_esp32_ble_voice_control", False):
            return None
        if esp32_ble is None:
            return None
        speed_step = max(1, int(getattr(self.args, "esp32_ble_voice_speed_step", 32) or 32))
        # Avoid touching the sidecar on normal conversation turns. Only fetch live
        # status after the text parser sees an ESP32 fan/LED intent.
        commands = esp32_ble.resolve_input_to_ble_commands(  # type: ignore[union-attr]
            transcript,
            None,
            speed_step=speed_step,
        )
        if not commands:
            return None
        status = self.latest_status()
        commands = esp32_ble.resolve_input_to_ble_commands(  # type: ignore[union-attr]
            transcript,
            status,
            speed_step=speed_step,
        ) or commands
        connected_before = self.is_connected()
        if connected_before and esp32_commands_are_fan_off_only(commands) and esp32_status_fan_is_off(status):
            return {
                "ok": True,
                "queued": 0,
                "commands": commands,
                "connected": connected_before,
                "was_connected": connected_before,
                "reconnect_requested": False,
                "source": "voice",
                "intent": True,
                "transcript": transcript,
                "noop": True,
                "already_state": "fan_off",
            }
        result = self.send_commands(commands, source="voice")
        result["intent"] = True
        result["transcript"] = transcript
        return result


class Esp32BleApiClientManager:
    """Thin Wake Bridge client for the standalone ESP32 BLE sidecar API."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        base = str(getattr(args, "esp32_ble_api_url", "") or "").strip()
        if not base:
            host = str(getattr(args, "esp32_dashboard_host", DEFAULT_ESP32_DASHBOARD_HOST) or DEFAULT_ESP32_DASHBOARD_HOST)
            port = int(getattr(args, "esp32_dashboard_port", DEFAULT_ESP32_DASHBOARD_PORT) or DEFAULT_ESP32_DASHBOARD_PORT)
            base = f"http://{host}:{port}/api/esp32"
        self.base_url = base.rstrip("/")
        self.status_url = self.base_url + "/status"
        self.control_url = self.base_url + "/control"
        self._lock = threading.RLock()
        self._last_payload: dict[str, Any] | None = None
        self._last_payload_at = 0.0
        self._last_error = ""

    def is_enabled(self) -> bool:
        return bool(getattr(self.args, "esp32_ble", False)) and not bool(
            getattr(self.args, "_esp32_ble_runtime_unavailable", False)
        )

    def unavailable_reason(self) -> str:
        return str(getattr(self.args, "_esp32_ble_runtime_unavailable_reason", "") or "").strip()

    def is_running(self) -> bool:
        payload = self._status_payload(allow_stale=True)
        return bool(payload.get("running", self.is_enabled()))

    def is_connected(self) -> bool:
        payload = self._status_payload(allow_stale=True)
        return bool(payload.get("connected", False))

    def latest_status(self) -> Any | None:
        payload = self._status_payload(allow_stale=True)
        if not payload or payload.get("temp_c") is None and payload.get("fan") is None:
            return None
        return argparse.Namespace(
            temp_c=payload.get("temp_c", payload.get("temperature_c")),
            fan=payload.get("fan"),
            speed=payload.get("speed"),
            led=payload.get("led"),
            raw=payload.get("raw", ""),
            received_at=payload.get("updated_at", ""),
        )

    def queue_stats(self) -> dict[str, int]:
        payload = self._status_payload(allow_stale=True)
        return {
            "queued_pending": int(payload.get("queued_pending", 0) or 0),
            "dropped_pending": int(payload.get("dropped_pending", 0) or 0),
        }

    def start(self) -> bool:
        if not self.is_enabled():
            return False
        payload = self._status_payload(allow_stale=False)
        if payload.get("running", False) or "connected" in payload or "ok" in payload:
            print(f"ESP32 BLE sidecar API: {self.status_url}")
            return True
        print(f"WARNING: ESP32 BLE sidecar API not ready: {self._last_error or self.status_url}")
        return False

    def stop(self) -> None:
        return

    def _timeout_sec(self) -> float:
        return max(0.05, float(getattr(self.args, "esp32_ble_api_timeout", 0.2) or 0.2))

    def _status_cache_sec(self) -> float:
        return max(0.0, float(getattr(self.args, "esp32_ble_api_status_cache_sec", 0.5) or 0.5))

    def _http_json(self, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data: bytes | None = None
        method = "GET"
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            method = "POST"
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=self._timeout_sec()) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"ok": False, "error": "non-object JSON"}

    def _status_payload(self, *, allow_stale: bool) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            cached = dict(self._last_payload) if self._last_payload is not None else None
            cached_age = now - self._last_payload_at
        if allow_stale and cached is not None and cached_age <= self._status_cache_sec():
            return cached
        try:
            payload = self._http_json(self.status_url)
        except Exception as exc:
            self._last_error = str(exc)
            if cached is not None:
                cached["api_error"] = self._last_error
                return cached
            return {"ok": False, "enabled": self.is_enabled(), "running": False, "connected": False, "error": self._last_error}
        with self._lock:
            self._last_payload = dict(payload)
            self._last_payload_at = time.monotonic()
            self._last_error = ""
        return payload

    def send_commands(self, commands: list[str], *, source: str = "") -> dict[str, Any]:
        cleaned = [str(command or "").strip() for command in commands if str(command or "").strip()]
        if not cleaned or not self.is_enabled():
            return {"ok": False, "queued": 0, "commands": cleaned, "connected": False, "source": source}
        payload = {"commands": cleaned, "source": source or "wake_bridge"}
        try:
            result = self._http_json(self.control_url, payload)
        except Exception as exc:
            self._last_error = str(exc)
            print(f"WARNING: ESP32 BLE sidecar command failed: {exc}")
            return {
                "ok": False,
                "queued": 0,
                "commands": cleaned,
                "connected": False,
                "was_connected": False,
                "reconnect_requested": False,
                "source": source,
                "error": str(exc),
            }
        status = result.get("status")
        if isinstance(status, dict):
            with self._lock:
                self._last_payload = dict(status)
                self._last_payload_at = time.monotonic()
        return result

    def handle_frdm_fan_event(self, event: dict[str, Any]) -> bool:
        if not self.is_enabled() or getattr(self.args, "no_esp32_ble_frdm_control", False):
            return False
        if esp32_ble is None:
            return False
        percent = clamp_int(int(event.get("percent", 0) or 0), 0, 100)
        if not bool(event.get("power", percent > 0)):
            commands = ["FAN_OFF"]
        else:
            commands = ["FAN_ON", f"FAN_SPEED:{esp32_ble.percent_to_pwm(percent)}"]  # type: ignore[union-attr]
        result = self.send_commands(commands, source="frdm_uart")
        print(
            "ESP32 BLE sidecar fan relay: "
            f"percent={percent} commands={','.join(commands)} "
            f"queued={result.get('queued', 0)}/{len(commands)} connected={result.get('connected', False)}"
        )
        return bool(result.get("ok") or result.get("queued"))

    def handle_voice_transcript(self, transcript: str) -> dict[str, Any] | None:
        if not self.is_enabled() or getattr(self.args, "no_esp32_ble_voice_control", False):
            return None
        if esp32_ble is None:
            return None
        status = self.latest_status()
        commands = esp32_ble.resolve_input_to_ble_commands(  # type: ignore[union-attr]
            transcript,
            status,
            speed_step=max(1, int(getattr(self.args, "esp32_ble_voice_speed_step", 32) or 32)),
        )
        if not commands:
            return None
        connected_before = self.is_connected()
        if connected_before and esp32_commands_are_fan_off_only(commands) and esp32_status_fan_is_off(status):
            return {
                "ok": True,
                "queued": 0,
                "commands": commands,
                "connected": connected_before,
                "was_connected": connected_before,
                "reconnect_requested": False,
                "source": "voice",
                "intent": True,
                "transcript": transcript,
                "noop": True,
                "already_state": "fan_off",
            }
        result = self.send_commands(commands, source="voice")
        result["intent"] = True
        result["transcript"] = transcript
        return result


def esp32_status_payload(manager: Esp32BleBridgeManager | None) -> dict[str, Any]:
    if manager is None:
        return {"ok": False, "enabled": False, "running": False, "connected": False, "error": "esp32 manager unavailable"}
    status = manager.latest_status()
    queue_stats = manager.queue_stats()
    payload: dict[str, Any] = {
        "ok": manager.is_enabled(),
        "requested": bool(getattr(manager.args, "esp32_ble", False)),
        "enabled": manager.is_enabled(),
        "unavailable_reason": manager.unavailable_reason(),
        "running": manager.is_running(),
        "connected": manager.is_connected(),
        **queue_stats,
        "updated_at": "",
        "raw": "",
    }
    if not manager.is_enabled():
        payload["ok"] = False
        if payload["requested"] and payload["unavailable_reason"]:
            payload["error"] = "ESP32 BLE unavailable; reconnect loop disabled"
        else:
            payload["error"] = "ESP32 BLE disabled"
        return payload
    if status is None:
        payload["ok"] = False
        payload["error"] = "no ESP32 status yet"
        return payload
    speed = getattr(status, "speed", None)
    try:
        speed_percent = esp32_ble.pwm_to_percent(speed) if esp32_ble is not None and speed is not None else 0
    except Exception:
        speed_percent = 0
    received_at = getattr(status, "received_at", None)
    payload.update(
        {
            "ok": True,
            "temp_c": getattr(status, "temp_c", None),
            "temperature_c": getattr(status, "temp_c", None),
            "fan": getattr(status, "fan", None),
            "speed": speed,
            "speed_percent": speed_percent,
            "led": getattr(status, "led", None),
            "updated_at": received_at.isoformat(timespec="seconds") if hasattr(received_at, "isoformat") else "",
            "raw": getattr(status, "raw", "") or "",
        }
    )
    return payload


class Esp32DashboardControlServer:
    """Small local HTTP bridge: dashboard device controls -> ESP32 BLE commands."""

    def __init__(self, args: argparse.Namespace, manager: Esp32BleBridgeManager) -> None:
        self.args = args
        self.manager = manager
        self.host = str(getattr(args, "esp32_dashboard_host", DEFAULT_ESP32_DASHBOARD_HOST) or DEFAULT_ESP32_DASHBOARD_HOST)
        self.port = int(getattr(args, "esp32_dashboard_port", DEFAULT_ESP32_DASHBOARD_PORT) or DEFAULT_ESP32_DASHBOARD_PORT)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._server is not None:
            return True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MakeNTUEsp32DashboardHTTP/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                if getattr(owner.args, "esp32_dashboard_debug", False):
                    print("ESP32 dashboard HTTP: " + (fmt % args))

            def end_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                super().end_headers()

            def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def read_payload(self) -> tuple[dict[str, Any] | None, str | None]:
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    return None, "invalid Content-Length"
                if length > 4096:
                    return None, "request too large"
                raw = self.rfile.read(max(0, length)).decode("utf-8", errors="replace")
                if not raw.strip():
                    return {}, None
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return None, f"invalid JSON: {exc}"
                if not isinstance(parsed, dict):
                    return None, "JSON body must be an object"
                return parsed, None

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.end_headers()

            def do_GET(self) -> None:
                path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
                if path == "/api/esp32/status":
                    self.send_json(200, esp32_status_payload(owner.manager))
                    return
                self.send_json(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:
                path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
                if path != "/api/esp32/control":
                    self.send_json(404, {"ok": False, "error": "not found"})
                    return
                payload, error = self.read_payload()
                if error:
                    self.send_json(400, {"ok": False, "error": error})
                    return
                assert payload is not None
                result = owner.handle_control(payload)
                self.send_json(200 if result.get("ok") or result.get("queued") else 503, result)

        class ReusableThreadingHTTPServer(ThreadingHTTPServer):
            allow_reuse_address = True

        try:
            self._server = ReusableThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            print(f"WARNING: ESP32 dashboard control could not listen on {self.host}:{self.port}: {exc}")
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, name="esp32-dashboard-http", daemon=True)
        self._thread.start()
        print(f"ESP32 dashboard control: http://{self.host}:{self.port}/api/esp32/status")
        return True

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def handle_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.manager.is_enabled():
            return {"ok": False, "error": "ESP32 BLE is disabled"}
        if esp32_ble is None:
            return {"ok": False, "error": f"ESP32 BLE helper unavailable: {ESP32_BLE_IMPORT_ERROR}"}
        commands = self.commands_from_payload(payload)
        if not commands:
            return {"ok": False, "error": "no ESP32 command resolved", "request": payload}
        result = self.manager.send_commands(commands, source=str(payload.get("source") or "dashboard_http"))
        return {**result, "ok": bool(result.get("ok")), "request": payload, "status": esp32_status_payload(self.manager)}

    def commands_from_payload(self, payload: dict[str, Any]) -> list[str]:
        raw_commands = payload.get("commands")
        if isinstance(raw_commands, list):
            return [str(command).strip() for command in raw_commands if str(command).strip()]
        if payload.get("command"):
            return [str(payload.get("command")).strip()]
        if payload.get("text"):
            resolved = esp32_ble.resolve_input_to_ble_commands(  # type: ignore[union-attr]
                str(payload.get("text") or ""),
                self.manager.latest_status(),
                speed_step=max(1, int(getattr(self.args, "esp32_ble_voice_speed_step", 32) or 32)),
            )
            return resolved or []

        device_id = str(payload.get("device_id") or "").strip().lower()
        device_type = str(payload.get("type") or "").strip().lower()
        state = str(payload.get("state") or "").strip().lower()
        value = payload.get("value")
        is_light = device_type == "light" or device_id in {"living_light", "led", "led_light"}
        is_fan = device_type == "fan" or device_id in {"desk_fan", "fan"}
        if is_light:
            return ["LED_OFF"] if state == "off" else ["LED_ON"]
        if is_fan:
            percent = clamp_int(value if value is not None else (100 if state != "off" else 0), 0, 100)
            if state == "off" or percent <= 0:
                return ["FAN_OFF"]
            return ["FAN_ON", f"FAN_SPEED:{esp32_ble.percent_to_pwm(percent)}"]  # type: ignore[union-attr]
        return []


def _weather_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def format_weather_uart_payload(weather_result: dict[str, Any], *, local_temperature_c: float | None = None) -> str | None:
    """Return compact FRDM payload: kind,low_or_temp,high_or_temp,pop,weather_code[,local_temp_c_x10]."""
    if not weather_result.get("ok") or not weather_result.get("handled", weather_result.get("ok")):
        return None
    summary = weather_result.get("weather")
    if not isinstance(summary, dict):
        return None
    kind = str(summary.get("kind", "current") or "current").strip().lower()
    if kind not in {"current", "hourly", "daily"}:
        kind = "current"

    if kind == "daily":
        low = _weather_int(summary.get("temperature_min_c"))
        high = _weather_int(summary.get("temperature_max_c"), low)
        pop = _weather_int(summary.get("precipitation_probability_max"))
    else:
        temp = _weather_int(summary.get("temperature_c"))
        low = temp
        high = temp
        pop = _weather_int(summary.get("precipitation_probability"))
    code = _weather_int(summary.get("weather_code"), -1)
    pop = clamp_int(pop, 0, 100)
    code = clamp_int(code, -1, 999)
    payload = f"{kind},{low},{high},{pop},{code}"
    if local_temperature_c is None:
        local_temperature_c = weather_result.get("local_temperature_c")  # type: ignore[assignment]
    temp_field = format_temperature_uart_field(local_temperature_c)
    if temp_field is not None:
        payload = f"{payload},{temp_field}"
    return payload


def send_weather_uart_update(
    args: argparse.Namespace,
    robot: RobotUartController,
    weather_result: dict[str, Any],
    *,
    reason: str,
) -> str | None:
    if getattr(args, "no_uart", False):
        return None
    reading = attach_local_temperature_to_weather_result(args, weather_result)
    payload = format_weather_uart_payload(weather_result)
    if not payload:
        return None
    ok = robot.send_uart_raw_line(f"Weather {payload}", reason=reason, read_ms=100)
    if ok:
        if reading is not None:
            print(f"Weather UART sent: Weather {payload} (local={float(reading['temperature_c']):.1f} C)")
        else:
            print(f"Weather UART sent: Weather {payload} (local temperature unavailable)")
    else:
        print("WARNING: Weather UART was not sent.")
    return payload


def format_time_uart_payload(current: datetime | None = None) -> str:
    """Return compact FRDM payload: yyyymmdd,hhmmss,isoweekday,utc_offset_min."""
    local_now = current if current is not None else datetime.now().astimezone()
    if local_now.tzinfo is None:
        local_now = local_now.astimezone()
    offset = local_now.utcoffset()
    offset_min = int(offset.total_seconds() // 60) if offset is not None else 0
    return f"{local_now:%Y%m%d},{local_now:%H%M%S},{local_now.isoweekday()},{offset_min:+d}"


def send_startup_time_update(args: argparse.Namespace, robot: RobotUartController) -> str | None:
    if getattr(args, "no_startup_time", False):
        print("Startup time UART update skipped.")
        return None
    if getattr(args, "no_uart", False):
        return None

    payload = format_time_uart_payload()
    ok = robot.send_uart_raw_line(f"Time {payload}", reason="startup time update", read_ms=100)
    if ok:
        print(f"Startup Time UART sent: Time {payload}")
    else:
        print("WARNING: Startup Time UART was not sent.")
    return payload


def dashboard_field(value: Any, *, max_chars: int = 40, max_encoded_chars: int = 72) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\r\n,]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    encoded = urllib.parse.quote(text, safe="-_.~")
    while len(encoded) > max_encoded_chars and text:
        text = text[:-1]
        encoded = urllib.parse.quote(text, safe="-_.~")
    return encoded


def todo_uart_counts(todo_manager: TodoListManager | None) -> tuple[int, int]:
    if todo_manager is None or not todo_manager.is_enabled():
        return 0, 0
    data = todo_manager._read_data()
    return len(todo_manager.open_items(data)), len(todo_manager.done_items(data))


def format_todo_uart_payload(todo_manager: TodoListManager | None) -> str:
    open_count, done_count = todo_uart_counts(todo_manager)
    return f"{open_count},{done_count}"


def todo_uart_detail_lines(todo_manager: TodoListManager | None, *, limit: int) -> list[str]:
    if todo_manager is None or not todo_manager.is_enabled():
        return ["TodoEnd 0"]
    data = todo_manager._read_data()
    open_items = todo_manager.open_items(data)
    visible = open_items[: max(0, int(limit))]
    lines: list[str] = []
    for slot, item in enumerate(visible, start=1):
        try:
            item_id = int(item.get("id", 0) or 0)
        except (TypeError, ValueError):
            item_id = 0
        if item_id <= 0:
            continue
        text = dashboard_field(item.get("text", ""), max_chars=28, max_encoded_chars=72)
        lines.append(f"TodoItem {slot},{item_id},open,{text}")
    lines.append(f"TodoEnd {len(lines)}")
    return lines


def send_todo_uart_update(
    args: argparse.Namespace,
    robot: RobotUartController,
    todo_manager: TodoListManager | None,
    *,
    reason: str,
    include_items: bool = True,
) -> str | None:
    if getattr(args, "no_dashboard_uart", False) or getattr(args, "no_uart", False):
        return None
    payload = format_todo_uart_payload(todo_manager)
    ok = robot.send_uart_raw_line(f"Todo {payload}", reason=reason, read_ms=80)
    if ok:
        print(f"Dashboard Todo UART sent: Todo {payload}")
    else:
        print("WARNING: Dashboard Todo UART was not sent.")
    if include_items:
        limit = int(getattr(args, "dashboard_todo_item_limit", 8) or 8)
        for line in todo_uart_detail_lines(todo_manager, limit=limit):
            robot.send_uart_raw_line(line, reason=f"{reason} items", read_ms=60)
    return payload


def format_music_uart_payload(data: dict[str, Any] | None, args: argparse.Namespace) -> str:
    info = data if isinstance(data, dict) else {}
    action = str(info.get("action", "") or "").strip().lower()
    ok = bool(info.get("ok", False))
    active = bool(info.get("active", False))
    paused = bool(info.get("paused", False))
    stopped = bool(info.get("stopped", False))
    resumed = bool(info.get("resumed", False))

    if action == "play" and ok:
        state = "playing"
    elif action == "resume" and (resumed or ok):
        state = "playing"
    elif action == "pause" and paused:
        state = "paused"
    elif action == "stop" or stopped:
        state = "stopped"
    elif active and paused:
        state = "paused"
    elif active:
        state = "playing"
    elif info.get("ok") is False and info.get("error"):
        state = "offline"
    else:
        state = "stopped"

    title = info.get("query") or info.get("last_query") or ""
    backend = info.get("backend") or info.get("last_backend") or resolve_music_backend(args)
    return f"{state},{dashboard_field(title)},{dashboard_field(backend, max_chars=16)}"


def get_music_health(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "no_music", False):
        return None
    try:
        return voice_chat.get_json(music_health_url(args.music_url), timeout_sec=0.5)
    except Exception as exc:
        if getattr(args, "music_debug", False):
            print(f"Music dashboard health unavailable: {exc}")
        return {"ok": False, "error": str(exc), "backend": resolve_music_backend(args)}


def send_music_uart_update(
    args: argparse.Namespace,
    robot: RobotUartController,
    data: dict[str, Any] | None,
    *,
    reason: str,
) -> str | None:
    if getattr(args, "no_dashboard_uart", False) or getattr(args, "no_uart", False):
        return None
    payload = format_music_uart_payload(data, args)
    ok = robot.send_uart_raw_line(f"Music {payload}", reason=reason, read_ms=80)
    if ok:
        print(f"Dashboard Music UART sent: Music {payload}")
    else:
        print("WARNING: Dashboard Music UART was not sent.")
    return payload


def format_focus_uart_payload(state: str, remaining_min: int | float = 0, streak: int = 0) -> str:
    normalized = str(state or "idle").strip().lower()
    if not re.fullmatch(r"[a-z_]{1,20}", normalized):
        normalized = "idle"
    try:
        remaining = int(round(float(remaining_min)))
    except (TypeError, ValueError):
        remaining = 0
    try:
        streak_count = int(streak)
    except (TypeError, ValueError):
        streak_count = 0
    return f"{normalized},{clamp_int(remaining, 0, 999)},{clamp_int(streak_count, 0, 999)}"


def send_focus_uart_update(
    args: argparse.Namespace,
    robot: RobotUartController,
    *,
    state: str,
    remaining_min: int | float = 0,
    streak: int = 0,
    reason: str,
) -> str | None:
    if getattr(args, "no_dashboard_uart", False) or getattr(args, "no_uart", False):
        return None
    payload = format_focus_uart_payload(state, remaining_min, streak)
    ok = robot.send_uart_raw_line(f"Focus {payload}", reason=reason, read_ms=80)
    if ok:
        print(f"Dashboard Focus UART sent: Focus {payload}")
    else:
        print("WARNING: Dashboard Focus UART was not sent.")
    return payload


def _health_bit(ok: bool) -> int:
    return 1 if ok else 0


def _probe_json_ok(url: str, *, timeout_sec: float = 0.6) -> bool:
    try:
        return bool(voice_chat.get_json(url, timeout_sec=timeout_sec).get("ok"))
    except Exception:
        return False


def format_health_uart_payload(args: argparse.Namespace, camera_manager: CameraManager | None) -> str:
    win_ok = _probe_json_ok(voice_chat.endpoint_url(args.server_url, "/health"))
    tts_ok = _probe_json_ok(urllib.parse.urljoin(voice_chat.tts_base_url(args.tts_url) + "/", "health"))
    music_info = get_music_health(args)
    music_ok = bool(music_info and music_info.get("ok"))
    camera_ok = bool(camera_manager is not None and getattr(camera_manager, "executor", None) is not None)
    return (
        f"win={_health_bit(win_ok)},"
        f"tts={_health_bit(tts_ok)},"
        f"music={_health_bit(music_ok)},"
        f"camera={_health_bit(camera_ok)}"
    )


def send_health_uart_update(
    args: argparse.Namespace,
    robot: RobotUartController,
    camera_manager: CameraManager | None,
    *,
    reason: str,
) -> str | None:
    if getattr(args, "no_dashboard_uart", False) or getattr(args, "no_uart", False):
        return None
    payload = format_health_uart_payload(args, camera_manager)
    ok = robot.send_uart_raw_line(f"Health {payload}", reason=reason, read_ms=80)
    if ok:
        print(f"Dashboard Health UART sent: Health {payload}")
    else:
        print("WARNING: Dashboard Health UART was not sent.")
    return payload


def send_startup_dashboard_updates(
    args: argparse.Namespace,
    robot: RobotUartController,
    *,
    todo_manager: TodoListManager | None,
    camera_manager: CameraManager | None,
) -> None:
    if getattr(args, "no_dashboard_uart", False):
        print("Dashboard UART updates skipped.")
        return
    if getattr(args, "no_uart", False):
        return
    send_todo_uart_update(args, robot, todo_manager, reason="startup dashboard todo")
    send_music_uart_update(args, robot, get_music_health(args), reason="startup dashboard music")
    send_focus_uart_update(args, robot, state="idle", remaining_min=0, streak=0, reason="startup dashboard focus")
    send_health_uart_update(args, robot, camera_manager, reason="startup dashboard health")


def _send_startup_weather_text(
    args: argparse.Namespace,
    robot: RobotUartController,
    *,
    text: str,
    label: str,
) -> dict[str, Any] | None:
    response: dict[str, Any] = {"transcript": text}
    route = detect_weather_route(response, args)
    if not route.get("should_call"):
        route["should_call"] = True
        route["intent"] = True
        route["action"] = "weather"
        route["location"] = route.get("location") or getattr(args, "weather_default_location", DEFAULT_WEATHER_LOCATION)
        route["reason"] = f"startup_weather_{label}"

    result = execute_weather_route(route, args, response, phase=f"startup_{label}")
    if not result:
        print(f"Startup weather {label} UART update skipped: no weather result.")
        return None
    payload = send_weather_uart_update(args, robot, result, reason=f"startup weather {label} update")
    if not payload:
        print(f"Startup weather {label} UART update skipped: weather result did not contain compact numeric data.")
        return result
    return result


def send_startup_weather_update(args: argparse.Namespace, robot: RobotUartController) -> dict[str, Any] | None:
    if getattr(args, "no_weather", False) or getattr(args, "no_startup_weather", False):
        print("Startup weather UART update skipped.")
        return None
    if getattr(args, "no_uart", False):
        return None

    daily_text = str(getattr(args, "startup_weather_text", "") or "今天天氣如何").strip()
    current_text = str(getattr(args, "startup_weather_current_text", "") or "現在天氣如何").strip()
    daily_result = _send_startup_weather_text(args, robot, text=daily_text, label="daily")
    current_result = _send_startup_weather_text(args, robot, text=current_text, label="current")
    if daily_result is None and current_result is None:
        return None
    return {"daily": daily_result, "current": current_result}


@dataclass
class UartTxRequest:
    wire: str
    reason: str = ""
    read_ms: int = 0
    rx_lines: list[str] = field(default_factory=list)
    ok: bool = False
    cancelled: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    finish_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.wire = str(self.wire or "").strip()
        self.reason = str(self.reason or "")
        self.read_ms = max(0, int(self.read_ms or 0))

    def finish(self, ok: bool) -> None:
        with self.finish_lock:
            if self.done.is_set():
                return
            self.ok = bool(ok)
            self.done.set()


def frdm_uart_events_active(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "_frdm_uart_bus_active", False))


class FrdmUartEventRouter:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.handlers: list[tuple[str, Any]] = []

    def add_handler(self, name: str, handler: Any) -> None:
        self.handlers.append((name, handler))

    def handle_line(self, line: str) -> bool:
        text = str(line or "").strip()
        if not text:
            return False
        handled = False
        for name, handler in list(self.handlers):
            try:
                handled = bool(handler(text)) or handled
            except Exception as exc:
                print(f"WARNING: FRDM UART event handler {name} failed for {text!r}: {exc}")
        return handled


class FrdmUartBus:
    """Single owner for FRDM serial I/O.

    The old implementation opened the same USB CDC port for every TX and had a
    short polling reader for TodoDone. That could miss FRDM touch events during
    Speaking/head-motion sequences. This bus keeps one serial handle open, sends
    outbound lines through a queue, and dispatches inbound lines continuously.
    """

    def __init__(self, args: argparse.Namespace, *, line_handler: Any | None = None) -> None:
        self.args = args
        self.line_handler = line_handler
        self.stop_event = threading.Event()
        self.tx_queue: queue.Queue[UartTxRequest] = queue.Queue()
        self.rx_queue: queue.Queue[str] = queue.Queue()
        self.thread: threading.Thread | None = None
        self.dispatch_thread: threading.Thread | None = None
        self._cached_port = ""
        self._last_open_log = ""
        self._health_lock = threading.Lock()
        self._tx_failure_count = 0
        self._disabled_until = 0.0
        self._disable_notice_last = 0.0
        self._last_error = ""
        self._connected = False
        self._waiting_notice_last = 0.0
        self._waiting_notice_detail = ""

    def is_enabled(self) -> bool:
        if getattr(self.args, "no_frdm_uart_bus", False):
            return False
        if getattr(self.args, "no_uart", False) or getattr(self.args, "uart_dry_run", False):
            return False
        return True

    def start(self) -> bool:
        if not self.is_enabled():
            if getattr(self.args, "no_uart", False):
                print("FRDM UART event bus: disabled because live UART is off.")
            elif getattr(self.args, "uart_dry_run", False):
                print("FRDM UART event bus: disabled in UART dry-run mode.")
            else:
                print("FRDM UART event bus: disabled; using legacy per-command UART writes.")
            return False
        if self.thread is not None and self.thread.is_alive():
            return True
        try:
            import serial  # noqa: F401
        except ImportError as exc:
            print(f"WARNING: FRDM UART event bus disabled: pyserial missing: {exc}")
            return False
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._serial_loop, name="frdm_uart_bus_serial", daemon=True)
        self.dispatch_thread = threading.Thread(target=self._dispatch_loop, name="frdm_uart_bus_dispatch", daemon=True)
        self.thread.start()
        self.dispatch_thread.start()
        print("FRDM UART event bus: enabled; one serial owner handles TX queue + continuous RX.")
        return True

    def stop(self) -> None:
        self.stop_event.set()
        self._cancel_pending_tx("stop")
        for thread in (self.thread, self.dispatch_thread):
            if thread is not None and thread.is_alive():
                try:
                    thread.join(timeout=1.5)
                except KeyboardInterrupt:
                    print("FRDM UART bus cleanup interrupted; continuing shutdown.")
        self.thread = None
        self.dispatch_thread = None

    def _configured_tx_timeout(self, read_ms: int = 0) -> float:
        base = float(getattr(self.args, "frdm_uart_tx_timeout", 0.45) or 0.45)
        return max(0.05, min(5.0, base + max(0, int(read_ms or 0)) / 1000.0))

    def _disabled_remaining(self) -> float:
        with self._health_lock:
            return max(0.0, self._disabled_until - time.monotonic())

    def is_connected(self) -> bool:
        with self._health_lock:
            return self._connected

    def _set_connected(self, connected: bool) -> None:
        with self._health_lock:
            self._connected = bool(connected)

    def _note_tx_success(self) -> None:
        with self._health_lock:
            self._tx_failure_count = 0
            self._last_error = ""

    def _note_tx_failure(self, detail: str) -> None:
        threshold = max(1, int(getattr(self.args, "frdm_uart_failure_threshold", 2) or 2))
        disable_sec = max(0.0, float(getattr(self.args, "frdm_uart_circuit_breaker_sec", 4.0) or 0.0))
        should_cancel = False
        with self._health_lock:
            self._tx_failure_count += 1
            self._last_error = str(detail or "unknown")
            if self._tx_failure_count >= threshold and disable_sec > 0.0:
                self._disabled_until = max(self._disabled_until, time.monotonic() + disable_sec)
                self._tx_failure_count = 0
                should_cancel = True
                print(
                    "WARNING: FRDM UART bus temporarily bypassing TX for "
                    f"{disable_sec:.1f}s after repeated failures ({self._last_error}). "
                    "RX monitoring remains active."
                )
        if should_cancel:
            self._cancel_pending_tx("UART bus failure")

    def _cancel_pending_tx(self, reason: str) -> None:
        while True:
            try:
                request = self.tx_queue.get_nowait()
            except queue.Empty:
                return
            request.cancelled = True
            request.finish(False)
            self.tx_queue.task_done()
            if getattr(self.args, "uart_debug", False):
                print(f"FRDM UART bus cancelled TX during {reason}: {request.wire}")

    def _waiting_detail(self) -> str:
        requested = str(getattr(self.args, "uart_port", "auto") or "auto").strip()
        if auto_uart_requested(self.args):
            if discover_demo_uart_paths() or self.is_connected():
                return ""
            return f"no visible {UART_DEVICE_DESCRIPTION}"
        if requested.startswith("/dev/") and not Path(requested).exists() and not self.is_connected():
            return f"{requested} is not present"
        return ""

    def _notice_waiting_for_device(self, detail: str) -> None:
        now = time.monotonic()
        detail = str(detail or "UART device unavailable").strip()
        if detail == self._waiting_notice_detail and now - self._waiting_notice_last < 15.0:
            return
        self._waiting_notice_detail = detail
        self._waiting_notice_last = now
        print(f"FRDM UART bus waiting for device: {detail}. TX is paused until it appears.")

    def send_line(
        self,
        wire: str,
        *,
        reason: str = "",
        read_ms: int = 0,
        timeout_sec: float | None = None,
    ) -> tuple[bool, list[str]]:
        if not self.is_enabled():
            return False, []
        if self.stop_event.is_set():
            return False, []
        waiting_detail = self._waiting_detail()
        if waiting_detail:
            self._notice_waiting_for_device(waiting_detail)
            return False, []
        disabled_remaining = self._disabled_remaining()
        if disabled_remaining > 0.0:
            now = time.monotonic()
            if getattr(self.args, "uart_debug", False) or now - self._disable_notice_last >= 1.0:
                print(
                    "WARNING: FRDM UART bus TX bypassed while recovering "
                    f"({disabled_remaining:.1f}s left): {str(wire or '').strip()}"
                )
                self._disable_notice_last = now
            return False, []
        request = UartTxRequest(wire, reason=reason, read_ms=read_ms)
        if not request.wire:
            return False, []
        self.tx_queue.put(request)
        wait_timeout = timeout_sec
        if wait_timeout is None:
            wait_timeout = self._configured_tx_timeout(request.read_ms)
        if not self.is_connected() and not self._waiting_detail():
            reconnect_sec = max(0.1, min(5.0, float(getattr(self.args, "frdm_uart_reconnect_sec", 1.0) or 1.0)))
            wait_timeout = max(wait_timeout, reconnect_sec + self._configured_tx_timeout(request.read_ms))
        if not request.done.wait(max(0.1, wait_timeout)):
            request.cancelled = True
            request.finish(False)
            self._note_tx_failure("tx timeout")
            print(f"WARNING: FRDM UART bus TX timed out: {request.wire}")
            return False, list(request.rx_lines)
        return request.ok, list(request.rx_lines)

    def _resolve_port(self) -> str | None:
        requested = str(getattr(self.args, "uart_port", "auto") or "auto")
        if self._cached_port and Path(self._cached_port).exists():
            return self._cached_port
        if auto_uart_requested(self.args) and not discover_demo_uart_paths():
            return None
        self._cached_port = bridge.resolve_uart_port(requested)
        return self._cached_port

    def _dispatch_line(self, line: str, request: UartTxRequest | None = None) -> None:
        text = str(line or "").strip()
        if not text:
            return
        if request is not None:
            request.rx_lines.append(text)
        self.rx_queue.put(text)

    def _read_available_lines(self, ser: Any, *, request: UartTxRequest | None = None, read_ms: int = 0) -> None:
        deadline = time.monotonic() + max(0.0, read_ms / 1000.0)
        while read_ms > 0 and time.monotonic() < deadline and not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            ser.timeout = max(0.001, min(0.02, remaining))
            raw = ser.readline()
            if not raw:
                continue
            self._dispatch_line(raw.decode("utf-8", errors="replace"), request=request)

    def _serial_loop(self) -> None:
        try:
            import serial
        except ImportError as exc:
            print(f"WARNING: FRDM UART event bus disabled: pyserial missing: {exc}")
            return

        ser: Any | None = None
        current_request: UartTxRequest | None = None
        reconnect_sec = max(0.1, min(5.0, float(getattr(self.args, "frdm_uart_reconnect_sec", 1.0) or 1.0)))
        while not self.stop_event.is_set():
            try:
                if ser is None:
                    port = self._resolve_port()
                    if not port:
                        self._set_connected(False)
                        self._notice_waiting_for_device(f"no visible {UART_DEVICE_DESCRIPTION}")
                        self.stop_event.wait(reconnect_sec)
                        continue
                    ser = serial.Serial(
                        port=port,
                        baudrate=getattr(self.args, "uart_baudrate", 115200),
                        timeout=0.02,
                        write_timeout=min(float(getattr(self.args, "uart_timeout", 0.2) or 0.2), 0.2),
                    )
                    time.sleep(0.04)
                    try:
                        ser.reset_input_buffer()
                    except Exception:
                        pass
                    try:
                        ser.reset_output_buffer()
                    except Exception:
                        pass
                    if port != self._last_open_log:
                        print(f"FRDM UART bus opened {port}.")
                        if getattr(self.args, "_frdm_uart_startup_missing", False):
                            print("FRDM UART auto-recovery: device is back; future UART commands will be sent.")
                        self._last_open_log = port
                    self._set_connected(True)
                    setattr(self.args, "_frdm_uart_startup_missing", False)

                try:
                    current_request = self.tx_queue.get(timeout=0.02)
                except queue.Empty:
                    current_request = None
                    ser.timeout = 0.02
                    raw = ser.readline()
                    if raw:
                        self._dispatch_line(raw.decode("utf-8", errors="replace"))
                    continue

                if current_request.cancelled:
                    current_request.finish(False)
                    self.tx_queue.task_done()
                    current_request = None
                    continue
                ser.write(current_request.wire.encode("utf-8") + bridge.line_ending_bytes(getattr(self.args, "uart_line_ending", "crlf")))
                print(f"FRDM UART TX: {current_request.wire}" + (f" ({current_request.reason})" if current_request.reason else ""))
                self._read_available_lines(ser, request=current_request, read_ms=current_request.read_ms)
                self._note_tx_success()
                current_request.finish(True)
                self.tx_queue.task_done()
                current_request = None
            except Exception as exc:
                request_cancelled = current_request.cancelled if current_request is not None else False
                if ser is not None:
                    try:
                        ser.reset_output_buffer()
                    except Exception:
                        pass
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                self._set_connected(False)
                self._cached_port = ""
                if current_request is not None and not current_request.done.is_set():
                    current_request.finish(False)
                    try:
                        self.tx_queue.task_done()
                    except ValueError:
                        pass
                    current_request = None
                if not request_cancelled:
                    self._note_tx_failure(str(exc))
                if getattr(self.args, "uart_debug", False):
                    print(f"FRDM UART bus reconnect after error: {exc}")
                self.stop_event.wait(reconnect_sec)

        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._set_connected(False)

    def _dispatch_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                line = self.rx_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if getattr(self.args, "uart_debug", False):
                print(f"FRDM UART RX: {line}")
            if self.line_handler is not None:
                try:
                    self.line_handler(line)
                except Exception as exc:
                    print(f"WARNING: FRDM UART event dispatch failed for {line!r}: {exc}")


class RobotUartController:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.persistent_state = "normal"
        self._lock = threading.RLock()
        self._motor_safety_lockout_reason = ""
        self._last_motor_step: MotorStep | None = None
        self._active_speaking_thread: threading.Thread | None = None
        self._active_speaking_stop: threading.Event | None = None
        self._screen_state = ""
        self._screen_state_at = 0.0
        self.uart_bus: FrdmUartBus | None = None

    def attach_uart_bus(self, uart_bus: FrdmUartBus | None) -> None:
        self.uart_bus = uart_bus

    def _active_uart_bus(self) -> FrdmUartBus | None:
        bus = self.uart_bus
        if bus is None or not bus.is_enabled():
            return None
        return bus

    def motor_step_delay(self) -> float:
        requested = float(getattr(self.args, "motor_step_delay", MOTOR_STEP_DELAY_SEC) or MOTOR_STEP_DELAY_SEC)
        live_floor = 0.0 if getattr(self.args, "uart_dry_run", False) else MOTOR_LIVE_MIN_STEP_DELAY_SEC
        return max(live_floor, requested)

    def motor_smooth_step_deg(self) -> int:
        return max(1, min(120, int(getattr(self.args, "motor_smooth_step_deg", MOTOR_SMOOTH_STEP_DEG) or MOTOR_SMOOTH_STEP_DEG)))

    def motor_speaking_step_delay(self) -> float:
        requested = float(getattr(self.args, "motor_speaking_step_delay", MOTOR_SPEAKING_STEP_DELAY_SEC) or MOTOR_SPEAKING_STEP_DELAY_SEC)
        live_floor = 0.0 if getattr(self.args, "uart_dry_run", False) else 0.12
        return max(live_floor, requested)

    def motor_speaking_smooth_step_deg(self) -> int:
        return max(
            1,
            min(
                120,
                int(getattr(self.args, "motor_speaking_smooth_step_deg", MOTOR_SPEAKING_SMOOTH_STEP_DEG) or MOTOR_SPEAKING_SMOOTH_STEP_DEG),
            ),
        )

    def motor_stop_timeout(self) -> float:
        return max(0.5, min(8.0, float(getattr(self.args, "motor_stop_timeout", MOTOR_STOP_TIMEOUT_SEC) or MOTOR_STOP_TIMEOUT_SEC)))

    def motor_reset_delay(self) -> float:
        requested = float(getattr(self.args, "motor_reset_delay", MOTOR_RESET_DELAY_SEC) or MOTOR_RESET_DELAY_SEC)
        live_floor = 0.0 if getattr(self.args, "uart_dry_run", False) else MOTOR_LIVE_MIN_RESET_DELAY_SEC
        return max(live_floor, requested)

    def motor_reset_repeats(self) -> int:
        return max(1, min(8, int(getattr(self.args, "motor_reset_repeats", MOTOR_RESET_REPEATS) or MOTOR_RESET_REPEATS)))

    def motor_read_ms(self) -> int:
        return max(0, min(120, int(getattr(self.args, "motor_read_ms", MOTOR_READ_MS) or MOTOR_READ_MS)))

    def head_motor_enabled(self) -> bool:
        if getattr(self.args, "uart_dry_run", False):
            return True
        if getattr(self.args, "no_uart", False):
            return False
        if getattr(self.args, "disable_head_motor", False):
            return False
        return bool(getattr(self.args, "enable_head_motor", False))

    def head_motor_disabled_reason(self) -> str:
        if self.head_motor_enabled():
            return ""
        if getattr(self.args, "no_uart", False):
            return "live UART is disabled"
        if getattr(self.args, "disable_head_motor", False):
            return "--disable-head-motor is set"
        return "--enable-head-motor not set"

    def live_uart_enabled(self) -> bool:
        return not bool(getattr(self.args, "no_uart", False))

    def current_screen_state(self) -> str:
        return self._screen_state

    def _note_screen_state(self, state: str) -> None:
        self._screen_state = state
        self._screen_state_at = time.monotonic()

    def screen_state_age(self) -> float:
        if not self._screen_state_at:
            return 999999.0
        return max(0.0, time.monotonic() - self._screen_state_at)

    def is_screen_state_recent(self, state: str, within_sec: float = SCREEN_STATE_DEDUPE_SEC) -> bool:
        validated = self._validate_command(state, 0, 0)
        command = validated[0] if validated else str(state or "").strip()
        return command == self._screen_state and self.screen_state_age() <= max(0.0, within_sec)

    def _note_motor_step(self, command: str, v1: int, v2: int = 0) -> None:
        if command in MOTOR_COMMANDS:
            self._last_motor_step = clamp_motor_step(command, v1, v2)

    def head_is_centered(self) -> bool:
        return self._last_motor_step == yaw_pitch(YAW_CENTER, PITCH_CENTER)

    def _validate_command(self, command: str, v1: int = 0, v2: int = 0) -> tuple[str, int, int] | None:
        name = str(command or "").strip()
        aliases = {
            "sleep": "Sleep",
            "normal": "Normal",
            "thinking": "Thinking",
            "speaking": "Speaking",
            "music": "Music",
            "focus": "Focus",
            "shownum": "ShowNum",
            "show_num": "ShowNum",
            "time": "Time",
            "clock": "Time",
            "todo": "Todo",
            "todos": "Todo",
            "todoitem": "TodoItem",
            "todo_item": "TodoItem",
            "todoend": "TodoEnd",
            "todo_end": "TodoEnd",
            "health": "Health",
            "device": "Device",
            "temproom": "TempRoom",
            "roomtemp": "TempRoom",
            "roomtemperature": "TempRoom",
            "weather": "Weather",
            "weatherinfo": "Weather",
            "motorpitch": "MotorPitch",
            "pitch": "MotorPitch",
            "motoryaw": "MotorYaw",
            "yaw": "MotorYaw",
            "motoryawpitch": "MotorYawPitch",
            "yawpitch": "MotorYawPitch",
            "motorrollpitch": "MotorYawPitch",
            "rollpitch": "MotorYawPitch",
        }
        compact = re.sub(r"[\s_-]+", "", name).lower()
        name = aliases.get(compact, name)
        if name not in ALLOWED_UART_COMMANDS:
            print(f"WARNING: refusing unknown UART command {command!r}.")
            return None

        if name == "MotorPitch":
            v1 = clamp_motor_value(name, v1)
            v2 = 0
        elif name == "MotorYaw":
            v1 = clamp_motor_value(name, v1)
            v2 = 0
        elif name == "MotorYawPitch":
            _name, v1, v2 = clamp_motor_step(name, v1, v2)
        elif name == "ShowNum":
            v1 = clamp_int(v1, 0, 999999)
            v2 = clamp_int(v2, 0, 999999)
        elif name == "Speaking":
            v1 = clamp_int(v1, 0, 5)
            v2 = clamp_int(v2, 0, 999999)
        elif name == "Weather":
            v1 = clamp_int(v1, -999999, 999999)
            v2 = clamp_int(v2, -999999, 999999)
        elif name == "Time":
            v1 = clamp_int(v1, -999999, 999999)
            v2 = clamp_int(v2, -999999, 999999)
        elif name in DATA_COMMANDS:
            v1 = clamp_int(v1, -999999, 999999)
            v2 = clamp_int(v2, -999999, 999999)
        else:
            v1 = clamp_int(v1, -999999, 999999)
            v2 = clamp_int(v2, -999999, 999999)
        return name, v1, v2

    def _line_ending(self) -> bytes:
        return bridge.line_ending_bytes(getattr(self.args, "uart_line_ending", "crlf"))

    def _uart_read_config(self, read_ms: int | None) -> tuple[int, float, float, float]:
        safe_read_ms = min(int(read_ms if read_ms is not None else getattr(self.args, "uart_read_ms", 30)), 120)
        read_window_sec = max(0.0, safe_read_ms / 1000.0)
        configured_timeout = float(getattr(self.args, "uart_timeout", 0.2) or 0.2)
        per_read_timeout = max(0.005, min(configured_timeout, read_window_sec if read_window_sec > 0 else 0.005))
        return safe_read_ms, read_window_sec, configured_timeout, per_read_timeout

    def _uart_tx_timeout(self, configured_timeout: float, read_window_sec: float) -> float:
        base_timeout = float(getattr(self.args, "frdm_uart_tx_timeout", 0.45) or 0.45)
        return max(0.05, min(5.0, base_timeout + max(0.0, read_window_sec)))

    def _send_wire_via_bus(
        self,
        wire: str,
        *,
        reason: str,
        read_ms: int,
        configured_timeout: float,
        read_window_sec: float,
    ) -> tuple[bool, list[str]]:
        bus = self._active_uart_bus()
        if bus is None:
            return False, []
        return bus.send_line(
            wire,
            reason=reason,
            read_ms=read_ms,
            timeout_sec=self._uart_tx_timeout(configured_timeout, read_window_sec),
        )

    def _handle_motor_ack_problem(
        self,
        command: str,
        expected_value: int,
        expected_value2: int,
        rx_lines: list[str],
        stop_event: threading.Event | None = None,
    ) -> bool:
        problem = motor_ack_problem(command, expected_value, rx_lines, expected_value2)
        if not problem:
            return True
        self._motor_safety_lockout_reason = problem
        print(f"ERROR: {problem}")
        print(
            "ERROR: Disabling further head motor commands in this process. "
            "Fix the FRDM MotorControlPitch/MotorControlYaw/MotorControlYawPitch parser, then restart this bridge."
        )
        if stop_event is not None:
            stop_event.set()
        return False

    def send_uart_sequence(
        self,
        steps: list[tuple[str, int, int]],
        *,
        reason: str = "",
        delay_sec: float | list[float] | tuple[float, ...] = 0.0,
        read_ms: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> bool:
        valid_steps: list[tuple[str, int, int, str]] = []
        invalid_count = 0
        skipped_disabled_motor_count = 0
        skipped_lockout_motor_count = 0
        for command, v1, v2 in steps:
            validated = self._validate_command(command, v1, v2)
            if validated is None:
                invalid_count += 1
                continue
            name, safe_v1, safe_v2 = validated
            if name in MOTOR_COMMANDS and not getattr(self.args, "no_uart", False) and not self.head_motor_enabled():
                skipped_disabled_motor_count += 1
                wire = format_uart_wire_command(name, safe_v1, safe_v2)
                print(f"FRDM UART motor skipped ({self.head_motor_disabled_reason()}): {wire}")
                continue
            if name in MOTOR_COMMANDS and self._motor_safety_lockout_reason:
                skipped_lockout_motor_count += 1
                wire = format_uart_wire_command(name, safe_v1, safe_v2)
                print(f"FRDM UART motor skipped (safety lockout): {wire}; {self._motor_safety_lockout_reason}")
                continue
            valid_steps.append((name, safe_v1, safe_v2, format_uart_wire_command(name, safe_v1, safe_v2)))

        if not valid_steps:
            return invalid_count == 0 and skipped_lockout_motor_count == 0
        if getattr(self.args, "no_uart", False):
            return False

        read_ms, read_window_sec, configured_timeout, per_read_timeout = self._uart_read_config(read_ms)

        def step_delay_at(index: int) -> float:
            if isinstance(delay_sec, (list, tuple)):
                if not delay_sec:
                    return 0.0
                safe_index = min(index, len(delay_sec) - 1)
                return max(0.0, float(delay_sec[safe_index] or 0.0))
            return max(0.0, float(delay_sec or 0.0))

        bus = self._active_uart_bus()
        if bus is not None:
            all_ok = True
            for index, (name, safe_v1, safe_v2, wire) in enumerate(valid_steps):
                if stop_event is not None and stop_event.is_set():
                    break
                ok, rx_lines = self._send_wire_via_bus(
                    wire,
                    reason=reason,
                    read_ms=read_ms,
                    configured_timeout=configured_timeout,
                    read_window_sec=read_window_sec,
                )
                if not ok:
                    all_ok = False
                    if getattr(self.args, "require_uart", False):
                        return False
                    if name in MOTOR_COMMANDS:
                        break
                if not self._handle_motor_ack_problem(name, safe_v1, safe_v2, rx_lines, stop_event):
                    return False
                if ok:
                    self._note_motor_step(name, safe_v1, safe_v2)
                current_delay = step_delay_at(index)
                if current_delay > 0 and not sleep_interruptible(current_delay, stop_event):
                    break
            return all_ok

        try:
            with self._lock:
                if getattr(self.args, "uart_dry_run", False):
                    for index, (_name, _v1, _v2, wire) in enumerate(valid_steps):
                        if stop_event is not None and stop_event.is_set():
                            break
                        print(f"FRDM UART dry-run TX: {wire}" + (f" ({reason})" if reason else ""))
                        self._note_motor_step(_name, _v1, _v2)
                        current_delay = step_delay_at(index)
                        if current_delay > 0 and not sleep_interruptible(current_delay, stop_event):
                            break
                    return True

                try:
                    import serial
                except ImportError as exc:
                    raise RuntimeError("Missing dependency: pyserial. Install with: python -m pip install pyserial") from exc

                port = bridge.resolve_uart_port(getattr(self.args, "uart_port", "auto"))
                with serial.Serial(
                    port=port,
                    baudrate=getattr(self.args, "uart_baudrate", 115200),
                    timeout=per_read_timeout,
                    write_timeout=min(configured_timeout, 0.2),
                ) as ser:
                    time.sleep(0.04)
                    try:
                        ser.reset_input_buffer()
                    except Exception:
                        pass
                    for index, (_name, _v1, _v2, wire) in enumerate(valid_steps):
                        if stop_event is not None and stop_event.is_set():
                            break
                        ser.write(wire.encode("utf-8") + self._line_ending())
                        ser.flush()
                        print(f"FRDM UART TX: {wire}" + (f" ({reason})" if reason else ""))
                        rx_lines: list[str] = []
                        deadline = time.monotonic() + read_window_sec
                        while read_window_sec > 0 and time.monotonic() < deadline:
                            remaining = deadline - time.monotonic()
                            ser.timeout = max(0.001, min(per_read_timeout, remaining))
                            line = ser.readline()
                            if line:
                                rx_lines.append(line.decode("utf-8", errors="replace").rstrip())
                        if getattr(self.args, "uart_debug", False):
                            for line in rx_lines:
                                print(f"FRDM UART RX: {line}")
                        if not self._handle_motor_ack_problem(_name, _v1, _v2, rx_lines, stop_event):
                            return False
                        self._note_motor_step(_name, _v1, _v2)
                        current_delay = step_delay_at(index)
                        if current_delay > 0 and not sleep_interruptible(current_delay, stop_event):
                            break
            return True
        except Exception as exc:
            print(f"WARNING: UART error while sending {reason or valid_steps[-1][3]}: {exc}")
            return not getattr(self.args, "require_uart", False)

    def send_uart_command(self, command: str, v1: int = 0, v2: int = 0, *, reason: str = "", read_ms: int | None = None) -> bool:
        return self.send_uart_sequence([(command, v1, v2)], reason=reason, read_ms=read_ms)

    def send_uart_raw_line(self, line: str, *, reason: str = "", read_ms: int | None = None) -> bool:
        wire = str(line or "").strip()
        if not wire:
            print("WARNING: refusing empty UART raw line.")
            return False
        command = wire.split(maxsplit=1)[0]
        if self._validate_command(command, 0, 0) is None:
            print(f"WARNING: refusing unknown UART raw line {wire!r}.")
            return False
        if any(ch in wire for ch in "\r\n"):
            print(f"WARNING: refusing UART raw line with newline: {wire!r}.")
            return False
        if len(wire.encode("utf-8")) > 120:
            print(f"WARNING: refusing overlong UART raw line ({len(wire.encode('utf-8'))} bytes): {wire!r}.")
            return False
        if getattr(self.args, "no_uart", False):
            return False

        read_ms, read_window_sec, configured_timeout, per_read_timeout = self._uart_read_config(read_ms)
        bus = self._active_uart_bus()
        if bus is not None:
            ok, _rx_lines = self._send_wire_via_bus(
                wire,
                reason=reason,
                read_ms=read_ms,
                configured_timeout=configured_timeout,
                read_window_sec=read_window_sec,
            )
            return ok
        try:
            with self._lock:
                if getattr(self.args, "uart_dry_run", False):
                    print(f"FRDM UART dry-run TX: {wire}" + (f" ({reason})" if reason else ""))
                    return True

                try:
                    import serial
                except ImportError as exc:
                    raise RuntimeError("Missing dependency: pyserial. Install with: python -m pip install pyserial") from exc

                port = bridge.resolve_uart_port(getattr(self.args, "uart_port", "auto"))
                with serial.Serial(
                    port=port,
                    baudrate=getattr(self.args, "uart_baudrate", 115200),
                    timeout=per_read_timeout,
                    write_timeout=min(configured_timeout, 0.2),
                ) as ser:
                    time.sleep(0.04)
                    try:
                        ser.reset_input_buffer()
                    except Exception:
                        pass
                    ser.write(wire.encode("utf-8") + self._line_ending())
                    ser.flush()
                    print(f"FRDM UART TX: {wire}" + (f" ({reason})" if reason else ""))
                    rx_lines: list[str] = []
                    deadline = time.monotonic() + read_window_sec
                    while read_window_sec > 0 and time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        ser.timeout = max(0.001, min(per_read_timeout, remaining))
                        raw = ser.readline()
                        if raw:
                            rx_lines.append(raw.decode("utf-8", errors="replace").rstrip())
                    if getattr(self.args, "uart_debug", False):
                        for rx in rx_lines:
                            print(f"FRDM UART RX: {rx}")
            return True
        except Exception as exc:
            print(f"WARNING: UART error while sending {reason or wire}: {exc}")
            return not getattr(self.args, "require_uart", False)

    def reset_head_position(self, *, reason: str = "head_motion reset") -> bool:
        if not self.head_motor_enabled():
            if not getattr(self.args, "no_uart", False):
                print(f"head motion reset skipped ({self.head_motor_disabled_reason()}): {reason}")
            return True
        if self.head_is_centered():
            print(f"head motion reset skipped: already centered ({reason})")
            return True
        steps: list[tuple[str, int, int]] = []
        for _ in range(self.motor_reset_repeats()):
            steps.append(yaw_pitch(YAW_CENTER, PITCH_CENTER))
        ok = self.send_uart_sequence(
            steps,
            reason=reason,
            delay_sec=self.motor_reset_delay(),
            read_ms=self.motor_read_ms(),
        )
        if ok:
            print(
                "head motion reset sent: "
                f"repeats={self.motor_reset_repeats()}, delay={self.motor_reset_delay():.2f}s"
            )
        else:
            print("WARNING: head motion reset was not sent.")
        return ok

    def set_screen_state(self, state: str, *, reason: str = "", force: bool = False) -> bool:
        validated = self._validate_command(state, 0, 0)
        if validated is None or validated[0] not in CORE_SCREEN_COMMANDS:
            print(f"WARNING: refusing non-screen state {state!r}.")
            return False
        command = validated[0]
        if getattr(self.args, "no_uart", False):
            return False
        if (
            not force
            and command == self._screen_state
            and self.screen_state_age() <= SCREEN_STATE_DEDUPE_SEC
        ):
            print(
                f"FRDM UART screen skipped duplicate: {command} "
                f"(age={self.screen_state_age():.2f}s, reason={reason or 'screen state'})"
            )
            return True
        ok = self.send_uart_command(command, 0, 0, reason=reason or f"screen state {command}", read_ms=80)
        if ok:
            self._note_screen_state(command)
        return ok

    def set_screen_mode(self, mode: str, *, reason: str = "") -> bool:
        normalized = str(mode or "").strip().lower()
        command = SCREEN_MODE_TO_COMMAND.get(normalized)
        if command is None:
            print(f"WARNING: unknown screen mode {mode!r}; screen unchanged.")
            return False
        if normalized in {"normal", "sleep"}:
            self.set_persistent_state(normalized)
        ok = self.set_screen_state(command, reason=reason or f"screen mode {normalized}")
        if getattr(self.args, "no_uart", False):
            return False
        if ok:
            print(f"UART {command} sent ({reason or 'screen mode ' + normalized}).")
        else:
            print(f"WARNING: UART {command} not sent; FRDM UART is unavailable.")
        return ok

    def set_persistent_state(self, state: str) -> None:
        if state in {"normal", "sleep"}:
            if state != self.persistent_state:
                print(f"persistent_state: {self.persistent_state} -> {state}")
            self.persistent_state = state

    def restore_persistent_screen_state(self) -> bool:
        command = "Sleep" if self.persistent_state == "sleep" else "Normal"
        ok = self.send_uart_command(command, 0, 0, reason=f"restore persistent state {self.persistent_state}", read_ms=100)
        if getattr(self.args, "no_uart", False):
            return False
        if ok:
            self._note_screen_state(command)
            print(f"UART {command} sent (restore persistent_state={self.persistent_state}).")
        else:
            print(f"WARNING: UART {command} not sent; FRDM UART is unavailable.")
        return ok

    def send_emotion_screen(self, emotion: str) -> bool:
        return self.send_speaking_and_emotion(emotion)

    def send_speaking_and_emotion(self, emotion: str) -> bool:
        """Switch to Speaking and pass the FRDM 0..5 speaking emotion code."""
        normalized = normalize_emotion_name(emotion, default="neutral")
        code = speaking_code_for_emotion(normalized)
        ok = self.send_uart_command(
            "Speaking",
            code,
            0,
            reason=f"speaking emotion {normalized} code {code}",
            read_ms=80,
        )
        if getattr(self.args, "no_uart", False):
            return False
        if ok:
            self._note_screen_state("Speaking")
            print(f"UART Speaking sent with emotion={normalized}, code={code}.")
        else:
            print(f"WARNING: UART Speaking {code} not sent; FRDM UART is unavailable.")
        return ok

    def run_head_motion(self, head_motion: str) -> bool:
        motion = head_motion if head_motion in HEAD_MOTION_SEQUENCES else "none"
        if not self.head_motor_enabled():
            if not getattr(self.args, "no_uart", False):
                print(f"head motion skipped ({self.head_motor_disabled_reason()}): {motion}")
            return True
        keyframes = list(HEAD_MOTION_SEQUENCES.get(motion, HEAD_MOTION_SEQUENCES["none"]))
        sequence = smooth_motor_sequence(keyframes, self.motor_smooth_step_deg())
        delays = natural_motor_delays(sequence, self.motor_step_delay(), speaking=False)
        ok = False
        reset_ok = False
        try:
            print(
                f"head motion started: {motion} "
                f"(keyframes={len(keyframes)}, expanded_steps={len(sequence)}, "
                f"smooth_step={self.motor_smooth_step_deg()}deg, base_step_delay={self.motor_step_delay():.2f}s, "
                f"reset_repeats={self.motor_reset_repeats()})"
            )
            if getattr(self.args, "uart_debug", False):
                print(f"head motion keyframes: {format_motor_sequence(keyframes)}")
                print(f"head motion expanded: {format_motor_sequence(sequence)}")
                print(f"head motion delays: {' -> '.join(f'{delay:.2f}s' for delay in delays)}")
            ok = self.send_uart_sequence(
                sequence,
                reason=f"head_motion {motion}",
                delay_sec=delays,
                read_ms=self.motor_read_ms(),
            )
            if not ok:
                print(f"WARNING: head motion {motion} was not sent; FRDM head will not move.")
        except Exception as exc:
            print(f"WARNING: head motion failed: {exc}")
        finally:
            reset_ok = self.reset_head_position(reason="head_motion reset")
            print(f"head motion ended: {motion}")
        return ok and reset_ok

    def start_head_motion(self, head_motion: str) -> threading.Thread:
        motion = head_motion if head_motion in HEAD_MOTION_SEQUENCES else "none"
        if motion == "none":
            print("head motion skipped: none")
            thread = threading.Thread(target=lambda: None, name="head_motion_none_skipped", daemon=True)
            thread.start()
            return thread
        thread = threading.Thread(target=self.run_head_motion, args=(head_motion,), name=f"head_motion_{head_motion}", daemon=True)
        thread.start()
        return thread

    def run_speaking_head_motion(self, head_motion: str, stop_event: threading.Event) -> bool:
        motion = head_motion if head_motion in SPEAKING_HEAD_MOTION_LOOPS else "none"
        if motion == "none":
            print("speaking head motion skipped: none")
            return True

        keyframes = list(SPEAKING_HEAD_MOTION_LOOPS[motion])
        sequence = smooth_motor_sequence(keyframes, self.motor_speaking_smooth_step_deg())
        delays = natural_motor_delays(sequence, self.motor_speaking_step_delay(), speaking=True)
        cycle_count = 0
        all_ok = True
        try:
            print(
                f"speaking head motion loop started: {motion} "
                f"(keyframes={len(keyframes)}, expanded_steps={len(sequence)}, "
                f"smooth_step={self.motor_speaking_smooth_step_deg()}deg, "
                f"base_step_delay={self.motor_speaking_step_delay():.2f}s)"
            )
            if getattr(self.args, "uart_debug", False):
                print(f"speaking head motion keyframes: {format_motor_sequence(keyframes)}")
                print(f"speaking head motion expanded: {format_motor_sequence(sequence)}")
                print(f"speaking head motion delays: {' -> '.join(f'{delay:.2f}s' for delay in delays)}")

            while not stop_event.is_set():
                cycle_count += 1
                if getattr(self.args, "uart_debug", False):
                    print(f"speaking head motion cycle {cycle_count}: {motion}")
                ok = self.send_uart_sequence(
                    sequence,
                    reason=f"speaking_head_motion {motion} cycle={cycle_count}",
                    delay_sec=delays,
                    read_ms=self.motor_read_ms(),
                    stop_event=stop_event,
                )
                all_ok = ok and all_ok
                if not ok:
                    break
                if not sleep_interruptible(0.02, stop_event):
                    break
        except Exception as exc:
            print(f"WARNING: speaking head motion failed: {exc}")
            all_ok = False
        finally:
            self.reset_head_position(reason="speaking_head_motion reset")
            print(f"speaking head motion loop stopped: {motion} cycles={cycle_count}")
        return all_ok

    def start_speaking_head_motion(self, head_motion: str) -> tuple[threading.Thread | None, threading.Event | None]:
        motion = head_motion if head_motion in SPEAKING_HEAD_MOTION_LOOPS else "none"
        self.stop_active_speaking_head_motion(reason="before applying speaking head motion")
        if motion == "none":
            print("speaking head motion skipped: none")
            return None, None
        if not self.head_motor_enabled():
            if not getattr(self.args, "no_uart", False):
                print(f"speaking head motion skipped ({self.head_motor_disabled_reason()}): {motion}")
            return None, None
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self.run_speaking_head_motion,
            args=(motion, stop_event),
            name=f"speaking_head_motion_{motion}",
            daemon=True,
        )
        with self._lock:
            self._active_speaking_thread = thread
            self._active_speaking_stop = stop_event
        thread.start()
        return thread, stop_event

    def stop_speaking_head_motion(
        self,
        thread: threading.Thread | None,
        stop_event: threading.Event | None,
        *,
        reason: str = "speaking head motion stop",
    ) -> None:
        if thread is None or stop_event is None:
            return
        stop_event.set()
        if thread is threading.current_thread():
            with self._lock:
                if self._active_speaking_thread is thread:
                    self._active_speaking_thread = None
                    self._active_speaking_stop = None
            print(f"WARNING: speaking head motion stop requested from its own thread; stop event set ({reason}).")
            return
        thread.join(timeout=self.motor_stop_timeout())
        if thread.is_alive():
            print(f"WARNING: speaking head motion still running after stop timeout; sending center reset ({reason}).")
            self.reset_head_position(reason=reason)
        with self._lock:
            if self._active_speaking_thread is thread:
                self._active_speaking_thread = None
                self._active_speaking_stop = None

    def stop_active_speaking_head_motion(self, *, reason: str = "stop active speaking head motion") -> None:
        with self._lock:
            thread = self._active_speaking_thread
            stop_event = self._active_speaking_stop
            self._active_speaking_thread = None
            self._active_speaking_stop = None
        if thread is not None and stop_event is not None:
            self.stop_speaking_head_motion(thread, stop_event, reason=reason)

    def force_motion_idle(self, *, reason: str = "force motion idle") -> None:
        self.stop_active_speaking_head_motion(reason=reason)
        self.reset_head_position(reason=reason)


class FrdmUartProxyServer:
    def __init__(self, args: argparse.Namespace, robot: RobotUartController) -> None:
        self.args = args
        self.robot = robot
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> str:
        if getattr(self.args, "no_uart", False) or getattr(self.args, "uart_dry_run", False):
            return ""
        if self.server is not None:
            return self.url
        proxy = self

        class UartProxyHandler(BaseHTTPRequestHandler):
            server_version = "WakeBridgeUartProxy/1.0"

            def log_message(self, format: str, *args: Any) -> None:
                if getattr(proxy.args, "uart_debug", False):
                    super().log_message(format, *args)

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path.rstrip("/") != "/uart":
                    self._send_json(404, {"ok": False, "error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = 0
                raw = self.rfile.read(min(length, 4096)).decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return
                line = str(payload.get("line", "") or "").strip()
                reason = str(payload.get("reason", "focus uart proxy") or "focus uart proxy")
                try:
                    read_ms = int(payload.get("read_ms", 80) or 80)
                except (TypeError, ValueError):
                    read_ms = 80
                if not line:
                    self._send_json(400, {"ok": False, "error": "empty_line"})
                    return
                ok = proxy.robot.send_uart_raw_line(line, reason=reason, read_ms=read_ms)
                self._send_json(200 if ok else 503, {"ok": ok})

        host = str(getattr(self.args, "uart_proxy_host", "127.0.0.1") or "127.0.0.1")
        port = int(getattr(self.args, "uart_proxy_port", 0) or 0)
        try:
            self.server = ThreadingHTTPServer((host, port), UartProxyHandler)
        except OSError as exc:
            print(f"WARNING: could not start FRDM UART proxy: {exc}")
            return ""
        actual_host, actual_port = self.server.server_address[:2]
        self.url = f"http://{actual_host}:{actual_port}/uart"
        self.thread = threading.Thread(target=self.server.serve_forever, name="frdm_uart_proxy", daemon=True)
        self.thread.start()
        print(f"FRDM UART proxy: {self.url}")
        return self.url

    def stop(self) -> None:
        server = self.server
        self.server = None
        if server is not None:
            def shutdown_server() -> None:
                try:
                    server.shutdown()
                except Exception as exc:
                    if getattr(self.args, "uart_debug", False):
                        print(f"FRDM UART proxy shutdown warning: {exc}")

            shutdown_thread = threading.Thread(target=shutdown_server, name="frdm_uart_proxy_shutdown", daemon=True)
            shutdown_thread.start()
            try:
                shutdown_thread.join(timeout=1.0)
            except KeyboardInterrupt:
                print("FRDM UART proxy cleanup interrupted; continuing shutdown.")
            if shutdown_thread.is_alive():
                print("WARNING: FRDM UART proxy shutdown timed out; closing server socket.")
            try:
                server.server_close()
            except Exception as exc:
                if getattr(self.args, "uart_debug", False):
                    print(f"FRDM UART proxy close warning: {exc}")
        thread = self.thread
        self.thread = None
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=1.0)
            except KeyboardInterrupt:
                print("FRDM UART proxy thread cleanup interrupted; continuing shutdown.")


class FocusModeManager:
    def __init__(self, args: argparse.Namespace, camera_manager: Any | None = None, *, uart_proxy_url: str = "") -> None:
        self.args = args
        self.camera_manager = camera_manager
        self.uart_proxy_url = str(uart_proxy_url or "")
        self.process: subprocess.Popen[Any] | None = None
        self.camera_released_for_focus = False
        self.dashboard_state = "idle"
        self.dashboard_remaining_min = 0
        self.dashboard_streak = 0
        self.uart_gate_file: Path | None = None

    def is_enabled(self) -> bool:
        return not bool(getattr(self.args, "no_focus_mode", False))

    def is_running(self) -> bool:
        self.poll()
        return self.process is not None and self.process.poll() is None

    def poll(self) -> None:
        if self.process is None:
            return
        code = self.process.poll()
        if code is None:
            return
        print(f"Focus work mode exited with code {code}.")
        self.process = None
        self.dashboard_state = "idle"
        self.dashboard_remaining_min = 0
        self.dashboard_streak = 0
        self._clear_uart_gate_file()
        self._restart_camera_after_focus()

    def _terminate_process(self, *, graceful_timeout: float, kill_timeout: float) -> None:
        process = self.process
        if process is None:
            self._restart_camera_after_focus()
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=max(0.1, graceful_timeout))
            except subprocess.TimeoutExpired:
                print("WARNING: focus work mode did not stop after terminate; killing it.")
                process.kill()
                try:
                    process.wait(timeout=max(0.1, kill_timeout))
                except subprocess.TimeoutExpired:
                    print("WARNING: focus work mode did not exit after kill; continuing cleanup.")
        self.process = None
        self._clear_uart_gate_file()
        self._restart_camera_after_focus()

    def _new_uart_gate_file(self) -> Path:
        gate_dir = Path(os.getenv("WAKE_FOCUS_UART_GATE_DIR", "/tmp")).expanduser()
        return gate_dir / f"wake_focus_uart_gate_{uuid.uuid4().hex}.ready"

    def close_uart_gate(self) -> None:
        gate_file = self.uart_gate_file
        if gate_file is None:
            return
        try:
            gate_file.unlink(missing_ok=True)
        except OSError as exc:
            print(f"WARNING: could not close focus UART gate {gate_file}: {exc}")

    def open_uart_gate(self) -> None:
        gate_file = self.uart_gate_file
        if gate_file is None:
            return
        try:
            gate_file.parent.mkdir(parents=True, exist_ok=True)
            gate_file.touch()
            print(f"Focus mode UART gate opened: {gate_file}")
        except OSError as exc:
            print(f"WARNING: could not open focus UART gate {gate_file}: {exc}")

    def _clear_uart_gate_file(self) -> None:
        gate_file = self.uart_gate_file
        self.uart_gate_file = None
        if gate_file is None:
            return
        try:
            gate_file.unlink(missing_ok=True)
        except OSError:
            pass

    def start(self, transcript: str) -> tuple[bool, str]:
        if not self.is_enabled():
            return False, "專心工作模式目前沒有啟用。"
        if self.is_running():
            return True, "我已經在專心工作模式了。要結束的話，再叫我結束工作。"

        script = Path(str(getattr(self.args, "focus_script", "") or DEFAULT_FOCUS_SCRIPT)).expanduser()
        if not script.exists():
            return False, f"找不到專心工作模式腳本：{script}"

        duration_min = parse_focus_duration_min(transcript)
        if duration_min is None:
            configured_duration = float(getattr(self.args, "focus_duration_min", 0.0) or 0.0)
            duration_min = configured_duration if configured_duration > 0 else None
        task = extract_focus_task(transcript) or str(getattr(self.args, "focus_task", "") or "").strip()

        focus_server_url = str(getattr(self.args, "focus_server_url", "") or "").strip()
        if not focus_server_url:
            focus_server_url = voice_chat.endpoint_url(self.args.server_url, "/focus-check")

        self.uart_gate_file = self._new_uart_gate_file()
        self.close_uart_gate()
        self._release_camera_for_focus()
        command = [
            sys.executable,
            str(script),
            "--server-url",
            focus_server_url,
            "--interval-sec",
            str(getattr(self.args, "focus_interval_sec", 180)),
            "--log-root",
            str(getattr(self.args, "focus_log_root", THIS_DIR / "logs" / "focus_sessions")),
            "--camera-id",
            str(getattr(self.args, "camera_id", "auto")),
            "--camera-width",
            str(getattr(self.args, "camera_width", 640)),
            "--camera-height",
            str(getattr(self.args, "camera_height", 480)),
            "--camera-max-side",
            str(getattr(self.args, "camera_max_side", 640)),
            "--camera-jpeg-quality",
            str(getattr(self.args, "camera_jpeg_quality", 78)),
            "--camera-warmup-frames",
            str(getattr(self.args, "camera_warmup_frames", 3)),
            "--uart-port",
            str(getattr(self.args, "uart_port", "auto")),
            "--uart-baudrate",
            str(getattr(self.args, "uart_baudrate", 115200)),
            "--uart-timeout",
            str(getattr(self.args, "uart_timeout", 0.08)),
            "--uart-line-ending",
            str(getattr(self.args, "uart_line_ending", "crlf")),
            "--alert-threshold",
            str(getattr(self.args, "focus_alert_threshold", 2)),
            "--alert-cooldown-sec",
            str(getattr(self.args, "focus_alert_cooldown_sec", 90.0)),
            "--alert-tts-url",
            str(getattr(self.args, "tts_url", "http://127.0.0.1:8777/speak_async")),
            "--alert-tts-timeout",
            str(getattr(self.args, "tts_timeout", 5.0)),
            "--alert-volume-gain",
            str(getattr(self.args, "tts_volume_gain", 2.25)),
            "--first-sample-delay-sec",
            str(getattr(self.args, "focus_first_sample_delay_sec", -1.0)),
            "--todo-list-path",
            str(getattr(self.args, "todo_list_path", THIS_DIR / "logs" / "todo_list.json")),
            "--notify-mode",
            str(getattr(self.args, "focus_notify_mode", "none")),
            "--notify-timeout",
            str(getattr(self.args, "focus_notify_timeout", 8.0)),
            "--uart-gate-file",
            str(self.uart_gate_file),
        ]
        discord_webhook_url = str(getattr(self.args, "focus_discord_webhook_url", "") or "").strip()
        if discord_webhook_url:
            command.extend(["--discord-webhook-url", discord_webhook_url])
        if task:
            command.extend(["--task", task])
        if duration_min is not None and duration_min > 0:
            command.extend(["--duration-min", f"{duration_min:g}"])
        if getattr(self.args, "no_uart", False):
            command.append("--no-uart")
        if getattr(self.args, "uart_dry_run", False):
            command.append("--uart-dry-run")
        if getattr(self.args, "uart_debug", False):
            command.append("--uart-debug")
        if self.uart_proxy_url:
            command.extend(["--uart-proxy-url", self.uart_proxy_url])
        if getattr(self.args, "no_tts", False) or getattr(self.args, "no_focus_alert_tts", False):
            command.append("--no-alert-tts")
        else:
            command.append("--alert-tts-no-interrupt")
        if getattr(self.args, "no_focus_alert_motion", False):
            command.append("--no-alert-motion")
        command.append("--no-active-screen-uart")
        if getattr(self.args, "focus_save_images", False):
            command.append("--save-images")
        if getattr(self.args, "focus_notify_dry_run", False):
            command.append("--notify-dry-run")

        child_env = os.environ.copy()
        child_env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(script.parent),
                stdin=subprocess.DEVNULL,
                env=child_env,
            )
        except Exception as exc:
            self._clear_uart_gate_file()
            self._restart_camera_after_focus()
            return False, f"專心工作模式啟動失敗：{exc}"

        self.dashboard_state = "active"
        self.dashboard_remaining_min = int(round(float(duration_min or 0)))
        self.dashboard_streak = 0
        duration_text = f"{duration_min:g} 分鐘" if duration_min else "直到你叫我結束"
        task_text = f"這次目標是「{task}」。" if task else ""
        return True, f"工作模式開始。我會安靜陪你專心，並定時記錄工作狀態，{duration_text}。{task_text}要結束時再叫我結束工作。"

    def stop(self) -> tuple[bool, str]:
        if not self.is_enabled():
            return False, "專心工作模式目前沒有啟用。"
        if not self.is_running():
            self._restart_camera_after_focus()
            self.dashboard_state = "idle"
            self.dashboard_remaining_min = 0
            self.dashboard_streak = 0
            return False, "目前沒有正在進行的專心工作模式。"
        self._terminate_process(graceful_timeout=8.0, kill_timeout=3.0)
        self.dashboard_state = "idle"
        self.dashboard_remaining_min = 0
        self.dashboard_streak = 0
        return True, "工作模式結束。我已經切回一般互動狀態，稍後可以查看這次的工作紀錄。"

    def _release_camera_for_focus(self) -> None:
        if self.camera_manager is None or self.camera_released_for_focus:
            return
        try:
            self.camera_manager.release()
            self.camera_released_for_focus = True
            print("Focus mode: released normal camera manager.")
        except Exception as exc:
            print(f"WARNING: could not release normal camera for focus mode: {exc}")

    def _restart_camera_after_focus(self) -> None:
        if self.camera_manager is None or not self.camera_released_for_focus:
            return
        try:
            self.camera_manager.start()
            print("Focus mode: normal camera manager restarted.")
        except Exception as exc:
            print(f"WARNING: could not restart normal camera after focus mode: {exc}")
        finally:
            self.camera_released_for_focus = False

    def shutdown(self) -> None:
        if self.process is None:
            return
        self._terminate_process(graceful_timeout=3.0, kill_timeout=1.0)


class PetIdleReflectionManager:
    def __init__(
        self,
        args: argparse.Namespace,
        robot: RobotUartController,
        focus_manager: FocusModeManager | None,
    ) -> None:
        self.args = args
        self.robot = robot
        self.focus_manager = focus_manager
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._reflection_lock = threading.Lock()
        now = time.monotonic()
        self.last_user_activity_at = now
        self.last_spoken_at = 0.0
        self.next_reflection_at = now + pet_idle_next_delay(args)
        self.external_busy_count = 0

    def is_enabled(self) -> bool:
        return pet_idle_reflection_enabled(self.args)

    def start(self) -> None:
        if not self.is_enabled():
            return
        if self.thread is not None and self.thread.is_alive():
            return
        self.mark_user_activity("pet idle reflection start")
        self.thread = threading.Thread(target=self._run, name="pet_idle_reflection", daemon=True)
        self.thread.start()
        print(
            "Pet idle reflection started: "
            f"interval={float(getattr(self.args, 'pet_idle_interval_sec', 30.0) or 30.0):g}s, "
            f"jitter={float(getattr(self.args, 'pet_idle_jitter_sec', 0.0) or 0.0):g}s."
        )

    def shutdown(self) -> None:
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def mark_user_activity(self, reason: str = "") -> None:
        del reason
        now = time.monotonic()
        with self._state_lock:
            self.last_user_activity_at = now
            self.next_reflection_at = now + pet_idle_next_delay(self.args)

    def begin_user_interaction(self, reason: str = "") -> None:
        del reason
        now = time.monotonic()
        with self._state_lock:
            self.external_busy_count += 1
            self.last_user_activity_at = now
            self.next_reflection_at = now + pet_idle_next_delay(self.args)

    def end_user_interaction(self, reason: str = "") -> None:
        del reason
        now = time.monotonic()
        with self._state_lock:
            if self.external_busy_count > 0:
                self.external_busy_count -= 1
            self.last_user_activity_at = now
            self.next_reflection_at = now + pet_idle_next_delay(self.args)

    def _focus_running(self) -> bool:
        if self.focus_manager is None:
            return False
        try:
            return self.focus_manager.is_running()
        except Exception as exc:
            print(f"WARNING: could not check focus mode before pet idle reflection: {exc}")
            return True

    def _snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._state_lock:
            return {
                "now": now,
                "last_user_activity_at": self.last_user_activity_at,
                "last_spoken_at": self.last_spoken_at,
                "next_reflection_at": self.next_reflection_at,
                "external_busy_count": self.external_busy_count,
            }

    def _schedule_next(self, *, from_now: float | None = None) -> None:
        now = time.monotonic()
        with self._state_lock:
            delay = pet_idle_next_delay(self.args) if from_now is None else max(1.0, float(from_now))
            self.next_reflection_at = now + delay

    def _run(self) -> None:
        while not self.stop_event.is_set():
            snapshot = self._snapshot()
            wait_sec = max(0.2, min(2.0, float(snapshot["next_reflection_at"]) - float(snapshot["now"])))
            if self.stop_event.wait(wait_sec):
                return
            now = time.monotonic()
            if now < float(self._snapshot()["next_reflection_at"]):
                continue
            self._maybe_reflect()

    def _blocked_reason(self, snapshot: dict[str, Any]) -> str:
        if int(snapshot.get("external_busy_count", 0) or 0) > 0:
            return "voice turn active"
        if self._focus_running():
            return "focus mode active"
        if self.robot.persistent_state == "sleep" and not bool(getattr(self.args, "pet_idle_while_sleeping", False)):
            return "sleep state"
        min_idle = max(1.0, float(getattr(self.args, "pet_idle_min_silent_sec", 0.0) or 0.0))
        idle_sec = float(snapshot["now"]) - float(snapshot["last_user_activity_at"])
        if idle_sec < min_idle:
            return f"idle {idle_sec:.1f}s < {min_idle:.1f}s"
        return ""

    def _maybe_reflect(self) -> None:
        snapshot = self._snapshot()
        blocked = self._blocked_reason(snapshot)
        if blocked:
            if bool(getattr(self.args, "pet_idle_debug", False)):
                print(f"Pet idle reflection skipped: {blocked}.")
            self._schedule_next()
            return
        if not self._reflection_lock.acquire(blocking=False):
            self._schedule_next(from_now=1.0)
            return
        try:
            self._run_reflection()
        finally:
            self._reflection_lock.release()
            self._schedule_next()

    def _run_reflection(self) -> None:
        snapshot = self._snapshot()
        idle_sec = float(snapshot["now"]) - float(snapshot["last_user_activity_at"])
        seconds_since_share = (
            999999.0
            if float(snapshot["last_spoken_at"]) <= 0.0
            else float(snapshot["now"]) - float(snapshot["last_spoken_at"])
        )
        share_cooldown = max(0.0, float(getattr(self.args, "pet_idle_share_cooldown_sec", 0.0) or 0.0))
        allow_share = seconds_since_share >= share_cooldown
        prompt = build_pet_idle_reflection_prompt(
            idle_seconds=idle_sec,
            seconds_since_share=seconds_since_share,
            allow_share=allow_share,
        )
        text_url = voice_chat.endpoint_url(self.args.server_url, "/text-chat")
        timeout_sec = max(1.0, float(getattr(self.args, "pet_idle_timeout", 20.0) or 20.0))

        if bool(getattr(self.args, "pet_idle_debug", False)):
            print(
                "Pet idle reflection: asking model "
                f"(idle={idle_sec:.1f}s, allow_share={allow_share}, cooldown={share_cooldown:g}s)."
            )
        if bool(getattr(self.args, "pet_idle_show_thinking", False)):
            self.robot.set_screen_mode("thinking", reason="pet idle reflection")
        try:
            response = voice_chat.post_json(text_url, {"text": prompt}, timeout_sec=timeout_sec)
        except Exception as exc:
            print(f"Pet idle reflection skipped: text-chat failed: {exc}")
            if bool(getattr(self.args, "pet_idle_show_thinking", False)) and not self._focus_running():
                self.robot.restore_persistent_screen_state()
            return

        if self._focus_running():
            print("Pet idle reflection discarded because focus mode became active.")
            return
        self._handle_response(response, allow_share=allow_share)

    def _control_from_response(self, response: dict[str, Any]) -> dict[str, str]:
        response["transcript"] = "pet_idle_reflection"
        control = normalize_control(response)
        emotion = normalize_emotion_name(control.get("emotion", "curious"), default="curious")
        if emotion in {"angry", "sad", "sleepy"}:
            emotion = "concerned" if emotion != "sleepy" else "neutral"
        return {
            "persistent_state": "unchanged",
            "screen_mode": "unchanged",
            "emotion": emotion,
            "head_motion": head_motion_for_emotion(emotion, control.get("head_motion", "")),
            "reason": "pet idle reflection",
        }

    def _handle_response(self, response: dict[str, Any], *, allow_share: bool) -> None:
        if int(self._snapshot().get("external_busy_count", 0) or 0) > 0:
            print("Pet idle reflection discarded because a voice turn started.")
            return
        debug = response.get("debug") if isinstance(response.get("debug"), dict) else {}
        if response.get("fallback_reason") or debug.get("ok") is False:
            if bool(getattr(self.args, "pet_idle_debug", False)):
                print("Pet idle reflection stayed silent because the model response used a fallback.")
            return
        raw_reply = str(response.get("reply", "") or "").strip()
        if pet_idle_silence_reply(raw_reply) or not allow_share:
            if bool(getattr(self.args, "pet_idle_debug", False)):
                print(f"Pet idle reflection stayed silent: {short_preview(raw_reply or PET_IDLE_SILENCE_TOKEN, 80)}")
            if bool(getattr(self.args, "pet_idle_show_thinking", False)) and not self._focus_running():
                self.robot.restore_persistent_screen_state()
            return

        control = self._control_from_response(response)
        response["control"] = control
        response["emotion"] = emotion_summary_from_control(control)
        reply = sanitize_reply(response)
        if pet_idle_silence_reply(reply):
            if bool(getattr(self.args, "pet_idle_debug", False)):
                print("Pet idle reflection sanitized to silence.")
            if bool(getattr(self.args, "pet_idle_show_thinking", False)) and not self._focus_running():
                self.robot.restore_persistent_screen_state()
            return
        if self._focus_running():
            print("Pet idle reflection discarded before speaking because focus mode is active.")
            return
        if int(self._snapshot().get("external_busy_count", 0) or 0) > 0:
            print("Pet idle reflection discarded before speaking because a voice turn started.")
            return

        print(f"Pet idle reflection sharing: {short_preview(reply, 100)}")
        if bool(getattr(self.args, "pet_idle_debug", False)):
            print_control_summary(control)

        timing = TimingLogger()
        speaking_cue = SpeakingPlaybackCue(
            self.robot,
            control["emotion"],
            control["head_motion"],
            timing,
            timing_label="UART Speaking emotion code sent",
            reset_reason="speaking_head_motion pet idle reset",
        )
        try:
            speak_reply_and_wait(response, self.args, on_playback_start=speaking_cue.start)
        finally:
            speaking_cue.stop()
        timing.mark("pet idle TTS finished")
        if not self._focus_running():
            self.robot.restore_persistent_screen_state()
        with self._state_lock:
            self.last_spoken_at = time.monotonic()


class TodoListManager:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.path = Path(str(getattr(args, "todo_list_path", DEFAULT_TODO_LIST_PATH))).expanduser()

    def is_enabled(self) -> bool:
        return not bool(getattr(self.args, "no_todo_list", False))

    def _empty_data(self) -> dict[str, Any]:
        return {"version": 1, "next_id": 1, "items": []}

    def _read_data(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_data()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"WARNING: could not read to-do list {self.path}: {exc}")
            return self._empty_data()
        if not isinstance(raw, dict):
            return self._empty_data()
        items = raw.get("items")
        if not isinstance(items, list):
            items = []
        cleaned_items: list[dict[str, Any]] = []
        max_id = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            text = clean_todo_text(str(item.get("text", "") or ""))
            if not text:
                continue
            try:
                item_id = int(item.get("id", 0) or 0)
            except (TypeError, ValueError):
                item_id = 0
            if item_id <= 0:
                item_id = max_id + 1
            max_id = max(max_id, item_id)
            status = str(item.get("status", "open") or "open").strip().lower()
            if status not in {"open", "done"}:
                status = "open"
            cleaned_items.append(
                {
                    "id": item_id,
                    "text": text,
                    "status": status,
                    "created_at": str(item.get("created_at", "") or ""),
                    "completed_at": item.get("completed_at") if item.get("completed_at") else None,
                    "source": str(item.get("source", "voice") or "voice"),
                }
            )
        try:
            next_id = int(raw.get("next_id", max_id + 1) or max_id + 1)
        except (TypeError, ValueError):
            next_id = max_id + 1
        return {"version": 1, "next_id": max(next_id, max_id + 1), "items": cleaned_items}

    def _write_data(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_path, self.path)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def open_items(self, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if data is None:
            data = self._read_data()
        items = data.get("items") if isinstance(data.get("items"), list) else []
        return [item for item in items if isinstance(item, dict) and item.get("status") == "open"]

    def done_items(self, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if data is None:
            data = self._read_data()
        items = data.get("items") if isinstance(data.get("items"), list) else []
        return [item for item in items if isinstance(item, dict) and item.get("status") == "done"]

    def add_item(self, text: str) -> dict[str, Any]:
        item_text = clean_todo_text(text)
        if not item_text:
            return {
                "ok": False,
                "action": "add",
                "reply": "我有聽到你要新增待辦，但沒有聽清楚內容。可以說：新增待辦，寫報告。",
            }
        data = self._read_data()
        item_id = int(data.get("next_id", 1) or 1)
        item = {
            "id": item_id,
            "text": item_text,
            "status": "open",
            "created_at": todo_timestamp(),
            "completed_at": None,
            "source": "voice",
        }
        data["items"].append(item)
        data["next_id"] = item_id + 1
        self._write_data(data)
        count = len(self.open_items(data))
        return {
            "ok": True,
            "action": "add",
            "item": item,
            "reply": f"已加入待辦：{item_text}。目前還有 {count} 個未完成。",
        }

    def list_items(self) -> dict[str, Any]:
        data = self._read_data()
        open_items = self.open_items(data)
        done_items = self.done_items(data)
        if not open_items:
            suffix = f" 已完成的有 {len(done_items)} 個。" if done_items else ""
            return {
                "ok": True,
                "action": "list",
                "items": [],
                "reply": f"目前沒有未完成待辦。{suffix}".strip(),
            }
        visible = open_items[:8]
        item_text = "；".join(f"{index}. {item.get('text', '')}" for index, item in enumerate(visible, start=1))
        more = len(open_items) - len(visible)
        suffix = f"；還有 {more} 個沒有唸出來" if more > 0 else ""
        return {
            "ok": True,
            "action": "list",
            "items": open_items,
            "reply": f"目前有 {len(open_items)} 個待辦：{item_text}{suffix}。",
        }

    def complete_item(self, *, number: int | None = None, text: str = "") -> dict[str, Any]:
        data = self._read_data()
        open_items = self.open_items(data)
        if not open_items:
            return {"ok": False, "action": "done", "reply": "目前沒有未完成待辦。"}

        target: dict[str, Any] | None = None
        if number is not None:
            if 1 <= number <= len(open_items):
                target = open_items[number - 1]
            else:
                for item in open_items:
                    if int(item.get("id", 0) or 0) == number:
                        target = item
                        break
            if target is None:
                return {"ok": False, "action": "done", "reply": f"找不到第 {number} 個未完成待辦。"}
        else:
            target_text = normalize_intent_text(text)
            if not target_text:
                return {
                    "ok": False,
                    "action": "done",
                    "reply": "要完成哪一個待辦？可以說：完成待辦 1，或完成待辦 寫報告。",
                }
            matches = []
            for item in open_items:
                item_text = normalize_intent_text(str(item.get("text", "") or ""))
                if target_text and (target_text in item_text or item_text in target_text):
                    matches.append(item)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                return {"ok": False, "action": "done", "reply": "我找到多個相似待辦，可以改說完成待辦第幾項。"}
            else:
                return {"ok": False, "action": "done", "reply": f"找不到和「{clean_todo_text(text)}」相符的未完成待辦。"}

        target_id = int(target.get("id", 0) or 0)
        completed_text = str(target.get("text", "") or "")
        for item in data["items"]:
            if isinstance(item, dict) and int(item.get("id", 0) or 0) == target_id:
                item["status"] = "done"
                item["completed_at"] = todo_timestamp()
                break
        self._write_data(data)
        remaining = len(self.open_items(data))
        return {
            "ok": True,
            "action": "done",
            "item": target,
            "reply": f"已完成待辦：{completed_text}。剩下 {remaining} 個未完成。",
        }

    def clear_completed(self) -> dict[str, Any]:
        data = self._read_data()
        before = len(data["items"])
        data["items"] = [item for item in data["items"] if not isinstance(item, dict) or item.get("status") != "done"]
        removed = before - len(data["items"])
        self._write_data(data)
        return {"ok": True, "action": "clear_completed", "reply": f"已清除 {removed} 個已完成待辦。"}

    def clear_all(self) -> dict[str, Any]:
        data = self._read_data()
        count = len([item for item in data["items"] if isinstance(item, dict)])
        data["items"] = []
        data["next_id"] = 1
        self._write_data(data)
        return {"ok": True, "action": "clear_all", "reply": f"已清空待辦清單，共移除 {count} 個項目。"}

    def complete_item_by_id(self, item_id: int, *, source: str = "frdm") -> dict[str, Any]:
        data = self._read_data()
        target: dict[str, Any] | None = None
        for item in data["items"]:
            if not isinstance(item, dict):
                continue
            try:
                current_id = int(item.get("id", 0) or 0)
            except (TypeError, ValueError):
                current_id = 0
            if current_id == item_id:
                target = item
                break
        if target is None:
            return {"ok": False, "action": "done", "source": source, "reply": f"找不到 ID {item_id} 的待辦。"}
        if target.get("status") == "done":
            remaining = len(self.open_items(data))
            return {
                "ok": True,
                "action": "done",
                "source": source,
                "item": target,
                "already_done": True,
                "remaining": remaining,
                "reply": f"待辦已經完成：{target.get('text', '')}。剩下 {remaining} 個未完成。",
            }

        target["status"] = "done"
        target["completed_at"] = todo_timestamp()
        target["source"] = source
        self._write_data(data)
        remaining = len(self.open_items(data))
        return {
            "ok": True,
            "action": "done",
            "source": source,
            "item": target,
            "remaining": remaining,
            "reply": f"已完成待辦：{target.get('text', '')}。剩下 {remaining} 個未完成。",
        }

    def handle_transcript(self, transcript: str) -> dict[str, Any] | None:
        if not self.is_enabled():
            return None
        intent = detect_todo_intent(transcript)
        if intent is None:
            return None
        if intent == "add":
            result = self.add_item(extract_todo_add_text(transcript))
        elif intent == "list":
            result = self.list_items()
        elif intent == "done":
            result = self.complete_item(
                number=extract_todo_done_number(transcript),
                text=extract_todo_done_text(transcript),
            )
        elif intent == "clear_completed":
            result = self.clear_completed()
        elif intent == "clear_all":
            result = self.clear_all()
        else:
            return None
        result["intent"] = intent
        result["path"] = str(self.path)
        if getattr(self.args, "todo_debug", False):
            print(f"To-do debug: intent={intent}, result={json.dumps(result, ensure_ascii=False, default=str)}")
        return result


def frdm_event_parts(line: str) -> list[str]:
    text = str(line or "").strip()
    if not text:
        return []
    if text.startswith("$") and "*" in text:
        text = text[1:].split("*", 1)[0]
    normalized = re.sub(r"[,=:]+", " ", text)
    parts = normalized.split()
    if not parts:
        return []
    if parts[0].upper() == "EVT":
        parts = parts[1:]
    return parts


def parse_frdm_todo_done_event(line: str) -> int | None:
    parts = frdm_event_parts(line)
    if not parts:
        return None
    command = parts[0].strip().lower()
    if command not in {"tododone", "todocheck", "todocomplete", "todochecked"}:
        return None
    if len(parts) < 2:
        return None
    try:
        item_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    return item_id if item_id > 0 else None


def parse_bool_token(value: str) -> bool | None:
    token = str(value or "").strip().lower()
    if token in {"1", "on", "true", "yes", "y", "open", "enable", "enabled", "開", "开", "開啟", "开启"}:
        return True
    if token in {"0", "off", "false", "no", "n", "close", "closed", "disable", "disabled", "關", "关", "關閉", "关闭"}:
        return False
    return None


def parse_frdm_fan_event(line: str, *, speed_max: int = 3) -> dict[str, Any] | None:
    text = str(line or "").strip()
    if not text:
        return None
    parts = frdm_event_parts(text)
    if not parts:
        return None

    command = parts[0].strip().lower()
    power: bool | None = None
    speed: int | None = None

    if command in {"fan", "fanset", "fancontrol"}:
        if len(parts) < 2:
            return None
        power = parse_bool_token(parts[1])
        if power is None:
            try:
                speed = int(float(parts[1]))
            except (TypeError, ValueError):
                return None
            power = speed > 0
        if len(parts) >= 3:
            try:
                speed = int(float(parts[2]))
            except (TypeError, ValueError):
                speed = None
    elif command in {"fanpower", "fanswitch", "fanonoff"}:
        if len(parts) < 2:
            return None
        power = parse_bool_token(parts[1])
        if power is None:
            return None
    elif command in {"fanspeed", "fanlevel"}:
        if len(parts) < 2:
            return None
        try:
            speed = int(float(parts[1]))
        except (TypeError, ValueError):
            return None
        power = speed > 0
    else:
        return None

    safe_speed_max = max(1, int(speed_max or 3))
    if speed is None:
        speed = safe_speed_max if power else 0
    speed = max(0, int(speed))
    if not power:
        speed = 0
    if speed <= safe_speed_max:
        percent = int(round((speed / safe_speed_max) * 100.0))
    else:
        percent = max(0, min(100, speed))
    return {
        "power": bool(power),
        "state": "on" if power else "off",
        "speed": speed,
        "percent": percent,
        "raw": text,
    }


class FrdmTodoEventListener:
    def __init__(self, args: argparse.Namespace, robot: RobotUartController, todo_manager: TodoListManager) -> None:
        self.args = args
        self.robot = robot
        self.todo_manager = todo_manager

    def start(self) -> None:
        if getattr(self.args, "no_frdm_todo_events", False):
            print("FRDM to-do checkbox events: disabled.")
            return
        if getattr(self.args, "no_dashboard_uart", False):
            print("FRDM to-do checkbox events: skipped because dashboard UART sync is disabled.")
            return
        if not frdm_uart_events_active(self.args):
            print("FRDM to-do checkbox events: skipped because UART event bus is inactive.")
            return
        if getattr(self.args, "no_uart", False) or getattr(self.args, "uart_dry_run", False):
            print("FRDM to-do checkbox events: skipped without live UART.")
            return
        if not self.todo_manager.is_enabled():
            print("FRDM to-do checkbox events: skipped because to-do list is disabled.")
            return
        print("FRDM to-do checkbox events: listening on UART bus for TodoDone <id>.")

    def stop(self) -> None:
        return

    def handle_line(self, line: str) -> bool:
        if not frdm_uart_events_active(self.args):
            return False
        if getattr(self.args, "no_frdm_todo_events", False) or getattr(self.args, "no_dashboard_uart", False):
            return False
        if not self.todo_manager.is_enabled():
            return False
        item_id = parse_frdm_todo_done_event(line)
        if item_id is None:
            return False
        result = self.todo_manager.complete_item_by_id(item_id, source="frdm")
        print(
            "FRDM to-do checkbox event: "
            f"item_id={item_id}, ok={result.get('ok')}, already_done={result.get('already_done', False)}"
        )
        if result.get("reply"):
            print(f"  {result.get('reply')}")
        send_todo_uart_update(self.args, self.robot, self.todo_manager, reason=f"frdm todo done {item_id}")
        return True


class FrdmFanControlManager:
    def __init__(self, args: argparse.Namespace, esp32_ble_manager: Esp32BleBridgeManager | None = None) -> None:
        self.args = args
        self.esp32_ble_manager = esp32_ble_manager
        self.last_event_key = ""
        self.last_event_at = 0.0
        self.suppressed_duplicate_count = 0

    def is_enabled(self) -> bool:
        return not bool(getattr(self.args, "no_frdm_fan_events", False))

    def start(self) -> None:
        if not self.is_enabled():
            print("FRDM fan events: disabled.")
            return
        if not frdm_uart_events_active(self.args):
            print("FRDM fan events: skipped because UART event bus is inactive.")
            return
        if getattr(self.args, "no_uart", False) or getattr(self.args, "uart_dry_run", False):
            print("FRDM fan events: skipped without live UART.")
            return
        print(
            "FRDM fan events: listening on UART bus for "
            "Fan <on/off>,<speed> or EVT,Fan,<on/off>,<speed>."
        )

    def stop(self) -> None:
        return

    def handle_line(self, line: str) -> bool:
        if not frdm_uart_events_active(self.args):
            return False
        if not self.is_enabled():
            return False
        event = parse_frdm_fan_event(line, speed_max=int(getattr(self.args, "fan_speed_max", 3) or 3))
        if event is None:
            return False
        key = f"{event['state']}:{event['speed']}:{event['percent']}"
        now = time.monotonic()
        duplicate_suppress_sec = max(0.0, float(getattr(self.args, "fan_duplicate_suppress_sec", 2.0) or 0.0))
        if key == self.last_event_key and now - self.last_event_at < duplicate_suppress_sec:
            self.suppressed_duplicate_count += 1
            return True
        suppressed = self.suppressed_duplicate_count
        self.suppressed_duplicate_count = 0
        self.last_event_key = key
        self.last_event_at = now
        detail = f"state={event['state']} speed={event['speed']} percent={event['percent']} raw={event['raw']!r}"
        if suppressed:
            detail += f" suppressed_duplicates={suppressed}"
        print(f"FRDM fan event: {detail}")
        ble_ok = self._sync_ble(event)
        dashboard_ok = self._sync_dashboard(event)
        command_ok = self._run_control_command(event)
        if (
            not ble_ok
            and not dashboard_ok
            and not command_ok
            and not str(getattr(self.args, "fan_control_command", "") or "").strip()
        ):
            print("FRDM fan event handled in software only; configure --fan-control-command for GPIO/PWM hardware control.")
        return True

    def _sync_ble(self, event: dict[str, Any]) -> bool:
        if self.esp32_ble_manager is None:
            return False
        try:
            return self.esp32_ble_manager.handle_frdm_fan_event(event)
        except Exception as exc:
            print(f"WARNING: ESP32 BLE fan relay failed: {exc}")
            return False

    def _dashboard_url(self) -> str:
        template = str(getattr(self.args, "fan_dashboard_url", "") or DEFAULT_FAN_DASHBOARD_URL)
        device_id = str(getattr(self.args, "fan_device_id", "desk_fan") or "desk_fan")
        quoted = urllib.parse.quote(device_id, safe="")
        try:
            return template.format(device_id=quoted)
        except Exception:
            return template

    def _sync_dashboard(self, event: dict[str, Any]) -> bool:
        if getattr(self.args, "no_fan_dashboard_sync", False):
            return False
        url = self._dashboard_url()
        if not url:
            return False
        payload = {
            "state": event["state"],
            "value": int(event["percent"]),
            "online": True,
            "source": "frdm_uart",
        }
        try:
            result = voice_chat.post_json(
                url,
                payload,
                timeout_sec=max(0.1, float(getattr(self.args, "fan_dashboard_timeout", 1.5) or 1.5)),
            )
        except Exception as exc:
            print(f"WARNING: fan dashboard sync failed: {exc}")
            return False
        print(f"Fan dashboard sync: ok={result.get('ok')} url={url}")
        return bool(result.get("ok", True))

    def _run_control_command(self, event: dict[str, Any]) -> bool:
        template = str(getattr(self.args, "fan_control_command", "") or "").strip()
        if not template:
            return False
        values = {
            "state": event["state"],
            "power": "1" if event["power"] else "0",
            "speed": str(event["speed"]),
            "percent": str(event["percent"]),
            "device_id": str(getattr(self.args, "fan_device_id", "desk_fan") or "desk_fan"),
        }
        try:
            command = shlex.split(template.format(**values))
        except Exception as exc:
            print(f"WARNING: fan control command format failed: {exc}")
            return False
        if not command:
            return False
        env = os.environ.copy()
        env.update(
            {
                "FAN_STATE": values["state"],
                "FAN_POWER": values["power"],
                "FAN_SPEED": values["speed"],
                "FAN_PERCENT": values["percent"],
                "FAN_DEVICE_ID": values["device_id"],
            }
        )
        try:
            completed = subprocess.run(
                command,
                env=env,
                timeout=max(0.1, float(getattr(self.args, "fan_command_timeout", 2.0) or 2.0)),
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            print(f"WARNING: fan control command failed: {exc}")
            return False
        if completed.returncode != 0:
            stderr = short_preview(completed.stderr, 160)
            print(f"WARNING: fan control command exited {completed.returncode}: {stderr}")
            return False
        stdout = short_preview(completed.stdout, 120)
        print(f"Fan control command OK" + (f": {stdout}" if stdout else ""))
        return True


def parse_camera_id(raw: str) -> str | int:
    value = str(raw).strip()
    if value.lower() in {"", "auto"}:
        return "auto"
    try:
        return int(value)
    except ValueError:
        return value


def pulse_sink_by_keyword(keyword: str) -> str:
    if not shutil.which("pactl"):
        return ""
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        return ""
    try:
        completed = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.3,
        )
    except Exception:
        return ""
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            sink = fields[1].strip()
            if keyword in sink.lower() or keyword.replace(" ", "_") in sink.lower():
                return sink
    return ""


def alsa_playback_device_by_keyword(keyword: str) -> str:
    keyword = str(keyword or "").strip()
    if not keyword:
        return ""
    try:
        cards_text = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    keyword_lower = keyword.lower()
    for line in cards_text.splitlines():
        match = re.match(r"\s*\d+\s+\[([^\]]+)\]\s*:\s*(.*)$", line)
        if not match:
            continue
        card_name = match.group(1).strip()
        description = match.group(2).strip()
        if keyword_lower in card_name.lower() or keyword_lower in description.lower():
            return f"plughw:CARD={card_name},DEV=0"
    return ""


def write_beep_wav(path: Path, *, duration_ms: int, frequency_hz: float, volume: float, sample_rate: int = 48_000) -> None:
    sample_count = max(1, int(round(sample_rate * duration_ms / 1000.0)))
    t = np.arange(sample_count, dtype=np.float32) / float(sample_rate)
    base = np.sin(2.0 * np.pi * float(frequency_hz) * t)
    harmonic = 0.25 * np.sin(2.0 * np.pi * float(frequency_hz) * 2.0 * t)
    tone = (base + harmonic).astype(np.float32)
    tone /= max(1.0, float(np.max(np.abs(tone))))
    tone *= float(max(0.0, min(volume, 1.0)))
    fade = max(1, int(round(sample_rate * 0.005)))
    if sample_count > fade * 2:
        ramp = np.linspace(0.0, 1.0, num=fade, dtype=np.float32)
        tone[:fade] *= ramp
        tone[-fade:] *= ramp[::-1]
    pcm = np.clip(tone * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def run_external_beep_player(
    *,
    wav_path: Path,
    player: str,
    keyword: str,
    timeout_sec: float,
) -> tuple[bool, str]:
    requested = str(player or "auto").strip().lower()
    attempts: list[tuple[str, list[str]]] = []
    pulse_sink = pulse_sink_by_keyword(keyword)
    # In auto mode, avoid falling through to PulseAudio's default sink when the
    # requested USB speaker is not present as a Pulse sink. On the Jetson that
    # default may be the built-in output, which makes the cue sound inconsistent
    # with TTS/music and can be perceived as poor speaker quality.
    should_try_paplay = requested in {"pulse", "paplay"} or (requested == "auto" and bool(pulse_sink))
    if should_try_paplay and shutil.which("paplay"):
        command = [
            "paplay",
            "--client-name=MakeNTU Wake Beep",
            "--stream-name=recording-cue",
            "--latency-msec=20",
            "--process-time-msec=5",
        ]
        if pulse_sink:
            command.append(f"--device={pulse_sink}")
        command.append(str(wav_path))
        attempts.append(("paplay", command))
    if requested in {"auto", "aplay"} and shutil.which("aplay"):
        alsa_device = alsa_playback_device_by_keyword(keyword)
        for device_name in ([alsa_device] if alsa_device else []) + ["pulse", "default"]:
            attempts.append(("aplay", ["aplay", "-q", "-D", device_name, str(wav_path)]))
    if requested not in {"auto", "pulse", "paplay", "aplay", "sounddevice"}:
        return False, f"unknown beep player: {player}"
    if not attempts:
        return False, "no external beep player available"

    last_error = ""
    for label, command in attempts:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            last_error = f"{label} timed out"
            continue
        except Exception as exc:
            last_error = f"{label} failed: {exc}"
            continue
        if completed.returncode == 0:
            return True, label
        last_error = f"{label} exited {completed.returncode}: {short_preview(completed.stderr, 160)}"
    return False, last_error


def play_recording_beep_sounddevice(
    *,
    duration_ms: int,
    frequency_hz: float,
    volume: float,
    device: int | None = None,
) -> bool:
    import sounddevice as sd

    sample_rates: list[int] = []
    if device is not None:
        try:
            info = sd.query_devices(device, "output")
            sample_rates.append(int(round(float(info.get("default_samplerate", 0)))))
        except Exception:
            pass
    sample_rates.extend([48_000, 44_100, 32_000])
    for sample_rate in dict.fromkeys(rate for rate in sample_rates if rate > 0):
        sample_count = max(1, int(round(sample_rate * duration_ms / 1000.0)))
        t = np.arange(sample_count, dtype=np.float32) / float(sample_rate)
        base = np.sin(2.0 * np.pi * float(frequency_hz) * t)
        harmonic = 0.25 * np.sin(2.0 * np.pi * float(frequency_hz) * 2.0 * t)
        tone = (base + harmonic).astype(np.float32)
        tone /= max(1.0, float(np.max(np.abs(tone))))
        tone *= float(max(0.0, min(volume, 1.0)))
        fade = max(1, int(round(sample_rate * 0.005)))
        if sample_count > fade * 2:
            ramp = np.linspace(0.0, 1.0, num=fade, dtype=np.float32)
            tone[:fade] *= ramp
            tone[-fade:] *= ramp[::-1]
        sd.play(tone, samplerate=sample_rate, device=device, blocking=True)
        return True
    return False


def play_recording_beep(
    *,
    duration_ms: int = 180,
    frequency_hz: float = 1320.0,
    volume: float = 0.55,
    device: int | None = None,
    keyword: str = "UACDemo",
    player: str = "auto",
) -> bool:
    """Play a short local cue without making recording depend on audio output."""
    if duration_ms <= 0 or volume <= 0.0:
        return True
    requested_player = str(player or "auto").strip().lower()
    try:
        if requested_player == "sounddevice":
            return play_recording_beep_sounddevice(
                duration_ms=duration_ms,
                frequency_hz=frequency_hz,
                volume=volume,
                device=device,
            )

        with tempfile.NamedTemporaryFile(prefix="makentu_beep_", suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            write_beep_wav(wav_path, duration_ms=duration_ms, frequency_hz=frequency_hz, volume=volume)
            ok, detail = run_external_beep_player(
                wav_path=wav_path,
                player=requested_player,
                keyword=keyword,
                timeout_sec=max(0.2, duration_ms / 1000.0 + 0.35),
            )
            if ok:
                return True
            raise RuntimeError(detail)
        finally:
            try:
                wav_path.unlink()
            except OSError:
                pass
    except Exception as exc:
        print(f"WARNING: recording beep failed: {exc}")
        return False


class CameraManager:
    def __init__(
        self,
        *,
        enabled: bool,
        camera_id: str | int,
        width: int,
        height: int,
        max_side: int,
        jpeg_quality: int,
        read_timeout: float,
        latest_timeout: float,
        frame_max_age: float,
        warmup_frames: int,
        continuous: bool,
    ) -> None:
        self.enabled = enabled
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.max_side = max_side
        self.jpeg_quality = max(1, min(int(jpeg_quality), 100))
        self.read_timeout = read_timeout
        self.latest_timeout = max(0.0, float(latest_timeout))
        self.frame_max_age = max(0.1, float(frame_max_age))
        self.warmup_frames = max(1, int(warmup_frames))
        self.continuous = continuous
        self.executor: ThreadPoolExecutor | None = None
        self._latest_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_at = 0.0
        self._latest_detail = ""
        self._camera_stop = threading.Event()
        self._camera_thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self.executor is not None:
            print("Camera already started; keeping the existing camera worker.")
            return
        self._camera_stop.clear()
        self._clear_latest_frame()
        candidates = self._camera_candidates()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wake_camera")
        mode = "continuous warm-reader mode" if self.continuous else "one-shot mode"
        print(f"Camera ready in {mode}.")
        if candidates:
            print(f"  candidates       : {', '.join(str(item) for item in candidates)}")
        else:
            print("  candidates       : none now; auto mode will rescan /dev/video* on every wake")
        print(f"  capture          : {self.width}x{self.height}, jpeg_quality={self.jpeg_quality}")
        print(f"  timeout          : {self.read_timeout:.2f}s")
        if self.continuous:
            print(f"  latest timeout   : {self.latest_timeout:.2f}s, max_age={self.frame_max_age:.2f}s")
        if str(self.camera_id).lower() == "auto":
            print("  replug handling  : enabled; camera device numbers may change")
        if self.continuous:
            self._camera_thread = threading.Thread(target=self._continuous_capture_loop, name="wake_camera_warm_reader", daemon=True)
            self._camera_thread.start()

    def capture_async(self, *, delay_sec: float = 0.0) -> Future[bytes | None] | None:
        if not self.enabled or self.executor is None:
            return None

        def capture_after_delay() -> bytes | None:
            delay = max(0.0, float(delay_sec or 0.0))
            if delay > 0.0:
                time.sleep(delay)
            if self.executor is None:
                return None
            return self.capture_jpeg_bytes()

        return self.executor.submit(capture_after_delay)

    def capture_jpeg_bytes(self) -> bytes | None:
        if not self.enabled:
            return None
        if self.continuous:
            latest = self._wait_for_latest_frame()
            if latest is not None:
                return latest
            print("WARNING: camera warm reader has no fresh frame; continuing without image.")
            return None

        started = time.perf_counter()
        command = [
            sys.executable,
            "-c",
            CAMERA_CAPTURE_HELPER,
            str(self.camera_id),
            str(self.width),
            str(self.height),
            str(self.max_side),
            str(self.jpeg_quality),
            str(self.warmup_frames),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=max(0.2, self.read_timeout),
                check=False,
            )
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            if result.returncode != 0 or not result.stdout:
                reason = stderr or f"exit code {result.returncode}"
                print(f"WARNING: camera capture failed: {reason}")
                return None
            elapsed = int((time.perf_counter() - started) * 1000)
            print(f"Image captured: {len(result.stdout)} bytes, capture_ms={elapsed}")
            if stderr:
                print(f"  camera detail: {stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            print(f"WARNING: camera capture timed out after {self.read_timeout:.2f}s; continuing without image.")
            return None
        except Exception as exc:
            print(f"WARNING: camera capture failed: {exc}")
            return None

    def release(self) -> None:
        self._camera_stop.set()
        thread = self._camera_thread
        self._camera_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
            if thread.is_alive():
                print("WARNING: camera warm reader did not exit quickly; continuing cleanup.")
        executor = self.executor
        self.executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        self._clear_latest_frame()

    def _clear_latest_frame(self) -> None:
        with self._latest_lock:
            self._latest_jpeg = None
            self._latest_at = 0.0
            self._latest_detail = ""

    def _camera_candidates(self) -> list[str | int]:
        if str(self.camera_id).lower() != "auto":
            return [self.camera_id]
        candidates: list[str | int] = []
        for path in glob.glob("/dev/video*"):
            suffix = "".join(ch for ch in path if ch.isdigit())
            try:
                candidates.append(int(suffix))
            except ValueError:
                candidates.append(path)
        return sorted(candidates, key=lambda item: str(item))

    def _wait_for_latest_frame(self) -> bytes | None:
        deadline = time.monotonic() + self.latest_timeout
        while True:
            with self._latest_lock:
                latest = self._latest_jpeg
                latest_at = self._latest_at
                detail = self._latest_detail
            age = time.monotonic() - latest_at if latest_at > 0 else float("inf")
            if latest is not None and age <= self.frame_max_age:
                print(f"Image captured: {len(latest)} bytes, capture_ms=0")
                if detail:
                    print(f"  camera detail: {detail}, frame_age_ms={int(age * 1000)}")
                return latest
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.03)

    def _store_latest_frame(self, frame: Any, candidate: str | int) -> None:
        try:
            import cv2

            h, w = frame.shape[:2]
            largest_side = max(w, h)
            if self.max_side > 0 and largest_side > self.max_side:
                scale = self.max_side / float(largest_side)
                frame = cv2.resize(
                    frame,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
            if not ok:
                return
            data = encoded.tobytes()
            with self._latest_lock:
                self._latest_jpeg = data
                self._latest_at = time.monotonic()
                self._latest_detail = f"continuous camera={candidate} bytes={len(data)}"
        except Exception:
            return

    def _open_capture(self, candidate: str | int) -> Any | None:
        import cv2

        if hasattr(cv2, "CAP_V4L2"):
            cap = cv2.VideoCapture(candidate, cv2.CAP_V4L2)
            if cap.isOpened():
                return cap
            cap.release()
        cap = cv2.VideoCapture(candidate)
        if cap.isOpened():
            return cap
        cap.release()
        return None

    def _configure_capture(self, cap: Any) -> None:
        import cv2

        if hasattr(cv2, "CAP_PROP_FOURCC"):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "CAP_PROP_FPS"):
            cap.set(cv2.CAP_PROP_FPS, 10)

    def _continuous_capture_loop(self) -> None:
        try:
            import cv2  # noqa: F401
            try:
                cv2.setLogLevel(2)
            except Exception:
                pass
        except Exception as exc:
            print(f"WARNING: camera warm reader disabled: {exc}")
            return

        last_warn_at = 0.0
        while not self._camera_stop.is_set():
            candidates = self._camera_candidates()
            if not candidates:
                now = time.monotonic()
                if now - last_warn_at >= 10.0:
                    print("WARNING: camera warm reader found no /dev/video* candidates.")
                    last_warn_at = now
                time.sleep(1.0)
                continue

            opened = False
            for candidate in candidates:
                if self._camera_stop.is_set():
                    return
                cap = self._open_capture(candidate)
                if cap is None:
                    continue
                opened = True
                print(f"Camera warm reader opened camera {candidate}.")
                try:
                    self._configure_capture(cap)
                    missed_reads = 0
                    while not self._camera_stop.is_set():
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            missed_reads = 0
                            self._store_latest_frame(frame, candidate)
                        else:
                            missed_reads += 1
                            if missed_reads >= 10:
                                break
                        time.sleep(0.03)
                except Exception as exc:
                    print(f"WARNING: camera warm reader error on {candidate}: {exc}")
                finally:
                    cap.release()
                if not self._camera_stop.is_set():
                    time.sleep(0.5)
                    break

            if not opened:
                now = time.monotonic()
                if now - last_warn_at >= 10.0:
                    print(f"WARNING: camera warm reader could not open candidates: {candidates}")
                    last_warn_at = now
                time.sleep(1.0)


def capture_jpeg_bytes(camera_manager: CameraManager | None) -> bytes | None:
    if camera_manager is None:
        return None
    return camera_manager.capture_jpeg_bytes()


def wait_for_image_future(future: Future[bytes | None] | None, timeout_sec: float) -> bytes | None:
    if future is None:
        return None
    try:
        return future.result(timeout=max(0.0, timeout_sec))
    except FutureTimeout:
        print("WARNING: camera capture did not finish before timeout; sending audio only.")
        future.cancel()
        return None
    except Exception as exc:
        print(f"WARNING: camera capture task failed: {exc}")
        return None


def image_wait_timeout_for_context(args: argparse.Namespace, wake_context: dict[str, Any]) -> float:
    base_timeout = max(0.0, float(getattr(args, "camera_result_timeout", 1.0) or 0.0))
    metadata = wake_context.get("metadata") if isinstance(wake_context.get("metadata"), dict) else {}
    delay_sec = max(0.0, float(metadata.get("image_capture_delay_sec", 0.0) or 0.0))
    if delay_sec <= 0.0:
        return base_timeout
    return delay_sec + base_timeout + 0.05


def send_audio_and_optional_image_to_server(
    url: str,
    audio_path: Path,
    *,
    image_bytes: bytes | None = None,
    metadata: dict[str, Any] | None = None,
    timeout_sec: float,
) -> dict[str, Any]:
    boundary = "----JetsonVoiceVisionBoundary" + uuid.uuid4().hex
    parts: list[bytes] = []

    def add_bytes_field(field_name: str, filename: str, content_type: str, data: bytes) -> None:
        parts.append(f"--{boundary}\r\n".encode("ascii"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(data)
        parts.append(b"\r\n")

    def add_text_field(field_name: str, content_type: str, text: str) -> None:
        parts.append(f"--{boundary}\r\n".encode("ascii"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(text.encode("utf-8"))
        parts.append(b"\r\n")

    audio_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
    add_bytes_field("audio", audio_path.name, audio_type, audio_path.read_bytes())

    if image_bytes:
        add_bytes_field("image", "wake_capture.jpg", "image/jpeg", image_bytes)

    if metadata is not None:
        add_text_field("metadata", "application/json; charset=utf-8", json.dumps(metadata, ensure_ascii=False))

    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    body = b"".join(parts)

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(voice_chat.format_http_error(exc, url)) from exc

    text = raw.decode("utf-8", errors="replace")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("server JSON response is not an object")
    return parsed


class WakeVolumeRecorder:
    def __init__(self, args: argparse.Namespace, sample_rate: int, wake_hook: Any | None = None) -> None:
        self.args = args
        self.sample_rate = sample_rate
        self.target_rate = voice_chat.SAMPLE_RATE
        self.frames_per_chunk = max(256, int(round(sample_rate * args.wake_chunk_ms / 1000.0)))
        self.oww = None
        self.wake_hook = wake_hook
        self.ambient_volumes: list[int] = []
        self.ambient_max_chunks = max(5, int(round(5.0 * self.sample_rate / self.frames_per_chunk)))

    def refresh_input_device(self) -> None:
        """Re-resolve the USB mic before opening each PortAudio stream."""
        selected = select_input_device(self.args)
        self.args.device = selected
        self.sample_rate = voice_chat.choose_input_sample_rate(selected, self.args.input_sample_rate)
        self.frames_per_chunk = max(256, int(round(self.sample_rate * self.args.wake_chunk_ms / 1000.0)))
        self.ambient_max_chunks = max(5, int(round(5.0 * self.sample_rate / self.frames_per_chunk)))

    def load_wake_model(self) -> None:
        if self.args.no_wake_word:
            return
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: openwakeword. Install it in the voice venv:\n"
                "  cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender\n"
                "  source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate\n"
                "  python3 -m pip install -r requirements.txt"
            ) from exc

        try:
            download_models([self.args.wake_word])
        except Exception as exc:
            print(f"WARNING: openWakeWord model download/check failed: {exc}")

        print(f"Loading openWakeWord model: {self.args.wake_word}")
        self.oww = Model(
            wakeword_models=[self.args.wake_word],
            inference_framework="onnx",
            vad_threshold=0.0,
        )
        print("openWakeWord ready.")

    def chunk_to_16k_int16(self, audio_float: np.ndarray) -> np.ndarray:
        audio_16k = resample_float(audio_float, self.sample_rate, self.target_rate)
        return to_int16(audio_16k)

    def wake_score(self, audio_16k_int16: np.ndarray) -> float:
        if self.args.no_wake_word:
            return 1.0
        if self.oww is None:
            raise RuntimeError("wake model is not loaded")
        scores: dict[str, Any] = self.oww.predict(audio_16k_int16)
        return float(scores.get(self.args.wake_word, 0.0))

    def reset_wake(self) -> None:
        if self.oww is not None:
            self.oww.reset()

    def recording_meta(
        self,
        *,
        reason: str,
        wake_score: float,
        wake_context: dict[str, Any],
        turn_source: str = "wake",
        noise_floor: int = 0,
        speech_start_threshold: int | None = None,
        silence_base_threshold: int | None = None,
        peak_volume: int = 0,
        duration_sec: float | None = None,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "wake_score": wake_score,
            "reason": reason,
            "turn_source": turn_source,
            "wake_context": wake_context,
            "input_sample_rate": self.sample_rate,
            "noise_floor": noise_floor,
            "speech_start_threshold": speech_start_threshold,
            "silence_base_threshold": silence_base_threshold,
            "peak_volume": peak_volume,
        }
        if duration_sec is not None:
            meta["duration_sec"] = duration_sec
        return meta

    def remember_ambient(self, volume: int) -> None:
        self.ambient_volumes.append(int(volume))
        if len(self.ambient_volumes) > self.ambient_max_chunks:
            del self.ambient_volumes[: len(self.ambient_volumes) - self.ambient_max_chunks]

    def record_once(self) -> tuple[np.ndarray | None, dict[str, Any]]:
        import sounddevice as sd

        self.refresh_input_device()
        device_label = "default" if self.args.device is None else str(self.args.device)
        print()
        if self.args.no_wake_word:
            print("Listening for voice volume. Wake word is disabled.")
        else:
            print(
                f"Listening for wake word '{self.args.wake_word}' "
                f"(device={device_label}, wake_threshold={self.args.wake_threshold})."
            )

        state = "waiting_wake"
        record_chunks: list[np.ndarray] = []
        speech_started_at: float | None = None
        silence_started_at: float | None = None
        wake_detected_at: float | None = None
        wake_score_at_start = 0.0
        wake_context: dict[str, Any] = {}
        last_ignored_wake_at = 0.0
        last_standby_progress_log_at = 0.0
        last_wake_status_write_at = 0.0
        last_recording_progress_log_at = 0.0
        ambient_volumes: list[int] = []
        ambient_max_chunks = max(5, int(round(5.0 * self.sample_rate / self.frames_per_chunk)))
        recent_wake_volumes: list[int] = []
        wake_volume_window_chunks = max(
            1,
            int(
                round(
                    float(getattr(self.args, "wake_volume_window_seconds", 1.0) or 1.0)
                    * self.sample_rate
                    / self.frames_per_chunk
                )
            ),
        )
        pre_speech_chunks: list[np.ndarray] = []
        pre_speech_max_chunks = max(1, int(round(self.args.pre_speech_seconds * self.sample_rate / self.frames_per_chunk)))
        noise_floor = 0
        speech_start_threshold = int(self.args.volume_min)
        silence_base_threshold = int(self.args.volume_min)
        peak_volume = 0
        last_audio_timeout_warn_at = 0.0
        audio_status_warn_at = 0.0
        music_guard_active = False
        music_guard_checked_at = 0.0
        music_guard_confirm_chunks = 0
        music_guard_notice_at = 0.0
        max_queue_chunks = max(20, int(round(3.0 * self.sample_rate / self.frames_per_chunk)))
        audio_read_timeout = max(0.1, float(getattr(self.args, "audio_read_timeout", 1.0) or 1.0))
        progress_interval = max(0.25, float(getattr(self.args, "recording_progress_interval", 1.0) or 1.0))
        standby_progress_interval = max(0.0, float(getattr(self.args, "standby_progress_interval", 1.5) or 0.0))
        audio_queue: queue.Queue = queue.Queue(maxsize=max_queue_chunks)
        callback_state = {"dropped_chunks": 0}
        write_wake_status(
            self.args,
            phase="standby",
            volume=0,
            recent_peak=0,
            wake_score=0.0,
            wake_threshold=float(self.args.wake_threshold),
            wake_volume_threshold=int(getattr(self.args, "wake_volume_min", 0) or 0),
            noise_floor=0,
        )

        def audio_callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            chunk = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
            item = (chunk, status)
            try:
                audio_queue.put_nowait(item)
            except queue.Full:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    audio_queue.put_nowait(item)
                except queue.Full:
                    pass
                callback_state["dropped_chunks"] = int(callback_state.get("dropped_chunks", 0)) + 1

        def drain_audio_queue() -> int:
            drained = 0
            while True:
                try:
                    audio_queue.get_nowait()
                    drained += 1
                except queue.Empty:
                    return drained

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.args.device,
            blocksize=self.frames_per_chunk,
            callback=audio_callback,
        ):
            while True:
                now = time.monotonic()
                try:
                    chunk, input_status = audio_queue.get(timeout=audio_read_timeout)
                except queue.Empty:
                    now = time.monotonic()
                    if state == "recording" and wake_detected_at is not None:
                        elapsed_since_wake = now - wake_detected_at
                        max_recording_seconds = float(getattr(self.args, "max_recording_seconds", 0.0) or 0.0)
                        if max_recording_seconds > 0.0 and elapsed_since_wake >= max_recording_seconds:
                            if speech_started_at is not None and record_chunks:
                                print(
                                    f"Max recording wall-clock reached ({max_recording_seconds:.1f}s since wake) "
                                    "during audio input timeout; sending buffered audio."
                                )
                                break
                            print(
                                f"Max recording wall-clock reached ({max_recording_seconds:.1f}s since wake) "
                                "during audio input timeout before speech; returning to standby."
                            )
                            return None, self.recording_meta(
                                reason="max_recording_before_speech_audio_timeout",
                                wake_score=wake_score_at_start,
                                wake_context=wake_context,
                                noise_floor=noise_floor,
                                speech_start_threshold=speech_start_threshold,
                                silence_base_threshold=silence_base_threshold,
                                peak_volume=peak_volume,
                            )
                        if speech_started_at is None and elapsed_since_wake >= self.args.wake_listen_timeout:
                            print(
                                "No speech after wake word while audio input was timing out; returning to standby."
                            )
                            return None, self.recording_meta(
                                reason="no_speech_after_wake_audio_timeout",
                                wake_score=wake_score_at_start,
                                wake_context=wake_context,
                                noise_floor=noise_floor,
                                speech_start_threshold=speech_start_threshold,
                                silence_base_threshold=silence_base_threshold,
                                peak_volume=peak_volume,
                            )
                    if now - last_audio_timeout_warn_at >= 2.0:
                        print(
                            "WARNING: microphone input produced no audio chunk for "
                            f"{audio_read_timeout:.1f}s; "
                            "will recover by exiting/reopening this recording loop if it persists."
                        )
                        last_audio_timeout_warn_at = now
                    if state == "waiting_wake":
                        return None, self.recording_meta(
                            reason="audio_input_timeout_waiting_wake",
                            wake_score=wake_score_at_start,
                            wake_context=wake_context,
                        )
                    continue

                audio_16k_int16 = self.chunk_to_16k_int16(chunk)
                volume = int16_volume(audio_16k_int16)
                now = time.monotonic()

                if input_status:
                    if now - audio_status_warn_at >= 2.0:
                        print(f"Audio input status: {input_status}; continuing.")
                        audio_status_warn_at = now
                dropped_chunks = int(callback_state.get("dropped_chunks", 0))
                if dropped_chunks:
                    callback_state["dropped_chunks"] = 0
                    print(f"Audio input queue dropped {dropped_chunks} stale chunk(s); continuing.")

                if state == "waiting_wake":
                    score = self.wake_score(audio_16k_int16)
                    recent_wake_volumes.append(volume)
                    if len(recent_wake_volumes) > wake_volume_window_chunks:
                        del recent_wake_volumes[: len(recent_wake_volumes) - wake_volume_window_chunks]
                    wake_gate_volume = max(recent_wake_volumes) if recent_wake_volumes else volume
                    wake_noise_floor, wake_volume_threshold = adaptive_wake_volume_threshold(
                        self.args,
                        ambient_volumes,
                        fallback_volume=volume,
                    )
                    music_guard_enabled = (
                        bool(getattr(self.args, "music_wake_guard", False))
                        and not getattr(self.args, "no_music_wake_guard", False)
                        and not getattr(self.args, "no_music", False)
                        and not getattr(self.args, "no_wake_word", False)
                    )
                    if music_guard_enabled:
                        health_interval = max(
                            0.2,
                            float(getattr(self.args, "music_wake_health_interval", 1.0) or 1.0),
                        )
                        if now - music_guard_checked_at >= health_interval:
                            music_guard_active = music_playback_active(self.args, timeout_sec=0.12)
                            music_guard_checked_at = now
                            if not music_guard_active:
                                music_guard_confirm_chunks = 0
                    else:
                        music_guard_active = False
                        music_guard_confirm_chunks = 0

                    active_wake_threshold = float(self.args.wake_threshold)
                    required_confirm_chunks = 1
                    if music_guard_active:
                        active_wake_threshold = max(
                            active_wake_threshold,
                            float(getattr(self.args, "music_wake_threshold", 0.98) or 0.98),
                        )
                        required_confirm_chunks = max(
                            1,
                            int(getattr(self.args, "music_wake_confirm_chunks", 2) or 2),
                        )
                        music_wake_volume_min = int(getattr(self.args, "music_wake_volume_min", 0) or 0)
                        if music_wake_volume_min > 0:
                            wake_volume_threshold = max(wake_volume_threshold, music_wake_volume_min)
                    if now - last_wake_status_write_at >= max(0.5, standby_progress_interval or 1.0):
                        write_wake_status(
                            self.args,
                            phase="standby",
                            volume=volume,
                            recent_peak=wake_gate_volume,
                            wake_score=round(score, 4),
                            wake_threshold=round(active_wake_threshold, 4),
                            wake_volume_threshold=wake_volume_threshold,
                            noise_floor=wake_noise_floor,
                            music_guard=music_guard_active,
                        )
                        last_wake_status_write_at = now
                    if self.args.listen_debug:
                        print(
                            f"vol={volume:5d} | recent_peak={wake_gate_volume:5d} | wake={score:.3f} | "
                            f"wake>={active_wake_threshold:.2f} | wake_vol>={wake_volume_threshold} | standby",
                            end="\r",
                            file=sys.stderr,
                        )
                    elif (
                        standby_progress_interval > 0.0
                        and now - last_standby_progress_log_at >= standby_progress_interval
                        and (volume >= self.args.idle_volume_print_min or score >= 0.1)
                    ):
                        print(
                            "Standby audio: "
                            f"volume={volume}, recent_peak={wake_gate_volume}, wake={score:.3f}, "
                            f"wake_threshold={active_wake_threshold:.2f}, "
                            f"wake_volume_threshold={wake_volume_threshold}, noise_floor={wake_noise_floor}"
                            + (
                                f", music_guard=on confirm={music_guard_confirm_chunks}/{required_confirm_chunks}"
                                if music_guard_active
                                else ""
                            )
                        )
                        last_standby_progress_log_at = now

                    if music_guard_active and self.args.wake_threshold <= score < active_wake_threshold:
                        music_guard_confirm_chunks = 0
                        if now - music_guard_notice_at >= 2.0:
                            print(
                                "\nMusic wake-like score ignored: "
                                f"score={score:.2f} < music_wake_threshold={active_wake_threshold:.2f}. "
                                "Say Hey Jarvis louder/closer to interrupt music."
                            )
                            music_guard_notice_at = now

                    if score >= active_wake_threshold and wake_gate_volume < wake_volume_threshold:
                        music_guard_confirm_chunks = 0
                        if now - last_ignored_wake_at >= 2.0:
                            print(
                                f"\nLow-volume wake-like score ignored: score={score:.2f}, "
                                f"volume={volume}, recent_peak={wake_gate_volume} "
                                f"< dynamic_wake_volume_threshold={wake_volume_threshold} "
                                f"(noise_floor={wake_noise_floor}). "
                                "Speak closer, lower --wake-volume-ratio, or increase --wake-volume-window-seconds if this was really you."
                            )
                            last_ignored_wake_at = now

                    wake_candidate = score >= active_wake_threshold and wake_gate_volume >= wake_volume_threshold
                    if music_guard_active:
                        if wake_candidate:
                            music_guard_confirm_chunks += 1
                            if music_guard_confirm_chunks < required_confirm_chunks:
                                if now - music_guard_notice_at >= 2.0:
                                    print(
                                        "\nMusic wake candidate needs confirmation: "
                                        f"score={score:.2f}, "
                                        f"confirm={music_guard_confirm_chunks}/{required_confirm_chunks}."
                                    )
                                    music_guard_notice_at = now
                                ambient_volumes.append(volume)
                                self.remember_ambient(volume)
                                if len(ambient_volumes) > ambient_max_chunks:
                                    del ambient_volumes[: len(ambient_volumes) - ambient_max_chunks]
                                continue
                        else:
                            music_guard_confirm_chunks = 0

                    if wake_candidate:
                        noise_floor, speech_start_threshold, silence_base_threshold = adaptive_recording_thresholds(
                            self.args,
                            ambient_volumes,
                            fallback_volume=wake_gate_volume,
                        )
                        wake_detected_at = now
                        wake_score_at_start = score
                        state = "recording"
                        record_chunks = []
                        pre_speech_chunks = []
                        speech_started_at = None
                        silence_started_at = None
                        last_recording_progress_log_at = 0.0
                        peak_volume = 0
                        self.reset_wake()
                        print()
                        print(f"Wake detected: {self.args.wake_word} score={score:.2f}")
                        print(
                            "Recording thresholds: "
                            f"noise_floor={noise_floor}, "
                            f"speech_start_threshold={speech_start_threshold}, "
                            f"silence_base_threshold={silence_base_threshold}, "
                            f"wake_volume_threshold={wake_volume_threshold}, "
                            f"wake_gate_volume={wake_gate_volume}, "
                            f"music_guard={'on' if music_guard_active else 'off'}, "
                            f"adaptive={'off' if self.args.no_adaptive_volume else 'on'}"
                        )
                        write_wake_status(
                            self.args,
                            phase="wake_detected",
                            volume=volume,
                            recent_peak=wake_gate_volume,
                            wake_score=round(score, 4),
                            wake_threshold=round(active_wake_threshold, 4),
                            wake_volume_threshold=wake_volume_threshold,
                            noise_floor=noise_floor,
                            speech_start_threshold=speech_start_threshold,
                            silence_base_threshold=silence_base_threshold,
                        )
                        last_wake_status_write_at = now
                        if self.wake_hook is not None:
                            try:
                                hook_result = self.wake_hook(
                                    {
                                        "wake_score": score,
                                        "wake_detected_at": now,
                                        "wake_word": self.args.wake_word,
                                    }
                                )
                                if isinstance(hook_result, dict):
                                    wake_context = hook_result
                                    music_pause_result = hook_result.get("music_pause_result")
                                    if (
                                        getattr(self.args, "music_reset_recording_gate_on_wake", True)
                                        and isinstance(music_pause_result, dict)
                                        and (music_pause_result.get("paused") or music_pause_result.get("stopped"))
                                    ):
                                        (
                                            noise_floor,
                                            speech_start_threshold,
                                            silence_base_threshold,
                                        ) = adaptive_recording_thresholds(
                                            self.args,
                                            [],
                                            fallback_volume=int(getattr(self.args, "volume_min", 700)),
                                        )
                                        ambient_volumes.clear()
                                        self.ambient_volumes.clear()
                                        if getattr(self.args, "music_debug", False):
                                            print(
                                                "Music wake gate reset after pause: "
                                                f"noise_floor={noise_floor}, "
                                                f"speech_start_threshold={speech_start_threshold}, "
                                                f"silence_base_threshold={silence_base_threshold}"
                                            )
                            except Exception as exc:
                                print(f"WARNING: wake hook failed: {exc}")
                        drained_chunks = drain_audio_queue()
                        wake_detected_at = time.monotonic()
                        if drained_chunks and self.args.listen_debug:
                            print(f"Drained {drained_chunks} pre-recording audio chunk(s) after wake hook.")
                        print("Recording. Speak now; I will stop after silence.")
                    else:
                        ambient_volumes.append(volume)
                        self.remember_ambient(volume)
                        if len(ambient_volumes) > ambient_max_chunks:
                            del ambient_volumes[: len(ambient_volumes) - ambient_max_chunks]
                    continue

                elapsed_since_wake = now - wake_detected_at if wake_detected_at is not None else 0.0
                max_recording_seconds = float(getattr(self.args, "max_recording_seconds", 0.0) or 0.0)
                if wake_detected_at is not None and max_recording_seconds > 0.0 and elapsed_since_wake >= max_recording_seconds:
                    if speech_started_at is not None and record_chunks:
                        print(
                            f"Max recording wall-clock reached ({max_recording_seconds:.1f}s since wake); sending."
                        )
                        break
                    print(
                        f"Max recording wall-clock reached ({max_recording_seconds:.1f}s since wake) "
                        "before speech; returning to standby."
                    )
                    return None, self.recording_meta(
                        reason="max_recording_before_speech",
                        wake_score=wake_score_at_start,
                        wake_context=wake_context,
                        noise_floor=noise_floor,
                        speech_start_threshold=speech_start_threshold,
                        silence_base_threshold=silence_base_threshold,
                        peak_volume=peak_volume,
                    )

                is_voice_loud = volume >= speech_start_threshold
                if self.args.listen_debug:
                    current_silence_threshold = adaptive_silence_threshold(self.args, silence_base_threshold, peak_volume)
                    print(
                        f"vol={volume:5d} | start>={speech_start_threshold} "
                        f"| silence<={current_silence_threshold} | recording",
                        end="\r",
                        file=sys.stderr,
                    )
                elif wake_detected_at is not None and now - last_recording_progress_log_at >= progress_interval:
                    current_silence_threshold = adaptive_silence_threshold(self.args, silence_base_threshold, peak_volume)
                    phase = "speech" if speech_started_at is not None else "waiting_speech"
                    cap_label = f", max_recording={max_recording_seconds:.1f}s" if max_recording_seconds > 0 else ""
                    wait_label = ""
                    if speech_started_at is None and wake_detected_at is not None:
                        remaining = max(0.0, self.args.wake_listen_timeout - elapsed_since_wake)
                        wait_label = f", wake_timeout_in={remaining:.1f}s"
                    print(
                        f"Recording progress: phase={phase}, elapsed={elapsed_since_wake:.1f}s, "
                        f"volume={volume}, start_threshold={speech_start_threshold}, "
                        f"silence_threshold={current_silence_threshold}, peak={peak_volume}{cap_label}{wait_label}"
                    )
                    write_wake_status(
                        self.args,
                        phase=phase,
                        volume=volume,
                        recent_peak=max(peak_volume, volume),
                        wake_score=round(wake_score_at_start, 4),
                        wake_threshold=round(float(self.args.wake_threshold), 4),
                        noise_floor=noise_floor,
                        speech_start_threshold=speech_start_threshold,
                        silence_base_threshold=silence_base_threshold,
                        silence_threshold=current_silence_threshold,
                    )
                    last_recording_progress_log_at = now

                if speech_started_at is None:
                    pre_speech_chunks.append(chunk.copy())
                    if len(pre_speech_chunks) > pre_speech_max_chunks:
                        del pre_speech_chunks[: len(pre_speech_chunks) - pre_speech_max_chunks]

                if speech_started_at is None and is_voice_loud:
                    if speech_started_at is None:
                        speech_started_at = now
                        peak_volume = max(peak_volume, volume)
                        record_chunks.extend(pre_speech_chunks)
                        pre_speech_chunks = []
                        print(
                            f"Speech started. volume={volume}, "
                            f"start_threshold={speech_start_threshold}, noise_floor={noise_floor}"
                        )
                    silence_started_at = None
                elif speech_started_at is not None:
                    peak_volume = max(peak_volume, volume)
                    record_chunks.append(chunk.copy())
                    silence_threshold = adaptive_silence_threshold(self.args, silence_base_threshold, peak_volume)
                    is_silence = volume <= silence_threshold
                    if is_silence:
                        if silence_started_at is None:
                            silence_started_at = now
                            if self.args.listen_debug:
                                print(
                                    f"\nSilence candidate. volume={volume} <= "
                                    f"silence_threshold={silence_threshold}, peak={peak_volume}"
                                )
                        elif now - silence_started_at >= self.args.silence_duration:
                            print(
                                f"Silence detected. volume={volume}, "
                                f"silence_threshold={silence_threshold}, peak={peak_volume}"
                            )
                            break
                    else:
                        silence_started_at = None
                elif wake_detected_at is not None and now - wake_detected_at >= self.args.wake_listen_timeout:
                    print(
                        "No speech after wake word; returning to standby. "
                        f"If you were speaking, lower --volume-min or --speech-start-margin."
                    )
                    return None, self.recording_meta(
                        reason="no_speech_after_wake",
                        wake_score=wake_score_at_start,
                        wake_context=wake_context,
                        noise_floor=noise_floor,
                        speech_start_threshold=speech_start_threshold,
                        silence_base_threshold=silence_base_threshold,
                        peak_volume=peak_volume,
                    )

                if speech_started_at is not None and now - speech_started_at >= self.args.max_speech_seconds:
                    print(f"Max speech length reached ({self.args.max_speech_seconds:.1f}s); sending.")
                    break

        if not record_chunks:
            return None, {
                "wake_score": wake_score_at_start,
                "reason": "empty_recording",
                "turn_source": "wake",
                "input_sample_rate": self.sample_rate,
            }

        audio = np.concatenate(record_chunks).astype(np.float32)
        duration = len(audio) / float(self.sample_rate)
        if duration < self.args.min_speech_seconds:
            print(f"Recording too short ({duration:.2f}s); dropping.")
            return None, {
                "wake_score": wake_score_at_start,
                "duration_sec": duration,
                "reason": "too_short",
                "turn_source": "wake",
                "wake_context": wake_context,
                "input_sample_rate": self.sample_rate,
            }

        return audio, {
            "wake_score": wake_score_at_start,
            "duration_sec": duration,
            "reason": "ok",
            "turn_source": "wake",
            "wake_context": wake_context,
            "input_sample_rate": self.sample_rate,
            "noise_floor": noise_floor,
            "speech_start_threshold": speech_start_threshold,
            "silence_base_threshold": silence_base_threshold,
            "peak_volume": peak_volume,
        }

    def record_followup_turn(
        self,
        *,
        listen_timeout: float | None = None,
        turn_source: str = "conversation_followup",
    ) -> tuple[np.ndarray | None, dict[str, Any]]:
        """Record one already-awake conversation turn without running wake-word ASR upload."""
        import sounddevice as sd

        timeout = float(self.args.turn_listen_timeout if listen_timeout is None else listen_timeout)
        self.refresh_input_device()
        device_label = "default" if self.args.device is None else str(self.args.device)
        print()
        print(f"Conversation listening for follow-up speech (device={device_label}, timeout={timeout:.1f}s).")

        record_chunks: list[np.ndarray] = []
        pre_speech_chunks: list[np.ndarray] = []
        pre_speech_max_chunks = max(1, int(round(self.args.pre_speech_seconds * self.sample_rate / self.frames_per_chunk)))
        speech_started_at: float | None = None
        silence_started_at: float | None = None
        listen_started_at = time.monotonic()
        wake_score_at_start = 1.0
        noise_floor, speech_start_threshold, silence_base_threshold = adaptive_recording_thresholds(
            self.args,
            self.ambient_volumes,
            fallback_volume=0,
        )
        peak_volume = 0
        last_recording_progress_log_at = 0.0
        last_audio_timeout_warn_at = 0.0
        audio_status_warn_at = 0.0
        max_queue_chunks = max(20, int(round(3.0 * self.sample_rate / self.frames_per_chunk)))
        audio_read_timeout = max(0.1, float(getattr(self.args, "audio_read_timeout", 1.0) or 1.0))
        progress_interval = max(0.25, float(getattr(self.args, "recording_progress_interval", 1.0) or 1.0))
        audio_queue: queue.Queue = queue.Queue(maxsize=max_queue_chunks)
        callback_state = {"dropped_chunks": 0}

        print(
            "Follow-up thresholds: "
            f"noise_floor={noise_floor}, "
            f"speech_start_threshold={speech_start_threshold}, "
            f"silence_base_threshold={silence_base_threshold}, "
            f"speech_ratio={float(getattr(self.args, 'speech_start_ratio', 1.25) or 0.0):g}, "
            f"silence_noise_ratio={float(getattr(self.args, 'silence_noise_ratio', 1.15) or 0.0):g}, "
            f"adaptive={'off' if self.args.no_adaptive_volume else 'on'}"
        )

        def audio_callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            chunk = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
            item = (chunk, status)
            try:
                audio_queue.put_nowait(item)
            except queue.Full:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    audio_queue.put_nowait(item)
                except queue.Full:
                    pass
                callback_state["dropped_chunks"] = int(callback_state.get("dropped_chunks", 0)) + 1

        def drain_audio_queue() -> int:
            drained = 0
            while True:
                try:
                    audio_queue.get_nowait()
                    drained += 1
                except queue.Empty:
                    return drained

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.args.device,
            blocksize=self.frames_per_chunk,
            callback=audio_callback,
        ):
            drain_audio_queue()
            while True:
                now = time.monotonic()
                try:
                    chunk, input_status = audio_queue.get(timeout=audio_read_timeout)
                except queue.Empty:
                    now = time.monotonic()
                    elapsed = now - listen_started_at
                    max_recording_seconds = float(getattr(self.args, "max_recording_seconds", 0.0) or 0.0)
                    if max_recording_seconds > 0.0 and elapsed >= max_recording_seconds:
                        if speech_started_at is not None and record_chunks:
                            print(
                                f"Max recording wall-clock reached ({max_recording_seconds:.1f}s); "
                                "sending buffered follow-up audio."
                            )
                            break
                        print("Max recording wall-clock reached before follow-up speech; returning to standby.")
                        return None, self.recording_meta(
                            reason="max_recording_before_followup_speech_audio_timeout",
                            wake_score=wake_score_at_start,
                            wake_context={},
                            turn_source=turn_source,
                            noise_floor=noise_floor,
                            speech_start_threshold=speech_start_threshold,
                            silence_base_threshold=silence_base_threshold,
                            peak_volume=peak_volume,
                        )
                    if speech_started_at is None and elapsed >= timeout:
                        print(f"No follow-up speech for {timeout:.1f}s; returning to standby.")
                        return None, self.recording_meta(
                            reason="no_followup_speech_audio_timeout",
                            wake_score=wake_score_at_start,
                            wake_context={},
                            turn_source=turn_source,
                            noise_floor=noise_floor,
                            speech_start_threshold=speech_start_threshold,
                            silence_base_threshold=silence_base_threshold,
                            peak_volume=peak_volume,
                        )
                    if now - last_audio_timeout_warn_at >= 2.0:
                        print(
                            "WARNING: microphone input produced no audio chunk for "
                            f"{audio_read_timeout:.1f}s while listening for follow-up."
                        )
                        last_audio_timeout_warn_at = now
                    continue

                audio_16k_int16 = self.chunk_to_16k_int16(chunk)
                volume = int16_volume(audio_16k_int16)
                now = time.monotonic()
                elapsed = now - listen_started_at

                if input_status and now - audio_status_warn_at >= 2.0:
                    print(f"Audio input status: {input_status}; continuing.")
                    audio_status_warn_at = now
                dropped_chunks = int(callback_state.get("dropped_chunks", 0))
                if dropped_chunks:
                    callback_state["dropped_chunks"] = 0
                    print(f"Audio input queue dropped {dropped_chunks} stale chunk(s); continuing.")

                max_recording_seconds = float(getattr(self.args, "max_recording_seconds", 0.0) or 0.0)
                if max_recording_seconds > 0.0 and elapsed >= max_recording_seconds:
                    if speech_started_at is not None and record_chunks:
                        print(f"Max recording wall-clock reached ({max_recording_seconds:.1f}s); sending.")
                        break
                    print("Max recording wall-clock reached before follow-up speech; returning to standby.")
                    return None, self.recording_meta(
                        reason="max_recording_before_followup_speech",
                        wake_score=wake_score_at_start,
                        wake_context={},
                        turn_source=turn_source,
                        noise_floor=noise_floor,
                        speech_start_threshold=speech_start_threshold,
                        silence_base_threshold=silence_base_threshold,
                        peak_volume=peak_volume,
                    )

                is_voice_loud = volume >= speech_start_threshold
                current_silence_threshold = adaptive_silence_threshold(self.args, silence_base_threshold, peak_volume)
                if self.args.listen_debug:
                    print(
                        f"vol={volume:5d} | start>={speech_start_threshold} "
                        f"| silence<={current_silence_threshold} | conversation",
                        end="\r",
                        file=sys.stderr,
                    )
                elif now - last_recording_progress_log_at >= progress_interval:
                    phase = "speech" if speech_started_at is not None else "waiting_speech"
                    wait_label = ""
                    if speech_started_at is None:
                        remaining = max(0.0, timeout - elapsed)
                        wait_label = f", followup_timeout_in={remaining:.1f}s"
                    print(
                        f"Follow-up progress: phase={phase}, elapsed={elapsed:.1f}s, "
                        f"volume={volume}, start_threshold={speech_start_threshold}, "
                        f"silence_threshold={current_silence_threshold}, peak={peak_volume}{wait_label}"
                    )
                    last_recording_progress_log_at = now

                if speech_started_at is None:
                    pre_speech_chunks.append(chunk.copy())
                    if len(pre_speech_chunks) > pre_speech_max_chunks:
                        del pre_speech_chunks[: len(pre_speech_chunks) - pre_speech_max_chunks]
                    if not is_voice_loud:
                        self.remember_ambient(volume)

                if speech_started_at is None and is_voice_loud:
                    speech_started_at = now
                    peak_volume = max(peak_volume, volume)
                    record_chunks.extend(pre_speech_chunks)
                    pre_speech_chunks = []
                    print(
                        f"Follow-up speech started. volume={volume}, "
                        f"start_threshold={speech_start_threshold}, noise_floor={noise_floor}"
                    )
                    silence_started_at = None
                elif speech_started_at is not None:
                    peak_volume = max(peak_volume, volume)
                    record_chunks.append(chunk.copy())
                    silence_threshold = adaptive_silence_threshold(self.args, silence_base_threshold, peak_volume)
                    is_silence = volume <= silence_threshold
                    if is_silence:
                        if silence_started_at is None:
                            silence_started_at = now
                            if self.args.listen_debug:
                                print(
                                    f"\nFollow-up silence candidate. volume={volume} <= "
                                    f"silence_threshold={silence_threshold}, peak={peak_volume}"
                                )
                        elif now - silence_started_at >= self.args.silence_duration:
                            print(
                                f"Follow-up silence detected. volume={volume}, "
                                f"silence_threshold={silence_threshold}, peak={peak_volume}"
                            )
                            break
                    else:
                        silence_started_at = None
                elif elapsed >= timeout:
                    print(f"No follow-up speech for {timeout:.1f}s; returning to standby.")
                    return None, self.recording_meta(
                        reason="no_followup_speech",
                        wake_score=wake_score_at_start,
                        wake_context={},
                        turn_source=turn_source,
                        noise_floor=noise_floor,
                        speech_start_threshold=speech_start_threshold,
                        silence_base_threshold=silence_base_threshold,
                        peak_volume=peak_volume,
                    )

                if speech_started_at is not None and now - speech_started_at >= self.args.max_speech_seconds:
                    print(f"Max follow-up speech length reached ({self.args.max_speech_seconds:.1f}s); sending.")
                    break

        if not record_chunks:
            return None, self.recording_meta(
                reason="empty_followup_recording",
                wake_score=wake_score_at_start,
                wake_context={},
                turn_source=turn_source,
                noise_floor=noise_floor,
                speech_start_threshold=speech_start_threshold,
                silence_base_threshold=silence_base_threshold,
                peak_volume=peak_volume,
            )

        audio = np.concatenate(record_chunks).astype(np.float32)
        duration = len(audio) / float(self.sample_rate)
        if duration < self.args.min_speech_seconds:
            print(f"Follow-up recording too short ({duration:.2f}s); dropping.")
            return None, self.recording_meta(
                reason="followup_too_short",
                wake_score=wake_score_at_start,
                wake_context={},
                turn_source=turn_source,
                noise_floor=noise_floor,
                speech_start_threshold=speech_start_threshold,
                silence_base_threshold=silence_base_threshold,
                peak_volume=peak_volume,
                duration_sec=duration,
            )

        return audio, self.recording_meta(
            reason="ok",
            wake_score=wake_score_at_start,
            wake_context={},
            turn_source=turn_source,
            noise_floor=noise_floor,
            speech_start_threshold=speech_start_threshold,
            silence_base_threshold=silence_base_threshold,
            peak_volume=peak_volume,
            duration_sec=duration,
        )


def select_input_device(args: argparse.Namespace) -> int | None:
    keyword = str(getattr(args, "mic_keyword", "") or "").strip()
    manual_device = bool(getattr(args, "_manual_input_device", args.device is not None))
    recovery_hint = (
        "If USB devices disappeared from lsusb, run:\n"
        "  cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender && ./recover_demo_usb.sh"
    )

    # In demo mode the USB microphone's PortAudio index can change after
    # unplug/replug. If the user selected by keyword, always rescan by name
    # instead of trusting the previous numeric index.
    if keyword and not manual_device and not bool(getattr(args, "no_mic_fallback", False)):
        selected = find_device_by_keyword(keyword)
        if selected is None:
            selected = wait_for_sounddevice_keyword(
                keyword,
                output=False,
                timeout_sec=float(getattr(args, "device_ready_timeout", 12.0) or 0.0),
                label="input",
            )
        if selected is not None:
            if getattr(args, "_last_selected_input_device", None) != selected:
                print(f"Selected input device {selected} by keyword {keyword!r}.")
            args._last_selected_input_device = selected
            return selected

        now = time.monotonic()
        if now - float(getattr(args, "_last_input_missing_warn_at", 0.0)) >= 5.0:
            list_sounddevice_inputs()
            args._last_input_missing_warn_at = now
        if not args.allow_default_mic:
            raise RuntimeError(
                f"No microphone matching --mic-keyword {keyword!r} was found. "
                "Replug the UACDemo USB audio device, run `python3 wake_voice_chat_frdm_bridge.py --list-mics`, "
                "or pass --allow-default-mic for a temporary fallback.\n"
                f"{recovery_hint}"
            )
        print("WARNING: using default input because no matching USB microphone was found.")
        return None

    selected = bridge.resolve_input_device(args)
    if selected is not None:
        args._last_selected_input_device = selected
    if selected is None and keyword:
        list_sounddevice_inputs()
        if not args.allow_default_mic:
            raise RuntimeError(
                f"No microphone matching --mic-keyword {keyword!r} was found. "
                "USB audio is probably disconnected or the Jetson USB controller reset. "
                "Run `lsusb`, `arecord -l`, or reset USB before starting the bridge.\n"
                f"{recovery_hint}"
            )
        print("WARNING: using default input because no matching USB microphone was found.")
    return selected


def select_beep_output_device(args: argparse.Namespace) -> int | None:
    if args.no_beep:
        return None

    cached_device = getattr(args, "_last_selected_beep_device", None)
    if cached_device is not None and output_device_info(cached_device) is not None:
        return int(cached_device)

    manual_device = bool(getattr(args, "_manual_beep_device", args.beep_device is not None))
    if manual_device and args.beep_device is not None:
        if output_device_info(args.beep_device) is not None:
            return args.beep_device
        print(f"WARNING: --beep-device {args.beep_device} is not a usable output device; trying keyword fallback.")

    if manual_device and not str(getattr(args, "beep_keyword", "") or "").strip():
        return None

    keyword = str(getattr(args, "beep_keyword", "") or getattr(args, "mic_keyword", "") or "").strip()
    if not keyword:
        return None
    found = find_output_device_by_keyword(keyword)
    if found is None:
        found = wait_for_sounddevice_keyword(
            keyword,
            output=True,
            timeout_sec=float(getattr(args, "beep_device_lookup_timeout", 0.25) or 0.0),
            label="output",
        )
    if found is not None:
        if getattr(args, "_last_selected_beep_device", None) != found:
            print(f"Selected beep output device {found} by keyword {keyword!r}.")
        args._last_selected_beep_device = found
        return found

    now = time.monotonic()
    if now - float(getattr(args, "_last_beep_missing_warn_at", 0.0)) >= 5.0:
        print(f"WARNING: no output device contains --beep-keyword {keyword!r}; beep will use system default output.")
        list_sounddevice_outputs()
        args._last_beep_missing_warn_at = now
    args._last_selected_beep_device = None
    return None


def play_recording_cue(args: argparse.Namespace, *, label: str = "Recording") -> bool:
    if args.no_beep:
        print(f"{label} beep skipped.")
        return True

    beep_player = str(getattr(args, "beep_player", "auto") or "auto").strip().lower()
    if beep_player == "sounddevice":
        args.beep_device = select_beep_output_device(args)
        beep_device = args.beep_device
    else:
        beep_device = None
    ok = play_recording_beep(
        duration_ms=args.beep_duration_ms,
        frequency_hz=args.beep_frequency,
        volume=args.beep_volume,
        device=beep_device,
        keyword=str(getattr(args, "beep_keyword", "") or getattr(args, "mic_keyword", "") or "UACDemo"),
        player=beep_player,
    )
    if not ok and beep_player == "sounddevice" and args.beep_device is not None and not getattr(args, "no_beep_default_retry", False):
        retry_delay = max(0.0, float(getattr(args, "beep_retry_delay", 0.12) or 0.0))
        if retry_delay > 0.0:
            time.sleep(retry_delay)
        print("Retrying recording beep on default output.")
        ok = play_recording_beep(
            duration_ms=args.beep_duration_ms,
            frequency_hz=args.beep_frequency,
            volume=args.beep_volume,
            device=None,
            keyword=str(getattr(args, "beep_keyword", "") or getattr(args, "mic_keyword", "") or "UACDemo"),
            player="sounddevice",
        )

    if ok:
        print(f"{label} beep played.")
    else:
        print(f"WARNING: {label.lower()} beep unavailable; continuing.")
    return ok


def build_wake_hook(
    args: argparse.Namespace,
    camera_manager: CameraManager | None,
    robot: RobotUartController,
    turn_state: dict[str, Any],
    pet_idle_manager: Any | None = None,
) -> Any:
    def on_wake(info: dict[str, Any]) -> dict[str, Any]:
        if pet_idle_manager is not None:
            pet_idle_manager.begin_user_interaction("wake detected")
        timing = TimingLogger()
        turn_state.clear()
        turn_state["timing"] = timing
        timing.mark("wake detected")

        pause_result = pause_music_for_wake(args)
        timing.mark("music paused for wake")
        settle_after_music_wake_pause(args, pause_result)

        metadata: dict[str, Any] = {
            "capture_timestamp": datetime.now(timezone.utc).isoformat(),
            "image_format": "jpeg",
            "source": "jetson",
            "client_version": CLIENT_VERSION,
            "wake_score": info.get("wake_score"),
            "wake_word": info.get("wake_word"),
            "camera_id": args.camera_id,
            "camera_width": args.camera_width,
            "camera_height": args.camera_height,
            "camera_jpeg_quality": args.camera_jpeg_quality,
            "vision_mode": "off" if args.no_vision else ("force" if args.force_vision else "auto"),
            "force_vision": args.force_vision,
            "no_vision": args.no_vision,
        }

        play_recording_cue(args, label="Recording")
        timing.mark("beep done")

        if (
            getattr(args, "music_wake_dashboard_update", False)
            and pause_result is not None
            and (pause_result.get("paused") or pause_result.get("stopped"))
        ):
            send_music_uart_update(args, robot, pause_result, reason="wake paused music dashboard")
            if not getattr(args, "no_uart", False):
                timing.mark("music pause dashboard sent")

        if not getattr(args, "no_uart", False):
            if robot.set_screen_state("Thinking"):
                print("UART Thinking sent.")
            else:
                print("WARNING: UART Thinking not sent; FRDM UART is unavailable.")
            timing.mark("UART Thinking sent")

        return {
            "image_future": None,
            "metadata": metadata,
            "timing": timing,
            "music_pause_result": pause_result,
        }

    return on_wake


def build_conversation_turn_context(
    args: argparse.Namespace,
    camera_manager: CameraManager | None,
    robot: RobotUartController,
    turn_state: dict[str, Any],
    *,
    session_id: str,
    turn_index: int,
    meta: dict[str, Any],
    play_cue: bool = False,
) -> dict[str, Any]:
    timing = TimingLogger()
    turn_state.clear()
    turn_state["timing"] = timing
    timing.mark("conversation follow-up cue started" if play_cue else "conversation follow-up context built")

    metadata: dict[str, Any] = {
        "capture_timestamp": datetime.now(timezone.utc).isoformat(),
        "image_format": "jpeg",
        "source": "jetson",
        "client_version": CLIENT_VERSION,
        "wake_score": meta.get("wake_score"),
        "wake_word": "conversation_followup",
        "conversation_mode": True,
        "conversation_session_id": session_id,
        "conversation_turn_index": turn_index,
        "turn_source": meta.get("turn_source", "conversation_followup"),
        "camera_id": args.camera_id,
        "camera_width": args.camera_width,
        "camera_height": args.camera_height,
        "camera_jpeg_quality": args.camera_jpeg_quality,
        "vision_mode": "off" if args.no_vision else ("force" if args.force_vision else "auto"),
        "force_vision": args.force_vision,
        "no_vision": args.no_vision,
    }

    if play_cue:
        play_recording_cue(args, label="Follow-up recording")
        timing.mark("follow-up beep done")

    if not getattr(args, "no_uart", False):
        if robot.is_screen_state_recent("Thinking", within_sec=8.0):
            print(f"UART Thinking skipped; already active {robot.screen_state_age():.2f}s ago for this conversation follow-up.")
        elif robot.set_screen_state("Thinking", reason="conversation follow-up listening"):
            print("UART Thinking sent.")
        else:
            print("WARNING: UART Thinking not sent; FRDM UART is unavailable.")
        timing.mark("UART Thinking sent")

    return {
        "image_future": None,
        "metadata": metadata,
        "timing": timing,
    }


def response_vision_summary(response: dict[str, Any]) -> None:
    if "vision_requested" not in response and "used_vision" not in response and "vision_intent" not in response:
        return
    print()
    print("Vision routing:")
    if "normalized_transcript" in response:
        print(f"  normalized_transcript : {response.get('normalized_transcript')}")
    print(f"  vision_intent    : {response.get('vision_intent', response.get('vision_requested', False))}")
    if "vision_reason" in response:
        print(f"  vision_reason    : {response.get('vision_reason')}")
    if "auto_vision_intent" in response:
        print(f"  auto_intent      : {response.get('auto_vision_intent')} ({response.get('auto_vision_reason')})")
    print(f"  used_vision       : {response.get('used_vision', False)}")
    if "image_received" in response:
        print(f"  image_received    : {response.get('image_received')}")
    if "image_size_bytes" in response or "image_bytes" in response:
        print(f"  image_size_bytes  : {response.get('image_size_bytes', response.get('image_bytes', 0))}")
    model = str(response.get("vision_model", "")).strip()
    if model:
        print(f"  vision_model      : {model}")
    attempted_model = str(response.get("vision_attempted_model", "")).strip()
    if attempted_model:
        print(f"  attempted_model   : {attempted_model}")
    error = str(response.get("vision_error", "")).strip()
    if error:
        print(f"  vision_error      : {error}")


def estimate_tts_seconds(text: str) -> float:
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))
    other_chars = max(0, len(text.strip()) - cjk_chars)
    estimate = cjk_chars / 4.5 + english_words / 2.6 + other_chars / 18.0 + 0.5
    return max(1.2, min(20.0, estimate))


def tts_queue_url(tts_url: str) -> str:
    return urllib.parse.urljoin(voice_chat.tts_base_url(tts_url) + "/", "queue")


def tts_health_url(tts_url: str) -> str:
    return urllib.parse.urljoin(voice_chat.tts_base_url(tts_url) + "/", "health")


def tts_audio_playing(args: argparse.Namespace) -> bool:
    health_url = tts_health_url(args.tts_url)
    try:
        health = voice_chat.get_json(health_url, timeout_sec=min(float(getattr(args, "tts_timeout", 5.0) or 5.0), 0.6))
    except Exception:
        return False
    audio = health.get("audio") if isinstance(health.get("audio"), dict) else {}
    return bool(audio.get("playing"))


def wait_for_tts_job(
    job_id: str,
    args: argparse.Namespace,
    *,
    timeout_sec: float,
    on_playback_start: Callable[[], None] | None = None,
) -> bool:
    if not job_id:
        require_audio_playing = bool(getattr(args, "tts_speaking_require_audio", True))
        if require_audio_playing:
            deadline = time.monotonic() + max(0.1, float(getattr(args, "tts_speaking_start_timeout", 1.2) or 1.2))
            while time.monotonic() < deadline:
                if tts_audio_playing(args):
                    if on_playback_start is not None:
                        on_playback_start()
                    break
                time.sleep(max(0.05, min(float(getattr(args, "tts_start_poll_interval", 0.12) or 0.12), 0.25)))
            else:
                if getattr(args, "tts_debug", False):
                    print("TTS playback was not observed for job without id; Speaking UART was not sent.")
        elif on_playback_start is not None:
            on_playback_start()
        time.sleep(timeout_sec)
        print(f"TTS estimated finished after {timeout_sec:.1f}s (no job id).")
        return True

    queue_url = tts_queue_url(args.tts_url)
    deadline = time.monotonic() + timeout_sec
    last_error = ""
    saw_current_job = False
    current_job_seen_at = 0.0
    playback_start_notified = False
    require_audio_playing = bool(getattr(args, "tts_speaking_require_audio", True))
    poll_interval = max(0.1, min(float(getattr(args, "tts_poll_interval", 0.75) or 0.75), 2.0))
    start_poll_interval = max(
        0.05,
        min(float(getattr(args, "tts_start_poll_interval", 0.12) or 0.12), poll_interval, 0.25),
    )
    start_fallback_sec = max(0.2, min(float(getattr(args, "tts_speaking_start_timeout", 1.2) or 1.2), 4.0))

    def notify_playback_start(detail: str) -> None:
        nonlocal playback_start_notified
        if playback_start_notified:
            return
        playback_start_notified = True
        if getattr(args, "tts_debug", False):
            print(f"TTS playback observed: {detail}")
        if on_playback_start is not None:
            on_playback_start()

    while time.monotonic() < deadline:
        try:
            status = voice_chat.get_json(queue_url, timeout_sec=min(args.tts_timeout, 2.0))
        except Exception as exc:
            last_error = str(exc)
            if not playback_start_notified and tts_audio_playing(args):
                notify_playback_start("TTS audio player reports output while queue status is unavailable")
            time.sleep(start_poll_interval if not playback_start_notified else poll_interval)
            continue

        last_result = status.get("last_result") if isinstance(status.get("last_result"), dict) else {}
        if last_result.get("job_id") == job_id:
            if not playback_start_notified:
                if not require_audio_playing:
                    notify_playback_start("job finished before start poll observed output")
                elif getattr(args, "tts_debug", False):
                    print("TTS finished before audio.playing was observed; Speaking UART was not sent.")
            if getattr(args, "tts_debug", False):
                playback = last_result.get("playback") if isinstance(last_result.get("playback"), dict) else {}
                print(f"TTS finished: job_id={job_id}, playback_volume_gain={playback.get('volume_gain', 'unknown')}")
            else:
                print(f"TTS finished: job_id={job_id}")
            return True
        current = status.get("current") if isinstance(status.get("current"), dict) else {}
        if current.get("id") == job_id:
            saw_current_job = True
            if current_job_seen_at <= 0.0:
                current_job_seen_at = time.monotonic()
            if not playback_start_notified:
                if tts_audio_playing(args):
                    notify_playback_start("TTS audio player reports output")
                elif not require_audio_playing and time.monotonic() - current_job_seen_at >= start_fallback_sec:
                    notify_playback_start("TTS queue current fallback")
        last_error_value = str(status.get("last_error", "") or "").strip()
        if last_error_value and saw_current_job and not status.get("running"):
            print(f"WARNING: TTS worker error for job_id={job_id}: {last_error_value}")
            return False
        time.sleep(start_poll_interval if not playback_start_notified else poll_interval)

    print(f"WARNING: TTS wait timed out after {timeout_sec:.1f}s for job_id={job_id}. last_error={last_error}")
    return False


class SpeakingPlaybackCue:
    def __init__(
        self,
        robot: RobotUartController,
        emotion: str,
        head_motion: str,
        timing: TimingLogger | None,
        *,
        timing_label: str = "UART Speaking emotion code sent",
        reset_reason: str = "speaking_head_motion stop reset",
    ) -> None:
        self.robot = robot
        self.emotion = emotion
        self.head_motion = head_motion
        self.timing = timing
        self.timing_label = timing_label
        self.reset_reason = reset_reason
        self.started = False
        self.head_thread: threading.Thread | None = None
        self.head_stop: threading.Event | None = None

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        sent = self.robot.send_speaking_and_emotion(self.emotion)
        if sent:
            self.head_thread, self.head_stop = self.robot.start_speaking_head_motion(self.head_motion)
        else:
            self.robot.stop_active_speaking_head_motion(reason=f"{self.reset_reason}; Speaking UART not sent")
        if self.timing is not None and sent:
            self.timing.mark(self.timing_label)

    def stop(self) -> None:
        if not self.started:
            self.robot.stop_active_speaking_head_motion(reason=f"{self.reset_reason}; playback cue never started")
            return
        self.started = False
        head_thread = self.head_thread
        head_stop = self.head_stop
        self.head_thread = None
        self.head_stop = None
        self.robot.stop_speaking_head_motion(head_thread, head_stop, reason=self.reset_reason)


def run_self_test() -> int:
    print("Running wake bridge self-test...")
    response_cases = [
        (
            {
                "transcript": "去睡覺吧",
                "reply": '{"reply":"好，我先安靜陪你休息。","control":{"persistent_state":"normal","emotion":"happy","head_motion":"nod","reason":"model"}}',
            },
            "sleep",
            "sleepy",
            "sleepy_drop",
        ),
        (
            {
                "transcript": "起床，回來",
                "reply": "我回來了，繼續待命！",
                "control": {"persistent_state": "sleep", "emotion": "sleepy", "head_motion": "sleepy_drop"},
            },
            "normal",
            "happy",
            "happy_bounce",
        ),
        (
            {
                "transcript": "講個笑話",
                "reply": '{"reply":"嘿嘿，這個有趣。","control":{"persistent_state":"unchanged","emotion":"happy","head_motion":"nod","reason":"joke"}}',
            },
            "unchanged",
            "happy",
            "nod",
        ),
        (
            {
                "transcript": "講個開心的事情",
                "reply": "好耶！",
                "control": {"persistent_state": "unchanged", "emotion": "happy", "head_motion": "none"},
            },
            "unchanged",
            "happy",
            "happy_bounce",
        ),
        (
            {
                "transcript": "講個笑話",
                "reply": "emotion 是 happy，所以我要送 Happy 0 0。",
            },
            "unchanged",
            "neutral",
            "gentle_nod",
        ),
    ]
    for response, expected_state, expected_emotion, expected_motion in response_cases:
        control = normalize_control(response)
        response["control"] = control
        reply = sanitize_reply(response)
        if control["persistent_state"] != expected_state:
            raise AssertionError(f"bad persistent_state: {control}")
        if control["emotion"] != expected_emotion:
            raise AssertionError(f"bad emotion: {control}")
        if control["head_motion"] != expected_motion:
            raise AssertionError(f"bad head_motion: {control}")
        lowered_reply = reply.lower()
        if any(marker in lowered_reply for marker in ("json", "uart", "motorpitch", "motoryaw", "persistent_state", "head_motion")):
            raise AssertionError(f"reply leaked internal text: {reply!r}")

    if set(HEAD_MOTION_SEQUENCES) != VALID_HEAD_MOTIONS:
        raise AssertionError(f"HEAD_MOTION_SEQUENCES mismatch: {sorted(set(HEAD_MOTION_SEQUENCES) ^ VALID_HEAD_MOTIONS)}")
    if set(SPEAKING_HEAD_MOTION_LOOPS) != VALID_HEAD_MOTIONS:
        raise AssertionError(f"SPEAKING_HEAD_MOTION_LOOPS mismatch: {sorted(set(SPEAKING_HEAD_MOTION_LOOPS) ^ VALID_HEAD_MOTIONS)}")

    for motion, sequence in HEAD_MOTION_SEQUENCES.items():
        if not sequence:
            raise AssertionError(f"empty head motion sequence: {motion}")
        if sequence[-1] != yaw_pitch(YAW_CENTER, PITCH_CENTER):
            raise AssertionError(f"head motion {motion} must end at center: {format_motor_sequence(sequence)}")
        for command, v1, v2 in sequence:
            if command not in MOTOR_COMMANDS:
                raise AssertionError(f"head motion {motion} uses non-motor command {command}")
            if command == "MotorYawPitch":
                in_range = (
                    clamp_int(v1, MOTOR_YAW_MIN, MOTOR_YAW_MAX) == int(v1)
                    and clamp_int(v2, MOTOR_PITCH_MIN, MOTOR_PITCH_MAX) == int(v2)
                )
            elif command == "MotorPitch":
                in_range = clamp_int(v1, MOTOR_PITCH_MIN, MOTOR_PITCH_MAX) == int(v1)
            else:
                in_range = clamp_int(v1, MOTOR_YAW_MIN, MOTOR_YAW_MAX) == int(v1)
            if not in_range:
                raise AssertionError(f"head motion {motion} value out of range: {command} {v1} {v2}")
            if command != "MotorYawPitch" and int(v2) != 0:
                raise AssertionError(f"head motion {motion} should keep the internal compatibility value at 0: {command} {v1} {v2}")

    combo_steps = [
        step
        for sequence in HEAD_MOTION_SEQUENCES.values()
        for step in sequence
        if step[0] == "MotorYawPitch" and step[1] != YAW_CENTER and step[2] != PITCH_CENTER
    ]
    if not combo_steps:
        raise AssertionError("head motions should include combined yaw+pitch MotorYawPitch poses")

    expanded_look = smooth_motor_sequence(HEAD_MOTION_SEQUENCES["look_around"], MOTOR_SMOOTH_STEP_DEG)
    if len(expanded_look) > len(HEAD_MOTION_SEQUENCES["look_around"]) + 2:
        raise AssertionError("look_around should stay keyframe-driven, not over-expanded into jittery micro-steps")
    if yaw_pitch(YAW_RIGHT, PITCH_ATTENTIVE) not in expanded_look or yaw_pitch(YAW_LEFT, PITCH_ATTENTIVE) not in expanded_look:
        raise AssertionError("look_around should visit clear right/left held poses")
    right_index = expanded_look.index(yaw_pitch(YAW_RIGHT, PITCH_ATTENTIVE))
    left_soft_index = expanded_look.index(yaw_pitch(YAW_LEFT_SOFT, PITCH_ATTENTIVE))
    between_right_and_left = expanded_look[right_index + 1 : left_soft_index]
    if any(step[0] == "MotorYawPitch" and step[1] == YAW_CENTER for step in between_right_and_left):
        raise AssertionError(f"look_around should turn directly from right to left without inserted center: {format_motor_sequence(expanded_look)}")
    previous_combo: tuple[int, int] | None = None
    previous_by_command: dict[str, int] = {}
    for command, value, value2 in expanded_look:
        if command == "MotorYawPitch":
            if previous_combo is not None:
                prev_yaw, prev_pitch = previous_combo
                allowed_direct_cross = crosses_yaw_center(prev_yaw, value)
                if (
                    not allowed_direct_cross
                    and (abs(value - prev_yaw) > MOTOR_SMOOTH_STEP_DEG or abs(value2 - prev_pitch) > MOTOR_SMOOTH_STEP_DEG)
                ):
                    raise AssertionError(f"smoothed {command} jump too large: {(prev_yaw, prev_pitch)} -> {(value, value2)}")
            previous_combo = (value, value2)
            continue
        previous = previous_by_command.get(command)
        if previous is not None and abs(value - previous) > MOTOR_SMOOTH_STEP_DEG:
            raise AssertionError(f"smoothed {command} jump too large: {previous} -> {value}")
        previous_by_command[command] = value

    dry_args = argparse.Namespace(
        no_uart=False,
        uart_dry_run=True,
        require_uart=False,
        uart_port="auto",
        uart_baudrate=115200,
        uart_timeout=0.2,
        uart_read_ms=30,
        uart_line_ending="crlf",
        uart_debug=False,
        no_frdm_uart_bus=False,
        frdm_uart_tx_timeout=0.45,
        frdm_uart_failure_threshold=2,
        frdm_uart_circuit_breaker_sec=4.0,
        _frdm_uart_bus_active=True,
        no_dashboard_uart=False,
        dashboard_todo_item_limit=8,
        no_frdm_todo_events=False,
        no_frdm_fan_events=False,
        no_fan_dashboard_sync=True,
        fan_speed_max=3,
        fan_duplicate_suppress_sec=2.0,
        fan_control_command="",
        motor_step_delay=0.02,
        motor_smooth_step_deg=MOTOR_SMOOTH_STEP_DEG,
        motor_speaking_step_delay=0.02,
        motor_speaking_smooth_step_deg=MOTOR_SPEAKING_SMOOTH_STEP_DEG,
        motor_stop_timeout=0.5,
        motor_reset_repeats=2,
        motor_reset_delay=0.02,
    )
    robot = RobotUartController(dry_args)
    safe_args = argparse.Namespace(uart_dry_run=False, enable_head_motor=False, disable_head_motor=False)
    if RobotUartController(safe_args).head_motor_enabled():
        raise AssertionError("head motor should be disabled by default")
    safe_args.enable_head_motor = True
    if not RobotUartController(safe_args).head_motor_enabled():
        raise AssertionError("--enable-head-motor should enable head motor")
    safe_args.disable_head_motor = True
    if RobotUartController(safe_args).head_motor_enabled():
        raise AssertionError("--disable-head-motor should override --enable-head-motor")
    if not robot.send_uart_command("Thinking", 0, 0, reason="self-test"):
        raise AssertionError("Thinking dry-run failed")
    if not robot.send_uart_command("Music", 0, 0, reason="self-test"):
        raise AssertionError("Music dry-run failed")
    if not robot.send_uart_command("Focus", 0, 0, reason="self-test"):
        raise AssertionError("Focus dry-run failed")
    if not robot.send_uart_raw_line("Time 20260509,213005,6,+480", reason="self-test"):
        raise AssertionError("Time raw dry-run failed")
    if not robot.send_uart_raw_line("Weather daily,23,29,40,61", reason="self-test"):
        raise AssertionError("Weather raw dry-run failed")
    if not robot.send_uart_raw_line("Weather daily,23,29,40,61,254", reason="self-test"):
        raise AssertionError("Weather+local-temperature raw dry-run failed")
    if not robot.send_uart_raw_line("Weather current,27,27,0,2", reason="self-test"):
        raise AssertionError("Weather current raw dry-run failed")
    if not robot.send_uart_raw_line("TempRoom 254", reason="self-test"):
        raise AssertionError("TempRoom raw dry-run failed")
    if not robot.send_uart_raw_line("Todo 3,1", reason="self-test"):
        raise AssertionError("Todo raw dry-run failed")
    if not robot.send_uart_raw_line("TodoItem 1,42,open,Write%20report", reason="self-test"):
        raise AssertionError("TodoItem raw dry-run failed")
    if not robot.send_uart_raw_line("TodoEnd 1", reason="self-test"):
        raise AssertionError("TodoEnd raw dry-run failed")
    if not robot.send_uart_raw_line("Music playing,Lo-fi%20Study,mpv", reason="self-test"):
        raise AssertionError("Music dashboard raw dry-run failed")
    if not robot.send_uart_raw_line("Focus focused,25,2", reason="self-test"):
        raise AssertionError("Focus dashboard raw dry-run failed")
    if not robot.send_uart_raw_line("Health win=1,tts=1,music=1,camera=1", reason="self-test"):
        raise AssertionError("Health raw dry-run failed")
    if not robot.send_uart_raw_line("Device desk_fan,on,67", reason="self-test"):
        raise AssertionError("Device raw dry-run failed")
    if robot.send_uart_command("UnknownCommand", 0, 0, reason="self-test"):
        raise AssertionError("unknown UART command should be rejected")
    if robot.send_uart_command("Happy", 0, 0, reason="self-test"):
        raise AssertionError("old emotion screen commands should be rejected")
    if not robot.set_screen_state("Thinking", reason="self-test first screen state"):
        raise AssertionError("Thinking screen state dry-run failed")
    first_thinking_at = robot._screen_state_at
    if not robot.set_screen_state("Thinking", reason="self-test duplicate screen state"):
        raise AssertionError("duplicate Thinking screen state should be a safe no-op")
    if robot._screen_state_at != first_thinking_at:
        raise AssertionError("duplicate Thinking should not resend/update screen state immediately")
    if robot._validate_command("Speaking", 9, 0) != ("Speaking", 5, 0):
        raise AssertionError("Speaking emotion code clamp failed")
    if robot._validate_command("MotorPitch", 999, 0) != ("MotorPitch", MOTOR_PITCH_MAX, 0):
        raise AssertionError("MotorPitch clamp failed")
    if robot._validate_command("MotorYaw", -99, 0) != ("MotorYaw", MOTOR_YAW_MIN, 0):
        raise AssertionError("MotorYaw clamp failed")
    if robot._validate_command("MotorYawPitch", 999, -99) != ("MotorYawPitch", MOTOR_YAW_MAX, MOTOR_PITCH_MIN):
        raise AssertionError("MotorYawPitch clamp failed")
    if robot._validate_command("MotorPitch", 90, -5) != ("MotorPitch", MOTOR_PITCH_CENTER, 0):
        raise AssertionError("MotorPitch second value clamp failed")
    if robot._validate_command("MotorYaw", MOTOR_YAW_CENTER, 123) != ("MotorYaw", MOTOR_YAW_CENTER, 0):
        raise AssertionError("MotorYaw second value clamp failed")
    if format_uart_wire_command("MotorPitch", MOTOR_PITCH_CENTER, 0) != "MotorPitch 90":
        raise AssertionError("MotorPitch wire format should use one argument")
    if format_uart_wire_command("MotorYaw", MOTOR_YAW_CENTER, 0) != "MotorYaw 90":
        raise AssertionError("MotorYaw wire format should use one argument")
    if format_uart_wire_command("MotorYawPitch", 120, 90) != "MotorYawPitch 120 90":
        raise AssertionError("MotorYawPitch wire format should use two arguments")
    if format_uart_wire_command("Thinking", 0, 0) != "Thinking 0 0":
        raise AssertionError("screen command wire format should keep two arguments")
    if format_uart_wire_command("Speaking", 3, 0) != "Speaking 3":
        raise AssertionError("Speaking should carry one 0..5 emotion-code argument")
    if format_uart_wire_command("TempRoom", 254, 0) != "TempRoom 254":
        raise AssertionError("TempRoom should carry one Celsius*10 argument")
    nod_control = normalize_control({"transcript": "你可以點頭嗎", "control": {"emotion": "neutral", "head_motion": "none"}})
    if nod_control["head_motion"] != "nod":
        raise AssertionError(f"direct nod intent should select nod head motion: {nod_control}")
    shake_control = normalize_control({"transcript": "你搖頭一下", "control": {"emotion": "neutral", "head_motion": "none"}})
    if shake_control["head_motion"] != "shake":
        raise AssertionError(f"direct shake intent should select shake head motion: {shake_control}")
    time_payload = format_time_uart_payload(datetime(2026, 5, 9, 21, 30, 5, tzinfo=timezone.utc))
    if time_payload != "20260509,213005,6,+0":
        raise AssertionError(f"time UART payload formatting failed: {time_payload}")
    if format_music_uart_payload({"ok": True, "action": "play", "query": "Lo-fi Study", "backend": "mpv"}, dry_args) != "playing,Lo-fi%20Study,mpv":
        raise AssertionError("music UART payload formatting failed")
    if format_focus_uart_payload("focused", 25, 2) != "focused,25,2":
        raise AssertionError("focus UART payload formatting failed")
    if parse_frdm_todo_done_event("TodoDone 42") != 42:
        raise AssertionError("FRDM TodoDone event parsing failed")
    if parse_frdm_todo_done_event("EVT,TodoDone,42") != 42:
        raise AssertionError("FRDM comma TodoDone event parsing failed")
    if frdm_event_parts("$EVT,Fan,1,2*00") != ["Fan", "1", "2"]:
        raise AssertionError("FRDM event part normalization failed")
    fan_event = parse_frdm_fan_event("EVT,Fan,1,2", speed_max=3)
    if not fan_event or fan_event["state"] != "on" or fan_event["speed"] != 2 or fan_event["percent"] != 67:
        raise AssertionError(f"FRDM fan event parsing failed: {fan_event}")
    fan_percent_event = parse_frdm_fan_event("FanSpeed 75", speed_max=100)
    if not fan_percent_event or fan_percent_event["state"] != "on" or fan_percent_event["percent"] != 75:
        raise AssertionError(f"FRDM fan percent parsing failed: {fan_percent_event}")
    fan_off = parse_frdm_fan_event("Fan off,0", speed_max=3)
    if not fan_off or fan_off["state"] != "off" or fan_off["percent"] != 0:
        raise AssertionError(f"FRDM fan off parsing failed: {fan_off}")
    if esp32_ble is None:
        raise AssertionError(f"ESP32 BLE helper import failed: {ESP32_BLE_IMPORT_ERROR}")
    if esp32_ble.percent_to_pwm(50) != esp32_ble.apply_min_nonzero_pwm(128):
        raise AssertionError("ESP32 BLE percent->PWM conversion failed")
    if esp32_ble.percent_to_pwm(16) != esp32_ble.apply_min_nonzero_pwm(41):
        raise AssertionError("ESP32 BLE low nonzero PWM floor failed")
    if esp32_ble.frdm_event_to_ble_commands("FanSpeed 75") != ["FAN_ON", f"FAN_SPEED:{esp32_ble.apply_min_nonzero_pwm(191)}"]:
        raise AssertionError("ESP32 BLE FRDM percent command conversion failed")
    voice_ble = esp32_ble.voice_text_to_ble_commands("請幫我開風扇", None)
    if voice_ble != ["FAN_ON", f"FAN_SPEED:{esp32_ble.apply_min_nonzero_pwm(180)}"]:
        raise AssertionError(f"ESP32 BLE voice fan command parsing failed: {voice_ble}")
    multi_ble = esp32_ble.voice_text_to_ble_commands("幫我開燈以及開風扇", None)
    if multi_ble != ["LED_ON", "FAN_ON", f"FAN_SPEED:{esp32_ble.apply_min_nonzero_pwm(180)}"]:
        raise AssertionError(f"ESP32 BLE multi-command parsing failed: {multi_ble}")
    if esp32_ble.voice_text_to_ble_commands("音樂太小聲，幫我調大音量", None) is not None:
        raise AssertionError("ESP32 BLE should not intercept audio volume requests")
    off_ble_status = esp32_ble.Esp32Status(
        raw="TEMP:24.00,FAN:OFF,SPEED:0,LED:OFF",
        temp_c=24.0,
        fan="OFF",
        speed=0,
        led="OFF",
    )
    faster_from_off = esp32_ble.voice_text_to_ble_commands("幫我調高風扇", off_ble_status)
    expected_faster_from_off = ["FAN_ON", f"FAN_SPEED:{esp32_ble.apply_min_nonzero_pwm(32)}"]
    if faster_from_off != expected_faster_from_off:
        raise AssertionError(f"ESP32 BLE fan faster from off failed: {faster_from_off}")
    if not esp32_status_fan_is_off(off_ble_status):
        raise AssertionError("ESP32 BLE fan-off status detection failed")
    fan_off_reply = esp32_ble_reply_for_commands(["FAN_OFF"], connected=True, queued=1)
    if "風扇關掉" not in fan_off_reply:
        raise AssertionError(f"ESP32 BLE fan-off confirmation should be spoken: {fan_off_reply}")
    already_off_reply = esp32_ble_reply_for_commands(
        ["FAN_OFF"],
        connected=True,
        queued=0,
        already_state="fan_off",
    )
    if "明明已經是關的" not in already_off_reply:
        raise AssertionError(f"ESP32 BLE already-off reply missing state wording: {already_off_reply}")
    disconnected_ble_reply = esp32_ble_reply_for_commands(["FAN_ON"], connected=False, queued=1, reconnecting=True)
    if "沒有連上 ESP32-S3 藍芽" not in disconnected_ble_reply or "正在重新連線" not in disconnected_ble_reply:
        raise AssertionError(f"ESP32 BLE disconnected reply missing reconnect wording: {disconnected_ble_reply}")
    fan_manager = FrdmFanControlManager(dry_args)
    if not fan_manager.handle_line("EVT,Fan,1,2"):
        raise AssertionError("FRDM fan manager should handle fan event")
    if fan_manager.handle_line("TodoDone 42"):
        raise AssertionError("FRDM fan manager should ignore non-fan event")
    router_hits: list[str] = []
    router = FrdmUartEventRouter(dry_args)
    router.add_handler("test", lambda line: router_hits.append(line) or True)
    if not router.handle_line("EVT,Fan,1,2") or router_hits != ["EVT,Fan,1,2"]:
        raise AssertionError("FRDM UART event router failed")
    if command_matches_stale_demo_process("python3 music_web_player.py --server --backend mpv", ("mpv",)):
        raise AssertionError("preflight must not kill music_web_player just because --backend mpv is present")
    if not command_matches_stale_demo_process("/usr/bin/mpv --no-video ytdl://ytsearch1:test", ("mpv",)):
        raise AssertionError("preflight should still detect real mpv playback processes")
    expected_emotion_codes = {
        "neutral": 0,
        "concerned": 1,
        "angry": 2,
        "sad": 3,
        "happy": 4,
        "curious": 5,
        "excited": 4,
        "confused": 5,
        "sleepy": 3,
    }
    for emotion, expected_code in expected_emotion_codes.items():
        if speaking_code_for_emotion(emotion) != expected_code:
            raise AssertionError(f"emotion to Speaking code mapping failed: {emotion}")
        if head_motion_for_emotion(emotion) != EMOTION_TO_HEAD_MOTION[emotion]:
            raise AssertionError(f"emotion to head motion mapping failed: {emotion}")
    alias_cases = {
        "surprised": ("excited", 4),
        "sad": ("sad", 3),
        "angry": ("angry", 2),
        "anxious": ("concerned", 1),
        "tired": ("sleepy", 3),
        "unsure": ("confused", 5),
    }
    for raw_emotion, (expected_emotion, expected_code) in alias_cases.items():
        if normalize_emotion_name(raw_emotion) != expected_emotion:
            raise AssertionError(f"emotion alias normalization failed: {raw_emotion}")
        if speaking_code_for_emotion(raw_emotion) != expected_code:
            raise AssertionError(f"emotion alias Speaking code failed: {raw_emotion}")
    alias_control = normalize_control({"transcript": "我有點擔心", "control": {"emotion": "anxious"}})
    if alias_control["emotion"] != "concerned" or alias_control["head_motion"] != "concerned_tilt":
        raise AssertionError(f"emotion alias control failed: {alias_control}")
    strong_user_tone_control = normalize_control({"transcript": "我操你妈的！", "control": {"emotion": "concerned"}})
    if strong_user_tone_control["emotion"] != "concerned" or speaking_code_for_emotion(strong_user_tone_control["emotion"]) != 1:
        raise AssertionError(f"strong user tone should keep robot reaction concerned/Speaking 1: {strong_user_tone_control}")
    if detect_focus_mode_intent("回來") is not None:
        raise AssertionError("plain return intent should not look like focus stop unless focus is active")
    if detect_persistent_state_intent("回來") != "normal":
        raise AssertionError("plain return intent should restore normal mode")
    if detect_focus_mode_intent("停止專注") != "stop":
        raise AssertionError("explicit focus stop should still be detected")
    if not conversation_should_end_after_sleep_control(
        {"control": {"persistent_state": "sleep", "screen_mode": "sleep"}},
        argparse.Namespace(conversation_mode=True),
    ):
        raise AssertionError("sleep control should end conversation mode")
    if conversation_should_end_after_sleep_control(
        {"control": {"persistent_state": "sleep", "screen_mode": "sleep"}},
        argparse.Namespace(conversation_mode=False),
    ):
        raise AssertionError("sleep control should not end non-conversation mode")
    if motor_ack_problem("MotorPitch", 90, ["Motor Pitch = 90"]):
        raise AssertionError("valid MotorPitch ACK should not trip safety")
    if not motor_ack_problem("MotorPitch", 90, ["Motor Pitch = 537190203"]):
        raise AssertionError("pointer-like MotorPitch ACK should trip safety")
    if motor_ack_problem("MotorYaw", 90, ["Motor Yaw = 90"]):
        raise AssertionError("valid MotorYaw ACK should not trip safety")
    if not motor_ack_problem("MotorYaw", 90, ["Motor Yaw = 537190201"]):
        raise AssertionError("pointer-like MotorYaw ACK should trip safety")
    if motor_ack_problem("MotorYawPitch", 120, ["Motor YawPitch = yaw:120 pitch:90"], 90):
        raise AssertionError("valid MotorYawPitch ACK should not trip safety")
    if not motor_ack_problem("MotorYawPitch", 120, ["Motor YawPitch = yaw:999 pitch:90"], 90):
        raise AssertionError("out-of-range MotorYawPitch yaw ACK should trip safety")
    if not motor_ack_problem("MotorYawPitch", 120, ["Motor YawPitch = yaw:120 pitch:-1"], 90):
        raise AssertionError("out-of-range MotorYawPitch pitch ACK should trip safety")
    robot.send_emotion_screen("happy")
    robot.send_speaking_and_emotion("curious")
    head_thread = robot.start_head_motion("nod")
    head_thread.join(timeout=6.0)
    if head_thread.is_alive():
        raise AssertionError("head motion thread did not finish in self-test")
    speaking_thread, speaking_stop = robot.start_speaking_head_motion("nod")
    if speaking_thread is None or speaking_stop is None:
        raise AssertionError("speaking head motion should start in dry-run self-test")
    time.sleep(0.05)
    none_thread, none_stop = robot.start_speaking_head_motion("none")
    if none_thread is not None or none_stop is not None:
        raise AssertionError("head_motion=none should not start a speaking motion thread")
    speaking_thread.join(timeout=2.0)
    if speaking_thread.is_alive():
        raise AssertionError("head_motion=none should stop the previous speaking motion")
    if robot._active_speaking_thread is not None or robot._active_speaking_stop is not None:
        raise AssertionError("stopped speaking motion should not remain active")
    stale_thread, stale_stop = robot.start_speaking_head_motion("nod")
    if stale_thread is None or stale_stop is None:
        raise AssertionError("stale speaking head motion should start in dry-run self-test")
    time.sleep(0.05)
    SpeakingPlaybackCue(robot, "neutral", "nod", None, reset_reason="self-test cue cleanup").stop()
    stale_thread.join(timeout=2.0)
    if stale_thread.is_alive():
        raise AssertionError("unstarted playback cue stop should clear stale speaking motion")

    queue_url = tts_queue_url("http://127.0.0.1:8777/speak_async")
    if queue_url != "http://127.0.0.1:8777/queue":
        raise AssertionError(f"bad TTS queue URL: {queue_url}")
    if estimate_tts_seconds("好，我先安靜陪你休息。") < 1.2:
        raise AssertionError("TTS estimate below minimum")
    tts_probe_args = argparse.Namespace(
        tts_url="http://127.0.0.1:8777/speak_async",
        tts_timeout=0.1,
        tts_poll_interval=0.1,
        tts_start_poll_interval=0.05,
        tts_speaking_start_timeout=0.1,
        tts_debug=False,
        tts_speaking_require_audio=True,
    )
    original_get_json = voice_chat.get_json
    try:
        starts: list[str] = []

        def fake_tts_finished_without_audio(url: str, timeout_sec: float = 0.0) -> dict[str, Any]:
            return {
                "running": False,
                "last_result": {"job_id": "job-no-audio", "playback": {"volume_gain": 4.8}},
            }

        voice_chat.get_json = fake_tts_finished_without_audio  # type: ignore[assignment]
        if not wait_for_tts_job(
            "job-no-audio",
            tts_probe_args,
            timeout_sec=0.5,
            on_playback_start=lambda: starts.append("started"),
        ):
            raise AssertionError("strict TTS queue wait should succeed when job finished")
        if starts:
            raise AssertionError("FRDM Speaking should not start unless TTS audio.playing is observed")

        queue_polls = 0

        def fake_tts_audio_observed(url: str, timeout_sec: float = 0.0) -> dict[str, Any]:
            nonlocal queue_polls
            if str(url).endswith("/queue"):
                queue_polls += 1
                if queue_polls == 1:
                    return {"running": True, "current": {"id": "job-audio"}}
                return {
                    "running": False,
                    "last_result": {"job_id": "job-audio", "playback": {"volume_gain": 4.8}},
                }
            return {"audio": {"playing": True}}

        starts.clear()
        voice_chat.get_json = fake_tts_audio_observed  # type: ignore[assignment]
        if not wait_for_tts_job(
            "job-audio",
            tts_probe_args,
            timeout_sec=0.5,
            on_playback_start=lambda: starts.append("started"),
        ):
            raise AssertionError("TTS queue wait should succeed when audio is observed")
        if starts != ["started"]:
            raise AssertionError("FRDM Speaking should start exactly once when TTS audio.playing is observed")
    finally:
        voice_chat.get_json = original_get_json  # type: ignore[assignment]

    if detect_focus_mode_intent("開始專心工作 25 分鐘 寫 UART 報告") != "start":
        raise AssertionError("focus start intent detection failed")
    if detect_focus_mode_intent("結束工作，切回一般模式") != "stop":
        raise AssertionError("focus stop intent detection failed")
    conv_args = argparse.Namespace(conversation_mode=True)
    if not should_end_conversation_after_focus_turn(
        conv_args,
        focus_intent="start",
        focus_was_running=False,
        focus_is_running=True,
    ):
        raise AssertionError("focus start should end conversation follow-up mode")
    if not should_end_conversation_after_focus_turn(
        conv_args,
        focus_intent=None,
        focus_was_running=True,
        focus_is_running=True,
    ):
        raise AssertionError("active focus mode should end conversation follow-up mode")
    if should_end_conversation_after_focus_turn(
        argparse.Namespace(conversation_mode=False),
        focus_intent="start",
        focus_was_running=False,
        focus_is_running=True,
    ):
        raise AssertionError("focus should not affect classic one-turn mode")
    if not pet_idle_silence_reply(PET_IDLE_SILENCE_TOKEN):
        raise AssertionError("pet idle silence token should be recognized")
    if not pet_idle_silence_reply("我先不打擾你，繼續陪你。"):
        raise AssertionError("pet idle no-disturb reply should stay silent")
    pet_prompt = build_pet_idle_reflection_prompt(idle_seconds=30, seconds_since_share=999, allow_share=False)
    if PET_IDLE_SILENCE_TOKEN not in pet_prompt or "冷卻" not in pet_prompt:
        raise AssertionError("pet idle cooldown prompt should force silence token")
    duration = parse_focus_duration_min("開始工作 1.5 小時")
    if duration != 90.0:
        raise AssertionError(f"focus duration parsing failed: {duration}")
    task = extract_focus_task("開始專心工作 25 分鐘 寫 UART 報告")
    if "UART" not in task:
        raise AssertionError(f"focus task extraction failed: {task!r}")
    if detect_todo_intent("新增待辦 寫 UART 報告") != "add":
        raise AssertionError("to-do add intent detection failed")
    if detect_todo_intent("列出待辦清單") != "list":
        raise AssertionError("to-do list intent detection failed")
    if detect_todo_intent("完成待辦 1") != "done":
        raise AssertionError("to-do done intent detection failed")
    if extract_todo_add_text("幫我記一個待辦：整理投影片") != "整理投影片":
        raise AssertionError("to-do add text extraction failed")
    if extract_todo_add_text("把買牛奶加入待辦") != "買牛奶":
        raise AssertionError("to-do trailing add text extraction failed")
    if extract_todo_done_number("完成第二項待辦") != 2:
        raise AssertionError("to-do Chinese ordinal parsing failed")

    todo_test_path = Path(f"/tmp/wake_bridge_todo_self_test_{uuid.uuid4().hex}.json")
    todo_args = argparse.Namespace(no_todo_list=False, todo_list_path=str(todo_test_path), todo_debug=False)
    todo = TodoListManager(todo_args)
    try:
        add_result = todo.handle_transcript("新增待辦 寫 UART 報告")
        if not add_result or not add_result.get("ok"):
            raise AssertionError(f"to-do add failed: {add_result}")
        list_result = todo.handle_transcript("列出待辦")
        if not list_result or "寫 UART 報告" not in str(list_result.get("reply", "")):
            raise AssertionError(f"to-do list failed: {list_result}")
        done_result = todo.handle_transcript("完成待辦 1")
        if not done_result or not done_result.get("ok"):
            raise AssertionError(f"to-do done failed: {done_result}")
        if format_todo_uart_payload(todo) != "0,1":
            raise AssertionError(f"to-do UART payload failed: {format_todo_uart_payload(todo)}")
        empty_result = todo.handle_transcript("列出待辦")
        if not empty_result or "沒有未完成" not in str(empty_result.get("reply", "")):
            raise AssertionError(f"to-do empty list failed: {empty_result}")
        checkbox_item = todo.add_item("測試 FRDM checkbox")
        checkbox_id = int(checkbox_item.get("item", {}).get("id", 0) or 0)
        if checkbox_id <= 0:
            raise AssertionError(f"to-do checkbox test id failed: {checkbox_item}")
        detail_lines = todo_uart_detail_lines(todo, limit=8)
        if not any(f",{checkbox_id},open," in line for line in detail_lines):
            raise AssertionError(f"to-do detail UART lines failed: {detail_lines}")
        checkbox_done = todo.complete_item_by_id(checkbox_id, source="frdm")
        if not checkbox_done.get("ok"):
            raise AssertionError(f"FRDM checkbox completion failed: {checkbox_done}")
    finally:
        try:
            todo_test_path.unlink(missing_ok=True)
        except OSError:
            pass

    gate_args = argparse.Namespace(
        volume_min=700,
        no_adaptive_volume=False,
        noise_floor_percentile=75.0,
        speech_start_margin=350,
        silence_margin=500,
        speech_start_ratio=1.25,
        silence_noise_ratio=1.15,
        silence_peak_ratio=0.35,
    )
    noise_floor, start_threshold, silence_base = adaptive_recording_thresholds(
        gate_args,
        [1000, 1100, 1200, 1250, 1300],
        fallback_volume=1200,
    )
    if noise_floor <= 0 or start_threshold <= noise_floor or silence_base <= noise_floor:
        raise AssertionError(f"adaptive gate thresholds look wrong: {noise_floor}, {start_threshold}, {silence_base}")
    silence_threshold = adaptive_silence_threshold(gate_args, silence_base, peak_volume=4000)
    if silence_threshold < silence_base:
        raise AssertionError("adaptive silence threshold below base")
    wake_noise_floor, wake_volume_threshold = adaptive_wake_volume_threshold(
        argparse.Namespace(
            wake_volume_min=350,
            wake_volume_ratio=1.35,
            wake_volume_margin=1200,
            no_adaptive_volume=False,
            noise_floor_percentile=75.0,
        ),
        [9000, 10000, 10500, 11000, 11500],
        fallback_volume=10000,
    )
    if wake_noise_floor <= 0 or wake_volume_threshold < 14000:
        raise AssertionError(f"adaptive wake gate too low for noisy room: {wake_noise_floor}, {wake_volume_threshold}")

    noisy_args = argparse.Namespace(
        volume_min=1100,
        no_adaptive_volume=False,
        noise_floor_percentile=75.0,
        speech_start_margin=750,
        silence_margin=900,
        speech_start_ratio=1.45,
        silence_noise_ratio=1.30,
        silence_peak_ratio=0.35,
    )
    noisy_floor, noisy_start, noisy_silence_base = adaptive_recording_thresholds(
        noisy_args,
        [9000, 10000, 10500, 11000, 11500],
        fallback_volume=10000,
    )
    if noisy_start <= noisy_floor or noisy_start >= 19000 or noisy_silence_base >= noisy_start:
        raise AssertionError(f"noisy adaptive thresholds look wrong: {noisy_floor}, {noisy_start}, {noisy_silence_base}")

    music_args = argparse.Namespace(
        no_music=False,
        music_always_call=False,
        music_debug=False,
        music_url=DEFAULT_MUSIC_TOOL_URL,
        music_backend="auto",
        music_dry_run=True,
        music_timeout=0.1,
    )
    music_route = detect_music_route({"transcript": "我想要听《告白气球》。"}, music_args)
    if not music_route.get("intent") or music_route.get("action") != "play" or "告白" not in str(music_route.get("query", "")):
        raise AssertionError(f"music route detection failed: {music_route}")
    stop_route = detect_music_route({"transcript": "停止音樂"}, music_args)
    if stop_route.get("action") != "stop":
        raise AssertionError(f"music stop detection failed: {stop_route}")
    resume_route = detect_music_route({"transcript": "繼續播放音樂"}, music_args)
    if resume_route.get("action") != "resume":
        raise AssertionError(f"music resume detection failed: {resume_route}")
    audio_complaint_route = detect_music_route({"transcript": "為什麼沒聲音，因為我聽到聲音超小"}, music_args)
    if audio_complaint_route.get("intent"):
        raise AssertionError(f"audio complaint was misdetected as music: {audio_complaint_route}")
    audio_volume_route = detect_music_route({"transcript": "我聽到聲音超小，幫我調大音量"}, music_args)
    if not audio_volume_route.get("intent") or audio_volume_route.get("action") != "volume" or audio_volume_route.get("query") != "+10":
        raise AssertionError(f"volume route detection failed: {audio_volume_route}")
    if "關掉" not in music_control_reply("stop", {"ok": True, "stopped": True}):
        raise AssertionError("music stop confirmation should be spoken")
    if "沒有在播放" not in music_control_reply("stop", {"ok": True, "stopped": False, "message": "no active mpv process"}):
        raise AssertionError("music stop no-active confirmation should be spoken")
    local_response: dict[str, Any] = {}
    local_control = apply_local_control_reply(local_response, "好，我處理好了。", head_motion="nod", reason="self-test")
    if local_response.get("reply") != "好，我處理好了。" or local_control["head_motion"] != "nod":
        raise AssertionError(f"local control reply helper failed: {local_response}")
    fan_local_response: dict[str, Any] = {}
    fan_local_control = apply_local_control_reply(
        fan_local_response,
        "好，我幫你把風扇關掉。",
        head_motion="none",
        screen_mode="normal",
        reason="self-test ESP32 local control",
    )
    if fan_local_control["head_motion"] != "none" or fan_local_control["screen_mode"] != "normal":
        raise AssertionError(f"ESP32 local control should speak without head motion: {fan_local_response}")

    weather_args = argparse.Namespace(
        no_weather=False,
        weather_always_call=False,
        weather_debug=False,
        weather_url=DEFAULT_WEATHER_TOOL_URL,
        weather_default_location="Taipei",
        no_weather_local_temperature=True,
        esp32_temperature_mode="disabled",
    )
    weather_route = detect_weather_route({"transcript": "明天下午三點所在地天氣如何？"}, weather_args)
    if not weather_route.get("intent") or weather_route.get("action") != "weather":
        raise AssertionError(f"weather route detection failed: {weather_route}")
    weather_payload = format_weather_uart_payload(
        {
            "ok": True,
            "handled": True,
            "weather": {
                "kind": "daily",
                "temperature_min_c": 22.5,
                "temperature_max_c": 28.6,
                "precipitation_probability_max": 41,
                "weather_code": 61,
            },
        }
    )
    if weather_payload != "daily,22,29,41,61":
        raise AssertionError(f"weather UART payload formatting failed: {weather_payload}")
    current_weather_payload = format_weather_uart_payload(
        {
            "ok": True,
            "handled": True,
            "weather": {
                "kind": "current",
                "temperature_c": 20.1,
                "precipitation_probability": 0,
                "weather_code": 3,
            },
        }
    )
    if current_weather_payload != "current,20,20,0,3":
        raise AssertionError(f"weather current UART payload formatting failed: {current_weather_payload}")
    weather_payload_with_local = format_weather_uart_payload(
        {
            "ok": True,
            "handled": True,
            "weather": {
                "kind": "daily",
                "temperature_min_c": 22.5,
                "temperature_max_c": 28.6,
                "precipitation_probability_max": 41,
                "weather_code": 61,
            },
        },
        local_temperature_c=25.36,
    )
    if weather_payload_with_local != "daily,22,29,41,61,254":
        raise AssertionError(f"weather local temperature UART payload formatting failed: {weather_payload_with_local}")
    if format_temp_room_uart_payload(25.36) != "254":
        raise AssertionError("TempRoom UART payload should use Celsius*10")
    if extract_temperature_c({"ok": True, "temperature_c": 25.36}) != 25.36:
        raise AssertionError("ESP32 temperature JSON parsing failed")
    if extract_temperature_c("25.4") != 25.4:
        raise AssertionError("ESP32 plain temperature parsing failed")
    non_weather_route = detect_weather_route({"transcript": "講個笑話"}, weather_args)
    if non_weather_route.get("intent"):
        raise AssertionError(f"non-weather route should not trigger: {non_weather_route}")

    print("wake bridge self-test OK")
    return 0


def speak_reply_and_wait(
    response: dict[str, Any],
    args: argparse.Namespace,
    *,
    on_playback_start: Callable[[], None] | None = None,
) -> bool:
    response["_client_tts_attempted"] = False
    response["_client_tts_ok"] = True
    response["_client_tts_error"] = ""
    response["_client_tts_playback_started"] = False
    if args.no_tts:
        print("TTS skipped by --no-tts.")
        return True

    reply = str(response.get("reply", "")).strip()
    if not reply:
        print("TTS skipped: empty reply.")
        return True

    payload = voice_chat.build_tts_payload(reply, args)
    estimated_sec = estimate_tts_seconds(reply)
    timeout_sec = max(float(getattr(args, "tts_playback_timeout", 0.0) or 0.0), estimated_sec + 25.0)
    timeout_sec = min(max(timeout_sec, 8.0), 60.0)

    def notify_playback_start() -> None:
        if response.get("_client_tts_playback_started"):
            return
        response["_client_tts_playback_started"] = True
        if on_playback_start is not None:
            on_playback_start()

    print(f"TTS started: estimated={estimated_sec:.1f}s timeout={timeout_sec:.1f}s")
    response["_client_tts_attempted"] = True
    started = time.monotonic()
    try:
        tts_path = urllib.parse.urlsplit(args.tts_url).path.rstrip("/")
        post_timeout = timeout_sec if tts_path.endswith("/speak") else args.tts_timeout
        result = voice_chat.post_json(args.tts_url, payload, timeout_sec=post_timeout)
    except Exception as exc:
        response["_client_tts_ok"] = False
        response["_client_tts_error"] = str(exc)
        print(f"WARNING: TTS speak failed: {exc}")
        return False

    post_ms = int((time.monotonic() - started) * 1000)
    job_id = str(result.get("job_id", "")).strip()
    if args.tts_debug:
        print()
        print("TTS:")
        print(f"  url          : {args.tts_url}")
        print(f"  request_gain : {payload.get('volume_gain', 'none')}")
        print(f"  post_ms      : {post_ms}")
        print(f"  queued       : {result.get('queued', False)}")
        if job_id:
            print(f"  job_id       : {job_id}")
    if result.get("queued") and job_id:
        ok = wait_for_tts_job(job_id, args, timeout_sec=timeout_sec, on_playback_start=notify_playback_start)
        response["_client_tts_ok"] = ok
        if not ok:
            response["_client_tts_error"] = f"queue wait failed for job_id={job_id}"
        return ok

    require_audio_playing = bool(getattr(args, "tts_speaking_require_audio", True))
    # /speak blocking path: returning from POST means playback is done.
    playback = result.get("playback") if isinstance(result.get("playback"), dict) else {}
    if playback:
        if not require_audio_playing:
            notify_playback_start()
        elif args.tts_debug:
            print("TTS blocking playback already returned; Speaking UART was not sent after playback ended.")
        print("TTS finished: blocking playback returned.")
        response["_client_tts_ok"] = True
        return True

    if not require_audio_playing:
        notify_playback_start()
    elif args.tts_debug:
        print("TTS response had no queue job/playback marker; Speaking UART waits for audio.playing and was not sent.")
    time.sleep(estimated_sec)
    print(f"TTS estimated finished after {estimated_sec:.1f}s.")
    response["_client_tts_ok"] = True
    return True


def print_control_summary(control: dict[str, str]) -> None:
    print()
    print("AI control:")
    print(f"  persistent_state : {control.get('persistent_state')}")
    print(f"  screen_mode      : {control.get('screen_mode')}")
    print(f"  emotion          : {control.get('emotion')}")
    print(f"  head_motion      : {control.get('head_motion')}")
    print(f"  reason           : {control.get('reason')}")


def print_quiet_turn_summary(response: dict[str, Any]) -> None:
    request_id = str(response.get("request_id", "") or "").strip()
    timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
    print()
    print("Turn processed.")
    if request_id:
        print(f"request_id={request_id}")
    if timing:
        print(
            "Timing: "
            f"asr={timing.get('asr_ms', '?')} ms, "
            f"llm={timing.get('llm_ms', '?')} ms, "
            f"total={timing.get('total_ms', '?')} ms"
        )


def restore_after_conversation_end(
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
) -> None:
    robot.set_screen_mode("normal", reason="conversation ended")
    if timing is not None:
        timing.mark("conversation ended; Normal restored")


def conversation_music_end_action(response: dict[str, Any], args: argparse.Namespace) -> str:
    if not getattr(args, "conversation_mode", False) or getattr(args, "keep_conversation_after_music_control", False):
        return ""
    music_route = response.get("music") if isinstance(response.get("music"), dict) else {}
    action = str(music_route.get("action", "none") or "none").strip().lower()
    if action not in {"play", "resume", "pause", "stop"}:
        return ""
    if not (music_route.get("intent") or music_route.get("should_call") or music_route.get("handled") or music_route.get("ok")):
        return ""
    return action


def conversation_should_end_after_sleep_control(response: dict[str, Any], args: argparse.Namespace) -> bool:
    if not getattr(args, "conversation_mode", False):
        return False
    control = response.get("control") if isinstance(response.get("control"), dict) else {}
    persistent_state = str(control.get("persistent_state", "") or "").strip().lower()
    screen_mode = str(control.get("screen_mode", "") or "").strip().lower()
    return persistent_state == "sleep" or screen_mode == "sleep"


def set_post_reply_screen(
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
    *,
    control: dict[str, str] | None = None,
    music_action: str = "",
    focus_running: bool = False,
    focus_stopped: bool = False,
    reason: str = "post reply",
) -> None:
    control = control or {}
    screen_mode = str(control.get("screen_mode", "unchanged") or "unchanged").strip().lower()
    persistent_state = str(control.get("persistent_state", "unchanged") or "unchanged").strip().lower()
    music_action = str(music_action or "").strip().lower()

    def mark_uart(label: str) -> None:
        if timing is not None and not getattr(args, "no_uart", False):
            timing.mark(label)

    if music_action in {"play", "resume"}:
        robot.set_screen_mode("music", reason=f"{reason}; music {music_action}")
        mark_uart("UART Music sent")
        return
    if music_action in {"pause", "stop"}:
        robot.set_screen_mode("normal", reason=f"{reason}; music {music_action}")
        mark_uart("UART Normal sent after music stop/pause")
        return
    if focus_stopped:
        robot.set_screen_mode("normal", reason=f"{reason}; focus stopped")
        mark_uart("UART Normal sent after focus stop")
        return
    if focus_running:
        robot.set_screen_mode("focus", reason=f"{reason}; focus running")
        mark_uart("UART Focus sent")
        return
    if screen_mode in {"normal", "sleep", "music", "focus"}:
        robot.set_screen_mode(screen_mode, reason=f"{reason}; control screen_mode")
        mark_uart(f"UART {SCREEN_MODE_TO_COMMAND.get(screen_mode, screen_mode)} sent")
        return
    if persistent_state in {"normal", "sleep"}:
        robot.set_screen_mode(persistent_state, reason=f"{reason}; persistent_state")
        mark_uart(f"UART {SCREEN_MODE_TO_COMMAND.get(persistent_state, persistent_state)} sent")
        return
    if getattr(args, "conversation_mode", False):
        robot.set_screen_mode("thinking", reason=f"{reason}; waiting for follow-up")
        mark_uart("UART Thinking sent for follow-up")
        return

    robot.restore_persistent_screen_state()
    mark_uart("UART persistent screen state sent")


def apply_local_control_reply(
    response: dict[str, Any],
    reply: str,
    *,
    emotion: str = "neutral",
    head_motion: str = "none",
    screen_mode: str = "unchanged",
    persistent_state: str = "unchanged",
    reason: str = "local control",
) -> dict[str, str]:
    control = {
        "persistent_state": persistent_state,
        "screen_mode": screen_mode,
        "emotion": normalize_emotion_name(emotion, default="neutral"),
        "head_motion": head_motion if head_motion in VALID_HEAD_MOTIONS else "none",
        "reason": reason,
    }
    response["reply"] = str(reply or "").strip()
    response["control"] = control
    response["emotion"] = emotion_summary_from_control(control)
    return control


def music_control_reply(action: str, result: dict[str, Any] | None) -> str:
    action = str(action or "").strip().lower()
    result = result or {}
    ok = bool(result.get("ok", False))
    message = str(result.get("message", "") or "").strip().lower()
    no_active = "no active" in message or result.get("stopped") is False or result.get("paused") is False
    if action == "stop":
        if ok and not no_active:
            return "好，我把音樂關掉了。"
        if ok:
            return "音樂現在沒有在播放。"
        return "我想關掉音樂，但音樂播放器目前沒有回應。"
    if action == "pause":
        if ok and not no_active:
            return "好，我先暫停音樂。"
        if ok:
            return "音樂現在沒有在播放。"
        return "我想暫停音樂，但音樂播放器目前沒有回應。"
    if action == "volume":
        if ok:
            volume = result.get("volume_percent", result.get("volume", ""))
            try:
                volume_text = str(int(round(float(volume))))
            except (TypeError, ValueError):
                volume_text = ""
            if volume_text:
                return f"好，音量調到 {volume_text}。"
            return "好，我調整音量了。"
        return "我想調整音量，但音樂播放器目前沒有回應。"
    return "音樂控制已處理。"


def emotion_summary_from_control(control: dict[str, str]) -> dict[str, Any]:
    primary = control.get("emotion", "neutral")
    if primary not in VALID_EMOTIONS:
        primary = "neutral"
    presets = {
        "neutral": (0.25, 0.0, 0.25, False, "自然中性互動。"),
        "concerned": (0.65, -0.35, 0.35, True, "使用者可能需要關心。"),
        "angry": (0.90, -0.85, 0.90, False, "使用者明顯生氣或挫折。"),
        "sad": (0.70, -0.65, 0.25, True, "使用者情緒低落或難過。"),
        "happy": (0.65, 0.65, 0.55, False, "回覆語氣偏愉快。"),
        "curious": (0.45, 0.10, 0.45, False, "正在回答問題或分析畫面。"),
        "excited": (0.80, 0.75, 0.80, False, "互動能量較高。"),
        "confused": (0.55, -0.15, 0.45, False, "資訊不清楚或判斷不確定。"),
        "sleepy": (0.50, -0.05, 0.15, False, "進入休息或睡眠互動。"),
    }
    intensity, valence, arousal, support_needed, summary = presets[primary]
    return {
        "primary": primary,
        "intensity": intensity,
        "valence": valence,
        "arousal": arousal,
        "support_needed": support_needed,
        "summary": summary,
    }


def handle_focus_mode_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
    focus_manager: FocusModeManager,
) -> bool | None:
    transcript = str(response.get("transcript", "") or "").strip()
    focus_manager.poll()
    intent = detect_focus_mode_intent(transcript)
    state_intent = detect_persistent_state_intent(transcript)
    if intent is None and state_intent == "sleep":
        if focus_manager.is_running():
            focus_manager.stop()
        return None
    if intent is None and focus_manager.is_running() and state_intent == "normal":
        intent = "stop"
    if intent is None and not focus_manager.is_running():
        return None
    if intent is None and focus_manager.is_running():
        return None
    if focus_manager.is_running():
        focus_manager.close_uart_gate()

    if intent == "start":
        ok, reply = focus_manager.start(transcript)
        emotion = "happy" if ok else "confused"
        head_motion = "nod" if ok else "shake"
    elif intent == "stop":
        ok, reply = focus_manager.stop()
        emotion = "happy" if ok else "confused"
        head_motion = "nod" if ok else "shake"
    else:
        ok = True
        reply = "我還在專心工作模式中。要結束的話，再叫我結束工作。"
        emotion = "curious"
        head_motion = "gentle_nod"

    focus_should_show = intent != "stop" and ok and focus_manager.is_running()
    control = {
        "persistent_state": "unchanged",
        "screen_mode": "normal" if intent == "stop" else ("focus" if focus_should_show else "unchanged"),
        "emotion": emotion,
        "head_motion": head_motion,
        "reason": f"focus mode {intent or 'active'}",
    }
    response["reply"] = reply
    response["control"] = control
    response["emotion"] = emotion_summary_from_control(control)

    if getattr(args, "quiet_dialog", False):
        print_quiet_turn_summary(response)
    else:
        print_control_summary(control)
        print(f"parsed reply: {reply}")
        print(f"parsed control: {json.dumps(control, ensure_ascii=False)}")
        voice_chat.print_result(response, verbose_debug=args.debug)

    speaking_cue = SpeakingPlaybackCue(
        robot,
        emotion,
        head_motion,
        timing,
        timing_label="UART Speaking emotion code sent",
        reset_reason="speaking_head_motion focus stop reset",
    )
    if timing is not None:
        timing.mark("focus mode command handled")
    tts_ok = False
    try:
        tts_ok = speak_reply_and_wait(response, args, on_playback_start=speaking_cue.start)
    except Exception as exc:
        print(f"WARNING: focus mode TTS failed unexpectedly: {exc}")
        response["_client_tts_attempted"] = True
        response["_client_tts_ok"] = False
        response["_client_tts_error"] = str(exc)
        tts_ok = False
    finally:
        try:
            speaking_cue.stop()
        finally:
            if timing is not None:
                timing.mark("TTS finished or estimated finished")
            if intent == "stop":
                send_focus_uart_update(
                    args,
                    robot,
                    state=focus_manager.dashboard_state,
                    remaining_min=focus_manager.dashboard_remaining_min,
                    streak=focus_manager.dashboard_streak,
                    reason="focus mode stop dashboard",
                )
            set_post_reply_screen(
                args,
                robot,
                timing,
                control=control,
                focus_running=focus_manager.is_running(),
                focus_stopped=intent == "stop",
                reason="focus mode reply complete",
            )
            if intent != "stop" and focus_manager.is_running():
                send_focus_uart_update(
                    args,
                    robot,
                    state=focus_manager.dashboard_state,
                    remaining_min=focus_manager.dashboard_remaining_min,
                    streak=focus_manager.dashboard_streak,
                    reason=f"focus mode {intent or 'active'} dashboard",
                )
                focus_manager.open_uart_gate()
    return tts_ok or not getattr(args, "require_tts", False)


def handle_todo_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
    todo_manager: TodoListManager,
) -> bool | None:
    transcript = str(response.get("transcript", "") or "").strip()
    result = todo_manager.handle_transcript(transcript)
    if result is None:
        return None

    ok = bool(result.get("ok", False))
    action = str(result.get("action", "") or "")
    if action == "list":
        emotion = "curious"
        head_motion = "look_around"
    elif ok:
        emotion = "happy"
        head_motion = "nod"
    else:
        emotion = "confused"
        head_motion = "shake"
    control = {
        "persistent_state": "unchanged",
        "screen_mode": "unchanged",
        "emotion": emotion,
        "head_motion": head_motion,
        "reason": f"local todo list {action}",
    }
    reply = str(result.get("reply", "") or "").strip()
    response["reply"] = reply
    response["control"] = control
    response["emotion"] = emotion_summary_from_control(control)
    response["todo"] = result

    if getattr(args, "quiet_dialog", False):
        print_quiet_turn_summary(response)
    else:
        print()
        print("To-do list:")
        print(f"  action : {action}")
        print(f"  ok     : {ok}")
        print(f"  path   : {result.get('path')}")
        print_control_summary(control)
        print(f"parsed reply: {reply}")
        print(f"parsed control: {json.dumps(control, ensure_ascii=False)}")
        voice_chat.print_result(response, verbose_debug=args.debug)

    send_todo_uart_update(args, robot, todo_manager, reason=f"to-do {action} dashboard")

    speaking_cue = SpeakingPlaybackCue(
        robot,
        emotion,
        head_motion,
        timing,
        timing_label="UART Speaking emotion code sent",
        reset_reason="speaking_head_motion todo stop reset",
    )
    if timing is not None:
        timing.mark("to-do list command handled")

    try:
        tts_ok = speak_reply_and_wait(response, args, on_playback_start=speaking_cue.start)
    finally:
        speaking_cue.stop()
    if timing is not None:
        timing.mark("TTS finished or estimated finished")

    set_post_reply_screen(args, robot, timing, control=control, reason="to-do reply complete")
    return tts_ok or not getattr(args, "require_tts", False)


def esp32_ble_reply_for_commands(
    commands: list[str],
    *,
    connected: bool,
    queued: int | None = None,
    reconnecting: bool = False,
    already_state: str = "",
) -> str:
    upper = [command.upper() for command in commands]
    already_state = str(already_state or "").strip().lower()
    if already_state == "fan_off":
        base = "電風扇明明已經是關的，我就不再重複送關閉指令。"
    elif "ALL_OFF" in upper:
        base = "好，我幫你把風扇和 LED 都關掉。"
    elif any(command.startswith("LED_ON") for command in upper):
        base = "好，我幫你把 LED 打開。"
    elif any(command.startswith("LED_OFF") for command in upper):
        base = "好，我幫你把 LED 關掉。"
    elif any(command.startswith("LED_TOGGLE") for command in upper):
        base = "好，我幫你切換 LED。"
    elif "FAN_OFF" in upper:
        base = "好，我幫你把風扇關掉。"
    elif any(command.startswith("FAN_SPEED:") for command in upper):
        speed = upper[-1].split(":", 1)[1] if ":" in upper[-1] else ""
        for command in reversed(upper):
            if command.startswith("FAN_SPEED:") and ":" in command:
                speed = command.split(":", 1)[1]
                break
        base = f"好，我幫你把風扇速度調到 {speed}。"
    elif "FAN_ON" in upper:
        base = "好，我幫你打開風扇。"
    elif "FAN_TOGGLE" in upper:
        base = "好，我幫你切換風扇。"
    elif "TEMP?" in upper:
        base = "好，我幫你向 ESP32-S3 詢問目前溫度。"
    else:
        base = "好，我幫你送出 ESP32-S3 控制指令。"
    if connected:
        return base
    if already_state == "fan_off":
        reconnect_text = "我正在重新連線" if reconnecting else "我會繼續重新連線"
        return f"{base}另外我現在沒有連上 ESP32-S3 藍芽；{reconnect_text}。"
    if queued is None or queued > 0:
        reconnect_text = "我正在重新連線" if reconnecting else "我會繼續重新連線"
        return f"{base}不過我現在沒有連上 ESP32-S3 藍芽；我已經把指令排進佇列，{reconnect_text}，連上後會自動送出。"
    return "我現在沒有連上 ESP32-S3 藍芽；我正在重新連線，這次指令還沒有排進去，請你稍後再說一次。"


def handle_esp32_ble_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
    esp32_ble_manager: Esp32BleBridgeManager,
    *,
    focus_running: bool = False,
) -> bool | None:
    transcript = str(response.get("transcript", "") or "").strip()
    result = esp32_ble_manager.handle_voice_transcript(transcript)
    if result is None:
        return None

    ok = bool(result.get("ok", False))
    connected = bool(result.get("connected", False))
    commands = [str(command) for command in result.get("commands", [])]
    reply = esp32_ble_reply_for_commands(
        commands,
        connected=connected,
        queued=int(result.get("queued", 0) or 0),
        reconnecting=bool(result.get("reconnect_requested", False)),
        already_state=str(result.get("already_state", "") or ""),
    )
    noop = bool(result.get("noop", False))
    emotion = "neutral" if noop else ("happy" if ok else "confused")
    control = apply_local_control_reply(
        response,
        reply,
        emotion=emotion,
        head_motion="none",
        screen_mode="normal",
        reason="local ESP32-S3 BLE control",
    )
    response["esp32_ble"] = result
    response["_end_conversation_after_esp32_ble"] = bool(getattr(args, "conversation_mode", False))

    if getattr(args, "quiet_dialog", False):
        print_quiet_turn_summary(response)
    else:
        print()
        print("ESP32-S3 BLE control:")
        print(f"  ok        : {ok}")
        print(f"  connected : {connected}")
        print(f"  queued    : {result.get('queued')}/{len(commands)}")
        print(f"  commands  : {', '.join(commands)}")
        print_control_summary(control)
        print(f"parsed reply: {reply}")
        voice_chat.print_result(response, verbose_debug=args.debug)

    if timing is not None:
        timing.mark("ESP32-S3 BLE command queued")

    robot.stop_active_speaking_head_motion(reason="before ESP32-S3 BLE local control reply")
    speaking_cue = SpeakingPlaybackCue(
        robot,
        emotion,
        control["head_motion"],
        timing,
        timing_label="UART Speaking emotion code sent",
        reset_reason="speaking_head_motion esp32 ble stop reset",
    )
    try:
        tts_ok = speak_reply_and_wait(response, args, on_playback_start=speaking_cue.start)
    finally:
        speaking_cue.stop()
    if timing is not None:
        timing.mark("TTS finished or estimated finished")

    set_post_reply_screen(
        args,
        robot,
        timing,
        control=control,
        focus_running=focus_running,
        reason="ESP32-S3 BLE command complete",
    )
    return (ok and tts_ok) or (ok and not getattr(args, "require_tts", False))


def handle_wake_chat_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
    focus_manager: FocusModeManager | None = None,
    todo_manager: TodoListManager | None = None,
    esp32_ble_manager: Esp32BleBridgeManager | None = None,
) -> bool:
    focus_gate_closed_for_turn = False
    if focus_manager is not None and focus_manager.is_running():
        focus_manager.close_uart_gate()
        focus_gate_closed_for_turn = True
    if todo_manager is not None:
        handled = handle_todo_response(response, args, robot, timing, todo_manager)
        if handled is not None:
            if focus_gate_closed_for_turn and focus_manager is not None and focus_manager.is_running():
                focus_manager.open_uart_gate()
            return handled

    if focus_manager is not None:
        handled = handle_focus_mode_response(response, args, robot, timing, focus_manager)
        if handled is not None:
            return handled

    if esp32_ble_manager is not None:
        handled = handle_esp32_ble_response(
            response,
            args,
            robot,
            timing,
            esp32_ble_manager,
            focus_running=focus_manager.is_running() if focus_manager is not None else False,
        )
        if handled is not None:
            if focus_gate_closed_for_turn and focus_manager is not None and focus_manager.is_running():
                focus_manager.open_uart_gate()
            return handled

    weather_result = maybe_apply_weather_response(response, args)
    if weather_result is not None and timing is not None:
        timing.mark("weather tool handled" if weather_result.get("ok") else "weather tool failed")
    if weather_result is not None and weather_result.get("ok") and weather_result.get("handled", weather_result.get("ok")):
        weather_payload = send_weather_uart_update(args, robot, weather_result, reason="weather query update")
        if weather_payload and timing is not None:
            timing.mark("Weather UART sent")

    control = normalize_control(response)
    response["control"] = control
    reply = sanitize_reply(response)
    emotion_obj = response.get("emotion") if isinstance(response.get("emotion"), dict) else {}
    if not emotion_obj or str(emotion_obj.get("primary", "")).strip().lower() != control["emotion"]:
        response["emotion"] = emotion_summary_from_control(control)
    quiet_dialog = bool(getattr(args, "quiet_dialog", False))
    if quiet_dialog:
        print_quiet_turn_summary(response)
    else:
        print_control_summary(control)
        print(f"parsed reply: {reply}")
        print(f"parsed control: {json.dumps(control, ensure_ascii=False)}")

    if not quiet_dialog:
        voice_chat.print_result(response, verbose_debug=args.debug)
        response_vision_summary(response)
    music_route = detect_music_route(response, args)
    music_before_result: dict[str, Any] | None = None
    if music_route.get("action") in {"stop", "pause", "volume"}:
        music_before_result = execute_music_route(music_route, args, response, phase="before_tts")
        send_music_uart_update(args, robot, music_before_result, reason=f"music {music_route.get('action')} dashboard")
        action = str(music_route.get("action", "") or "")
        control = apply_local_control_reply(
            response,
            music_control_reply(action, music_before_result),
            emotion="neutral",
            head_motion="none",
            reason=f"local music {action} control",
        )
        print(f"Music {action} handled before TTS; local confirmation will be spoken.")
        if not quiet_dialog:
            print_control_summary(control)
            print(f"parsed reply: {response.get('reply', '')}")

        robot.stop_active_speaking_head_motion(reason=f"before music {action} local control reply")
        speaking_cue = SpeakingPlaybackCue(
            robot,
            control["emotion"],
            control["head_motion"],
            timing,
            timing_label="UART Speaking emotion code sent",
            reset_reason=f"speaking_head_motion music {action} reset",
        )
        try:
            tts_ok = speak_reply_and_wait(response, args, on_playback_start=speaking_cue.start)
        finally:
            speaking_cue.stop()
        if timing is not None:
            timing.mark("music stop/pause TTS finished or estimated finished")

        set_post_reply_screen(
            args,
            robot,
            timing,
            control=control,
            music_action=action,
            focus_running=focus_manager.is_running() if focus_manager is not None else False,
            reason=f"music {action} confirmation complete",
        )
        if focus_gate_closed_for_turn and focus_manager is not None and focus_manager.is_running():
            focus_manager.open_uart_gate()
        return tts_ok or not getattr(args, "require_tts", False)

    if control["persistent_state"] in {"normal", "sleep"}:
        robot.set_persistent_state(control["persistent_state"])

    speaking_cue = SpeakingPlaybackCue(
        robot,
        control["emotion"],
        control["head_motion"],
        timing,
        timing_label="UART Speaking emotion code sent",
        reset_reason="speaking_head_motion stop reset",
    )

    try:
        tts_ok = speak_reply_and_wait(response, args, on_playback_start=speaking_cue.start)
    finally:
        speaking_cue.stop()
    if timing is not None:
        timing.mark("TTS finished or estimated finished")

    if music_route.get("action") in {"play", "resume"}:
        music_after_result = execute_music_route(music_route, args, response, phase="after_tts")
        send_music_uart_update(args, robot, music_after_result, reason=f"music {music_route.get('action')} dashboard")
        if timing is not None:
            timing.mark("music triggered")

    set_post_reply_screen(
        args,
        robot,
        timing,
        control=control,
        music_action=str(music_route.get("action", "") or ""),
        focus_running=focus_manager.is_running() if focus_manager is not None else False,
        reason="chat reply complete",
    )
    if focus_gate_closed_for_turn and focus_manager is not None and focus_manager.is_running():
        focus_manager.open_uart_gate()
    return tts_ok or not getattr(args, "require_tts", False)


def run_wake_text_mode(args: argparse.Namespace) -> int:
    if not voice_chat.preflight_server(args):
        return 1
    if not voice_chat.preflight_tts(args):
        return 1

    text_url = voice_chat.endpoint_url(args.server_url, "/text-chat")
    robot = RobotUartController(args)
    focus_manager = FocusModeManager(args, None)
    todo_manager = TodoListManager(args)
    timing = TimingLogger()
    try:
        print(f"POST text to {text_url}")
        if not getattr(args, "no_uart", False):
            if robot.set_screen_state("Thinking"):
                print("UART Thinking sent.")
            else:
                print("WARNING: UART Thinking not sent; FRDM UART is unavailable.")
            timing.mark("UART Thinking sent")
        try:
            response = voice_chat.post_json(text_url, {"text": args.text}, timeout_sec=args.timeout)
        except Exception as exc:
            print(f"ERROR: text-chat failed: {exc}")
            robot.restore_persistent_screen_state()
            return 1

        timing.mark("AI reply received")
        debug_obj = response.get("debug") if isinstance(response.get("debug"), dict) else {}
        raw_preview = str(debug_obj.get("ollama_content_preview", "")).strip()
        if raw_preview:
            print(f"AI raw response preview: {raw_preview}")
        return 0 if handle_wake_chat_response(response, args, robot, timing, focus_manager, todo_manager, None) else 1
    finally:
        focus_manager.shutdown()


def run_head_motion_test(args: argparse.Namespace) -> int:
    """Exercise FRDM head motion directly without mic, camera, TTS, or AI."""
    head_motor_requested = bool(getattr(args, "enable_head_motor", False) and not getattr(args, "disable_head_motor", False))
    if not getattr(args, "uart_dry_run", False) and head_motor_requested:
        if not getattr(args, "no_uart", False):
            args.require_uart = True
        if not device_preflight(args):
            return 1

    robot = RobotUartController(args)
    requested_speaking_motion = str(getattr(args, "test_speaking_head_motion", "") or "").strip()
    requested_emotion = str(getattr(args, "test_head_emotion", "") or "").strip().lower()
    requested_motion = str(getattr(args, "test_head_motion", "") or "").strip()
    if requested_speaking_motion:
        motion = requested_speaking_motion if requested_speaking_motion in SPEAKING_HEAD_MOTION_LOOPS else "none"
        if motion == "all":
            motion = "shake"
        duration_sec = max(0.5, min(30.0, float(getattr(args, "test_speaking_seconds", 6.0) or 6.0)))
        print("Speaking head motion loop hardware test.")
        print("This mode simulates TTS playback: it loops head motion, then stops and resets.")
        print(
            "Motor settings: "
            f"enabled={robot.head_motor_enabled()}, "
            f"pitch={MOTOR_PITCH_MIN}..{MOTOR_PITCH_CENTER}..{MOTOR_PITCH_MAX} "
            "(down..center..up), "
            f"yaw={MOTOR_YAW_MIN}..{MOTOR_YAW_CENTER}..{MOTOR_YAW_MAX} "
            "(right..center..left), "
            f"speaking_smooth_step={robot.motor_speaking_smooth_step_deg()}deg, "
            f"speaking_step_delay={robot.motor_speaking_step_delay():.2f}s, "
            f"reset_repeats={robot.motor_reset_repeats()}, "
            f"reset_delay={robot.motor_reset_delay():.2f}s, "
            f"read_ms={robot.motor_read_ms()}"
        )
        if getattr(args, "uart_dry_run", False):
            print("UART mode: dry-run; commands will be printed but not sent.")
        elif not robot.head_motor_enabled():
            print("UART mode: head motor disabled; add --enable-head-motor only after FRDM ACK reports real angles.")
        else:
            print(f"UART mode: {args.uart_port} @ {args.uart_baudrate}")
            bridge.print_uart_ports()
        print(f"Testing speaking head motion loop: {motion} for {duration_sec:.1f}s")
        thread, stop_event = robot.start_speaking_head_motion(motion)
        try:
            time.sleep(duration_sec)
        finally:
            robot.stop_speaking_head_motion(thread, stop_event, reason="speaking_head_motion test stop reset")
        return 0

    if requested_emotion:
        if requested_emotion == "all":
            emotions = ["neutral", "concerned", "angry", "sad", "happy", "curious", "excited", "confused", "sleepy"]
        else:
            emotions = [requested_emotion]
        motion_plan = [(f"emotion:{emotion}", head_motion_for_emotion(emotion)) for emotion in emotions]
    elif requested_motion == "all":
        motion_plan = [(motion, motion) for motion in ["nod", "double_nod", "look_around", "shake", "gentle_nod", "sleepy_drop", "none"]]
    else:
        motion_plan = [(requested_motion, requested_motion)]

    repeats = max(1, min(10, int(getattr(args, "test_head_repeat", 1) or 1)))
    gap_sec = max(0.0, float(getattr(args, "test_head_gap", 0.7) or 0.0))

    print("Head motion hardware test.")
    print("This mode does not open microphone, camera, TTS, or Windows server.")
    print(
        "Motor settings: "
        f"enabled={robot.head_motor_enabled()}, "
        f"pitch={MOTOR_PITCH_MIN}..{MOTOR_PITCH_CENTER}..{MOTOR_PITCH_MAX} "
        "(down..center..up), "
        f"yaw={MOTOR_YAW_MIN}..{MOTOR_YAW_CENTER}..{MOTOR_YAW_MAX} "
        "(right..center..left), "
        f"smooth_step={robot.motor_smooth_step_deg()}deg, "
        f"step_delay={robot.motor_step_delay():.2f}s, "
        f"reset_repeats={robot.motor_reset_repeats()}, "
        f"reset_delay={robot.motor_reset_delay():.2f}s, "
        f"read_ms={robot.motor_read_ms()}"
    )
    if getattr(args, "uart_dry_run", False):
        print("UART mode: dry-run; commands will be printed but not sent.")
    elif not robot.head_motor_enabled():
        print("UART mode: head motor disabled; add --enable-head-motor only after FRDM ACK reports real angles.")
    else:
        print(f"UART mode: {args.uart_port} @ {args.uart_baudrate}")
        bridge.print_uart_ports()

    all_ok = True
    try:
        for repeat_index in range(repeats):
            if repeats > 1:
                print(f"Head motion test pass {repeat_index + 1}/{repeats}.")
            for label, motion in motion_plan:
                if motion not in HEAD_MOTION_SEQUENCES:
                    print(f"ERROR: unknown head motion {motion!r}.")
                    return 2
                print()
                if label.startswith("emotion:"):
                    print(f"Testing head emotion mapping: {label.removeprefix('emotion:')} -> {motion}")
                else:
                    print(f"Testing head motion: {motion}")
                all_ok = robot.run_head_motion(motion) and all_ok
                if gap_sec > 0:
                    time.sleep(gap_sec)
        if not all_ok:
            print("ERROR: one or more head motion commands failed.")
        return 0 if all_ok else 1
    except KeyboardInterrupt:
        print()
        print("Head motion test interrupted; sending reset.")
        robot.reset_head_position(reason="head_motion test interrupt reset")
        return 130


def cue_and_capture_speech_end_image(
    args: argparse.Namespace,
    camera_manager: CameraManager | None,
    wake_context: dict[str, Any],
    timing: TimingLogger | None,
) -> Future[bytes | None] | None:
    play_recording_cue(args, label="Speech-end capture")
    if timing is not None:
        timing.mark("speech-end beep done")

    metadata = wake_context.get("metadata") if isinstance(wake_context.get("metadata"), dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    wake_context["metadata"] = metadata
    metadata["capture_timestamp"] = datetime.now(timezone.utc).isoformat()
    metadata["image_capture_phase"] = "speech_end"

    image_delay_sec = max(0.0, float(getattr(args, "speech_end_image_delay", 0.0) or 0.0))
    image_future = camera_manager.capture_async(delay_sec=image_delay_sec) if camera_manager is not None else None
    wake_context["image_future"] = image_future
    metadata["image_capture_started"] = image_future is not None
    metadata["image_capture_delay_sec"] = image_delay_sec if image_future is not None else 0.0
    if image_future is not None:
        if image_delay_sec > 0.0:
            print(f"Speech-end image capture scheduled {image_delay_sec:.1f}s after end-of-speech cue.")
        else:
            print("Speech-end image capture started.")
    else:
        print("Speech-end image capture skipped or unavailable.")
    if timing is not None:
        timing.mark("speech-end image capture queued" if image_future is not None else "speech-end image unavailable")
    return image_future


def build_turn_upload_metadata(
    args: argparse.Namespace,
    *,
    recorder: WakeVolumeRecorder,
    meta: dict[str, Any],
    wake_context: dict[str, Any],
    image_bytes: bytes | None,
    input_sample_rate: int,
    conversation_session_id: str = "",
    conversation_turn_index: int = 1,
) -> dict[str, Any]:
    metadata = wake_context.get("metadata") if isinstance(wake_context.get("metadata"), dict) else {}
    metadata = dict(metadata)
    metadata["image_available"] = image_bytes is not None
    metadata["image_size_bytes"] = len(image_bytes) if image_bytes else 0
    metadata["vision_mode"] = "off" if args.no_vision else ("force" if args.force_vision else "auto")
    metadata["force_vision"] = args.force_vision
    metadata["no_vision"] = args.no_vision
    metadata["audio_duration_sec"] = meta.get("duration_sec")
    metadata["audio_rms"] = meta.get("audio_rms")
    metadata["input_sample_rate"] = int(meta.get("input_sample_rate") or recorder.sample_rate or input_sample_rate)
    metadata["turn_source"] = meta.get("turn_source", metadata.get("turn_source", "wake"))
    latency_profile = "ultra" if getattr(args, "ultra_response", False) else ("turbo" if getattr(args, "turbo_response", False) else "normal")
    fast_reply = bool(getattr(args, "fast_reply", False) or getattr(args, "turbo_response", False) or getattr(args, "ultra_response", False))
    metadata["latency_profile"] = latency_profile
    metadata["fast_reply"] = fast_reply
    reply_num_predict = int(getattr(args, "fast_reply_num_predict", 0) or 0)
    if fast_reply and reply_num_predict > 0:
        metadata["reply_num_predict"] = reply_num_predict
    if conversation_session_id:
        metadata["conversation_mode"] = bool(getattr(args, "conversation_mode", False))
        metadata["conversation_session_id"] = conversation_session_id
        metadata["conversation_turn_index"] = conversation_turn_index
    return metadata


def send_and_handle_audio_turn(
    args: argparse.Namespace,
    *,
    recorder: WakeVolumeRecorder,
    camera_manager: CameraManager | None,
    robot: RobotUartController,
    focus_manager: FocusModeManager | None,
    todo_manager: TodoListManager | None,
    esp32_ble_manager: Esp32BleBridgeManager | None,
    turn_state: dict[str, Any],
    audio: np.ndarray,
    meta: dict[str, Any],
    input_sample_rate: int,
    conversation_session_id: str = "",
    conversation_turn_index: int = 1,
) -> tuple[bool, bool]:
    """Send one recorded turn through the existing AI/tool/TTS/UART path.

    Returns (ok, end_conversation_session).
    """
    wav_path: Path | None = None
    try:
        wake_context = meta.get("wake_context") if isinstance(meta.get("wake_context"), dict) else {}
        if not wake_context:
            wake_context = build_conversation_turn_context(
                args,
                camera_manager,
                robot,
                turn_state,
                session_id=conversation_session_id or "single_turn",
                turn_index=conversation_turn_index,
                meta=meta,
            )
            meta["wake_context"] = wake_context

        timing = wake_context.get("timing") if isinstance(wake_context.get("timing"), TimingLogger) else turn_state.get("timing")
        if not isinstance(timing, TimingLogger):
            timing = None

        if timing is not None:
            timing.mark("audio recording finished")

        rms = voice_chat.rms_level(audio)
        print(f"Recorded {meta.get('duration_sec', 0.0):.2f}s; RMS={rms:.5f}")
        if "speech_start_threshold" in meta:
            print(
                "Recording gate: "
                f"noise_floor={meta.get('noise_floor')}, "
                f"start_threshold={meta.get('speech_start_threshold')}, "
                f"silence_base={meta.get('silence_base_threshold')}, "
                f"peak={meta.get('peak_volume')}"
            )
        if rms < args.rms_threshold:
            print("SKIP: audio RMS too low; not sending.")
            robot.force_motion_idle(reason="low RMS turn skipped")
            robot.restore_persistent_screen_state()
            return True, False
        meta["audio_rms"] = rms

        image_future = cue_and_capture_speech_end_image(args, camera_manager, wake_context, timing)
        image_bytes = wait_for_image_future(image_future, image_wait_timeout_for_context(args, wake_context))
        if timing is not None:
            timing.mark("image captured" if image_bytes else "image unavailable")

        metadata = build_turn_upload_metadata(
            args,
            recorder=recorder,
            meta=meta,
            wake_context=wake_context,
            image_bytes=image_bytes,
            input_sample_rate=input_sample_rate,
            conversation_session_id=conversation_session_id,
            conversation_turn_index=conversation_turn_index,
        )

        record_sample_rate = int(metadata["input_sample_rate"])
        wav_path = voice_chat.write_temp_wav_16k(audio, record_sample_rate)
        upload_label = "audio+image" if image_bytes else "audio only"
        print(
            f"POST {upload_label} to {args.server_url} "
            f"(vision_mode={metadata['vision_mode']}, image_size_bytes={metadata['image_size_bytes']})"
        )
        started = time.monotonic()
        response = send_audio_and_optional_image_to_server(
            args.server_url,
            wav_path,
            image_bytes=image_bytes,
            metadata=metadata,
            timeout_sec=args.timeout,
        )
        print(f"Round trip: {int((time.monotonic() - started) * 1000)} ms")
        if timing is not None:
            timing.mark("uploaded to server")
            timing.mark("transcript received")
            timing.mark("AI reply received")
        debug_obj = response.get("debug") if isinstance(response.get("debug"), dict) else {}
        raw_preview = str(debug_obj.get("ollama_content_preview", "")).strip()
        if raw_preview and not getattr(args, "quiet_dialog", False):
            print(f"AI raw response preview: {raw_preview}")

        transcript = str(response.get("transcript", "") or "")
        append_ai_trace(
            response,
            args,
            turn_source=str(metadata.get("turn_source") or meta.get("turn_source") or "wake"),
            recording_meta=meta,
            metadata=metadata,
        )
        focus_intent = detect_focus_mode_intent(transcript) if focus_manager is not None else None
        focus_was_running = focus_manager.is_running() if focus_manager is not None else False
        end_keyword = end_session_keyword(transcript)
        if getattr(args, "conversation_mode", False) and end_keyword and focus_intent is None:
            if getattr(args, "quiet_dialog", False):
                print_quiet_turn_summary(response)
            else:
                voice_chat.print_result(response, verbose_debug=args.debug)
                response_vision_summary(response)
            print(f"Conversation end keyword detected ({end_keyword}); returning to Normal and wake-only standby.")
            if getattr(args, "speak_end_reply", False):
                ok = handle_wake_chat_response(response, args, robot, timing, focus_manager, todo_manager, esp32_ble_manager)
                restore_after_conversation_end(args, robot, timing)
                recorder.reset_wake()
                return ok, True
            print("TTS skipped for end command.")
            robot.force_motion_idle(reason="conversation end without TTS")
            restore_after_conversation_end(args, robot, timing)
            recorder.reset_wake()
            return True, True

        ok = handle_wake_chat_response(response, args, robot, timing, focus_manager, todo_manager, esp32_ble_manager)
        if getattr(args, "conversation_mode", False) and response.get("_end_conversation_after_esp32_ble"):
            print("ESP32-S3 BLE control command handled; returning to wake-only standby.")
            recorder.reset_wake()
            return ok, True
        if conversation_should_end_after_sleep_control(response, args):
            print("Sleep mode requested; returning to wake-only standby so the next command requires Hey Jarvis.")
            recorder.reset_wake()
            return ok, True
        if focus_manager is not None and should_end_conversation_after_focus_turn(
            args,
            focus_intent=focus_intent,
            focus_was_running=focus_was_running,
            focus_is_running=focus_manager.is_running(),
        ):
            print("Focus mode turn handled; returning to wake-only standby so the next command requires Hey Jarvis.")
            recorder.reset_wake()
            return ok, True
        music_end_action = conversation_music_end_action(response, args)
        if music_end_action:
            print(
                f"Music control action ({music_end_action}) handled; "
                "returning to wake-only standby so the next music command requires Hey Jarvis."
            )
            recorder.reset_wake()
            post_music_standby_cooldown(args, music_end_action)
            return ok, True
        if getattr(args, "conversation_mode", False) and response.get("_client_tts_ok") is False:
            tts_error = str(response.get("_client_tts_error", "") or "").strip()
            detail = f" ({tts_error})" if tts_error else ""
            print(
                "TTS failed during this reply"
                f"{detail}; ending conversation follow-up so the next command requires Hey Jarvis."
            )
            if focus_manager is not None and focus_manager.is_running():
                robot.set_screen_mode("focus", reason="TTS failed; focus still running")
            else:
                robot.restore_persistent_screen_state()
            recorder.reset_wake()
            return ok, True
        return ok, False
    finally:
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass


def run_wake_voice_loop(args: argparse.Namespace) -> int:
    if not device_preflight(args):
        return 1
    lock = InstanceLock(args.instance_lock, enabled=not args.no_instance_lock)
    if not lock.acquire():
        return 1

    previous_sigint = signal.getsignal(signal.SIGINT)

    def request_stop(signum: int, frame: Any) -> None:
        print("\nStop requested; shutting down.")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_stop)

    focus_manager: FocusModeManager | None = None
    pet_idle_manager: PetIdleReflectionManager | None = None
    todo_event_listener: FrdmTodoEventListener | None = None
    fan_event_manager: FrdmFanControlManager | None = None
    esp32_ble_manager: Esp32BleBridgeManager | Esp32BleApiClientManager | None = None
    esp32_dashboard_server: Esp32DashboardControlServer | None = None
    temp_room_publisher: FrdmRoomTemperaturePublisher | None = None
    uart_bus: FrdmUartBus | None = None
    uart_proxy_server: FrdmUartProxyServer | None = None
    temperature_receiver: Esp32TemperatureReceiver | None = None
    try:
        temperature_receiver = maybe_start_esp32_temperature_receiver(args)
        temp_room_uart_enabled = (
            not getattr(args, "no_temp_room_uart", False)
            and float(getattr(args, "temp_room_uart_interval_sec", 10.0) or 0.0) > 0.0
        )
        if (
            bool(getattr(args, "esp32_ble", False))
            and not bool(getattr(args, "esp32_ble_sidecar", False))
            and not bool(getattr(args, "_esp32_ble_runtime_unavailable", False))
            and temperature_receiver is None
            and (not getattr(args, "no_weather_local_temperature", False) or temp_room_uart_enabled)
        ):
            temperature_receiver = Esp32TemperatureReceiver(args)
            setattr(args, "_esp32_temperature_receiver", temperature_receiver)
        if not voice_chat.preflight_server(args):
            signal.signal(signal.SIGINT, previous_sigint)
            if temperature_receiver is not None:
                temperature_receiver.stop()
            lock.release()
            return 1
        if not voice_chat.preflight_tts(args):
            signal.signal(signal.SIGINT, previous_sigint)
            if temperature_receiver is not None:
                temperature_receiver.stop()
            lock.release()
            return 1

        args.device = select_input_device(args)
        args.beep_device = select_beep_output_device(args)
        try:
            input_sample_rate = voice_chat.choose_input_sample_rate(args.device, args.input_sample_rate)
        except (RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}")
            signal.signal(signal.SIGINT, previous_sigint)
            if temperature_receiver is not None:
                temperature_receiver.stop()
            lock.release()
            return 1
    except Exception as exc:
        print(f"ERROR: {exc}")
        signal.signal(signal.SIGINT, previous_sigint)
        if temperature_receiver is not None:
            temperature_receiver.stop()
        lock.release()
        return 1

    camera_manager: CameraManager | None = None
    if args.no_vision:
        print("Camera disabled by --no-vision.")
    elif not args.no_camera:
        camera_manager = CameraManager(
            enabled=True,
            camera_id=parse_camera_id(args.camera_id),
            width=args.camera_width,
            height=args.camera_height,
            max_side=args.camera_max_side,
            jpeg_quality=args.camera_jpeg_quality,
            read_timeout=args.camera_read_timeout,
            latest_timeout=args.camera_latest_timeout,
            frame_max_age=args.camera_frame_max_age,
            warmup_frames=args.camera_warmup_frames,
            continuous=not args.camera_one_shot,
        )
        camera_manager.start()
    else:
        print("Camera disabled by --no-camera.")

    robot = RobotUartController(args)
    event_router = FrdmUartEventRouter(args)
    uart_bus = FrdmUartBus(args, line_handler=event_router.handle_line)
    uart_bus_started = uart_bus.start()
    setattr(args, "_frdm_uart_bus_active", uart_bus_started)
    if uart_bus_started:
        robot.attach_uart_bus(uart_bus)
        uart_proxy_server = FrdmUartProxyServer(args, robot)
        uart_proxy_server.start()
    focus_manager = FocusModeManager(args, camera_manager, uart_proxy_url=uart_proxy_server.url if uart_proxy_server is not None else "")
    pet_idle_manager = PetIdleReflectionManager(args, robot, focus_manager)
    todo_manager = TodoListManager(args)
    if bool(getattr(args, "esp32_ble_sidecar", False)):
        esp32_ble_manager = Esp32BleApiClientManager(args)
    else:
        esp32_ble_manager = Esp32BleBridgeManager(args, temperature_receiver)
    esp32_ble_manager.start()
    temp_room_publisher = FrdmRoomTemperaturePublisher(args, robot, temperature_receiver)
    temp_room_publisher.start()
    if (
        not getattr(args, "esp32_ble_sidecar", False)
        and not getattr(args, "no_esp32_dashboard_control", False)
        and bool(getattr(args, "esp32_ble", False))
    ):
        esp32_dashboard_server = Esp32DashboardControlServer(args, esp32_ble_manager)
        esp32_dashboard_server.start()
    todo_event_listener = FrdmTodoEventListener(args, robot, todo_manager)
    fan_event_manager = FrdmFanControlManager(args, esp32_ble_manager)
    event_router.add_handler("todo", todo_event_listener.handle_line)
    event_router.add_handler("fan", fan_event_manager.handle_line)
    todo_event_listener.start()
    fan_event_manager.start()
    turn_state: dict[str, Any] = {}
    recorder = WakeVolumeRecorder(
        args,
        sample_rate=input_sample_rate,
        wake_hook=build_wake_hook(args, camera_manager, robot, turn_state, pet_idle_manager),
    )
    try:
        recorder.load_wake_model()
    except Exception as exc:
        print(f"ERROR: {exc}")
        if fan_event_manager is not None:
            fan_event_manager.stop()
        if esp32_dashboard_server is not None:
            esp32_dashboard_server.stop()
        if temp_room_publisher is not None:
            temp_room_publisher.stop()
        if esp32_ble_manager is not None:
            esp32_ble_manager.stop()
        if todo_event_listener is not None:
            todo_event_listener.stop()
        if uart_proxy_server is not None:
            uart_proxy_server.stop()
        if uart_bus is not None:
            uart_bus.stop()
        if camera_manager is not None:
            camera_manager.release()
        if temperature_receiver is not None:
            temperature_receiver.stop()
        signal.signal(signal.SIGINT, previous_sigint)
        lock.release()
        return 1

    print("Wake voice chat + FRDM UART bridge ready.")
    print(f"Client version: {CLIENT_VERSION}")
    print("AI path: Jetson wake/record locally -> Windows desktop local /voice-chat -> local ASR/Ollama.")
    print("No Gemini/OpenAI cloud API is used by this bridge.")
    print(f"Server URL: {args.server_url}")
    if getattr(args, "no_uart", False):
        print("FRDM UART: disabled for this run.")
    else:
        print(
            f"FRDM UART: {args.uart_port} @ {args.uart_baudrate}, "
            f"line_ending={args.uart_line_ending}, "
            f"bus_tx_timeout={getattr(args, 'frdm_uart_tx_timeout', 0.45):g}s"
        )
        if getattr(args, "_frdm_uart_startup_missing", False):
            print("FRDM UART: waiting in auto-recovery mode; commands will resume after the device appears.")
    print(f"Input sample rate: {input_sample_rate} Hz; upload WAV sample rate: {voice_chat.SAMPLE_RATE} Hz")
    print(f"Wake word: {'disabled' if args.no_wake_word else args.wake_word}")
    print(
        f"wake_volume_min={args.wake_volume_min}, volume_min={args.volume_min}, "
        f"silence_duration={args.silence_duration}s, max_speech={args.max_speech_seconds}s, "
        f"max_recording={args.max_recording_seconds}s"
    )
    print(
        "Adaptive recording gate: "
        f"{'off' if args.no_adaptive_volume else 'on'}, "
        f"noise_p{args.noise_floor_percentile:g}, "
        f"speech_margin={args.speech_start_margin}, "
        f"speech_ratio={args.speech_start_ratio:g}, "
        f"silence_margin={args.silence_margin}, "
        f"silence_noise_ratio={args.silence_noise_ratio:g}, "
        f"peak_ratio={args.silence_peak_ratio:g}"
    )
    print(
        f"Audio read watchdog: callback queue, timeout={args.audio_read_timeout:g}s, "
        f"progress_interval={args.recording_progress_interval:g}s"
    )
    if args.conversation_mode:
        print(
            "Conversation mode: enabled, "
            f"turn_timeout={args.turn_listen_timeout:g}s, "
            f"idle_timeout={args.session_idle_timeout:g}s, "
            f"max_turns={args.max_session_turns}, "
            f"quiet_dialog={args.quiet_dialog}"
        )
        print("After an end phrase, wake-only standby is restored and normal speech is ignored until Hey Jarvis.")
    else:
        print("Conversation mode: disabled (classic one wake per turn).")
    beep_desc = (
        "disabled"
        if args.no_beep
        else (
            f"{args.beep_frequency:g} Hz, {args.beep_duration_ms} ms, "
            f"volume={args.beep_volume:g}, player={args.beep_player}, "
            f"device={args.beep_device if args.beep_device is not None else 'default'}"
        )
    )
    print(f"Recording beep: {beep_desc}")
    vision_mode = "off" if args.no_vision else ("force" if args.force_vision else "auto")
    print(f"Vision mode: {vision_mode}")
    print(f"Camera: {'disabled' if args.no_camera or args.no_vision else f'{args.camera_id}, {args.camera_width}x{args.camera_height}, jpeg_quality={args.camera_jpeg_quality}'}")
    focus_desc = (
        "disabled"
        if args.no_focus_mode
        else (
            f"enabled, script={args.focus_script}, interval={args.focus_interval_sec:g}s, "
            f"duration_default={args.focus_duration_min:g}min, notify={args.focus_notify_mode}, "
            f"alert_threshold={args.focus_alert_threshold}, alert_cooldown={args.focus_alert_cooldown_sec:g}s"
        )
    )
    print(f"Focus work mode: {focus_desc}")
    if args.no_pet_idle_reflection:
        pet_idle_desc = "disabled"
    else:
        pet_idle_desc = (
            f"enabled, interval={args.pet_idle_interval_sec:g}s +/- {args.pet_idle_jitter_sec:g}s, "
            f"min_idle={args.pet_idle_min_silent_sec:g}s, "
            f"share_cooldown={args.pet_idle_share_cooldown_sec:g}s, "
            f"show_thinking={args.pet_idle_show_thinking}"
        )
    print(f"Pet idle reflection: {pet_idle_desc}")
    todo_desc = "disabled" if args.no_todo_list else f"enabled, path={args.todo_list_path}"
    print(f"To-do list: {todo_desc}")
    uart_event_desc = "on" if frdm_uart_events_active(args) else "off"
    todo_event_desc = "off" if args.no_frdm_todo_events or args.no_dashboard_uart or not frdm_uart_events_active(args) else "on"
    dashboard_uart_desc = "disabled" if args.no_dashboard_uart or args.no_uart else "enabled"
    print(
        f"Dashboard UART data sync: {dashboard_uart_desc}, "
        f"todo_item_limit={args.dashboard_todo_item_limit}, "
        f"uart_event_bus={uart_event_desc}, "
        f"frdm_todo_events={todo_event_desc}"
    )
    fan_desc = "disabled" if args.no_frdm_fan_events or not frdm_uart_events_active(args) else (
        f"enabled, device={args.fan_device_id}, speed_max={args.fan_speed_max}, "
        f"dashboard_sync={not args.no_fan_dashboard_sync}, command={'set' if args.fan_control_command else 'not set'}"
    )
    print(f"FRDM fan touch events: {fan_desc}")
    if getattr(args, "esp32_ble", False) and bool(getattr(args, "_esp32_ble_runtime_unavailable", False)):
        esp32_ble_desc = (
            "requested but degraded, "
            f"reason={getattr(args, '_esp32_ble_runtime_unavailable_reason', 'unavailable')}; "
            "reconnect loop disabled so other features keep running"
        )
    elif getattr(args, "esp32_ble", False) and getattr(args, "esp32_ble_sidecar", False):
        esp32_ble_desc = (
            f"sidecar API, url={args.esp32_ble_api_url}, "
            f"voice_control={not args.no_esp32_ble_voice_control}, "
            f"frdm_relay={not args.no_esp32_ble_frdm_control}, "
            f"timeout={args.esp32_ble_api_timeout:g}s"
        )
    elif getattr(args, "esp32_ble", False):
        esp32_ble_desc = (
            f"enabled, name={args.esp32_ble_name}, "
            f"address={args.esp32_ble_address or 'scan-by-name'}, "
            f"voice_control={not args.no_esp32_ble_voice_control}, "
            f"frdm_relay={not args.no_esp32_ble_frdm_control}, "
            f"min_pwm={esp32_ble.min_nonzero_pwm() if esp32_ble is not None else '?'}, "
            f"passive_reminder={not args.no_esp32_ble_passive_reminder} "
            f"(>{args.esp32_ble_passive_threshold:g} C)"
        )
    else:
        esp32_ble_desc = "disabled"
    print(f"ESP32-S3 BLE fan/LED/temp: {esp32_ble_desc}")
    if esp32_dashboard_server is not None:
        print(f"ESP32 dashboard API: http://{esp32_dashboard_server.host}:{esp32_dashboard_server.port}/api/esp32/status")
    print(
        "Recording cues: start beep before each turn; "
        f"speech-end beep + image capture before upload (delay={args.speech_end_image_delay:g}s)"
    )
    if args.no_music:
        music_desc = "disabled"
    else:
        music_desc = (
            f"{args.music_url}, backend={args.music_backend}->{resolve_music_backend(args)}, "
            f"autostart={not args.no_music_autostart}, "
            f"mpv_audio={args.music_mpv_audio_device}, "
            f"mpv_volume={args.music_mpv_volume}/{args.music_mpv_volume_max}, "
            f"mpv_cookies={'set' if (args.music_mpv_ytdl_cookies or args.music_mpv_ytdl_cookies_from_browser) else 'not set'}, "
            f"pause_on_wake={not args.no_music_pause_on_wake}, "
            f"wake_guard={f'threshold={args.music_wake_threshold:g}/confirm={args.music_wake_confirm_chunks}' if (args.music_wake_guard and not args.no_music_wake_guard) else 'off'}, "
            f"beep_settle={args.music_wake_beep_settle:g}s, "
            f"wake_gate_reset={args.music_reset_recording_gate_on_wake}, "
            f"post_music_cooldown={args.post_music_standby_cooldown:g}s"
        )
    print(f"Music tool: {music_desc}")
    startup_time = "off" if args.no_startup_time or args.no_uart else "on"
    print(f"Startup time UART: {startup_time}")
    if args.no_weather:
        weather_desc = "disabled"
    else:
        startup_weather = (
            "off"
            if args.no_startup_weather or args.no_uart
            else f"daily:{args.startup_weather_text}; current:{args.startup_weather_current_text}"
        )
        weather_desc = (
            f"{args.weather_url}, default_location={args.weather_default_location}, "
            f"source=Open-Meteo, startup_uart={startup_weather}"
        )
    print(f"Weather tool: {weather_desc}")
    if args.no_weather_local_temperature:
        local_temp_desc = "disabled"
    elif (
        getattr(args, "esp32_ble", False)
        and not bool(getattr(args, "_esp32_ble_runtime_unavailable", False))
        and args.esp32_temperature_mode == "disabled"
    ):
        local_temp_desc = "BLE notify from ESP32-S3 status characteristic"
    elif args.esp32_temperature_mode == "disabled":
        local_temp_desc = "disabled"
    elif args.esp32_temperature_mode == "push":
        local_temp_desc = f"push receiver http://{args.esp32_temperature_host}:{args.esp32_temperature_port}{normalize_temperature_path(args.esp32_temperature_path)}"
    elif args.esp32_temperature_mode == "pull":
        local_temp_desc = f"pull {args.esp32_temperature_url or '(missing --esp32-temperature-url)'}"
    else:
        local_temp_desc = (
            f"push receiver http://{args.esp32_temperature_host}:{args.esp32_temperature_port}{normalize_temperature_path(args.esp32_temperature_path)}; "
            f"pull fallback {args.esp32_temperature_url or '(none)'}"
        )
    print(f"Weather local temperature: {local_temp_desc}")
    if getattr(args, "no_uart", False) or getattr(args, "no_temp_room_uart", False):
        temp_room_desc = "disabled"
    elif float(getattr(args, "temp_room_uart_interval_sec", 10.0) or 0.0) <= 0.0:
        temp_room_desc = "disabled (interval <= 0)"
    elif temperature_receiver is None and str(getattr(args, "esp32_temperature_mode", "disabled") or "disabled").strip().lower() in {"pull", "both"} and str(getattr(args, "esp32_temperature_url", "") or "").strip():
        temp_room_desc = (
            f"TempRoom every {args.temp_room_uart_interval_sec:g}s from "
            f"{args.esp32_temperature_url}, max_age={args.temp_room_uart_max_age_sec:g}s, payload=Celsius*10"
        )
    elif temperature_receiver is None:
        temp_room_desc = "disabled (no ESP32 temperature source)"
    else:
        temp_room_desc = (
            f"TempRoom every {args.temp_room_uart_interval_sec:g}s, "
            f"max_age={args.temp_room_uart_max_age_sec:g}s, payload=Celsius*10"
        )
    print(f"FRDM room temperature UART: {temp_room_desc}")
    print(
        "Head motor motion: "
        f"enabled={robot.head_motor_enabled()}, "
        f"smooth_step={robot.motor_smooth_step_deg()}deg, "
        f"step_delay={robot.motor_step_delay():g}s, "
        f"speaking_step_delay={robot.motor_speaking_step_delay():g}s, "
        f"speaking_smooth_step={robot.motor_speaking_smooth_step_deg()}deg, "
        f"reset_repeats={robot.motor_reset_repeats()}, "
        f"reset_delay={robot.motor_reset_delay():g}s, "
        f"read_ms={robot.motor_read_ms()}, "
        f"stop_timeout={robot.motor_stop_timeout():g}s, "
        f"join_timeout={args.motor_join_timeout:g}s"
    )
    print(
        f"TTS queue polling: every {args.tts_poll_interval:g}s, "
        f"start_poll={args.tts_start_poll_interval:g}s, "
        f"speaking_start_timeout={args.tts_speaking_start_timeout:g}s, "
        f"speaking_requires_audio={getattr(args, 'tts_speaking_require_audio', True)}, "
        f"playback_timeout={args.tts_playback_timeout:g}s, "
        f"volume_gain={getattr(args, 'tts_volume_gain', 1.0):g}"
    )
    if args._manual_input_device:
        print("WARNING: --device pins a numeric microphone index. Omit --device and use --mic-keyword for USB replug recovery.")
    if args._manual_beep_device:
        print("WARNING: --beep-device pins a numeric speaker index. Omit --beep-device and use --beep-keyword for USB replug recovery.")
    print(
        "USB auto-discovery: "
        f"mic={'fixed index ' + str(args.device) if args._manual_input_device else 'keyword ' + repr(args.mic_keyword)}; "
        f"beep={'disabled' if args.no_beep else ('fixed index ' + str(args.beep_device) if args._manual_beep_device else 'keyword ' + repr(args.beep_keyword))}; "
        f"camera={'disabled' if args.no_camera or args.no_vision else str(args.camera_id)}; "
        f"FRDM UART={'disabled' if args.no_uart else args.uart_port}"
    )
    boot_delay = max(0.0, float(getattr(args, "boot_normal_delay", 2.0) or 0.0))
    if boot_delay > 0:
        print(f"Boot screen settle: waiting {boot_delay:g}s, then sending startup dashboard data and Normal.")
        time.sleep(boot_delay)
    send_startup_time_update(args, robot)
    send_startup_weather_update(args, robot)
    send_startup_dashboard_updates(args, robot, todo_manager=todo_manager, camera_manager=camera_manager)
    robot.set_screen_mode("normal", reason="startup boot screen complete")
    if pet_idle_manager is not None:
        pet_idle_manager.start()
    print("Press Ctrl+C to quit.")

    try:
        while True:
            try:
                audio, meta = recorder.record_once()
                if audio is None:
                    wake_context = meta.get("wake_context") if isinstance(meta.get("wake_context"), dict) else {}
                    if wake_context:
                        print(f"No send after wake: {meta.get('reason', 'unknown')}; restoring persistent screen state.")
                        robot.restore_persistent_screen_state()
                        if pet_idle_manager is not None:
                            pet_idle_manager.end_user_interaction("wake without send")
                    continue

                try:
                    session_id = uuid.uuid4().hex[:10] if args.conversation_mode else ""
                    if args.conversation_mode:
                        print()
                        print(f"Conversation session started: session_id={session_id}")

                    ok, end_session = send_and_handle_audio_turn(
                        args,
                        recorder=recorder,
                        camera_manager=camera_manager,
                        robot=robot,
                        focus_manager=focus_manager,
                        todo_manager=todo_manager,
                        esp32_ble_manager=esp32_ble_manager,
                        turn_state=turn_state,
                        audio=audio,
                        meta=meta,
                        input_sample_rate=input_sample_rate,
                        conversation_session_id=session_id,
                        conversation_turn_index=1,
                    )
                    if not ok:
                        return 1
                    if not args.conversation_mode or end_session:
                        if args.conversation_mode:
                            print("Wake-only standby restored. Say Hey Jarvis before speaking again.")
                        continue

                    last_activity_at = time.monotonic()
                    max_session_turns = max(1, int(args.max_session_turns or 1))
                    if max_session_turns <= 1:
                        print("Max conversation turns reached (1); returning to wake-only standby.")
                        robot.set_screen_mode("normal", reason="conversation max turns reached")
                        print("Wake-only standby restored. Say Hey Jarvis before speaking again.")
                        continue

                    for turn_index in range(2, max_session_turns + 1):
                        idle_sec = time.monotonic() - last_activity_at
                        remaining_idle = max(0.0, float(args.session_idle_timeout or 0.0) - idle_sec)
                        followup_timeout = args.turn_listen_timeout
                        if args.session_idle_timeout > 0:
                            followup_timeout = min(float(args.turn_listen_timeout), remaining_idle)
                        if followup_timeout <= 0:
                            print("Conversation idle timeout reached; returning to wake-only standby.")
                            robot.set_screen_mode("normal", reason="conversation idle timeout")
                            break

                        followup_context = build_conversation_turn_context(
                            args,
                            camera_manager,
                            robot,
                            turn_state,
                            session_id=session_id,
                            turn_index=turn_index,
                            meta={
                                "wake_score": 1.0,
                                "turn_source": "conversation_followup",
                            },
                            play_cue=True,
                        )

                        followup_audio, followup_meta = recorder.record_followup_turn(
                            listen_timeout=followup_timeout,
                            turn_source="conversation_followup",
                        )
                        if followup_audio is None:
                            print(f"No follow-up send: {followup_meta.get('reason', 'unknown')}.")
                            robot.set_screen_mode("normal", reason="conversation follow-up timeout")
                            break
                        followup_meta["wake_context"] = followup_context

                        ok, end_session = send_and_handle_audio_turn(
                            args,
                            recorder=recorder,
                            camera_manager=camera_manager,
                            robot=robot,
                            focus_manager=focus_manager,
                            todo_manager=todo_manager,
                            esp32_ble_manager=esp32_ble_manager,
                            turn_state=turn_state,
                            audio=followup_audio,
                            meta=followup_meta,
                            input_sample_rate=input_sample_rate,
                            conversation_session_id=session_id,
                            conversation_turn_index=turn_index,
                        )
                        if not ok:
                            return 1
                        last_activity_at = time.monotonic()
                        if end_session:
                            break
                    else:
                        print(f"Max conversation turns reached ({args.max_session_turns}); returning to wake-only standby.")
                        robot.set_screen_mode("normal", reason="conversation max turns reached")

                    print("Wake-only standby restored. Say Hey Jarvis before speaking again.")
                finally:
                    if pet_idle_manager is not None:
                        pet_idle_manager.end_user_interaction("voice session complete")
            except KeyboardInterrupt:
                print()
                return 0
            except Exception as exc:
                print(f"ERROR: {exc}")
                robot.restore_persistent_screen_state()
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if pet_idle_manager is not None:
                pet_idle_manager.shutdown()
            if fan_event_manager is not None:
                fan_event_manager.stop()
            if esp32_dashboard_server is not None:
                esp32_dashboard_server.stop()
            if temp_room_publisher is not None:
                temp_room_publisher.stop()
            if esp32_ble_manager is not None:
                esp32_ble_manager.stop()
            if todo_event_listener is not None:
                todo_event_listener.stop()
            if focus_manager is not None:
                focus_manager.shutdown()
            if uart_proxy_server is not None:
                uart_proxy_server.stop()
            if uart_bus is not None:
                uart_bus.stop()
            if temperature_receiver is not None:
                temperature_receiver.stop()
            if camera_manager is not None:
                camera_manager.release()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            lock.release()


def add_wake_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.description = "Hands-free wake-word voice chat + FRDM MCXN947 UART bridge."
    safety_group = parser.add_argument_group("demo safety")
    safety_group.add_argument("--instance-lock", default=os.getenv("WAKE_BRIDGE_LOCK", DEFAULT_INSTANCE_LOCK))
    safety_group.add_argument("--no-instance-lock", action="store_true", help="Allow multiple bridge processes. Not recommended for demos.")
    safety_group.add_argument("--self-test", action="store_true", help="Run parser/UART/TTS timing dry-run checks and exit without hardware.")
    safety_group.add_argument("--test-beep", action="store_true", help="Play the recording cue beep once and exit.")
    safety_group.add_argument(
        "--noisy-room",
        action="store_true",
        help="Apply louder beep and stricter adaptive recording defaults for noisy demo rooms.",
    )
    safety_group.add_argument(
        "--boot-normal-delay",
        type=float,
        default=_env_float("BOOT_NORMAL_DELAY", 2.0),
        help="Seconds to keep the FRDM boot screen before sending Normal at startup. Set 0 to skip.",
    )
    safety_group.add_argument("--no-device-preflight", action="store_true", help="Do not release stale demo processes/device owners before opening mic/camera/UART.")
    safety_group.add_argument("--device-preflight-only", action="store_true", help="Run startup device preflight and exit without opening the bridge.")
    safety_group.add_argument("--device-preflight-dry-run", action="store_true", help="Print which processes would be stopped, but do not kill them.")
    safety_group.add_argument("--device-preflight-verbose", action="store_true", help="Print target device nodes checked during startup preflight.")
    safety_group.add_argument("--device-preflight-keep-music", action="store_true", help="Do not stop mpv/ffplay music playback during startup preflight.")
    safety_group.add_argument("--kill-audio-servers", action="store_true", help="Also allow preflight to stop pulseaudio/pipewire/wireplumber if they own target audio devices.")
    safety_group.add_argument("--device-preflight-grace", type=float, default=_env_float("DEVICE_PREFLIGHT_GRACE", 0.8))
    safety_group.add_argument("--device-preflight-settle", type=float, default=_env_float("DEVICE_PREFLIGHT_SETTLE", 0.8))
    safety_group.add_argument("--no-usb-reset-if-missing", action="store_true", help="Do not reset the Jetson USB host when demo USB devices are missing.")
    safety_group.add_argument("--usb-controller", default=os.getenv("USB_CONTROLLER", "3610000.usb"))
    safety_group.add_argument("--usb-reset-wait", type=float, default=_env_float("USB_RESET_WAIT", 6.0))
    safety_group.add_argument("--device-ready-timeout", type=float, default=_env_float("DEVICE_READY_TIMEOUT", 12.0))
    safety_group.add_argument(
        "--allow-default-mic",
        action="store_true",
        help="Use the system default input if --mic-keyword is not found. By default this bridge exits instead of recording from Jetson APE.",
    )

    group = parser.add_argument_group("wake-word auto recording")
    group.add_argument("--wake-word", default=os.getenv("WAKE_WORD", "hey_jarvis"))
    group.add_argument("--wake-threshold", type=float, default=_env_float("WAKE_THRESHOLD", 0.5))
    group.add_argument("--no-wake-word", action="store_true", help="Start recording by volume only, without openWakeWord.")
    group.add_argument(
        "--wake-volume-min",
        type=int,
        default=_env_int("WAKE_VOLUME_MIN", 350),
        help="Mean abs int16 volume required before accepting a wake score. This filters low-volume false positives.",
    )
    group.add_argument("--volume-min", type=int, default=_env_int("VOLUME_MIN", 700), help="Base mean abs int16 volume needed to count as speech.")
    group.add_argument("--silence-duration", type=float, default=_env_float("SILENCE_DURATION", 1.2))
    group.add_argument("--min-speech-seconds", type=float, default=_env_float("MIN_SPEECH_SECONDS", 0.4))
    group.add_argument("--max-speech-seconds", type=float, default=_env_float("MAX_SPEECH_SECONDS", 5.0))
    group.add_argument(
        "--max-recording-seconds",
        type=float,
        default=_env_float("MAX_RECORDING_SECONDS", 7.0),
        help=(
            "Hard wall-clock recording cap from wake detection. "
            "Unlike --max-speech-seconds, this also applies before speech is detected."
        ),
    )
    group.add_argument(
        "--audio-read-timeout",
        type=float,
        default=_env_float("AUDIO_READ_TIMEOUT", 0.75),
        help="Input-stream watchdog timeout. If the USB mic stops producing chunks, reopen/exit the current recording loop.",
    )
    group.add_argument(
        "--recording-progress-interval",
        type=float,
        default=_env_float("RECORDING_PROGRESS_INTERVAL", 1.0),
        help="Seconds between concise recording progress logs after wake detection.",
    )
    group.add_argument("--wake-listen-timeout", type=float, default=_env_float("WAKE_LISTEN_TIMEOUT", 6.0))
    group.add_argument("--wake-chunk-ms", type=int, default=_env_int("WAKE_CHUNK_MS", 80))
    group.add_argument("--idle-volume-print-min", type=int, default=_env_int("IDLE_VOLUME_PRINT_MIN", 100))
    group.add_argument(
        "--standby-progress-interval",
        type=float,
        default=_env_float("STANDBY_PROGRESS_INTERVAL", 1.5),
        help="Seconds between concise standby audio logs. Set 0 to hide standby volume logs unless --listen-debug is used.",
    )
    group.add_argument("--listen-debug", action="store_true", help="Print standby/recording volume on every chunk.")
    group.add_argument("--no-adaptive-volume", action="store_true", help="Disable adaptive noise-floor based speech/silence thresholds.")
    group.add_argument("--noise-floor-percentile", type=float, default=_env_float("NOISE_FLOOR_PERCENTILE", 75.0))
    group.add_argument(
        "--wake-volume-ratio",
        type=float,
        default=_env_float("WAKE_VOLUME_RATIO", 1.15),
        help="Adaptive wake volume gate as noise_floor * ratio. Useful when background is very loud.",
    )
    group.add_argument(
        "--wake-volume-margin",
        type=int,
        default=_env_int("WAKE_VOLUME_MARGIN", 0),
        help="Adaptive wake volume gate also requires noise_floor + this margin.",
    )
    group.add_argument(
        "--wake-volume-window-seconds",
        type=float,
        default=_env_float("WAKE_VOLUME_WINDOW_SECONDS", 1.0),
        help="Recent-volume peak window used when accepting delayed wake-word scores.",
    )
    group.add_argument("--speech-start-margin", type=int, default=_env_int("SPEECH_START_MARGIN", 350))
    group.add_argument("--silence-margin", type=int, default=_env_int("SILENCE_MARGIN", 650))
    group.add_argument(
        "--speech-start-ratio",
        type=float,
        default=_env_float("SPEECH_START_RATIO", 1.25),
        help="Adaptive speech-start gate also requires noise_floor * this ratio.",
    )
    group.add_argument(
        "--silence-noise-ratio",
        type=float,
        default=_env_float("SILENCE_NOISE_RATIO", 1.15),
        help="Adaptive silence gate also allows end-of-speech below noise_floor * this ratio.",
    )
    group.add_argument("--silence-peak-ratio", type=float, default=_env_float("SILENCE_PEAK_RATIO", 0.35))
    group.add_argument("--pre-speech-seconds", type=float, default=_env_float("PRE_SPEECH_SECONDS", 0.35))

    conversation_group = parser.add_argument_group("one-wake conversation mode")
    conversation_group.add_argument(
        "--conversation-mode",
        action="store_true",
        help="After one Hey Jarvis wake, keep listening for follow-up turns until an end phrase or timeout.",
    )
    conversation_group.add_argument("--turn-listen-timeout", type=float, default=_env_float("TURN_LISTEN_TIMEOUT", 6.0))
    conversation_group.add_argument("--session-idle-timeout", type=float, default=_env_float("SESSION_IDLE_TIMEOUT", 24.0))
    conversation_group.add_argument("--max-session-turns", type=int, default=_env_int("MAX_SESSION_TURNS", 20))
    conversation_group.add_argument(
        "--quiet-dialog",
        action="store_true",
        help="Do not print transcript/reply text in the terminal; keep request id, timing, and device/tool logs.",
    )
    conversation_group.add_argument(
        "--ai-trace-path",
        default=os.getenv("AI_TRACE_PATH", str(DEFAULT_AI_TRACE_PATH)),
        help="JSONL path for dashboard AI trace: user transcript + model reply.",
    )
    conversation_group.add_argument(
        "--no-ai-trace-log",
        action="store_true",
        help="Disable writing the dashboard AI trace JSONL.",
    )
    conversation_group.add_argument(
        "--wake-status-path",
        default=os.getenv("WAKE_STATUS_PATH", str(DEFAULT_WAKE_STATUS_PATH)),
        help="JSON status file consumed by the smart home dashboard for live listening/volume/wake-score display.",
    )
    conversation_group.add_argument(
        "--no-wake-status-log",
        action="store_true",
        help="Disable writing live wake/listening status for the dashboard.",
    )
    conversation_group.add_argument(
        "--speak-end-reply",
        action="store_true",
        help="In conversation mode, speak the AI farewell reply before returning to Normal and wake-only standby.",
    )
    conversation_group.add_argument(
        "--no-sleep-on-conversation-end",
        action="store_true",
        help="Legacy compatibility flag. End phrases now return to Normal by default.",
    )
    conversation_group.add_argument(
        "--keep-conversation-after-music-control",
        action="store_true",
        help="Do not auto-end conversation mode after play/pause/stop/resume music commands.",
    )
    conversation_group.add_argument(
        "--turbo-response",
        action="store_true",
        help="Apply more aggressive low-latency defaults for silence, follow-up timeout, TTS polling, and TTS speed.",
    )
    conversation_group.add_argument(
        "--ultra-response",
        action="store_true",
        help="Apply the fastest demo defaults. Shorter waits and shorter model replies; may cut long pauses sooner.",
    )
    conversation_group.add_argument(
        "--fast-reply",
        action="store_true",
        help="Ask the desktop server for shorter LLM replies via client metadata.",
    )
    conversation_group.add_argument(
        "--fast-reply-num-predict",
        type=int,
        default=_env_int("FAST_REPLY_NUM_PREDICT", 0),
        help="Ollama num_predict hint sent to the desktop server when fast replies are enabled.",
    )

    pet_group = parser.add_argument_group("pet idle reflection")
    pet_group.add_argument(
        "--no-pet-idle-reflection",
        action="store_true",
        default=not _env_bool("PET_IDLE_REFLECTION", True),
        help="Disable the desktop pet's idle self-reflection loop. Set PET_IDLE_REFLECTION=0 for the same effect.",
    )
    pet_group.add_argument(
        "--pet-idle-interval-sec",
        type=float,
        default=_env_float("PET_IDLE_INTERVAL_SEC", 30.0),
        help="Seconds between internal idle self-reflection checks.",
    )
    pet_group.add_argument(
        "--pet-idle-jitter-sec",
        type=float,
        default=_env_float("PET_IDLE_JITTER_SEC", 5.0),
        help="Random +/- jitter around --pet-idle-interval-sec so the pet feels less clock-like.",
    )
    pet_group.add_argument(
        "--pet-idle-min-silent-sec",
        type=float,
        default=_env_float("PET_IDLE_MIN_SILENT_SEC", 30.0),
        help="Minimum seconds since the last voice interaction before idle reflection may run.",
    )
    pet_group.add_argument(
        "--pet-idle-share-cooldown-sec",
        type=float,
        default=_env_float("PET_IDLE_SHARE_COOLDOWN_SEC", 180.0),
        help="Minimum seconds between spontaneous spoken shares; internal silent checks can still happen.",
    )
    pet_group.add_argument(
        "--pet-idle-timeout",
        type=float,
        default=_env_float("PET_IDLE_TIMEOUT", 25.0),
        help="HTTP timeout for idle /text-chat self-reflection calls.",
    )
    pet_group.add_argument(
        "--pet-idle-show-thinking",
        action="store_true",
        default=_env_bool("PET_IDLE_SHOW_THINKING", False),
        help="Show the Thinking face while running silent idle reflection checks.",
    )
    pet_group.add_argument(
        "--pet-idle-while-sleeping",
        action="store_true",
        default=_env_bool("PET_IDLE_WHILE_SLEEPING", False),
        help="Allow idle reflection while the persistent robot state is Sleep.",
    )
    pet_group.add_argument(
        "--pet-idle-debug",
        action="store_true",
        default=_env_bool("PET_IDLE_DEBUG", False),
        help="Print idle reflection skip/silence decisions.",
    )

    beep_group = parser.add_argument_group("recording cue beep")
    beep_group.add_argument("--no-beep", action="store_true", help="Disable the short beep after wake detection.")
    beep_group.add_argument("--beep-duration-ms", type=int, default=_env_int("BEEP_DURATION_MS", 180))
    beep_group.add_argument("--beep-frequency", type=float, default=_env_float("BEEP_FREQUENCY", 1320.0))
    beep_group.add_argument("--beep-volume", type=float, default=_env_float("BEEP_VOLUME", 0.55))
    beep_group.add_argument(
        "--beep-player",
        choices=["auto", "pulse", "paplay", "aplay", "sounddevice"],
        default=os.getenv("BEEP_PLAYER", "auto"),
        help="Recording cue playback backend. auto prefers paplay/PulseAudio and avoids PortAudio ALSA playback crashes.",
    )
    beep_group.add_argument("--beep-device", type=int, default=None, help="Optional sounddevice output device index for the beep.")
    beep_group.add_argument("--beep-keyword", default=os.getenv("BEEP_KEYWORD", "UACDemo"), help="Output-device keyword used when --beep-device is omitted.")
    beep_group.add_argument("--beep-device-lookup-timeout", type=float, default=_env_float("BEEP_DEVICE_LOOKUP_TIMEOUT", 0.25), help="Fast timeout for resolving the beep output device after startup.")
    beep_group.add_argument("--beep-retry-delay", type=float, default=_env_float("BEEP_RETRY_DELAY", 0.12))
    beep_group.add_argument("--no-beep-default-retry", action="store_true", help="Do not retry the beep on the default output device if the keyword output is busy.")

    camera_group = parser.add_argument_group("wake camera capture")
    camera_group.add_argument("--no-camera", action="store_true", help="Disable wake-time camera capture.")
    camera_group.add_argument("--camera-id", default=os.getenv("WAKE_CAMERA_ID", "auto"), help="Camera id, e.g. auto, 0, or /dev/video0.")
    camera_group.add_argument("--camera-width", type=int, default=_env_int("WAKE_CAMERA_WIDTH", 640))
    camera_group.add_argument("--camera-height", type=int, default=_env_int("WAKE_CAMERA_HEIGHT", 480))
    camera_group.add_argument("--camera-max-side", type=int, default=_env_int("WAKE_CAMERA_MAX_SIDE", 640))
    camera_group.add_argument("--camera-jpeg-quality", type=int, default=_env_int("WAKE_CAMERA_JPEG_QUALITY", 78))
    camera_group.add_argument("--camera-read-timeout", type=float, default=_env_float("WAKE_CAMERA_READ_TIMEOUT", 7.0))
    camera_group.add_argument("--camera-result-timeout", type=float, default=_env_float("WAKE_CAMERA_RESULT_TIMEOUT", 1.0))
    camera_group.add_argument("--camera-latest-timeout", type=float, default=_env_float("WAKE_CAMERA_LATEST_TIMEOUT", 1.0))
    camera_group.add_argument("--camera-frame-max-age", type=float, default=_env_float("WAKE_CAMERA_FRAME_MAX_AGE", 2.0))
    camera_group.add_argument("--camera-warmup-frames", type=int, default=_env_int("WAKE_CAMERA_WARMUP_FRAMES", 3))
    camera_group.add_argument("--camera-one-shot", action="store_true", help="Disable the continuous warm reader and open the camera only at wake time.")
    camera_group.add_argument(
        "--pre-record-image-delay",
        type=float,
        default=_env_float("PRE_RECORD_IMAGE_DELAY", 0.0),
        help="Deprecated compatibility option. Images are now captured after speech ends; use --speech-end-image-delay.",
    )
    camera_group.add_argument(
        "--speech-end-image-delay",
        type=float,
        default=_env_float("SPEECH_END_IMAGE_DELAY", 0.0),
        help="Seconds after the end-of-speech beep to capture the image uploaded with that voice turn.",
    )
    vision_group = parser.add_argument_group("vision routing")
    vision_group.add_argument("--force-vision", action="store_true", help="Force Windows server to use the uploaded image when one is available.")
    vision_group.add_argument("--no-vision", action="store_true", help="Disable camera capture and Windows vision analysis for this client.")

    focus_group = parser.add_argument_group("focus work mode")
    focus_group.add_argument("--no-focus-mode", action="store_true", help="Disable voice-triggered focus work mode start/stop.")
    focus_group.add_argument("--focus-script", default=str(DEFAULT_FOCUS_SCRIPT), help="Path to focus_work_mode.py.")
    focus_group.add_argument("--focus-server-url", default=os.getenv("FOCUS_SERVER_URL", ""), help="Optional /focus-check URL. Defaults to the current server base.")
    focus_group.add_argument("--focus-interval-sec", type=float, default=_env_float("FOCUS_INTERVAL_SEC", 60.0))
    focus_group.add_argument(
        "--focus-first-sample-delay-sec",
        type=float,
        default=_env_float("FOCUS_FIRST_SAMPLE_DELAY_SEC", -1.0),
        help="Delay before the first focus camera sample after Focus screen activation. Negative means use --focus-interval-sec.",
    )
    focus_group.add_argument("--focus-duration-min", type=float, default=_env_float("FOCUS_DURATION_MIN", 0.0), help="Default auto-stop duration. 0 means wait for voice stop.")
    focus_group.add_argument("--focus-log-root", default=os.getenv("FOCUS_LOG_ROOT", str(THIS_DIR / "logs" / "focus_sessions")))
    focus_group.add_argument("--focus-task", default=os.getenv("FOCUS_TASK", ""), help="Default focus task if the start command does not include one.")
    focus_group.add_argument("--focus-alert-threshold", type=int, default=_env_int("FOCUS_ALERT_THRESHOLD", 1))
    focus_group.add_argument("--focus-alert-cooldown-sec", type=float, default=_env_float("FOCUS_ALERT_COOLDOWN_SEC", 90.0))
    focus_group.add_argument("--no-focus-alert-tts", action="store_true", help="Disable spoken warnings from background focus monitoring.")
    focus_group.add_argument("--no-focus-alert-motion", action="store_true", help="Disable MotorYawPitch warnings from background focus monitoring.")
    focus_group.add_argument("--focus-save-images", action="store_true", help="Debug only: let focus_work_mode.py save sampled images.")
    focus_group.add_argument("--focus-notify-mode", choices=["none", "discord"], default=os.getenv("FOCUS_NOTIFY_MODE", "none"))
    focus_group.add_argument("--focus-discord-webhook-url", default=default_discord_webhook_url())
    focus_group.add_argument("--focus-notify-timeout", type=float, default=_env_float("FOCUS_NOTIFY_TIMEOUT", 8.0))
    focus_group.add_argument("--focus-notify-dry-run", action="store_true", help="Print focus notification payload without sending it.")

    todo_group = parser.add_argument_group("local to-do list")
    todo_group.add_argument("--no-todo-list", action="store_true", help="Disable local voice-triggered to-do list commands.")
    todo_group.add_argument(
        "--todo-list-path",
        default=os.getenv("TODO_LIST_PATH", str(DEFAULT_TODO_LIST_PATH)),
        help="JSON file for local to-do list state. Default: frdm_uart_context_sender/logs/todo_list.json.",
    )
    todo_group.add_argument("--todo-debug", action="store_true", help="Print local to-do intent parsing details.")

    music_group = parser.add_argument_group("music tool routing")
    music_group.add_argument("--no-music", action="store_true", help="Disable local Music Web Player sidecar routing.")
    music_group.add_argument("--music-url", default=os.getenv("MUSIC_TOOL_URL", DEFAULT_MUSIC_TOOL_URL), help="Music Web Player /music endpoint.")
    music_group.add_argument("--music-backend", choices=["auto", "browser", "mpv"], default=os.getenv("MUSIC_TOOL_BACKEND", "auto"))
    music_group.add_argument("--music-timeout", type=float, default=_env_float("MUSIC_TOOL_TIMEOUT", 3.0))
    music_group.add_argument("--music-mpv-audio-device", default=os.getenv("MUSIC_MPV_AUDIO_DEVICE", os.getenv("MPV_AUDIO_DEVICE", "auto")), help="Audio device passed to auto-started music_web_player mpv backend.")
    music_group.add_argument("--music-mpv-audio-keyword", default=os.getenv("MUSIC_MPV_AUDIO_KEYWORD", os.getenv("MPV_AUDIO_DEVICE_KEYWORD", "UACDemo")), help="Keyword for auto mpv audio device discovery.")
    music_group.add_argument("--music-mpv-ytdl-cookies", default=os.getenv("MUSIC_MPV_YTDL_COOKIES", os.getenv("MPV_YTDL_COOKIES", os.getenv("YTDLP_COOKIES", ""))), help="Optional cookies.txt passed to auto-started mpv/yt-dlp for logged-in YouTube playback.")
    music_group.add_argument("--music-mpv-ytdl-cookies-from-browser", default=os.getenv("MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER", os.getenv("MPV_YTDL_COOKIES_FROM_BROWSER", os.getenv("YTDLP_COOKIES_FROM_BROWSER", ""))), help="Optional yt-dlp browser cookie source for auto-started mpv, e.g. firefox or chrome:Profile 1.")
    music_group.add_argument("--music-mpv-volume", type=int, default=_env_int("MUSIC_MPV_VOLUME", _env_int("MPV_VOLUME", 150)), help="mpv music volume passed to auto-started sidecar.")
    music_group.add_argument("--music-mpv-volume-max", type=int, default=_env_int("MUSIC_MPV_VOLUME_MAX", _env_int("MPV_VOLUME_MAX", 200)), help="mpv --volume-max ceiling passed to auto-started sidecar.")
    music_group.add_argument("--music-mpv-ready-timeout", type=float, default=_env_float("MUSIC_MPV_READY_TIMEOUT", _env_float("MPV_READY_TIMEOUT", 1.5)), help="Seconds the music sidecar waits for mpv playback status.")
    music_group.add_argument(
        "--music-wake-pause-timeout",
        type=float,
        default=_env_float("MUSIC_WAKE_PAUSE_TIMEOUT", 0.25),
        help="Short local HTTP timeout for pausing music immediately after wake detection.",
    )
    music_group.add_argument(
        "--music-wake-beep-settle",
        type=float,
        default=_env_float("MUSIC_WAKE_BEEP_SETTLE", 0.05),
        help="Seconds to let mpv/music pause settle before playing the wake recording beep.",
    )
    music_group.add_argument(
        "--music-wake-dashboard-update",
        action="store_true",
        default=_env_bool("MUSIC_WAKE_DASHBOARD_UPDATE", False),
        help="Also send Music paused dashboard UART after a music wake pause. Disabled by default to keep the beep fast.",
    )
    music_group.add_argument(
        "--no-music-reset-recording-gate-on-wake",
        dest="music_reset_recording_gate_on_wake",
        action="store_false",
        default=_env_bool("MUSIC_RESET_RECORDING_GATE_ON_WAKE", True),
        help="Do not lower speech/silence thresholds after music is paused by wake.",
    )
    music_group.add_argument(
        "--post-music-standby-cooldown",
        type=float,
        default=_env_float("POST_MUSIC_STANDBY_COOLDOWN", 0.8),
        help="After play/resume auto-ends conversation mode, wait briefly before accepting the next wake to avoid music false wakes.",
    )
    music_group.add_argument(
        "--music-wake-guard",
        action="store_true",
        default=_env_bool("MUSIC_WAKE_GUARD", False),
        help="Enable stricter wake-word acceptance while music is playing. Disabled by default to preserve the 5aae453 demo behavior.",
    )
    music_group.add_argument(
        "--music-wake-threshold",
        type=float,
        default=_env_float("MUSIC_WAKE_THRESHOLD", 0.88),
        help="Wake score required while music wake guard is enabled.",
    )
    music_group.add_argument(
        "--music-wake-confirm-chunks",
        type=int,
        default=_env_int("MUSIC_WAKE_CONFIRM_CHUNKS", 1),
        help="Consecutive wake chunks required while music is actively playing.",
    )
    music_group.add_argument(
        "--music-wake-volume-min",
        type=int,
        default=_env_int("MUSIC_WAKE_VOLUME_MIN", 0),
        help="Optional minimum recent wake volume while music is playing. 0 keeps the adaptive wake gate.",
    )
    music_group.add_argument(
        "--music-wake-health-interval",
        type=float,
        default=_env_float("MUSIC_WAKE_HEALTH_INTERVAL", 1.0),
        help="Seconds between local music health checks used by the music wake guard.",
    )
    music_group.add_argument("--no-music-wake-guard", action="store_true", help="Compatibility flag; keeps stricter music wake guard disabled.")
    music_group.add_argument("--music-dry-run", action="store_true", help="Ask music sidecar to detect but not open/play.")
    music_group.add_argument("--music-always-call", action="store_true", help="POST every transcript to the music sidecar, even if local intent detection is false.")
    music_group.add_argument("--music-debug", action="store_true", help="Print music routing details even when no music intent was detected.")
    music_group.add_argument("--no-music-autostart", action="store_true", help="Do not auto-start the local music sidecar when /music is unreachable.")
    music_group.add_argument("--no-music-pause-on-wake", action="store_true", help="Do not pause active music as soon as Hey Jarvis is detected.")
    weather_group = parser.add_argument_group("weather tool routing")
    weather_group.add_argument("--no-weather", action="store_true", help="Disable local weather routing through the Music Web Player sidecar.")
    weather_group.add_argument("--weather-url", default=os.getenv("WEATHER_TOOL_URL", DEFAULT_WEATHER_TOOL_URL), help="Local tool /weather endpoint.")
    weather_group.add_argument("--weather-default-location", default=os.getenv("WEATHER_DEFAULT_LOCATION", DEFAULT_WEATHER_LOCATION), help="Location used for '所在地/這裡/here' weather requests.")
    weather_group.add_argument("--weather-timeout", type=float, default=_env_float("WEATHER_TOOL_TIMEOUT", 6.0), help="HTTP timeout for the local /weather endpoint.")
    weather_group.add_argument("--weather-api-timeout", type=float, default=_env_float("WEATHER_API_TIMEOUT", 4.5), help="Open-Meteo geocoding+forecast timeout budget passed to the local /weather endpoint.")
    weather_group.add_argument("--weather-always-call", action="store_true", help="POST every transcript to /weather, even if local intent detection is false.")
    weather_group.add_argument("--weather-debug", action="store_true", help="Print weather routing details even when no weather intent was detected.")
    weather_group.add_argument("--no-dashboard-uart", action="store_true", help="Do not send dashboard data UART lines such as Todo/Music/Focus/Health.")
    weather_group.add_argument("--dashboard-todo-item-limit", type=int, default=int(os.getenv("DASHBOARD_TODO_ITEM_LIMIT", "8")), help="Maximum open to-do items sent to FRDM dashboard pages.")
    weather_group.add_argument("--no-frdm-uart-bus", action="store_true", help="Disable the single-owner UART bus and use legacy per-command UART writes. FRDM-originated events will not be reliable.")
    weather_group.add_argument("--frdm-uart-reconnect-sec", type=float, default=_env_float("FRDM_UART_RECONNECT_SEC", 1.0), help="Reconnect delay for the single-owner UART bus.")
    weather_group.add_argument(
        "--frdm-uart-tx-timeout",
        type=float,
        default=_env_float("FRDM_UART_TX_TIMEOUT", 0.45),
        help="Max seconds to wait for one single-owner UART bus TX before failing fast. Keeps Speaking/Thinking from stalling if FRDM is wedged.",
    )
    weather_group.add_argument(
        "--frdm-uart-failure-threshold",
        type=int,
        default=_env_int("FRDM_UART_FAILURE_THRESHOLD", 2),
        help="Consecutive UART bus TX failures before temporarily bypassing TX while keeping RX monitoring alive.",
    )
    weather_group.add_argument(
        "--frdm-uart-circuit-breaker-sec",
        type=float,
        default=_env_float("FRDM_UART_CIRCUIT_BREAKER_SEC", 4.0),
        help="Seconds to bypass UART bus TX after repeated failures. Set 0 to disable the bypass window.",
    )
    weather_group.add_argument("--uart-proxy-host", default=os.getenv("UART_PROXY_HOST", "127.0.0.1"), help="Local host for child processes to proxy UART lines through Wake Bridge.")
    weather_group.add_argument("--uart-proxy-port", type=int, default=_env_int("UART_PROXY_PORT", 0), help="Local UART proxy port. 0 chooses a free port.")
    weather_group.add_argument("--no-frdm-todo-events", action="store_true", help="Do not listen for FRDM checkbox events such as TodoDone <id>.")
    weather_group.add_argument("--frdm-event-poll-interval", type=float, default=_env_float("FRDM_EVENT_POLL_INTERVAL", 0.25), help="Seconds between short UART polls for FRDM-originated events.")
    weather_group.add_argument("--no-frdm-fan-events", action="store_true", help="Do not listen for FRDM fan UI events.")
    weather_group.add_argument("--fan-device-id", default=os.getenv("FAN_DEVICE_ID", "desk_fan"), help="Dashboard device id controlled by FRDM fan events.")
    weather_group.add_argument(
        "--fan-speed-max",
        type=int,
        default=_env_int("FAN_SPEED_MAX", 100),
        help="Maximum FRDM fan speed value. Use 100 for the current slider percent, or 3 for legacy fan levels.",
    )
    weather_group.add_argument("--fan-duplicate-suppress-sec", type=float, default=_env_float("FAN_DUPLICATE_SUPPRESS_SEC", 2.0), help="Ignore repeated identical FRDM fan events for this many seconds to avoid slider log/dashboard spam.")
    weather_group.add_argument("--fan-dashboard-url", default=DEFAULT_FAN_DASHBOARD_URL, help="Dashboard set-device URL template. Use {device_id} for the URL-escaped device id.")
    weather_group.add_argument("--no-fan-dashboard-sync", action="store_true", help="Do not POST FRDM fan events to the Jetson dashboard API.")
    weather_group.add_argument("--fan-dashboard-timeout", type=float, default=_env_float("FAN_DASHBOARD_TIMEOUT", 1.5))
    weather_group.add_argument("--fan-control-command", default=os.getenv("FAN_CONTROL_COMMAND", ""), help="Optional hardware command template for fan control. Placeholders: {power}, {state}, {speed}, {percent}, {device_id}.")
    weather_group.add_argument("--fan-command-timeout", type=float, default=_env_float("FAN_COMMAND_TIMEOUT", 2.0))
    esp32_ble_group = parser.add_argument_group("ESP32-S3 BLE fan/LED/temperature")
    esp32_ble_group.add_argument(
        "--esp32-ble",
        action="store_true",
        default=_env_bool("ESP32_BLE", False),
        help="Enable ESP32-S3 BLE control for fan, LED, and DS18B20 status notify.",
    )
    esp32_ble_group.add_argument("--esp32-ble-name", default=os.getenv("ESP32_BLE_NAME", "ESP32S3_FAN_LED_TEMP"), help="ESP32-S3 BLE advertised device name.")
    esp32_ble_group.add_argument("--esp32-ble-address", default=os.getenv("ESP32_BLE_ADDRESS", ""), help="Optional BLE address/MAC; skips scanning by name.")
    esp32_ble_group.add_argument("--esp32-ble-adapter", default=os.getenv("ESP32_BLE_ADAPTER", os.getenv("BLE_ADAPTER", "")), help="Optional BlueZ adapter, e.g. hci0.")
    esp32_ble_group.add_argument("--esp32-ble-scan-mode", choices=["active", "passive"], default=os.getenv("ESP32_BLE_SCAN_MODE", "active"))
    esp32_ble_group.add_argument("--esp32-ble-scan-duplicates", action="store_true", default=_env_bool("ESP32_BLE_SCAN_DUPLICATES", False), help="Ask BlueZ to report duplicate advertisement data during scans.")
    esp32_ble_group.add_argument("--esp32-ble-scan-filter-service", action="store_true", default=_env_bool("ESP32_BLE_SCAN_FILTER_SERVICE", False), help="Ask BlueZ to filter scan results by the ESP32 service UUID.")
    esp32_ble_group.add_argument("--esp32-ble-scan-timeout", type=float, default=_env_float("ESP32_BLE_SCAN_TIMEOUT", 8.0))
    esp32_ble_group.add_argument("--esp32-ble-connect-timeout", type=float, default=_env_float("ESP32_BLE_CONNECT_TIMEOUT", 12.0))
    esp32_ble_group.add_argument("--esp32-ble-reconnect-sec", type=float, default=_env_float("ESP32_BLE_RECONNECT_SEC", 3.0))
    esp32_ble_group.add_argument("--esp32-ble-command-queue-max", type=int, default=_env_int("ESP32_BLE_COMMAND_QUEUE_MAX", 64), help="Maximum ESP32 BLE commands to keep queued while disconnected.")
    esp32_ble_group.add_argument("--esp32-ble-write-with-response", action="store_true", help="Force BLE writes to use write-with-response.")
    esp32_ble_group.add_argument(
        "--esp32-ble-write-response-auto",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("ESP32_BLE_WRITE_RESPONSE_AUTO", True),
        help="Let bleak choose BLE write response mode from characteristic properties.",
    )
    esp32_ble_group.add_argument(
        "--esp32-ble-read-status-on-connect",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("ESP32_BLE_READ_STATUS_ON_CONNECT", True),
        help="Read the status characteristic once immediately after connecting.",
    )
    esp32_ble_group.add_argument("--no-esp32-ble-voice-control", action="store_true", help="Do not intercept voice transcripts such as 開風扇 / LED off.")
    esp32_ble_group.add_argument("--no-esp32-ble-frdm-control", action="store_true", help="Do not relay FRDM Fan/FanSpeed UART events to ESP32 BLE.")
    esp32_ble_group.add_argument("--esp32-ble-min-fan-pwm", type=int, default=_env_int("FAN_MIN_PWM", 96), help="Minimum nonzero PWM sent to ESP32 for FRDM percent fan speeds.")
    esp32_ble_group.add_argument("--esp32-ble-voice-speed-step", type=int, default=_env_int("ESP32_BLE_VOICE_SPEED_STEP", 32), help="PWM step used for voice faster/slower commands.")
    esp32_ble_group.add_argument("--esp32-ble-passive-threshold", type=float, default=_env_float("ESP32_BLE_PASSIVE_THRESHOLD_C", 25.0), help="Temperature above which the bridge reminds the user about the fan.")
    esp32_ble_group.add_argument("--esp32-ble-passive-cooldown-sec", type=float, default=_env_float("ESP32_BLE_PASSIVE_COOLDOWN_SEC", 120.0))
    esp32_ble_group.add_argument("--no-esp32-ble-passive-reminder", action="store_true", help="Do not remind when ESP32 BLE temperature is above threshold.")
    esp32_ble_group.add_argument(
        "--esp32-ble-passive-message",
        default=os.getenv("ESP32_BLE_PASSIVE_MESSAGE", "現在溫度 {temp:.1f} 度，有點熱，要不要幫你開風扇？"),
        help="Passive reminder text. Supports {temp} and {threshold}.",
    )
    esp32_ble_group.add_argument("--no-esp32-ble-tts-reminder", action="store_true", help="Print passive reminders without calling Piper TTS.")
    esp32_ble_group.add_argument("--esp32-ble-tts-timeout", type=float, default=_env_float("ESP32_BLE_TTS_TIMEOUT", 1.5))
    esp32_ble_group.add_argument("--esp32-dashboard-host", default=DEFAULT_ESP32_DASHBOARD_HOST, help="Local host for dashboard -> ESP32 BLE HTTP control.")
    esp32_ble_group.add_argument("--esp32-dashboard-port", type=int, default=_env_int("ESP32_DASHBOARD_PORT", DEFAULT_ESP32_DASHBOARD_PORT), help="Local port for dashboard -> ESP32 BLE HTTP control.")
    esp32_ble_group.add_argument("--no-esp32-dashboard-control", action="store_true", help="Do not expose the local dashboard ESP32 BLE control API.")
    esp32_ble_group.add_argument("--esp32-dashboard-debug", action="store_true", help="Log dashboard ESP32 HTTP control requests.")
    esp32_ble_group.add_argument("--esp32-ble-sidecar", action=argparse.BooleanOptionalAction, default=_env_bool("ESP32_BLE_SIDECAR", False), help="Use the standalone ESP32 BLE HTTP sidecar instead of running BLE reconnect inside this Wake Bridge process.")
    esp32_ble_group.add_argument("--esp32-ble-api-url", default=os.getenv("ESP32_BLE_API_URL", f"http://{DEFAULT_ESP32_DASHBOARD_HOST}:{DEFAULT_ESP32_DASHBOARD_PORT}/api/esp32"), help="Base URL for the ESP32 BLE sidecar API.")
    esp32_ble_group.add_argument("--esp32-ble-api-timeout", type=float, default=_env_float("ESP32_BLE_API_TIMEOUT", 0.2), help="Short timeout for Wake Bridge -> ESP32 BLE sidecar API calls.")
    esp32_ble_group.add_argument("--esp32-ble-api-status-cache-sec", type=float, default=_env_float("ESP32_BLE_API_STATUS_CACHE_SEC", 0.5), help="Maximum cached ESP32 sidecar status age for local voice parsing.")
    esp32_ble_group.add_argument("--temp-room-uart-interval-sec", type=float, default=_env_float("TEMP_ROOM_UART_INTERVAL_SEC", 10.0), help="Send latest ESP32 room temperature to FRDM as TempRoom <Celsius*10> at this interval. Use 0 to disable.")
    esp32_ble_group.add_argument("--temp-room-uart-max-age-sec", type=float, default=_env_float("TEMP_ROOM_UART_MAX_AGE_SEC", 30.0), help="Maximum age of an ESP32 temperature reading before skipping TempRoom UART.")
    esp32_ble_group.add_argument("--no-temp-room-uart", action="store_true", help="Do not send periodic TempRoom UART updates to FRDM.")
    weather_group.add_argument("--no-startup-time", action="store_true", help="Do not send Time UART once at bridge startup.")
    weather_group.add_argument("--no-startup-weather", action="store_true", help="Do not fetch and send startup Weather daily/current UART payloads.")
    weather_group.add_argument(
        "--startup-weather-text",
        default=os.getenv("STARTUP_WEATHER_TEXT", "今天天氣如何"),
        help="Whole-day weather text routed through the existing weather tool at startup before sending Normal.",
    )
    weather_group.add_argument(
        "--startup-weather-current-text",
        default=os.getenv("STARTUP_WEATHER_CURRENT_TEXT", "現在天氣如何"),
        help="Current-weather text routed through the existing weather tool at startup before sending Normal.",
    )
    weather_group.add_argument(
        "--esp32-temperature-mode",
        choices=["disabled", "push", "pull", "both"],
        default=os.getenv("ESP32_TEMPERATURE_MODE", "disabled"),
        help="Local DS18B20 temperature source: ESP32 POSTs to Jetson, Jetson pulls ESP32 HTTP API, both, or disabled.",
    )
    weather_group.add_argument("--esp32-temperature-host", default=os.getenv("ESP32_TEMPERATURE_HOST", "0.0.0.0"), help="Host/IP for Jetson's ESP32 temperature receiver.")
    weather_group.add_argument("--esp32-temperature-port", type=int, default=_env_int("ESP32_TEMPERATURE_PORT", 8790), help="Port for Jetson's ESP32 temperature receiver.")
    weather_group.add_argument("--esp32-temperature-path", default=os.getenv("ESP32_TEMPERATURE_PATH", DEFAULT_ESP32_TEMPERATURE_PATH), help="HTTP path for ESP32 temperature POST/GET.")
    weather_group.add_argument("--esp32-temperature-url", default=os.getenv("ESP32_TEMPERATURE_URL", ""), help="ESP32 temperature JSON URL used in pull/both mode, for example http://192.168.1.50/temperature.")
    weather_group.add_argument("--esp32-temperature-timeout", type=float, default=_env_float("ESP32_TEMPERATURE_TIMEOUT", 0.6), help="HTTP timeout when pulling ESP32 temperature.")
    weather_group.add_argument("--esp32-temperature-max-age-sec", type=float, default=_env_float("ESP32_TEMPERATURE_MAX_AGE_SEC", 120.0), help="Maximum age for a pushed ESP32 temperature reading before it is ignored.")
    weather_group.add_argument("--esp32-temperature-debug", action="store_true", help="Print ESP32 temperature receiver/fetch debug logs.")
    weather_group.add_argument("--no-weather-local-temperature", action="store_true", help="Do not append ESP32 local temperature to Weather UART payloads.")
    motor_group = parser.add_argument_group("head motor motion")
    motor_group.add_argument(
        "--enable-head-motor",
        action="store_true",
        help="Actually send MotorYawPitch/MotorPitch/MotorYaw. Leave off until FRDM ACK/parser reports real angles.",
    )
    motor_group.add_argument(
        "--disable-head-motor",
        action="store_true",
        help="Force MotorPitch/MotorYaw off even if --enable-head-motor is also present. Useful while FRDM parser is unsafe.",
    )
    motor_group.add_argument(
        "--motor-step-delay",
        type=float,
        default=_env_float("MOTOR_STEP_DELAY_SEC", MOTOR_STEP_DELAY_SEC),
        help="Base seconds to hold each expanded head-motor step. Actual delays vary slightly for more natural motion.",
    )
    motor_group.add_argument(
        "--motor-smooth-step-deg",
        type=int,
        default=_env_int("MOTOR_SMOOTH_STEP_DEG", MOTOR_SMOOTH_STEP_DEG),
        help="Maximum degrees per interpolated motor UART step. Smaller values create smoother, longer motions.",
    )
    motor_group.add_argument(
        "--motor-speaking-step-delay",
        type=float,
        default=_env_float("MOTOR_SPEAKING_STEP_DELAY_SEC", MOTOR_SPEAKING_STEP_DELAY_SEC),
        help="Base seconds to hold each head-motion step while TTS is speaking. Actual delays vary slightly.",
    )
    motor_group.add_argument(
        "--motor-speaking-smooth-step-deg",
        type=int,
        default=_env_int("MOTOR_SPEAKING_SMOOTH_STEP_DEG", MOTOR_SPEAKING_SMOOTH_STEP_DEG),
        help="Maximum degrees per interpolated motor step during TTS speaking loops. Larger values mean clearer target-to-target moves.",
    )
    motor_group.add_argument(
        "--motor-stop-timeout",
        type=float,
        default=_env_float("MOTOR_STOP_TIMEOUT_SEC", MOTOR_STOP_TIMEOUT_SEC),
        help="Maximum seconds to wait for the speaking head-motion loop to stop and reset after TTS finishes.",
    )
    motor_group.add_argument(
        "--motor-reset-repeats",
        type=int,
        default=_env_int("MOTOR_RESET_REPEATS", MOTOR_RESET_REPEATS),
        help="How many times to resend MotorPitch/MotorYaw center angles at the end of each motion.",
    )
    motor_group.add_argument(
        "--motor-reset-delay",
        type=float,
        default=_env_float("MOTOR_RESET_DELAY_SEC", MOTOR_RESET_DELAY_SEC),
        help="Seconds between repeated center-angle reset motor commands.",
    )
    motor_group.add_argument(
        "--motor-read-ms",
        type=int,
        default=_env_int("MOTOR_READ_MS", MOTOR_READ_MS),
        help="UART read window after each motor command; keep modest so head motion does not delay TTS.",
    )
    motor_group.add_argument(
        "--motor-join-timeout",
        type=float,
        default=_env_float("MOTOR_JOIN_TIMEOUT_SEC", MOTOR_JOIN_TIMEOUT_SEC),
        help="Maximum seconds to wait after TTS for the head motion thread to finish resetting before changing the next FRDM screen.",
    )
    motor_group.add_argument(
        "--test-head-motion",
        choices=sorted(VALID_HEAD_MOTIONS | {"all"}),
        default="",
        help="Run a direct FRDM head-motion test and exit. Does not open mic/camera/TTS/Windows server.",
    )
    motor_group.add_argument(
        "--test-head-emotion",
        choices=sorted(VALID_EMOTIONS | {"all"}),
        default="",
        help="Run the emotion-to-head-motion mapping test and exit. Does not open mic/camera/TTS/Windows server.",
    )
    motor_group.add_argument(
        "--test-speaking-head-motion",
        choices=sorted(VALID_HEAD_MOTIONS - {"none"}),
        default="",
        help="Loop one head motion as if TTS is speaking, then stop and reset. Does not open mic/camera/TTS/Windows server.",
    )
    motor_group.add_argument(
        "--test-speaking-seconds",
        type=float,
        default=6.0,
        help="Duration for --test-speaking-head-motion.",
    )
    motor_group.add_argument(
        "--test-head-repeat",
        type=int,
        default=1,
        help="Repeat count for --test-head-motion.",
    )
    motor_group.add_argument(
        "--test-head-gap",
        type=float,
        default=0.7,
        help="Seconds to wait between motions in --test-head-motion all.",
    )
    tts_timing_group = parser.add_argument_group("tts timing")
    tts_timing_group.add_argument(
        "--tts-playback-timeout",
        type=float,
        default=_env_float("TTS_PLAYBACK_TIMEOUT", 45.0),
        help="Maximum seconds to wait for /speak_async queue completion before changing the next FRDM screen.",
    )
    tts_timing_group.add_argument(
        "--tts-poll-interval",
        type=float,
        default=_env_float("TTS_POLL_INTERVAL", 0.75),
        help="Seconds between /queue polls while waiting for /speak_async. Increase to reduce TTS terminal log spam.",
    )
    tts_timing_group.add_argument(
        "--tts-start-poll-interval",
        type=float,
        default=_env_float("TTS_START_POLL_INTERVAL", 0.12),
        help="Fast /queue+/health poll interval until TTS audio playback is observed and Speaking mode can start.",
    )
    tts_timing_group.add_argument(
        "--tts-speaking-start-timeout",
        type=float,
        default=_env_float("TTS_SPEAKING_START_TIMEOUT", 1.2),
        help="Fallback seconds after a TTS job becomes current before entering Speaking when --no-tts-speaking-require-audio is used.",
    )
    tts_timing_group.add_argument(
        "--tts-speaking-require-audio",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("TTS_SPEAKING_REQUIRE_AUDIO", True),
        help="Only send FRDM Speaking after TTS /health reports audio.playing=true. This keeps Speaking aligned with real speaker output.",
    )
    return parser


def apply_conversation_latency_preset(args: argparse.Namespace) -> None:
    if getattr(args, "ultra_response", False):
        args.silence_duration = 0.38
        args.min_speech_seconds = 0.25
        args.max_speech_seconds = 4.0
        args.max_recording_seconds = 5.2
        args.wake_listen_timeout = min(float(args.wake_listen_timeout), 3.5)
        args.turn_listen_timeout = 3.0
        args.session_idle_timeout = 10.0
        args.audio_read_timeout = 0.2
        args.recording_progress_interval = 1.5
        args.tts_poll_interval = 0.1
        args.tts_start_poll_interval = min(float(args.tts_start_poll_interval), 0.08)
        args.tts_speaking_start_timeout = min(float(args.tts_speaking_start_timeout), 0.8)
        args.tts_playback_timeout = min(float(args.tts_playback_timeout), 18.0)
        args.music_wake_pause_timeout = min(float(args.music_wake_pause_timeout), 0.15)
        args.camera_result_timeout = min(float(args.camera_result_timeout), 0.45 if args.force_vision else 0.18)
        args.motor_step_delay = max(float(args.motor_step_delay), 0.18)
        args.motor_smooth_step_deg = max(int(getattr(args, "motor_smooth_step_deg", MOTOR_SMOOTH_STEP_DEG) or MOTOR_SMOOTH_STEP_DEG), 18)
        args.motor_speaking_step_delay = max(float(args.motor_speaking_step_delay), 0.22)
        args.motor_speaking_smooth_step_deg = max(
            int(getattr(args, "motor_speaking_smooth_step_deg", MOTOR_SPEAKING_SMOOTH_STEP_DEG) or MOTOR_SPEAKING_SMOOTH_STEP_DEG),
            20,
        )
        args.motor_reset_repeats = min(int(args.motor_reset_repeats), 2)
        args.motor_reset_delay = max(float(args.motor_reset_delay), 0.14)
        args.motor_join_timeout = min(float(args.motor_join_timeout), 2.0)
        args.motor_read_ms = min(int(args.motor_read_ms), 15)
        args.fast_reply = True
        if int(getattr(args, "fast_reply_num_predict", 0) or 0) <= 0:
            args.fast_reply_num_predict = 70
        if getattr(args, "tts_length_scale", None) is None:
            args.tts_length_scale = 0.74
        print(
            "Ultra response preset enabled: "
            "silence=0.38s, max_speech=4s, max_recording=5.2s, "
            "turn_timeout=3s, camera_wait<=0.18s, tts_poll=0.1s, "
            "motor_motion=larger/held, tts_length_scale=0.74, reply_num_predict=70."
        )
        return

    if not getattr(args, "turbo_response", False):
        return
    args.silence_duration = 0.55
    args.max_speech_seconds = 5.0
    args.max_recording_seconds = 7.0
    args.turn_listen_timeout = 4.0
    args.session_idle_timeout = 18.0
    args.audio_read_timeout = 0.35
    args.recording_progress_interval = 0.75
    args.tts_poll_interval = 0.2
    args.tts_start_poll_interval = min(float(args.tts_start_poll_interval), 0.1)
    args.tts_speaking_start_timeout = min(float(args.tts_speaking_start_timeout), 1.0)
    args.tts_playback_timeout = min(float(args.tts_playback_timeout), 25.0)
    args.music_wake_pause_timeout = min(float(args.music_wake_pause_timeout), 0.25)
    args.camera_result_timeout = min(float(args.camera_result_timeout), 0.7 if args.force_vision else 0.35)
    args.motor_step_delay = max(float(args.motor_step_delay), 0.22)
    args.motor_smooth_step_deg = max(int(getattr(args, "motor_smooth_step_deg", MOTOR_SMOOTH_STEP_DEG) or MOTOR_SMOOTH_STEP_DEG), 16)
    args.motor_speaking_step_delay = max(float(args.motor_speaking_step_delay), 0.26)
    args.motor_speaking_smooth_step_deg = max(
        int(getattr(args, "motor_speaking_smooth_step_deg", MOTOR_SPEAKING_SMOOTH_STEP_DEG) or MOTOR_SPEAKING_SMOOTH_STEP_DEG),
        18,
    )
    args.motor_reset_repeats = min(int(args.motor_reset_repeats), 3)
    args.motor_reset_delay = max(float(args.motor_reset_delay), 0.16)
    args.fast_reply = True
    if int(getattr(args, "fast_reply_num_predict", 0) or 0) <= 0:
        args.fast_reply_num_predict = 110
    if getattr(args, "tts_length_scale", None) is None:
        args.tts_length_scale = 0.86
    print(
        "Turbo response preset enabled: "
        "silence=0.55s, max_speech=5s, max_recording=7s, "
        "turn_timeout=4s, camera_wait<=0.35s, tts_poll=0.2s, "
        "motor_motion=larger/held, tts_length_scale=0.86, reply_num_predict=110."
    )


def apply_noisy_room_preset(args: argparse.Namespace) -> None:
    if not getattr(args, "noisy_room", False):
        return

    noisy_defaults = {
        "wake_threshold": 0.75,
        "wake_volume_min": 500,
        "wake_volume_ratio": 1.35,
        "wake_volume_margin": 1200,
        "wake_volume_window_seconds": 1.0,
        "standby_progress_interval": 1.5,
        "volume_min": 1100,
        "speech_start_margin": 750,
        "speech_start_ratio": 1.45,
        "silence_margin": 900,
        "silence_noise_ratio": 1.30,
        "beep_volume": 0.70,
        "beep_duration_ms": 220,
        "beep_frequency": 1500.0,
    }
    for attr, noisy_value in noisy_defaults.items():
        if getattr(args, f"_manual_{attr}", False):
            continue
        current_value = getattr(args, attr, 0)
        if isinstance(noisy_value, int):
            setattr(args, attr, max(int(current_value or 0), noisy_value))
        else:
            setattr(args, attr, max(float(current_value or 0.0), float(noisy_value)))
    print(
        "Noisy-room preset enabled: "
        f"wake_threshold={args.wake_threshold:g}, wake_volume_min={args.wake_volume_min}, "
        f"wake_ratio={args.wake_volume_ratio:g}, volume_min={args.volume_min}, "
        f"speech_margin={args.speech_start_margin}, speech_ratio={args.speech_start_ratio:g}, "
        f"silence_margin={args.silence_margin}, silence_noise_ratio={args.silence_noise_ratio:g}, "
        f"beep={args.beep_frequency:g}Hz/"
        f"{args.beep_duration_ms}ms/vol={args.beep_volume:g}."
    )


def validate_runtime_args(args: argparse.Namespace) -> bool:
    if getattr(args, "conversation_mode", False) and getattr(args, "no_wake_word", False):
        print("ERROR: --conversation-mode requires wake word standby. Remove --no-wake-word.")
        return False
    if getattr(args, "conversation_mode", False):
        if float(getattr(args, "turn_listen_timeout", 0.0) or 0.0) <= 0:
            print("ERROR: --turn-listen-timeout must be > 0 in --conversation-mode.")
            return False
        if int(getattr(args, "max_session_turns", 0) or 0) <= 0:
            print("ERROR: --max-session-turns must be > 0 in --conversation-mode.")
            return False
    if float(getattr(args, "pre_record_image_delay", 0.0) or 0.0) < 0.0:
        print("ERROR: --pre-record-image-delay must be >= 0.")
        return False
    if float(getattr(args, "speech_end_image_delay", 0.0) or 0.0) < 0.0:
        print("ERROR: --speech-end-image-delay must be >= 0.")
        return False
    if float(getattr(args, "beep_retry_delay", 0.0) or 0.0) < 0.0:
        print("ERROR: --beep-retry-delay must be >= 0.")
        return False
    if float(getattr(args, "beep_device_lookup_timeout", 0.0) or 0.0) < 0.0:
        print("ERROR: --beep-device-lookup-timeout must be >= 0.")
        return False
    if float(getattr(args, "tts_start_poll_interval", 0.0) or 0.0) <= 0.0:
        print("ERROR: --tts-start-poll-interval must be > 0.")
        return False
    if float(getattr(args, "tts_speaking_start_timeout", 0.0) or 0.0) <= 0.0:
        print("ERROR: --tts-speaking-start-timeout must be > 0.")
        return False
    if float(getattr(args, "music_wake_pause_timeout", 0.0) or 0.0) < 0.0:
        print("ERROR: --music-wake-pause-timeout must be >= 0.")
        return False
    if float(getattr(args, "music_wake_beep_settle", 0.0) or 0.0) < 0.0:
        print("ERROR: --music-wake-beep-settle must be >= 0.")
        return False
    if float(getattr(args, "post_music_standby_cooldown", 0.0) or 0.0) < 0.0:
        print("ERROR: --post-music-standby-cooldown must be >= 0.")
        return False
    if not (0.0 < float(getattr(args, "music_wake_threshold", 0.0) or 0.0) <= 1.0):
        print("ERROR: --music-wake-threshold must be > 0 and <= 1.")
        return False
    if int(getattr(args, "music_wake_confirm_chunks", 0) or 0) <= 0:
        print("ERROR: --music-wake-confirm-chunks must be > 0.")
        return False
    if int(getattr(args, "music_wake_volume_min", 0) or 0) < 0:
        print("ERROR: --music-wake-volume-min must be >= 0.")
        return False
    if float(getattr(args, "music_wake_health_interval", 0.0) or 0.0) <= 0.0:
        print("ERROR: --music-wake-health-interval must be > 0.")
        return False
    if int(getattr(args, "music_mpv_volume", 100) or 0) < 0:
        print("ERROR: --music-mpv-volume must be >= 0.")
        return False
    if int(getattr(args, "music_mpv_volume_max", 200) or 0) < 100:
        print("ERROR: --music-mpv-volume-max must be >= 100.")
        return False
    if float(getattr(args, "music_mpv_ready_timeout", 0.0) or 0.0) < 0.0:
        print("ERROR: --music-mpv-ready-timeout must be >= 0.")
        return False
    for attr, flag in (
        ("wake_volume_ratio", "--wake-volume-ratio"),
        ("speech_start_ratio", "--speech-start-ratio"),
        ("silence_noise_ratio", "--silence-noise-ratio"),
        ("silence_peak_ratio", "--silence-peak-ratio"),
    ):
        if float(getattr(args, attr, 0.0) or 0.0) <= 0.0:
            print(f"ERROR: {flag} must be > 0.")
            return False
    if int(getattr(args, "wake_volume_margin", 0) or 0) < 0:
        print("ERROR: --wake-volume-margin must be >= 0.")
        return False
    if float(getattr(args, "wake_volume_window_seconds", 0.0) or 0.0) <= 0.0:
        print("ERROR: --wake-volume-window-seconds must be > 0.")
        return False
    if float(getattr(args, "standby_progress_interval", 0.0) or 0.0) < 0.0:
        print("ERROR: --standby-progress-interval must be >= 0.")
        return False
    if not getattr(args, "no_focus_mode", False):
        if float(getattr(args, "focus_interval_sec", 0.0) or 0.0) <= 0.0:
            print("ERROR: --focus-interval-sec must be > 0.")
            return False
        if float(getattr(args, "focus_first_sample_delay_sec", 0.0) or 0.0) < -1.0:
            print("ERROR: --focus-first-sample-delay-sec must be >= -1.")
            return False
        if int(getattr(args, "focus_alert_threshold", 0) or 0) < 1:
            print("ERROR: --focus-alert-threshold must be >= 1.")
            return False
        if float(getattr(args, "focus_alert_cooldown_sec", 0.0) or 0.0) < 0.0:
            print("ERROR: --focus-alert-cooldown-sec must be >= 0.")
            return False
    if float(getattr(args, "frdm_uart_reconnect_sec", 0.0) or 0.0) <= 0.0:
        print("ERROR: --frdm-uart-reconnect-sec must be > 0.")
        return False
    if float(getattr(args, "frdm_uart_tx_timeout", 0.0) or 0.0) <= 0.0:
        print("ERROR: --frdm-uart-tx-timeout must be > 0.")
        return False
    if int(getattr(args, "frdm_uart_failure_threshold", 0) or 0) <= 0:
        print("ERROR: --frdm-uart-failure-threshold must be > 0.")
        return False
    if float(getattr(args, "frdm_uart_circuit_breaker_sec", 0.0) or 0.0) < 0.0:
        print("ERROR: --frdm-uart-circuit-breaker-sec must be >= 0.")
        return False
    if int(getattr(args, "uart_proxy_port", 0) or 0) < 0:
        print("ERROR: --uart-proxy-port must be >= 0.")
        return False
    if int(getattr(args, "fan_speed_max", 0) or 0) <= 0:
        print("ERROR: --fan-speed-max must be > 0.")
        return False
    if float(getattr(args, "fan_duplicate_suppress_sec", 0.0) or 0.0) < 0.0:
        print("ERROR: --fan-duplicate-suppress-sec must be >= 0.")
        return False
    if float(getattr(args, "fan_dashboard_timeout", 0.0) or 0.0) <= 0.0:
        print("ERROR: --fan-dashboard-timeout must be > 0.")
        return False
    if float(getattr(args, "fan_command_timeout", 0.0) or 0.0) <= 0.0:
        print("ERROR: --fan-command-timeout must be > 0.")
        return False
    if bool(getattr(args, "esp32_ble", False)):
        ble_unavailable_reason = ""
        if esp32_ble is None:
            ble_unavailable_reason = f"helper import failed: {ESP32_BLE_IMPORT_ERROR}"
        else:
            try:
                __import__("bleak")
            except Exception as exc:
                ble_unavailable_reason = f"bleak is unavailable: {exc}"
        if ble_unavailable_reason:
            setattr(args, "_esp32_ble_runtime_unavailable", True)
            setattr(args, "_esp32_ble_runtime_unavailable_reason", ble_unavailable_reason)
            print(
                "WARNING: --esp32-ble requested but BLE will run in degraded mode "
                f"({ble_unavailable_reason}). Wake, TTS, music, weather, and FRDM UART will continue."
            )
        else:
            setattr(args, "_esp32_ble_runtime_unavailable", False)
            setattr(args, "_esp32_ble_runtime_unavailable_reason", "")
        if float(getattr(args, "esp32_ble_scan_timeout", 0.0) or 0.0) <= 0.0:
            print("ERROR: --esp32-ble-scan-timeout must be > 0.")
            return False
        if float(getattr(args, "esp32_ble_connect_timeout", 0.0) or 0.0) <= 0.0:
            print("ERROR: --esp32-ble-connect-timeout must be > 0.")
            return False
        if float(getattr(args, "esp32_ble_reconnect_sec", 0.0) or 0.0) <= 0.0:
            print("ERROR: --esp32-ble-reconnect-sec must be > 0.")
            return False
        if int(getattr(args, "esp32_ble_command_queue_max", 0) or 0) <= 0:
            print("ERROR: --esp32-ble-command-queue-max must be > 0.")
            return False
        if not 0 <= int(getattr(args, "esp32_ble_min_fan_pwm", 0) or 0) <= 255:
            print("ERROR: --esp32-ble-min-fan-pwm must be 0..255.")
            return False
        os.environ["FAN_MIN_PWM"] = str(int(getattr(args, "esp32_ble_min_fan_pwm", 96) or 96))
        if int(getattr(args, "esp32_ble_voice_speed_step", 0) or 0) <= 0:
            print("ERROR: --esp32-ble-voice-speed-step must be > 0.")
            return False
        if float(getattr(args, "esp32_ble_passive_cooldown_sec", 0.0) or 0.0) < 0.0:
            print("ERROR: --esp32-ble-passive-cooldown-sec must be >= 0.")
            return False
        if float(getattr(args, "esp32_ble_tts_timeout", 0.0) or 0.0) <= 0.0:
            print("ERROR: --esp32-ble-tts-timeout must be > 0.")
            return False
        if bool(getattr(args, "esp32_ble_sidecar", False)):
            if not str(getattr(args, "esp32_ble_api_url", "") or "").strip():
                print("ERROR: --esp32-ble-api-url is required when --esp32-ble-sidecar is enabled.")
                return False
            if float(getattr(args, "esp32_ble_api_timeout", 0.0) or 0.0) <= 0.0:
                print("ERROR: --esp32-ble-api-timeout must be > 0.")
                return False
            if float(getattr(args, "esp32_ble_api_status_cache_sec", 0.0) or 0.0) < 0.0:
                print("ERROR: --esp32-ble-api-status-cache-sec must be >= 0.")
                return False
    if float(getattr(args, "temp_room_uart_interval_sec", 0.0) or 0.0) < 0.0:
        print("ERROR: --temp-room-uart-interval-sec must be >= 0.")
        return False
    if float(getattr(args, "temp_room_uart_max_age_sec", 0.0) or 0.0) < 0.0:
        print("ERROR: --temp-room-uart-max-age-sec must be >= 0.")
        return False
    if not getattr(args, "no_pet_idle_reflection", False):
        if float(getattr(args, "pet_idle_interval_sec", 0.0) or 0.0) <= 0.0:
            print("ERROR: --pet-idle-interval-sec must be > 0.")
            return False
        if float(getattr(args, "pet_idle_jitter_sec", 0.0) or 0.0) < 0.0:
            print("ERROR: --pet-idle-jitter-sec must be >= 0.")
            return False
        if float(getattr(args, "pet_idle_min_silent_sec", 0.0) or 0.0) < 0.0:
            print("ERROR: --pet-idle-min-silent-sec must be >= 0.")
            return False
        if float(getattr(args, "pet_idle_share_cooldown_sec", 0.0) or 0.0) < 0.0:
            print("ERROR: --pet-idle-share-cooldown-sec must be >= 0.")
            return False
        if float(getattr(args, "pet_idle_timeout", 0.0) or 0.0) <= 0.0:
            print("ERROR: --pet-idle-timeout must be > 0.")
            return False
    return True


def build_arg_parser() -> argparse.ArgumentParser:
    return add_wake_args(bridge.build_arg_parser())


def cli_option_present(flag: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in sys.argv[1:])


def main() -> int:
    args = build_arg_parser().parse_args()
    manual_env_map = {
        "wake_threshold": ("--wake-threshold", "WAKE_THRESHOLD"),
        "wake_volume_min": ("--wake-volume-min", "WAKE_VOLUME_MIN"),
        "wake_volume_ratio": ("--wake-volume-ratio", "WAKE_VOLUME_RATIO"),
        "wake_volume_margin": ("--wake-volume-margin", "WAKE_VOLUME_MARGIN"),
        "wake_volume_window_seconds": ("--wake-volume-window-seconds", "WAKE_VOLUME_WINDOW_SECONDS"),
        "standby_progress_interval": ("--standby-progress-interval", "STANDBY_PROGRESS_INTERVAL"),
        "volume_min": ("--volume-min", "VOLUME_MIN"),
        "speech_start_margin": ("--speech-start-margin", "SPEECH_START_MARGIN"),
        "speech_start_ratio": ("--speech-start-ratio", "SPEECH_START_RATIO"),
        "silence_margin": ("--silence-margin", "SILENCE_MARGIN"),
        "silence_noise_ratio": ("--silence-noise-ratio", "SILENCE_NOISE_RATIO"),
        "beep_volume": ("--beep-volume", "BEEP_VOLUME"),
        "beep_duration_ms": ("--beep-duration-ms", "BEEP_DURATION_MS"),
        "beep_frequency": ("--beep-frequency", "BEEP_FREQUENCY"),
    }
    for attr, (flag, env_name) in manual_env_map.items():
        setattr(args, f"_manual_{attr}", cli_option_present(flag) or env_name in os.environ)
    args._manual_input_device = args.device is not None
    args._manual_beep_device = args.beep_device is not None
    args.server_url = voice_chat.normalize_server_url(args.server_url)
    args.tts_url = voice_chat.normalize_tts_url(args.tts_url, blocking=args.tts_blocking)
    args.music_url = normalize_music_url(args.music_url)
    args.weather_url = normalize_weather_url(args.weather_url)
    bridge.apply_default_tts_voice(args)
    if getattr(args, "tts_volume_gain", None) is None:
        args.tts_volume_gain = _env_float("TTS_VOLUME_GAIN", 2.25)
    args.tts_interrupt = not args.tts_no_interrupt
    args.tts_stream = False if args.tts_file_playback else None

    if args.list_uarts:
        bridge.print_uart_ports()
        return 0
    if args.list_mics:
        voice_chat.list_microphones()
        list_sounddevice_inputs()
        list_sounddevice_outputs()
        return 0
    if args.self_test:
        return run_self_test()
    if args.test_beep:
        apply_noisy_room_preset(args)
        if str(getattr(args, "beep_player", "auto") or "auto").strip().lower() == "sounddevice":
            args.beep_device = select_beep_output_device(args)
        ok = play_recording_cue(args, label="Test")
        return 0 if ok else 1
    if args.test_head_motion or args.test_head_emotion or args.test_speaking_head_motion:
        return run_head_motion_test(args)
    if args.device_preflight_only:
        return 0 if device_preflight(args) else 1
    if args.check_server:
        return bridge.run_check_server(args)
    if args.text:
        return run_wake_text_mode(args)
    apply_conversation_latency_preset(args)
    apply_noisy_room_preset(args)
    if not validate_runtime_args(args):
        return 2
    return run_wake_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
