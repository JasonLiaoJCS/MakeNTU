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
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

import numpy as np


THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
VOICE_DIR = PROJECT_ROOT / "emotion_robot_controller" / "voice_stt_remote"
VISION_DIR = PROJECT_ROOT / "vision"

if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))
if str(VISION_DIR) not in sys.path:
    sys.path.insert(0, str(VISION_DIR))

import voice_chat_frdm_uart_bridge as bridge  # noqa: E402
import jetson_fast_voice_chat as voice_chat  # noqa: E402


CLIENT_VERSION = "wake_voice_chat_frdm_bridge_vision_v2"
DEFAULT_INSTANCE_LOCK = "/tmp/wake_voice_chat_frdm_bridge.lock"
CAMERA_CAPTURE_HELPER = r"""
import glob
import os
import re
import sys
import time

import cv2


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
        warmup_frames: int,
    ) -> None:
        self.enabled = enabled
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.max_side = max_side
        self.jpeg_quality = max(1, min(int(jpeg_quality), 100))
        self.read_timeout = read_timeout
        self.warmup_frames = max(1, int(warmup_frames))
        self.executor: ThreadPoolExecutor | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        candidates = self._camera_candidates()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wake_camera")
        print("Camera ready in one-shot mode.")
        if candidates:
            print(f"  candidates       : {', '.join(str(item) for item in candidates)}")
        else:
            print("  candidates       : none now; auto mode will rescan /dev/video* on every wake")
        print(f"  capture          : {self.width}x{self.height}, jpeg_quality={self.jpeg_quality}")
        print(f"  timeout          : {self.read_timeout:.2f}s")
        if str(self.camera_id).lower() == "auto":
            print("  replug handling  : enabled; camera device numbers may change")

    def capture_async(self) -> Future[bytes | None] | None:
        if not self.enabled or self.executor is None:
            return None
        return self.executor.submit(capture_jpeg_bytes, self)

    def capture_jpeg_bytes(self) -> bytes | None:
        if not self.enabled:
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

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.args.device,
            blocksize=self.frames_per_chunk,
        ) as stream:
            while True:
                audio_float, overflowed = stream.read(self.frames_per_chunk)
                chunk = np.asarray(audio_float, dtype=np.float32).reshape(-1)
                audio_16k_int16 = self.chunk_to_16k_int16(chunk)
                volume = int16_volume(audio_16k_int16)
                now = time.monotonic()

                if overflowed:
                    print("Audio input overflow; continuing.")

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
                        wake_detected_at = now
                        wake_score_at_start = score
                        state = "recording"
                        record_chunks = []
                        speech_started_at = None
                        silence_started_at = None
                        self.reset_wake()
                        print()
                        print(f"Wake detected: {self.args.wake_word} score={score:.2f}")
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
                        print("Recording. Speak now; I will stop after silence.")
                    continue

                is_voice_loud = volume >= self.args.volume_min
                if self.args.listen_debug:
                    print(f"vol={volume:5d} | recording", end="\r", file=sys.stderr)

                if is_voice_loud:
                    if speech_started_at is None:
                        speech_started_at = now
                        print(f"Speech started. volume={volume}")
                    silence_started_at = None
                    record_chunks.append(chunk.copy())
                elif speech_started_at is not None:
                    record_chunks.append(chunk.copy())
                    if silence_started_at is None:
                        silence_started_at = now
                    elif now - silence_started_at >= self.args.silence_duration:
                        break
                elif wake_detected_at is not None and now - wake_detected_at >= self.args.wake_listen_timeout:
                    print("No speech after wake word; returning to standby.")
                    return None, {
                        "wake_score": wake_score_at_start,
                        "reason": "no_speech_after_wake",
                        "wake_context": wake_context,
                        "input_sample_rate": self.sample_rate,
                    }

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
        }


def select_input_device(args: argparse.Namespace) -> int | None:
    keyword = str(getattr(args, "mic_keyword", "") or "").strip()
    manual_device = bool(getattr(args, "_manual_input_device", args.device is not None))

    # In demo mode the USB microphone's PortAudio index can change after
    # unplug/replug. If the user selected by keyword, always rescan by name
    # instead of trusting the previous numeric index.
    if keyword and not manual_device and not bool(getattr(args, "no_mic_fallback", False)):
        selected = find_device_by_keyword(keyword)
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
                "or pass --allow-default-mic for a temporary fallback."
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
                "Run `lsusb`, `arecord -l`, or reset USB before starting the bridge."
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
    turn_state: dict[str, Any],
) -> Any:
    def on_wake(info: dict[str, Any]) -> dict[str, Any]:
        timing = TimingLogger()
        turn_state.clear()
        turn_state["timing"] = timing
        timing.mark("wake detected")

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
        timing.mark("beep done")

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


def handle_wake_chat_response(
    response: dict[str, Any],
    args: argparse.Namespace,
    timing: TimingLogger | None,
) -> bool:
    voice_chat.print_result(response, verbose_debug=args.debug)
    response_vision_summary(response)
    if not bridge.send_frdm_for_response(response, args):
        return False
    if timing is not None:
        timing.mark("UART sent")
    voice_chat.speak_reply(response, args)
    if timing is not None:
        timing.mark("TTS triggered")
    return True


def run_wake_voice_loop(args: argparse.Namespace) -> int:
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
            warmup_frames=args.camera_warmup_frames,
        )
        camera_manager.start()
    else:
        print("Camera disabled by --no-camera.")

    turn_state: dict[str, Any] = {}
    recorder = WakeVolumeRecorder(
        args,
        sample_rate=input_sample_rate,
        wake_hook=build_wake_hook(args, camera_manager, turn_state),
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
    print(f"wake_volume_min={args.wake_volume_min}, volume_min={args.volume_min}, silence_duration={args.silence_duration}s")
    beep_desc = "disabled" if args.no_beep else f"{args.beep_frequency:g} Hz, {args.beep_duration_ms} ms, device={args.beep_device if args.beep_device is not None else 'default'}"
    print(f"Recording beep: {beep_desc}")
    vision_mode = "off" if args.no_vision else ("force" if args.force_vision else "auto")
    print(f"Vision mode: {vision_mode}")
    print(f"Camera: {'disabled' if args.no_camera or args.no_vision else f'{args.camera_id}, {args.camera_width}x{args.camera_height}, jpeg_quality={args.camera_jpeg_quality}'}")
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
                    continue

                wake_context = meta.get("wake_context") if isinstance(meta.get("wake_context"), dict) else {}
                timing = wake_context.get("timing") if isinstance(wake_context.get("timing"), TimingLogger) else turn_state.get("timing")
                if not isinstance(timing, TimingLogger):
                    timing = None

                if timing is not None:
                    timing.mark("audio recording finished")

                rms = voice_chat.rms_level(audio)
                print(f"Recorded {meta.get('duration_sec', 0.0):.2f}s; RMS={rms:.5f}")
                if rms < args.rms_threshold:
                    print("SKIP: audio RMS too low; not sending.")
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
                if not handle_wake_chat_response(response, args, timing):
                    return 1
            except KeyboardInterrupt:
                print()
                return 0
            except Exception as exc:
                print(f"ERROR: {exc}")
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
    group.add_argument("--volume-min", type=int, default=_env_int("VOLUME_MIN", 14500), help="Mean abs int16 volume needed to count as speech.")
    group.add_argument("--silence-duration", type=float, default=_env_float("SILENCE_DURATION", 1.2))
    group.add_argument("--min-speech-seconds", type=float, default=_env_float("MIN_SPEECH_SECONDS", 0.4))
    group.add_argument("--max-speech-seconds", type=float, default=_env_float("MAX_SPEECH_SECONDS", 15.0))
    group.add_argument("--wake-listen-timeout", type=float, default=_env_float("WAKE_LISTEN_TIMEOUT", 6.0))
    group.add_argument("--wake-chunk-ms", type=int, default=_env_int("WAKE_CHUNK_MS", 80))
    group.add_argument("--idle-volume-print-min", type=int, default=_env_int("IDLE_VOLUME_PRINT_MIN", 100))
    group.add_argument("--listen-debug", action="store_true", help="Print standby/recording volume on every chunk.")

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
    camera_group.add_argument("--camera-read-timeout", type=float, default=_env_float("WAKE_CAMERA_READ_TIMEOUT", 2.5))
    camera_group.add_argument("--camera-result-timeout", type=float, default=_env_float("WAKE_CAMERA_RESULT_TIMEOUT", 0.25))
    camera_group.add_argument("--camera-warmup-frames", type=int, default=_env_int("WAKE_CAMERA_WARMUP_FRAMES", 3))
    vision_group = parser.add_argument_group("vision routing")
    vision_group.add_argument("--force-vision", action="store_true", help="Force Windows server to use the uploaded image when one is available.")
    vision_group.add_argument("--no-vision", action="store_true", help="Disable camera capture and Windows vision analysis for this client.")
    return parser


def build_arg_parser() -> argparse.ArgumentParser:
    return add_wake_args(bridge.build_arg_parser())


def main() -> int:
    args = build_arg_parser().parse_args()
    args._manual_input_device = args.device is not None
    args._manual_beep_device = args.beep_device is not None
    args.server_url = voice_chat.normalize_server_url(args.server_url)
    args.tts_url = voice_chat.normalize_tts_url(args.tts_url, blocking=args.tts_blocking)
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
    if args.check_server:
        return bridge.run_check_server(args)
    if args.text:
        return bridge.run_text_mode(args)
    return run_wake_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
