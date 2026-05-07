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
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import fcntl
import glob
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from typing import Any

import numpy as np


os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
VOICE_DIR = PROJECT_ROOT / "emotion_robot_controller" / "voice_stt_remote"
VISION_DIR = PROJECT_ROOT / "vision"
MUSIC_DIR = PROJECT_ROOT / "music_web_player"

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


CLIENT_VERSION = "wake_voice_chat_frdm_bridge_vision_v2"
DEFAULT_INSTANCE_LOCK = "/tmp/wake_voice_chat_frdm_bridge.lock"
DEFAULT_MUSIC_TOOL_URL = os.getenv("MUSIC_TOOL_URL", "http://127.0.0.1:8788/music")
DEFAULT_WEATHER_TOOL_URL = os.getenv("WEATHER_TOOL_URL", "http://127.0.0.1:8788/weather")
DEFAULT_WEATHER_LOCATION = os.getenv("WEATHER_DEFAULT_LOCATION", "Taipei")
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
DEVICE_OWNER_ALLOW_PATTERNS = (
    "jetson_piper_tts.server",
    "pulseaudio",
    "pipewire",
    "wireplumber",
)
DEMO_PREFLIGHT_SKIP_ARGS = (
    "--self-test",
    "--device-preflight-only",
    "--help",
    "--list-mics",
    "--list-uarts",
)

CORE_SCREEN_COMMANDS = {"Sleep", "Normal", "Thinking", "Speaking"}
MOTOR_COMMANDS = {"MotorPitch", "MotorYaw"}
UTILITY_COMMANDS = {"ShowNum"}
FUTURE_EMOTION_SCREEN_COMMANDS = {"Neutral", "Happy", "Curious", "Excited", "Confused", "Concerned", "Sleepy"}
ALLOWED_UART_COMMANDS = CORE_SCREEN_COMMANDS | MOTOR_COMMANDS | UTILITY_COMMANDS | FUTURE_EMOTION_SCREEN_COMMANDS

VALID_PERSISTENT_STATES = {"normal", "sleep", "unchanged"}
VALID_EMOTIONS = {"neutral", "happy", "curious", "excited", "confused", "concerned", "sleepy"}
VALID_HEAD_MOTIONS = {"none", "nod", "double_nod", "look_around", "shake", "gentle_nod", "sleepy_drop"}

EMOTION_TO_SCREEN_COMMAND = {
    "neutral": "Neutral",
    "happy": "Happy",
    "curious": "Curious",
    "excited": "Excited",
    "confused": "Confused",
    "concerned": "Concerned",
    "sleepy": "Sleepy",
}

EMOTION_TO_HEAD_MOTION = {
    "neutral": "none",
    "happy": "nod",
    "curious": "look_around",
    "excited": "double_nod",
    "confused": "shake",
    "concerned": "gentle_nod",
    "sleepy": "sleepy_drop",
}

MOTOR_MIN = -15
MOTOR_MAX = 15
MOTOR_STEP_DELAY_SEC = 0.08

HEAD_MOTION_SEQUENCES = {
    "none": [
        ("MotorPitch", 0, 0),
        ("MotorYaw", 0, 0),
    ],
    "nod": [
        ("MotorPitch", 8, 0),
        ("MotorPitch", -5, 0),
        ("MotorPitch", 0, 0),
    ],
    "double_nod": [
        ("MotorPitch", 10, 0),
        ("MotorPitch", -6, 0),
        ("MotorPitch", 9, 0),
        ("MotorPitch", -5, 0),
        ("MotorPitch", 0, 0),
    ],
    "look_around": [
        ("MotorYaw", -10, 0),
        ("MotorYaw", 10, 0),
        ("MotorYaw", 0, 0),
        ("MotorPitch", -5, 0),
        ("MotorPitch", 0, 0),
    ],
    "shake": [
        ("MotorYaw", -8, 0),
        ("MotorYaw", 8, 0),
        ("MotorYaw", -6, 0),
        ("MotorYaw", 0, 0),
    ],
    "gentle_nod": [
        ("MotorPitch", -5, 0),
        ("MotorPitch", 3, 0),
        ("MotorPitch", 0, 0),
    ],
    "sleepy_drop": [
        ("MotorPitch", -10, 0),
        ("MotorPitch", -14, 0),
        ("MotorPitch", 0, 0),
    ],
}

SLEEP_INTENT_KEYWORDS = (
    "去睡覺",
    "去睡觉",
    "睡覺吧",
    "睡觉吧",
    "休息一下",
    "晚安",
    "進入睡眠模式",
    "进入睡眠模式",
    "安靜一下",
    "安静一下",
    "不要吵我",
    "sleep",
    "go to sleep",
    "standby",
)

WAKE_INTENT_KEYWORDS = (
    "起床",
    "醒來",
    "醒来",
    "回來",
    "回来",
    "回到正常",
    "不要睡了",
    "回來陪我",
    "回来陪我",
    "wake up",
    "come back",
    "normal",
    "don't sleep",
    "do not sleep",
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
        speech_start_threshold = max(
            speech_start_threshold,
            noise_floor + int(getattr(args, "speech_start_margin", 350)),
        )
        silence_base_threshold = max(
            int(getattr(args, "volume_min", 700)),
            noise_floor + int(getattr(args, "silence_margin", 500)),
        )
    return noise_floor, speech_start_threshold, silence_base_threshold


def adaptive_silence_threshold(args: argparse.Namespace, silence_base_threshold: int, peak_volume: int) -> int:
    if getattr(args, "no_adaptive_volume", False):
        return int(getattr(args, "volume_min", 700))
    peak_ratio = float(getattr(args, "silence_peak_ratio", 0.35))
    return max(int(silence_base_threshold), int(round(max(0, peak_volume) * peak_ratio)))


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


def wait_for_sounddevice_keyword(keyword: str, *, output: bool, timeout_sec: float, label: str) -> int | None:
    keyword = str(keyword or "").strip()
    if not keyword:
        return None
    finder = find_output_device_by_keyword if output else find_device_by_keyword
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_report_at = 0.0
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


def output_device_info(device_index: int | None) -> dict[str, Any] | None:
    if device_index is None:
        return None
    try:
        import sounddevice as sd

        info = sd.query_devices(device_index, "output")
    except Exception:
        return None
    return info if isinstance(info, dict) else None


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


def demo_device_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if not getattr(args, "no_camera", False) and not getattr(args, "no_vision", False):
        paths.extend(Path(path) for path in glob.glob("/dev/video*"))
    if str(getattr(args, "uart_port", "auto")).lower() == "auto":
        paths.extend(Path(path) for path in glob.glob("/dev/ttyACM*"))
    else:
        uart_path = Path(str(args.uart_port))
        if str(uart_path).startswith("/dev/"):
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
    if str(getattr(args, "uart_port", "auto")).lower() == "auto" and not glob.glob("/dev/ttyACM*"):
        missing.append("FRDM /dev/ttyACM*")
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
        if command_matches(cmdline, patterns):
            pid_reasons[pid] = "stale demo/audio process"
    terminate_pids(pid_reasons, dry_run=args.device_preflight_dry_run, grace_sec=args.device_preflight_grace)


def wait_for_demo_devices_ready(args: argparse.Namespace) -> None:
    if getattr(args, "device_preflight_dry_run", False):
        return
    timeout_sec = float(getattr(args, "device_ready_timeout", 12.0) or 0.0)
    if timeout_sec <= 0:
        return

    if not getattr(args, "no_camera", False) and not getattr(args, "no_vision", False):
        wait_for_path_candidates("camera nodes", "/dev/video*", timeout_sec=timeout_sec)
    if str(getattr(args, "uart_port", "auto")).lower() == "auto":
        wait_for_path_candidates("FRDM UART nodes", "/dev/ttyACM*", timeout_sec=timeout_sec)

    manual_input = bool(getattr(args, "_manual_input_device", getattr(args, "device", None) is not None))
    if not manual_input:
        wait_for_sounddevice_keyword(
            str(getattr(args, "mic_keyword", "") or ""),
            output=False,
            timeout_sec=timeout_sec,
            label="input",
        )

    manual_beep = bool(getattr(args, "_manual_beep_device", getattr(args, "beep_device", None) is not None))
    if not getattr(args, "no_beep", False) and not manual_beep:
        wait_for_sounddevice_keyword(
            str(getattr(args, "beep_keyword", "") or ""),
            output=True,
            timeout_sec=timeout_sec,
            label="output",
        )


def device_preflight(args: argparse.Namespace) -> None:
    if getattr(args, "no_device_preflight", False):
        print("Device preflight skipped by --no-device-preflight.")
        return
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
    wait_for_demo_devices_ready(args)


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


def local_control_from_transcript(transcript: str, response: dict[str, Any] | None = None) -> dict[str, str]:
    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        return {
            "persistent_state": "sleep",
            "emotion": "sleepy",
            "head_motion": "sleepy_drop",
            "reason": "sleep intent",
        }
    if state_intent == "normal":
        return {
            "persistent_state": "normal",
            "emotion": "happy",
            "head_motion": "nod",
            "reason": "wake/normal intent",
        }

    emotion = "neutral"
    if response is not None:
        raw_emotion = response.get("emotion")
        if isinstance(raw_emotion, dict):
            emotion = str(raw_emotion.get("primary", emotion)).strip().lower()
        elif isinstance(raw_emotion, str):
            emotion = raw_emotion.strip().lower()
    if emotion not in VALID_EMOTIONS:
        emotion = "neutral"
    return {
        "persistent_state": "unchanged",
        "emotion": emotion,
        "head_motion": EMOTION_TO_HEAD_MOTION.get(emotion, "none"),
        "reason": "local fallback",
    }


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

    emotion = str(source.get("emotion", fallback["emotion"])).strip().lower()
    if emotion not in VALID_EMOTIONS:
        emotion = fallback["emotion"]

    head_motion = str(source.get("head_motion", "")).strip().lower()
    if head_motion not in VALID_HEAD_MOTIONS:
        head_motion = EMOTION_TO_HEAD_MOTION.get(emotion, fallback["head_motion"])

    reason = str(source.get("reason", fallback["reason"])).strip() or fallback["reason"]

    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        persistent_state = "sleep"
        emotion = "sleepy"
        head_motion = "sleepy_drop"
        reason = "sleep intent"
    elif state_intent == "normal":
        persistent_state = "normal"
        if emotion in {"sleepy", "concerned", "confused"}:
            emotion = "happy"
        if head_motion in {"sleepy_drop", "shake"}:
            head_motion = "nod"
        reason = "wake/normal intent"

    return {
        "persistent_state": persistent_state,
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
    play_pattern = re.compile(
        r"(?:播放|播一下|播|波一下|波|放一下|放|換成|换成|換一首|换一首|改播|切到|我想要聽|我想要听|想要聽|想要听|我想聽|我想听|想聽|想听|我要聽|我要听|聽一下|听一下|聽|听|play|listen to)\s*(?P<query>.+)",
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
    ]
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
    if "paused" in result:
        print(f"  paused  : {result.get('paused')}")
    if "resumed" in result:
        print(f"  resumed : {result.get('resumed')}")
    if "stopped" in result:
        print(f"  stopped : {result.get('stopped')}")
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


class RobotUartController:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.persistent_state = "normal"
        self._lock = threading.RLock()

    def _validate_command(self, command: str, v1: int = 0, v2: int = 0) -> tuple[str, int, int] | None:
        name = str(command or "").strip()
        aliases = {
            "sleep": "Sleep",
            "normal": "Normal",
            "thinking": "Thinking",
            "speaking": "Speaking",
            "shownum": "ShowNum",
            "show_num": "ShowNum",
            "motorpitch": "MotorPitch",
            "pitch": "MotorPitch",
            "motoryaw": "MotorYaw",
            "yaw": "MotorYaw",
            "neutral": "Neutral",
            "happy": "Happy",
            "curious": "Curious",
            "excited": "Excited",
            "confused": "Confused",
            "concerned": "Concerned",
            "sleepy": "Sleepy",
        }
        compact = re.sub(r"[\s_-]+", "", name).lower()
        name = aliases.get(compact, name)
        if name not in ALLOWED_UART_COMMANDS:
            print(f"WARNING: refusing unknown UART command {command!r}.")
            return None

        if name in MOTOR_COMMANDS:
            v1 = clamp_int(v1, MOTOR_MIN, MOTOR_MAX)
            v2 = clamp_int(v2, MOTOR_MIN, MOTOR_MAX)
        elif name == "ShowNum":
            v1 = clamp_int(v1, 0, 999999)
            v2 = clamp_int(v2, 0, 999999)
        else:
            v1 = clamp_int(v1, -999999, 999999)
            v2 = clamp_int(v2, -999999, 999999)
        return name, v1, v2

    def _line_ending(self) -> bytes:
        return bridge.line_ending_bytes(getattr(self.args, "uart_line_ending", "crlf"))

    def send_uart_sequence(
        self,
        steps: list[tuple[str, int, int]],
        *,
        reason: str = "",
        delay_sec: float = 0.0,
        read_ms: int | None = None,
    ) -> bool:
        valid_steps: list[tuple[str, int, int, str]] = []
        invalid_count = 0
        for command, v1, v2 in steps:
            validated = self._validate_command(command, v1, v2)
            if validated is None:
                invalid_count += 1
                continue
            name, safe_v1, safe_v2 = validated
            valid_steps.append((name, safe_v1, safe_v2, f"{name} {safe_v1} {safe_v2}"))

        if not valid_steps:
            return invalid_count == 0
        if getattr(self.args, "no_uart", False):
            for _name, _v1, _v2, wire in valid_steps:
                print(f"FRDM UART skipped (--no-uart): {wire}")
            return True

        read_ms = min(int(read_ms if read_ms is not None else getattr(self.args, "uart_read_ms", 30)), 120)
        read_window_sec = max(0.0, read_ms / 1000.0)
        configured_timeout = float(getattr(self.args, "uart_timeout", 0.2) or 0.2)
        per_read_timeout = max(0.005, min(configured_timeout, read_window_sec if read_window_sec > 0 else 0.005))
        try:
            with self._lock:
                if getattr(self.args, "uart_dry_run", False):
                    for _name, _v1, _v2, wire in valid_steps:
                        print(f"FRDM UART dry-run TX: {wire}" + (f" ({reason})" if reason else ""))
                        if delay_sec > 0:
                            time.sleep(delay_sec)
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
                    for _name, _v1, _v2, wire in valid_steps:
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
                        if delay_sec > 0:
                            time.sleep(delay_sec)
            return True
        except Exception as exc:
            print(f"WARNING: UART error while sending {reason or valid_steps[-1][3]}: {exc}")
            return not getattr(self.args, "require_uart", False)

    def send_uart_command(self, command: str, v1: int = 0, v2: int = 0, *, reason: str = "", read_ms: int | None = None) -> bool:
        return self.send_uart_sequence([(command, v1, v2)], reason=reason, read_ms=read_ms)

    def set_screen_state(self, state: str) -> bool:
        return self.send_uart_command(state, 0, 0, reason=f"screen state {state}", read_ms=80)

    def set_persistent_state(self, state: str) -> None:
        if state in {"normal", "sleep"}:
            if state != self.persistent_state:
                print(f"persistent_state: {self.persistent_state} -> {state}")
            self.persistent_state = state

    def restore_persistent_screen_state(self) -> bool:
        command = "Sleep" if self.persistent_state == "sleep" else "Normal"
        ok = self.send_uart_command(command, 0, 0, reason=f"restore persistent state {self.persistent_state}", read_ms=100)
        print(f"UART {command} sent (restore persistent_state={self.persistent_state}).")
        return ok

    def send_emotion_screen(self, emotion: str) -> str:
        normalized = emotion if emotion in VALID_EMOTIONS else "neutral"
        command = EMOTION_TO_SCREEN_COMMAND.get(normalized, "Neutral")
        self.send_uart_command(command, 0, 0, reason=f"emotion {normalized}", read_ms=60)
        print(f"emotion screen command sent: {command} 0 0")
        return command

    def send_speaking_and_emotion(self, emotion: str) -> str:
        """Switch to Speaking and apply the emotion screen in one serial session."""
        normalized = emotion if emotion in VALID_EMOTIONS else "neutral"
        command = EMOTION_TO_SCREEN_COMMAND.get(normalized, "Neutral")
        self.send_uart_sequence(
            [("Speaking", 0, 0), (command, 0, 0)],
            reason=f"speaking + emotion {normalized}",
            delay_sec=0.02,
            read_ms=80,
        )
        print("UART Speaking sent.")
        print(f"emotion screen command sent: {command} 0 0")
        return command

    def run_head_motion(self, head_motion: str) -> None:
        motion = head_motion if head_motion in HEAD_MOTION_SEQUENCES else "none"
        sequence = list(HEAD_MOTION_SEQUENCES.get(motion, HEAD_MOTION_SEQUENCES["none"]))
        try:
            print(f"head motion started: {motion}")
            self.send_uart_sequence(sequence, reason=f"head_motion {motion}", delay_sec=MOTOR_STEP_DELAY_SEC, read_ms=20)
        except Exception as exc:
            print(f"WARNING: head motion failed: {exc}")
        finally:
            self.send_uart_sequence(
                [("MotorPitch", 0, 0), ("MotorYaw", 0, 0)],
                reason="head_motion reset",
                delay_sec=MOTOR_STEP_DELAY_SEC,
                read_ms=20,
            )
            print(f"head motion ended: {motion}")

    def start_head_motion(self, head_motion: str) -> threading.Thread:
        thread = threading.Thread(target=self.run_head_motion, args=(head_motion,), name=f"head_motion_{head_motion}", daemon=True)
        thread.start()
        return thread


def parse_camera_id(raw: str) -> str | int:
    value = str(raw).strip()
    if value.lower() in {"", "auto"}:
        return "auto"
    try:
        return int(value)
    except ValueError:
        return value


def play_recording_beep(
    *,
    duration_ms: int = 120,
    frequency_hz: float = 880.0,
    volume: float = 0.14,
    device: int | None = None,
) -> bool:
    """Play a short local cue without making recording depend on audio output."""
    if duration_ms <= 0 or volume <= 0.0:
        return True
    try:
        import sounddevice as sd

        sample_rates: list[int] = []
        if device is not None:
            try:
                info = sd.query_devices(device, "output")
                sample_rates.append(int(round(float(info.get("default_samplerate", 0)))))
            except Exception:
                pass
        sample_rates.extend([48_000, 44_100, 32_000])

        last_error: Exception | None = None
        for sample_rate in dict.fromkeys(rate for rate in sample_rates if rate > 0):
            try:
                sample_count = max(1, int(round(sample_rate * duration_ms / 1000.0)))
                t = np.arange(sample_count, dtype=np.float32) / float(sample_rate)
                tone = np.sin(2.0 * np.pi * float(frequency_hz) * t).astype(np.float32)
                tone *= float(max(0.0, min(volume, 1.0)))

                fade = max(1, int(round(sample_rate * 0.005)))
                if sample_count > fade * 2:
                    ramp = np.linspace(0.0, 1.0, num=fade, dtype=np.float32)
                    tone[:fade] *= ramp
                    tone[-fade:] *= ramp[::-1]

                sd.play(tone, samplerate=sample_rate, device=device, blocking=True)
                return True
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("no usable output sample rate")
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
            self._camera_stop.clear()
            self._camera_thread = threading.Thread(target=self._continuous_capture_loop, name="wake_camera_warm_reader", daemon=True)
            self._camera_thread.start()

    def capture_async(self) -> Future[bytes | None] | None:
        if not self.enabled or self.executor is None:
            return None
        return self.executor.submit(capture_jpeg_bytes, self)

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
            thread.join(timeout=0.5)
        executor = self.executor
        self.executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

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
        return None
    except Exception as exc:
        print(f"WARNING: camera capture task failed: {exc}")
        return None


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

    def refresh_input_device(self) -> None:
        """Re-resolve the USB mic before opening each PortAudio stream."""
        selected = select_input_device(self.args)
        self.args.device = selected
        self.sample_rate = voice_chat.choose_input_sample_rate(selected, self.args.input_sample_rate)
        self.frames_per_chunk = max(256, int(round(self.sample_rate * self.args.wake_chunk_ms / 1000.0)))

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
        noise_floor: int = 0,
        speech_start_threshold: int | None = None,
        silence_base_threshold: int | None = None,
        peak_volume: int = 0,
    ) -> dict[str, Any]:
        return {
            "wake_score": wake_score,
            "reason": reason,
            "wake_context": wake_context,
            "input_sample_rate": self.sample_rate,
            "noise_floor": noise_floor,
            "speech_start_threshold": speech_start_threshold,
            "silence_base_threshold": silence_base_threshold,
            "peak_volume": peak_volume,
        }

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
        last_recording_progress_log_at = 0.0
        ambient_volumes: list[int] = []
        ambient_max_chunks = max(5, int(round(5.0 * self.sample_rate / self.frames_per_chunk)))
        pre_speech_chunks: list[np.ndarray] = []
        pre_speech_max_chunks = max(1, int(round(self.args.pre_speech_seconds * self.sample_rate / self.frames_per_chunk)))
        noise_floor = 0
        speech_start_threshold = int(self.args.volume_min)
        silence_base_threshold = int(self.args.volume_min)
        peak_volume = 0
        last_audio_timeout_warn_at = 0.0
        audio_status_warn_at = 0.0
        max_queue_chunks = max(20, int(round(3.0 * self.sample_rate / self.frames_per_chunk)))
        audio_read_timeout = max(0.1, float(getattr(self.args, "audio_read_timeout", 1.0) or 1.0))
        progress_interval = max(0.25, float(getattr(self.args, "recording_progress_interval", 1.0) or 1.0))
        audio_queue: queue.Queue = queue.Queue(maxsize=max_queue_chunks)
        callback_state = {"dropped_chunks": 0}

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
                    if self.args.listen_debug or volume > self.args.idle_volume_print_min:
                        print(f"vol={volume:5d} | wake={score:.3f} | standby", end="\r", file=sys.stderr)

                    if score >= self.args.wake_threshold and volume < self.args.wake_volume_min:
                        if now - last_ignored_wake_at >= 2.0:
                            print(
                                f"\nLow-volume wake-like score ignored: score={score:.2f}, volume={volume} "
                                f"< wake_volume_min={self.args.wake_volume_min}. "
                                "Speak a little closer or lower --wake-volume-min if this was really you."
                            )
                            last_ignored_wake_at = now

                    if score >= self.args.wake_threshold and volume >= self.args.wake_volume_min:
                        noise_floor, speech_start_threshold, silence_base_threshold = adaptive_recording_thresholds(
                            self.args,
                            ambient_volumes,
                            fallback_volume=volume,
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
                            f"adaptive={'off' if self.args.no_adaptive_volume else 'on'}"
                        )
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
                            except Exception as exc:
                                print(f"WARNING: wake hook failed: {exc}")
                        drained_chunks = drain_audio_queue()
                        if drained_chunks and self.args.listen_debug:
                            print(f"Drained {drained_chunks} pre-recording audio chunk(s) after wake hook.")
                        print("Recording. Speak now; I will stop after silence.")
                    else:
                        ambient_volumes.append(volume)
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
                "wake_context": wake_context,
                "input_sample_rate": self.sample_rate,
            }

        return audio, {
            "wake_score": wake_score_at_start,
            "duration_sec": duration,
            "reason": "ok",
            "wake_context": wake_context,
            "input_sample_rate": self.sample_rate,
            "noise_floor": noise_floor,
            "speech_start_threshold": speech_start_threshold,
            "silence_base_threshold": silence_base_threshold,
            "peak_volume": peak_volume,
        }


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
            timeout_sec=float(getattr(args, "device_ready_timeout", 12.0) or 0.0),
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


def build_wake_hook(
    args: argparse.Namespace,
    camera_manager: CameraManager | None,
    robot: RobotUartController,
    turn_state: dict[str, Any],
) -> Any:
    def on_wake(info: dict[str, Any]) -> dict[str, Any]:
        timing = TimingLogger()
        turn_state.clear()
        turn_state["timing"] = timing
        timing.mark("wake detected")

        pause_music_for_wake(args)
        timing.mark("music paused for wake")

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

        image_future = camera_manager.capture_async() if camera_manager is not None else None
        metadata["image_capture_started"] = image_future is not None
        if image_future is not None:
            print("Image capture task started.")
        else:
            print("Image capture skipped or unavailable.")

        if args.no_beep:
            print("Recording beep skipped.")
        else:
            args.beep_device = select_beep_output_device(args)
            play_recording_beep(
                duration_ms=args.beep_duration_ms,
                frequency_hz=args.beep_frequency,
                volume=args.beep_volume,
                device=args.beep_device,
            )
            print("beep played.")
        timing.mark("beep done")
        robot.set_screen_state("Thinking")
        print("UART Thinking sent.")
        timing.mark("UART Thinking sent")

        return {
            "image_future": image_future,
            "metadata": metadata,
            "timing": timing,
        }

    return on_wake


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


def wait_for_tts_job(job_id: str, args: argparse.Namespace, *, timeout_sec: float) -> bool:
    if not job_id:
        time.sleep(timeout_sec)
        print(f"TTS estimated finished after {timeout_sec:.1f}s (no job id).")
        return True

    queue_url = tts_queue_url(args.tts_url)
    deadline = time.monotonic() + timeout_sec
    last_error = ""
    saw_current_job = False
    poll_interval = max(0.2, min(float(getattr(args, "tts_poll_interval", 0.75) or 0.75), 2.0))
    while time.monotonic() < deadline:
        try:
            status = voice_chat.get_json(queue_url, timeout_sec=min(args.tts_timeout, 2.0))
        except Exception as exc:
            last_error = str(exc)
            time.sleep(poll_interval)
            continue

        last_result = status.get("last_result") if isinstance(status.get("last_result"), dict) else {}
        if last_result.get("job_id") == job_id:
            print(f"TTS finished: job_id={job_id}")
            return True
        current = status.get("current") if isinstance(status.get("current"), dict) else {}
        if current.get("id") == job_id:
            saw_current_job = True
        last_error_value = str(status.get("last_error", "") or "").strip()
        if last_error_value and saw_current_job and not status.get("running"):
            print(f"WARNING: TTS worker error for job_id={job_id}: {last_error_value}")
            return False
        time.sleep(poll_interval)

    print(f"WARNING: TTS wait timed out after {timeout_sec:.1f}s for job_id={job_id}. last_error={last_error}")
    return False


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
            "nod",
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
                "transcript": "講個笑話",
                "reply": "emotion 是 happy，所以我要送 Happy 0 0。",
            },
            "unchanged",
            "neutral",
            "none",
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

    for motion, sequence in HEAD_MOTION_SEQUENCES.items():
        if not sequence:
            raise AssertionError(f"empty head motion sequence: {motion}")
        for command, v1, v2 in sequence:
            if command not in MOTOR_COMMANDS:
                raise AssertionError(f"head motion {motion} uses non-motor command {command}")
            if clamp_int(v1, MOTOR_MIN, MOTOR_MAX) != int(v1) or clamp_int(v2, MOTOR_MIN, MOTOR_MAX) != int(v2):
                raise AssertionError(f"head motion {motion} value out of range: {command} {v1} {v2}")

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
    )
    robot = RobotUartController(dry_args)
    if not robot.send_uart_command("Thinking", 0, 0, reason="self-test"):
        raise AssertionError("Thinking dry-run failed")
    if robot.send_uart_command("UnknownCommand", 0, 0, reason="self-test"):
        raise AssertionError("unknown UART command should be rejected")
    if robot._validate_command("MotorPitch", 99, 0) != ("MotorPitch", MOTOR_MAX, 0):
        raise AssertionError("MotorPitch clamp failed")
    if robot._validate_command("MotorYaw", -99, 0) != ("MotorYaw", MOTOR_MIN, 0):
        raise AssertionError("MotorYaw clamp failed")
    robot.send_emotion_screen("happy")
    robot.send_speaking_and_emotion("curious")
    head_thread = robot.start_head_motion("nod")
    head_thread.join(timeout=3.0)
    if head_thread.is_alive():
        raise AssertionError("head motion thread did not finish in self-test")

    queue_url = tts_queue_url("http://127.0.0.1:8777/speak_async")
    if queue_url != "http://127.0.0.1:8777/queue":
        raise AssertionError(f"bad TTS queue URL: {queue_url}")
    if estimate_tts_seconds("好，我先安靜陪你休息。") < 1.2:
        raise AssertionError("TTS estimate below minimum")

    gate_args = argparse.Namespace(
        volume_min=700,
        no_adaptive_volume=False,
        noise_floor_percentile=75.0,
        speech_start_margin=350,
        silence_margin=500,
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

    weather_args = argparse.Namespace(
        no_weather=False,
        weather_always_call=False,
        weather_debug=False,
        weather_url=DEFAULT_WEATHER_TOOL_URL,
        weather_default_location="Taipei",
    )
    weather_route = detect_weather_route({"transcript": "明天下午三點所在地天氣如何？"}, weather_args)
    if not weather_route.get("intent") or weather_route.get("action") != "weather":
        raise AssertionError(f"weather route detection failed: {weather_route}")
    non_weather_route = detect_weather_route({"transcript": "講個笑話"}, weather_args)
    if non_weather_route.get("intent"):
        raise AssertionError(f"non-weather route should not trigger: {non_weather_route}")

    print("wake bridge self-test OK")
    return 0


def speak_reply_and_wait(response: dict[str, Any], args: argparse.Namespace) -> bool:
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

    print(f"TTS started: estimated={estimated_sec:.1f}s timeout={timeout_sec:.1f}s")
    started = time.monotonic()
    try:
        tts_path = urllib.parse.urlsplit(args.tts_url).path.rstrip("/")
        post_timeout = timeout_sec if tts_path.endswith("/speak") else args.tts_timeout
        result = voice_chat.post_json(args.tts_url, payload, timeout_sec=post_timeout)
    except Exception as exc:
        print(f"WARNING: TTS speak failed: {exc}")
        return False

    post_ms = int((time.monotonic() - started) * 1000)
    job_id = str(result.get("job_id", "")).strip()
    if args.tts_debug:
        print()
        print("TTS:")
        print(f"  url          : {args.tts_url}")
        print(f"  post_ms      : {post_ms}")
        print(f"  queued       : {result.get('queued', False)}")
        if job_id:
            print(f"  job_id       : {job_id}")
    if result.get("queued") and job_id:
        return wait_for_tts_job(job_id, args, timeout_sec=timeout_sec)

    # /speak blocking path: returning from POST means playback is done.
    playback = result.get("playback") if isinstance(result.get("playback"), dict) else {}
    if playback:
        print("TTS finished: blocking playback returned.")
        return True

    time.sleep(estimated_sec)
    print(f"TTS estimated finished after {estimated_sec:.1f}s.")
    return True


def print_control_summary(control: dict[str, str]) -> None:
    print()
    print("AI control:")
    print(f"  persistent_state : {control.get('persistent_state')}")
    print(f"  emotion          : {control.get('emotion')}")
    print(f"  head_motion      : {control.get('head_motion')}")
    print(f"  reason           : {control.get('reason')}")


def emotion_summary_from_control(control: dict[str, str]) -> dict[str, Any]:
    primary = control.get("emotion", "neutral")
    if primary not in VALID_EMOTIONS:
        primary = "neutral"
    presets = {
        "neutral": (0.25, 0.0, 0.25, False, "自然中性互動。"),
        "happy": (0.65, 0.65, 0.55, False, "回覆語氣偏愉快。"),
        "curious": (0.45, 0.10, 0.45, False, "正在回答問題或分析畫面。"),
        "excited": (0.80, 0.75, 0.80, False, "互動能量較高。"),
        "confused": (0.55, -0.15, 0.45, False, "資訊不清楚或判斷不確定。"),
        "concerned": (0.65, -0.35, 0.35, True, "使用者可能需要關心。"),
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


def handle_wake_chat_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    robot: RobotUartController,
    timing: TimingLogger | None,
) -> bool:
    weather_result = maybe_apply_weather_response(response, args)
    if weather_result is not None and timing is not None:
        timing.mark("weather tool handled" if weather_result.get("ok") else "weather tool failed")

    control = normalize_control(response)
    response["control"] = control
    reply = sanitize_reply(response)
    emotion_obj = response.get("emotion") if isinstance(response.get("emotion"), dict) else {}
    if not emotion_obj or str(emotion_obj.get("primary", "")).strip().lower() != control["emotion"]:
        response["emotion"] = emotion_summary_from_control(control)
    print_control_summary(control)
    print(f"parsed reply: {reply}")
    print(f"parsed control: {json.dumps(control, ensure_ascii=False)}")

    voice_chat.print_result(response, verbose_debug=args.debug)
    response_vision_summary(response)
    music_route = detect_music_route(response, args)
    if music_route.get("action") in {"stop", "pause"}:
        execute_music_route(music_route, args, response, phase="before_tts")

    if control["persistent_state"] in {"normal", "sleep"}:
        robot.set_persistent_state(control["persistent_state"])

    robot.send_speaking_and_emotion(control["emotion"])

    head_thread = robot.start_head_motion(control["head_motion"])
    if timing is not None:
        timing.mark("UART Speaking/emotion sent")

    tts_ok = speak_reply_and_wait(response, args)
    if timing is not None:
        timing.mark("TTS finished or estimated finished")

    if music_route.get("action") in {"play", "resume"}:
        execute_music_route(music_route, args, response, phase="after_tts")
        if timing is not None:
            timing.mark("music triggered")

    head_thread.join(timeout=2.0)
    if head_thread.is_alive():
        print("WARNING: head motion thread still running after TTS; restore will continue.")

    robot.restore_persistent_screen_state()
    if timing is not None:
        timing.mark("UART Normal/Sleep sent")
    return tts_ok or not getattr(args, "require_tts", False)


def run_wake_text_mode(args: argparse.Namespace) -> int:
    if not voice_chat.preflight_server(args):
        return 1
    if not voice_chat.preflight_tts(args):
        return 1

    text_url = voice_chat.endpoint_url(args.server_url, "/text-chat")
    robot = RobotUartController(args)
    timing = TimingLogger()
    print(f"POST text to {text_url}")
    robot.set_screen_state("Thinking")
    print("UART Thinking sent.")
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
    return 0 if handle_wake_chat_response(response, args, robot, timing) else 1


def run_wake_voice_loop(args: argparse.Namespace) -> int:
    device_preflight(args)
    lock = InstanceLock(args.instance_lock, enabled=not args.no_instance_lock)
    if not lock.acquire():
        return 1

    previous_sigint = signal.getsignal(signal.SIGINT)

    def request_stop(signum: int, frame: Any) -> None:
        print("\nStop requested; shutting down.")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_stop)

    try:
        if not voice_chat.preflight_server(args):
            signal.signal(signal.SIGINT, previous_sigint)
            lock.release()
            return 1
        if not voice_chat.preflight_tts(args):
            signal.signal(signal.SIGINT, previous_sigint)
            lock.release()
            return 1

        args.device = select_input_device(args)
        args.beep_device = select_beep_output_device(args)
        try:
            input_sample_rate = voice_chat.choose_input_sample_rate(args.device, args.input_sample_rate)
        except (RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}")
            signal.signal(signal.SIGINT, previous_sigint)
            lock.release()
            return 1
    except Exception as exc:
        print(f"ERROR: {exc}")
        signal.signal(signal.SIGINT, previous_sigint)
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
    turn_state: dict[str, Any] = {}
    recorder = WakeVolumeRecorder(
        args,
        sample_rate=input_sample_rate,
        wake_hook=build_wake_hook(args, camera_manager, robot, turn_state),
    )
    try:
        recorder.load_wake_model()
    except Exception as exc:
        print(f"ERROR: {exc}")
        if camera_manager is not None:
            camera_manager.release()
        signal.signal(signal.SIGINT, previous_sigint)
        lock.release()
        return 1

    print("Wake voice chat + FRDM UART bridge ready.")
    print("AI path: Jetson wake/record locally -> Windows desktop local /voice-chat -> local ASR/Ollama.")
    print("No Gemini/OpenAI cloud API is used by this bridge.")
    print(f"Server URL: {args.server_url}")
    print(f"FRDM UART: {args.uart_port} @ {args.uart_baudrate}, line_ending={args.uart_line_ending}")
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
        f"silence_margin={args.silence_margin}, "
        f"peak_ratio={args.silence_peak_ratio:g}"
    )
    print(
        f"Audio read watchdog: callback queue, timeout={args.audio_read_timeout:g}s, "
        f"progress_interval={args.recording_progress_interval:g}s"
    )
    beep_desc = "disabled" if args.no_beep else f"{args.beep_frequency:g} Hz, {args.beep_duration_ms} ms, device={args.beep_device if args.beep_device is not None else 'default'}"
    print(f"Recording beep: {beep_desc}")
    vision_mode = "off" if args.no_vision else ("force" if args.force_vision else "auto")
    print(f"Vision mode: {vision_mode}")
    print(f"Camera: {'disabled' if args.no_camera or args.no_vision else f'{args.camera_id}, {args.camera_width}x{args.camera_height}, jpeg_quality={args.camera_jpeg_quality}'}")
    if args.no_music:
        music_desc = "disabled"
    else:
        music_desc = (
            f"{args.music_url}, backend={args.music_backend}->{resolve_music_backend(args)}, "
            f"autostart={not args.no_music_autostart}, "
            f"pause_on_wake={not args.no_music_pause_on_wake}"
        )
    print(f"Music tool: {music_desc}")
    if args.no_weather:
        weather_desc = "disabled"
    else:
        weather_desc = f"{args.weather_url}, default_location={args.weather_default_location}, source=Open-Meteo"
    print(f"Weather tool: {weather_desc}")
    print(f"TTS queue polling: every {args.tts_poll_interval:g}s, playback_timeout={args.tts_playback_timeout:g}s")
    if args._manual_input_device:
        print("WARNING: --device pins a numeric microphone index. Omit --device and use --mic-keyword for USB replug recovery.")
    if args._manual_beep_device:
        print("WARNING: --beep-device pins a numeric speaker index. Omit --beep-device and use --beep-keyword for USB replug recovery.")
    print(
        "USB auto-discovery: "
        f"mic={'fixed index ' + str(args.device) if args._manual_input_device else 'keyword ' + repr(args.mic_keyword)}; "
        f"beep={'disabled' if args.no_beep else ('fixed index ' + str(args.beep_device) if args._manual_beep_device else 'keyword ' + repr(args.beep_keyword))}; "
        f"camera={'disabled' if args.no_camera or args.no_vision else str(args.camera_id)}; "
        f"FRDM UART={args.uart_port}"
    )
    print("Press Ctrl+C to quit.")

    try:
        while True:
            wav_path: Path | None = None
            try:
                audio, meta = recorder.record_once()
                if audio is None:
                    wake_context = meta.get("wake_context") if isinstance(meta.get("wake_context"), dict) else {}
                    if wake_context:
                        print(f"No send after wake: {meta.get('reason', 'unknown')}; restoring persistent screen state.")
                        robot.restore_persistent_screen_state()
                    continue

                wake_context = meta.get("wake_context") if isinstance(meta.get("wake_context"), dict) else {}
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
                    robot.restore_persistent_screen_state()
                    continue

                image_future = wake_context.get("image_future")
                if not isinstance(image_future, Future):
                    image_future = None
                image_bytes = wait_for_image_future(image_future, args.camera_result_timeout)
                if timing is not None:
                    timing.mark("image captured" if image_bytes else "image unavailable")

                metadata = wake_context.get("metadata") if isinstance(wake_context.get("metadata"), dict) else {}
                metadata = dict(metadata)
                metadata["image_available"] = image_bytes is not None
                metadata["image_size_bytes"] = len(image_bytes) if image_bytes else 0
                metadata["vision_mode"] = "off" if args.no_vision else ("force" if args.force_vision else "auto")
                metadata["force_vision"] = args.force_vision
                metadata["no_vision"] = args.no_vision
                metadata["audio_duration_sec"] = meta.get("duration_sec")
                metadata["audio_rms"] = rms
                metadata["input_sample_rate"] = int(meta.get("input_sample_rate") or recorder.sample_rate or input_sample_rate)

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
                if raw_preview:
                    print(f"AI raw response preview: {raw_preview}")
                if not handle_wake_chat_response(response, args, robot, timing):
                    return 1
            except KeyboardInterrupt:
                print()
                return 0
            except Exception as exc:
                print(f"ERROR: {exc}")
                robot.restore_persistent_screen_state()
            finally:
                if wav_path is not None:
                    try:
                        wav_path.unlink(missing_ok=True)
                    except OSError:
                        pass
    finally:
        if camera_manager is not None:
            camera_manager.release()
        signal.signal(signal.SIGINT, previous_sigint)
        lock.release()


def add_wake_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.description = "Hands-free wake-word voice chat + FRDM MCXN947 UART bridge."
    safety_group = parser.add_argument_group("demo safety")
    safety_group.add_argument("--instance-lock", default=os.getenv("WAKE_BRIDGE_LOCK", DEFAULT_INSTANCE_LOCK))
    safety_group.add_argument("--no-instance-lock", action="store_true", help="Allow multiple bridge processes. Not recommended for demos.")
    safety_group.add_argument("--self-test", action="store_true", help="Run parser/UART/TTS timing dry-run checks and exit without hardware.")
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
    group.add_argument("--listen-debug", action="store_true", help="Print standby/recording volume on every chunk.")
    group.add_argument("--no-adaptive-volume", action="store_true", help="Disable adaptive noise-floor based speech/silence thresholds.")
    group.add_argument("--noise-floor-percentile", type=float, default=_env_float("NOISE_FLOOR_PERCENTILE", 75.0))
    group.add_argument("--speech-start-margin", type=int, default=_env_int("SPEECH_START_MARGIN", 350))
    group.add_argument("--silence-margin", type=int, default=_env_int("SILENCE_MARGIN", 650))
    group.add_argument("--silence-peak-ratio", type=float, default=_env_float("SILENCE_PEAK_RATIO", 0.35))
    group.add_argument("--pre-speech-seconds", type=float, default=_env_float("PRE_SPEECH_SECONDS", 0.35))

    beep_group = parser.add_argument_group("recording cue beep")
    beep_group.add_argument("--no-beep", action="store_true", help="Disable the short beep after wake detection.")
    beep_group.add_argument("--beep-duration-ms", type=int, default=_env_int("BEEP_DURATION_MS", 120))
    beep_group.add_argument("--beep-frequency", type=float, default=_env_float("BEEP_FREQUENCY", 880.0))
    beep_group.add_argument("--beep-volume", type=float, default=_env_float("BEEP_VOLUME", 0.14))
    beep_group.add_argument("--beep-device", type=int, default=None, help="Optional sounddevice output device index for the beep.")
    beep_group.add_argument("--beep-keyword", default=os.getenv("BEEP_KEYWORD", "UACDemo"), help="Output-device keyword used when --beep-device is omitted.")

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
    vision_group = parser.add_argument_group("vision routing")
    vision_group.add_argument("--force-vision", action="store_true", help="Force Windows server to use the uploaded image when one is available.")
    vision_group.add_argument("--no-vision", action="store_true", help="Disable camera capture and Windows vision analysis for this client.")
    music_group = parser.add_argument_group("music tool routing")
    music_group.add_argument("--no-music", action="store_true", help="Disable local Music Web Player sidecar routing.")
    music_group.add_argument("--music-url", default=os.getenv("MUSIC_TOOL_URL", DEFAULT_MUSIC_TOOL_URL), help="Music Web Player /music endpoint.")
    music_group.add_argument("--music-backend", choices=["auto", "browser", "mpv"], default=os.getenv("MUSIC_TOOL_BACKEND", "auto"))
    music_group.add_argument("--music-timeout", type=float, default=_env_float("MUSIC_TOOL_TIMEOUT", 3.0))
    music_group.add_argument(
        "--music-wake-pause-timeout",
        type=float,
        default=_env_float("MUSIC_WAKE_PAUSE_TIMEOUT", 0.6),
        help="Short local HTTP timeout for pausing music immediately after wake detection.",
    )
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
    weather_group.add_argument("--weather-api-timeout", type=float, default=_env_float("WEATHER_API_TIMEOUT", 5.0), help="Open-Meteo request timeout inside the local weather tool.")
    weather_group.add_argument("--weather-always-call", action="store_true", help="POST every transcript to /weather, even if local intent detection is false.")
    weather_group.add_argument("--weather-debug", action="store_true", help="Print weather routing details even when no weather intent was detected.")
    tts_timing_group = parser.add_argument_group("tts timing")
    tts_timing_group.add_argument(
        "--tts-playback-timeout",
        type=float,
        default=_env_float("TTS_PLAYBACK_TIMEOUT", 45.0),
        help="Maximum seconds to wait for /speak_async queue completion before restoring Normal/Sleep.",
    )
    tts_timing_group.add_argument(
        "--tts-poll-interval",
        type=float,
        default=_env_float("TTS_POLL_INTERVAL", 0.75),
        help="Seconds between /queue polls while waiting for /speak_async. Increase to reduce TTS terminal log spam.",
    )
    return parser


def build_arg_parser() -> argparse.ArgumentParser:
    return add_wake_args(bridge.build_arg_parser())


def main() -> int:
    args = build_arg_parser().parse_args()
    args._manual_input_device = args.device is not None
    args._manual_beep_device = args.beep_device is not None
    args.server_url = voice_chat.normalize_server_url(args.server_url)
    args.tts_url = voice_chat.normalize_tts_url(args.tts_url, blocking=args.tts_blocking)
    args.music_url = normalize_music_url(args.music_url)
    args.weather_url = normalize_weather_url(args.weather_url)
    bridge.apply_default_tts_voice(args)
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
    if args.device_preflight_only:
        device_preflight(args)
        return 0
    if args.check_server:
        return bridge.run_check_server(args)
    if args.text:
        return run_wake_text_mode(args)
    return run_wake_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
