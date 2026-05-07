#!/usr/bin/env python3
"""
One-wake Jarvis conversation loop.

This version is intentionally built from the same backbone as
frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py:

    openWakeWord standby
    -> callback-queue microphone stream
    -> adaptive volume gate
    -> record until silence/max speech cap
    -> upload WAV to the existing /voice-chat server
    -> blocking TTS
    -> follow-up turns reuse the same recorder without another wake word

It stays standalone in this folder and does not modify the original bridge.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from typing import Any

import numpy as np


SAMPLE_RATE = 16_000
CHANNELS = 1
DEFAULT_SERVER_URL = os.getenv("JARVIS_SERVER_URL", "http://127.0.0.1:8766/voice-chat")
DEFAULT_TTS_URL = os.getenv("JARVIS_TTS_URL", "http://127.0.0.1:8777/speak_async")
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


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def require_sounddevice() -> Any:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: sounddevice. Install this folder's requirements:\n"
            "  cd /home/asrlab-yian/MakeNTU/jarvis_conversation_mode\n"
            "  python3 -m pip install -r requirements.txt"
        ) from exc
    return sd


def normalize_server_url(url: str) -> str:
    cleaned = url.strip().rstrip("~")
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned
    path = parsed.path.rstrip("/") or "/voice-chat"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_tts_url(url: str, *, async_tts: bool) -> str:
    cleaned = url.strip().rstrip("~")
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned
    desired = "/speak_async" if async_tts else "/speak"
    path = parsed.path.rstrip("/") or desired
    if async_tts and path == "/speak":
        path = "/speak_async"
    if not async_tts and path == "/speak_async":
        path = "/speak"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def endpoint_url(url: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def decode_json_response(raw: bytes) -> dict[str, Any]:
    parsed = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("HTTP response JSON is not an object")
    return parsed


def format_http_error(exc: urllib.error.HTTPError, url: str) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    body = " ".join(body.split())
    if len(body) > 500:
        body = body[:499] + "..."
    return f"HTTP {exc.code} {exc.reason} from {url}; body={body or '(empty)'}"


def get_json(url: str, timeout_sec: float) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return decode_json_response(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(format_http_error(exc, url)) from exc


def post_json(url: str, payload: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return decode_json_response(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(format_http_error(exc, url)) from exc


def post_multipart(
    url: str,
    *,
    audio_path: Path,
    metadata: dict[str, Any],
    timeout_sec: float,
) -> dict[str, Any]:
    boundary = "----JarvisConversationBoundary" + uuid.uuid4().hex
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
            return decode_json_response(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(format_http_error(exc, url)) from exc


def list_microphones() -> None:
    sd = require_sounddevice()
    print("sounddevice input devices:")
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        print(
            f"[{index:2d}] inputs={device.get('max_input_channels')} "
            f"default_sr={device.get('default_samplerate')} name={device.get('name')}"
        )


def find_input_device_by_keyword(keyword: str) -> int | None:
    sd = require_sounddevice()
    cleaned = keyword.strip().lower()
    if not cleaned:
        return None
    for index, device in enumerate(sd.query_devices()):
        name = str(device.get("name", ""))
        if cleaned in name.lower() and int(device.get("max_input_channels", 0)) > 0:
            return index
    return None


def choose_input_device(args: argparse.Namespace) -> int | None:
    keyword = str(args.mic_keyword or "").strip()
    manual_device = bool(getattr(args, "_manual_input_device", args.device is not None))

    # Match the original bridge behavior: when the user chooses by keyword,
    # rescan by name every time instead of pinning the previous numeric index.
    if keyword and not manual_device:
        deadline = time.monotonic() + max(0.0, float(args.device_ready_timeout))
        last_report_at = 0.0
        while True:
            selected = find_input_device_by_keyword(keyword)
            if selected is not None:
                if getattr(args, "_last_selected_input_device", None) != selected:
                    print(f"Selected input device {selected} by keyword {keyword!r}.")
                args._last_selected_input_device = selected
                return selected

            now = time.monotonic()
            if now >= deadline:
                break
            if now - last_report_at >= 2.0:
                print(f"Device preflight: waiting for microphone keyword {keyword!r}...")
                last_report_at = now
            time.sleep(0.5)

        if not args.allow_default_mic:
            raise RuntimeError(
                f"No microphone matching --mic-keyword {keyword!r} was found. "
                "Run `python3 jarvis_conversation_loop.py --list-mics`, "
                "or pass --device with the USB mic index."
            )
        print("WARNING: using default input because no matching USB microphone was found.")
        return None

    if args.device is not None:
        return args.device
    return None


def choose_input_sample_rate(device: int | None, requested_rate: int | None) -> int:
    if requested_rate:
        return requested_rate
    sd = require_sounddevice()
    info = sd.query_devices(device, "input")
    default_rate = int(round(float(info.get("default_samplerate", 48_000))))
    return default_rate if default_rate > 0 else 48_000


def resample_float(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if source_rate == target_rate or len(audio) == 0:
        return audio
    duration = len(audio) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def to_int16(audio_float: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(audio_float, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)


def int16_volume(audio_int16: np.ndarray) -> int:
    if len(audio_int16) == 0:
        return 0
    return int(np.abs(audio_int16.astype(np.int32)).mean())


def rms_level(audio: np.ndarray) -> float:
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(np.asarray(audio, dtype=np.float32)))))


def percentile_int(values: list[int], percentile: float, *, fallback: int = 0) -> int:
    cleaned = [int(value) for value in values if int(value) >= 0]
    if not cleaned:
        return int(fallback)
    return int(np.percentile(np.asarray(cleaned, dtype=np.float32), float(percentile)))


def adaptive_recording_thresholds(
    args: argparse.Namespace,
    ambient_volumes: list[int],
    *,
    fallback_volume: int,
) -> tuple[int, int, int]:
    noise_floor = percentile_int(
        ambient_volumes,
        args.noise_floor_percentile,
        fallback=fallback_volume,
    )
    speech_start_threshold = int(args.volume_min)
    silence_base_threshold = speech_start_threshold
    if not args.no_adaptive_volume:
        speech_start_threshold = max(speech_start_threshold, noise_floor + int(args.speech_start_margin))
        silence_base_threshold = max(
            int(args.volume_min),
            noise_floor + int(args.silence_margin),
        )
    return noise_floor, speech_start_threshold, silence_base_threshold


def adaptive_silence_threshold(args: argparse.Namespace, silence_base_threshold: int, peak_volume: int) -> int:
    if args.no_adaptive_volume:
        return int(args.volume_min)
    return max(int(silence_base_threshold), int(round(max(0, peak_volume) * args.silence_peak_ratio)))


def write_temp_wav_16k(audio: np.ndarray, source_rate: int) -> Path:
    audio_16k = resample_float(audio, source_rate, SAMPLE_RATE)
    pcm16 = to_int16(audio_16k).astype("<i2")
    handle = tempfile.NamedTemporaryFile(prefix="jarvis_turn_16k_", suffix=".wav", delete=False)
    path = Path(handle.name)
    handle.close()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16.tobytes())
    return path


class WakeVolumeRecorder:
    """Recorder shaped after wake_voice_chat_frdm_bridge.WakeVolumeRecorder."""

    def __init__(self, args: argparse.Namespace, sample_rate: int) -> None:
        self.args = args
        self.sample_rate = sample_rate
        self.target_rate = SAMPLE_RATE
        self.frames_per_chunk = max(256, int(round(sample_rate * args.wake_chunk_ms / 1000.0)))
        self.oww: Any | None = None
        self.ambient_volumes: list[int] = []
        self.ambient_max_chunks = max(5, int(round(5.0 * self.sample_rate / self.frames_per_chunk)))

    def refresh_input_device(self) -> None:
        selected = choose_input_device(self.args)
        self.args.device = selected
        self.sample_rate = choose_input_sample_rate(selected, self.args.input_sample_rate)
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
                "Missing dependency: openwakeword. Install this folder's requirements:\n"
                "  cd /home/asrlab-yian/MakeNTU/jarvis_conversation_mode\n"
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

    def remember_ambient(self, volume: int) -> None:
        self.ambient_volumes.append(int(volume))
        if len(self.ambient_volumes) > self.ambient_max_chunks:
            del self.ambient_volumes[: len(self.ambient_volumes) - self.ambient_max_chunks]

    def recording_meta(
        self,
        *,
        reason: str,
        wake_score: float,
        turn_source: str,
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
            "input_sample_rate": self.sample_rate,
            "noise_floor": noise_floor,
            "speech_start_threshold": speech_start_threshold,
            "silence_base_threshold": silence_base_threshold,
            "peak_volume": peak_volume,
        }
        if duration_sec is not None:
            meta["duration_sec"] = duration_sec
        return meta

    def record_once(self) -> tuple[np.ndarray | None, dict[str, Any]]:
        if self.args.no_wake_word:
            print("No-wake-word testing mode: this session starts by volume only.")
            return self.record_followup_turn(turn_source="initial_no_wake", listen_timeout=self.args.turn_listen_timeout)
        return self._record_turn(require_wake=True, turn_source="wake", listen_timeout=self.args.wake_listen_timeout)

    def record_followup_turn(
        self,
        *,
        turn_source: str = "followup",
        listen_timeout: float | None = None,
    ) -> tuple[np.ndarray | None, dict[str, Any]]:
        timeout = self.args.turn_listen_timeout if listen_timeout is None else listen_timeout
        return self._record_turn(require_wake=False, turn_source=turn_source, listen_timeout=timeout)

    def _record_turn(
        self,
        *,
        require_wake: bool,
        turn_source: str,
        listen_timeout: float,
    ) -> tuple[np.ndarray | None, dict[str, Any]]:
        sd = require_sounddevice()
        self.refresh_input_device()

        device_label = "default" if self.args.device is None else str(self.args.device)
        print()
        if require_wake:
            print(
                f"Listening for wake word '{self.args.wake_word}' "
                f"(device={device_label}, wake_threshold={self.args.wake_threshold})."
            )
            print("Wake-only standby: speech without the wake word is ignored and will not be sent to ASR/AI.")
        else:
            print(
                f"Conversation listening for follow-up speech "
                f"(device={device_label}, timeout={listen_timeout:.1f}s)."
            )

        state = "waiting_wake" if require_wake else "recording"
        record_chunks: list[np.ndarray] = []
        pre_speech_chunks: list[np.ndarray] = []
        pre_speech_max_chunks = max(1, int(round(self.args.pre_speech_seconds * self.sample_rate / self.frames_per_chunk)))
        speech_started_at: float | None = None
        silence_started_at: float | None = None
        wake_detected_at: float | None = None
        wake_score_at_start = 0.0
        noise_floor = 0
        speech_start_threshold = int(self.args.volume_min)
        silence_base_threshold = int(self.args.volume_min)
        peak_volume = 0
        last_ignored_wake_at = 0.0
        last_recording_progress_log_at = 0.0
        last_audio_timeout_warn_at = 0.0
        audio_status_warn_at = 0.0
        audio_read_timeout = max(0.1, float(self.args.audio_read_timeout or 1.0))
        progress_interval = max(0.25, float(self.args.recording_progress_interval or 1.0))
        max_queue_chunks = max(20, int(round(3.0 * self.sample_rate / self.frames_per_chunk)))
        audio_queue: queue.Queue[tuple[np.ndarray, Any]] = queue.Queue(maxsize=max_queue_chunks)
        callback_state = {"dropped_chunks": 0}

        if not require_wake:
            wake_detected_at = time.monotonic()
            wake_score_at_start = 1.0
            noise_floor, speech_start_threshold, silence_base_threshold = adaptive_recording_thresholds(
                self.args,
                self.ambient_volumes,
                fallback_volume=self.args.volume_min,
            )
            print(
                "Follow-up thresholds: "
                f"noise_floor={noise_floor}, "
                f"speech_start_threshold={speech_start_threshold}, "
                f"silence_base_threshold={silence_base_threshold}, "
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
            channels=CHANNELS,
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
                    if state == "recording" and wake_detected_at is not None:
                        no_speech_elapsed = now - wake_detected_at
                        max_recording_seconds = float(self.args.max_recording_seconds or 0.0)
                        if max_recording_seconds > 0.0 and no_speech_elapsed >= max_recording_seconds:
                            if speech_started_at is not None and record_chunks:
                                print(
                                    f"Max recording wall-clock reached ({max_recording_seconds:.1f}s); sending buffered audio."
                                )
                                break
                            print("Max recording wall-clock reached before speech; ending this turn.")
                            return None, self.recording_meta(
                                reason="max_recording_before_speech_audio_timeout",
                                wake_score=wake_score_at_start,
                                turn_source=turn_source,
                                noise_floor=noise_floor,
                                speech_start_threshold=speech_start_threshold,
                                silence_base_threshold=silence_base_threshold,
                                peak_volume=peak_volume,
                            )
                        if speech_started_at is None and no_speech_elapsed >= listen_timeout:
                            reason = "no_speech_after_wake_audio_timeout" if require_wake else "no_followup_speech_audio_timeout"
                            print(f"No speech for {listen_timeout:.1f}s; conversation turn ended.")
                            return None, self.recording_meta(
                                reason=reason,
                                wake_score=wake_score_at_start,
                                turn_source=turn_source,
                                noise_floor=noise_floor,
                                speech_start_threshold=speech_start_threshold,
                                silence_base_threshold=silence_base_threshold,
                                peak_volume=peak_volume,
                            )
                    if now - last_audio_timeout_warn_at >= 2.0:
                        print(
                            "WARNING: microphone input produced no audio chunk for "
                            f"{audio_read_timeout:.1f}s."
                        )
                        last_audio_timeout_warn_at = now
                    if state == "waiting_wake":
                        return None, self.recording_meta(
                            reason="audio_input_timeout_waiting_wake",
                            wake_score=wake_score_at_start,
                            turn_source=turn_source,
                        )
                    continue

                audio_16k_int16 = self.chunk_to_16k_int16(chunk)
                volume = int16_volume(audio_16k_int16)
                now = time.monotonic()

                if input_status and now - audio_status_warn_at >= 2.0:
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
                                f"< wake_volume_min={self.args.wake_volume_min}."
                            )
                            last_ignored_wake_at = now
                    elif score >= self.args.wake_threshold:
                        noise_floor, speech_start_threshold, silence_base_threshold = adaptive_recording_thresholds(
                            self.args,
                            self.ambient_volumes,
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
                        drain_audio_queue()
                        print()
                        print(f"Wake detected: {self.args.wake_word} score={score:.2f}")
                        print(
                            "Recording thresholds: "
                            f"noise_floor={noise_floor}, "
                            f"speech_start_threshold={speech_start_threshold}, "
                            f"silence_base_threshold={silence_base_threshold}, "
                            f"adaptive={'off' if self.args.no_adaptive_volume else 'on'}"
                        )
                        print("Recording. Speak now; I will stop after silence.")
                    else:
                        self.remember_ambient(volume)
                    continue

                elapsed_since_wake = now - wake_detected_at if wake_detected_at is not None else 0.0
                max_recording_seconds = float(self.args.max_recording_seconds or 0.0)
                if max_recording_seconds > 0.0 and elapsed_since_wake >= max_recording_seconds:
                    if speech_started_at is not None and record_chunks:
                        print(f"Max recording wall-clock reached ({max_recording_seconds:.1f}s); sending.")
                        break
                    print("Max recording wall-clock reached before speech; ending this turn.")
                    return None, self.recording_meta(
                        reason="max_recording_before_speech",
                        wake_score=wake_score_at_start,
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
                        f"| silence<={current_silence_threshold} | recording",
                        end="\r",
                        file=sys.stderr,
                    )
                elif now - last_recording_progress_log_at >= progress_interval:
                    phase = "speech" if speech_started_at is not None else "waiting_speech"
                    wait_label = ""
                    if speech_started_at is None:
                        remaining = max(0.0, listen_timeout - elapsed_since_wake)
                        wait_label = f", listen_timeout_in={remaining:.1f}s"
                    print(
                        f"Recording progress: phase={phase}, elapsed={elapsed_since_wake:.1f}s, "
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
                elif elapsed_since_wake >= listen_timeout:
                    reason = "no_speech_after_wake" if require_wake else "no_followup_speech"
                    print(f"No speech for {listen_timeout:.1f}s; conversation turn ended.")
                    return None, self.recording_meta(
                        reason=reason,
                        wake_score=wake_score_at_start,
                        turn_source=turn_source,
                        noise_floor=noise_floor,
                        speech_start_threshold=speech_start_threshold,
                        silence_base_threshold=silence_base_threshold,
                        peak_volume=peak_volume,
                    )

                if speech_started_at is not None and now - speech_started_at >= self.args.max_speech_seconds:
                    print(f"Max speech length reached ({self.args.max_speech_seconds:.1f}s); sending.")
                    break

        if not record_chunks:
            return None, self.recording_meta(
                reason="empty_recording",
                wake_score=wake_score_at_start,
                turn_source=turn_source,
                noise_floor=noise_floor,
                speech_start_threshold=speech_start_threshold,
                silence_base_threshold=silence_base_threshold,
                peak_volume=peak_volume,
            )

        audio = np.concatenate(record_chunks).astype(np.float32)
        duration = len(audio) / float(self.sample_rate)
        if duration < self.args.min_speech_seconds:
            print(f"Recording too short ({duration:.2f}s); dropping.")
            return None, self.recording_meta(
                reason="too_short",
                wake_score=wake_score_at_start,
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
            turn_source=turn_source,
            noise_floor=noise_floor,
            speech_start_threshold=speech_start_threshold,
            silence_base_threshold=silence_base_threshold,
            peak_volume=peak_volume,
            duration_sec=duration,
        )


def preflight_server(args: argparse.Namespace) -> bool:
    if args.no_preflight:
        return True
    health_url = endpoint_url(args.server_url, "/health")
    try:
        health = get_json(health_url, timeout_sec=min(args.timeout, 8.0))
    except Exception as exc:
        print(f"ERROR: server health check failed: {exc}")
        print(f"Check desktop server, then try: curl {health_url}")
        return False
    print("Server health:")
    print(f"  service    : {health.get('service', 'unknown')}")
    print(f"  chat_ready : {health.get('chat_ready', 'unknown')}")
    print(f"  asr_loaded : {health.get('asr_loaded', 'unknown')}")
    print(f"  ollama     : {health.get('ollama_model', 'unknown')}")
    return health.get("chat_ready") is True


def preflight_tts(args: argparse.Namespace) -> bool:
    if args.no_tts or args.no_tts_preflight:
        return True
    health_url = endpoint_url(args.tts_url, "/health")
    try:
        health = get_json(health_url, timeout_sec=min(args.tts_timeout, 4.0))
    except Exception as exc:
        if args.require_tts:
            print(f"ERROR: TTS health check failed: {exc}")
            return False
        print(f"WARNING: TTS health check failed: {exc}")
        print("Voice chat will continue, but replies will only print.")
        args.no_tts = True
        return True
    print("TTS health:")
    print(f"  ready : {health.get('ready', 'unknown')}")
    print(f"  url   : {args.tts_url}")
    if args.require_tts and health.get("ready") is not True:
        print("ERROR: TTS server is not ready.")
        return False
    return True


def estimate_tts_seconds(text: str) -> float:
    cleaned = " ".join(text.split())
    if not cleaned:
        return 0.0
    chinese_chars = sum(1 for char in cleaned if "\u4e00" <= char <= "\u9fff")
    other_chars = max(0, len(cleaned) - chinese_chars)
    return max(1.2, chinese_chars / 4.8 + other_chars / 12.0)


def tts_queue_url(tts_url: str) -> str:
    parsed = urllib.parse.urlsplit(tts_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/queue", "", ""))


def wait_for_tts_job(job_id: str, args: argparse.Namespace, *, timeout_sec: float) -> bool:
    queue_url = tts_queue_url(args.tts_url)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            status = get_json(queue_url, timeout_sec=min(args.tts_timeout, 4.0))
        except Exception as exc:
            print(f"WARNING: TTS queue check failed: {exc}")
            return False

        current = status.get("current") if isinstance(status.get("current"), dict) else None
        last_result = status.get("last_result") if isinstance(status.get("last_result"), dict) else None
        last_error = str(status.get("last_error", "") or "").strip()
        if last_result and str(last_result.get("job_id", "")) == job_id:
            print("TTS finished: queue job completed.")
            return True
        if last_error:
            print(f"WARNING: TTS queue error: {last_error}")
            return False
        if current and str(current.get("id", "")) == job_id:
            time.sleep(args.tts_poll_interval)
            continue
        time.sleep(args.tts_poll_interval)

    print(f"WARNING: TTS job {job_id} did not finish within {timeout_sec:.1f}s.")
    return False


def speak_reply_and_wait(reply: str, args: argparse.Namespace) -> bool:
    if args.no_tts:
        print("TTS skipped by --no-tts.")
        return True
    reply = reply.strip()
    if not reply:
        print("TTS skipped: empty reply.")
        return True

    payload: dict[str, Any] = {
        "text": reply,
        "blocking": not args.tts_async,
        "interrupt": True,
    }
    if args.tts_voice:
        payload["voice"] = args.tts_voice
    if args.tts_length_scale is not None:
        payload["length_scale"] = args.tts_length_scale

    estimated_sec = estimate_tts_seconds(reply)
    timeout_sec = max(float(args.tts_playback_timeout or 0.0), estimated_sec + 25.0)
    timeout_sec = min(max(timeout_sec, 8.0), 60.0)
    print(f"TTS started: estimated={estimated_sec:.1f}s timeout={timeout_sec:.1f}s")

    started = time.monotonic()
    try:
        post_timeout = args.tts_timeout if args.tts_async else timeout_sec
        result = post_json(args.tts_url, payload, timeout_sec=post_timeout)
    except Exception as exc:
        print(f"WARNING: TTS speak failed: {exc}")
        return False

    post_ms = int((time.monotonic() - started) * 1000)
    job_id = str(result.get("job_id", "")).strip()
    if args.tts_debug:
        print()
        print("TTS:")
        print(f"  url     : {args.tts_url}")
        print(f"  post_ms : {post_ms}")
        print(f"  queued  : {result.get('queued', False)}")
        if job_id:
            print(f"  job_id  : {job_id}")

    if result.get("queued") and job_id:
        return wait_for_tts_job(job_id, args, timeout_sec=timeout_sec)

    print("TTS finished: blocking playback returned.")
    return True


def normalize_session_text(text: str) -> str:
    lowered = text.strip().lower()
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


def should_end_session(transcript: str) -> bool:
    return end_session_keyword(transcript) is not None


def print_response(response: dict[str, Any], args: argparse.Namespace) -> None:
    transcript = str(response.get("transcript", "")).strip()
    reply = str(response.get("reply", "")).strip()
    request_id = str(response.get("request_id", "")).strip()
    timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
    if args.quiet_dialog:
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
        return

    print()
    print("Transcript:")
    if request_id:
        print(f"request_id={request_id}")
    print(transcript or "(empty)")
    print()
    print("Reply:")
    print(reply or "(no reply)")
    if timing:
        print()
        print(
            "Timing: "
            f"asr={timing.get('asr_ms', '?')} ms, "
            f"llm={timing.get('llm_ms', '?')} ms, "
            f"total={timing.get('total_ms', '?')} ms"
        )


def send_turn_to_server(
    args: argparse.Namespace,
    *,
    audio: np.ndarray,
    meta: dict[str, Any],
    session_id: str,
    turn_index: int,
) -> dict[str, Any] | None:
    rms = rms_level(audio)
    print(f"Recorded turn {turn_index}: {meta.get('duration_sec', 0.0):.2f}s; RMS={rms:.5f}")
    if "speech_start_threshold" in meta:
        print(
            "Recording gate: "
            f"noise_floor={meta.get('noise_floor')}, "
            f"start_threshold={meta.get('speech_start_threshold')}, "
            f"silence_base={meta.get('silence_base_threshold')}, "
            f"peak={meta.get('peak_volume')}"
        )
    if rms < args.rms_threshold:
        print(f"SKIP: audio RMS {rms:.5f} < threshold {args.rms_threshold:.5f}")
        return None

    wav_path: Path | None = None
    try:
        input_sample_rate = int(meta.get("input_sample_rate") or SAMPLE_RATE)
        wav_path = write_temp_wav_16k(audio, input_sample_rate)
        metadata = {
            "client": "jarvis_conversation_mode",
            "session_id": session_id,
            "turn_index": turn_index,
            "turn_source": meta.get("turn_source"),
            "audio_duration_sec": meta.get("duration_sec"),
            "audio_rms": rms,
            "wake_score": meta.get("wake_score"),
            "noise_floor": meta.get("noise_floor"),
            "speech_start_threshold": meta.get("speech_start_threshold"),
            "silence_base_threshold": meta.get("silence_base_threshold"),
            "peak_volume": meta.get("peak_volume"),
            "vision_mode": "auto",
        }
        print(f"POST audio to {args.server_url}")
        started = time.monotonic()
        response = post_multipart(
            args.server_url,
            audio_path=wav_path,
            metadata=metadata,
            timeout_sec=args.timeout,
        )
        print(f"Round trip: {int((time.monotonic() - started) * 1000)} ms")
        return response
    finally:
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass


def handle_turn_response(response: dict[str, Any], args: argparse.Namespace) -> bool:
    print_response(response, args)
    transcript = str(response.get("transcript", "")).strip()
    reply = str(response.get("reply", "")).strip()
    end_keyword = end_session_keyword(transcript)
    if end_keyword:
        print(f"End keyword detected in transcript ({end_keyword}); this will be the last turn in this session.")
        if not args.speak_end_reply:
            print("TTS skipped for end command; returning to standby now.")
            print("Returning to standby. Say Hey Jarvis to start a new conversation.")
            return False

    tts_ok = speak_reply_and_wait(reply, args)
    if args.post_tts_settle_seconds > 0:
        time.sleep(args.post_tts_settle_seconds)
    if not tts_ok and args.require_tts:
        return False
    if end_keyword:
        print("Returning to standby. Say Hey Jarvis to start a new conversation.")
        return False
    return True


def run_conversation_session(
    args: argparse.Namespace,
    recorder: WakeVolumeRecorder,
    first_audio: np.ndarray,
    first_meta: dict[str, Any],
) -> None:
    session_id = uuid.uuid4().hex[:10]
    print()
    print(f"Conversation session started: session_id={session_id}")

    audio: np.ndarray | None = first_audio
    meta: dict[str, Any] = first_meta
    last_activity_at = time.monotonic()

    for turn_index in range(1, args.max_session_turns + 1):
        if audio is None:
            return

        response = send_turn_to_server(
            args,
            audio=audio,
            meta=meta,
            session_id=session_id,
            turn_index=turn_index,
        )
        if response is not None:
            last_activity_at = time.monotonic()
            if not handle_turn_response(response, args):
                return

        idle_sec = time.monotonic() - last_activity_at
        remaining_idle = max(0.0, args.session_idle_timeout - idle_sec)
        followup_timeout = min(args.turn_listen_timeout, remaining_idle) if args.session_idle_timeout > 0 else args.turn_listen_timeout
        if followup_timeout <= 0:
            print("Session idle timeout reached; returning to standby.")
            return

        audio, meta = recorder.record_followup_turn(listen_timeout=followup_timeout)
        if audio is None:
            print(f"No follow-up speech ({meta.get('reason', 'unknown')}); returning to standby.")
            return

    print(f"Max session turns reached ({args.max_session_turns}); returning to standby.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-wake Hey Jarvis conversation loop based on the existing wake bridge recorder.")
    parser.add_argument("--list-mics", action="store_true")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--tts-url", default=DEFAULT_TTS_URL)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument(
        "--fast-response",
        action="store_true",
        help="Apply low-latency demo defaults: shorter silence/max speech/follow-up waits and faster TTS queue polling.",
    )
    parser.add_argument(
        "--turbo-response",
        action="store_true",
        help="More aggressive low-latency defaults for demo use. This may clip long pauses inside a sentence.",
    )
    parser.add_argument(
        "--quiet-dialog",
        action="store_true",
        help="Do not print transcript or reply text in the terminal; keep only request id and timing.",
    )

    mic = parser.add_argument_group("microphone")
    mic.add_argument("--device", type=int, default=None)
    mic.add_argument("--input-sample-rate", type=int, default=None)
    mic.add_argument("--mic-keyword", default=os.getenv("MIC_KEYWORD", "UACDemo"))
    mic.add_argument("--allow-default-mic", action="store_true")
    mic.add_argument("--device-ready-timeout", type=float, default=env_float("DEVICE_READY_TIMEOUT", 12.0))

    wake = parser.add_argument_group("wake-word auto recording")
    wake.add_argument("--wake-word", default=os.getenv("JARVIS_WAKE_WORD", "hey_jarvis"))
    wake.add_argument("--wake-threshold", type=float, default=env_float("JARVIS_WAKE_THRESHOLD", 0.5))
    wake.add_argument("--wake-volume-min", type=int, default=env_int("JARVIS_WAKE_VOLUME_MIN", 350))
    wake.add_argument("--no-wake-word", action="store_true", help="Testing mode: start a session by volume without openWakeWord.")
    wake.add_argument("--wake-listen-timeout", type=float, default=env_float("JARVIS_WAKE_LISTEN_TIMEOUT", 6.0))
    wake.add_argument("--wake-chunk-ms", "--chunk-ms", dest="wake_chunk_ms", type=int, default=env_int("JARVIS_WAKE_CHUNK_MS", 80))
    wake.add_argument("--audio-read-timeout", type=float, default=env_float("JARVIS_AUDIO_READ_TIMEOUT", 0.75))
    wake.add_argument("--recording-progress-interval", type=float, default=env_float("JARVIS_RECORDING_PROGRESS_INTERVAL", 1.0))
    wake.add_argument("--idle-volume-print-min", type=int, default=env_int("JARVIS_IDLE_VOLUME_PRINT_MIN", 100))
    wake.add_argument("--listen-debug", action="store_true")

    gate = parser.add_argument_group("adaptive volume gate")
    gate.add_argument("--volume-min", type=int, default=env_int("JARVIS_VOLUME_MIN", 700))
    gate.add_argument("--silence-duration", type=float, default=env_float("JARVIS_SILENCE_DURATION", 1.2))
    gate.add_argument("--min-speech-seconds", type=float, default=env_float("JARVIS_MIN_SPEECH_SECONDS", 0.4))
    gate.add_argument("--max-speech-seconds", type=float, default=env_float("JARVIS_MAX_SPEECH_SECONDS", 20.0))
    gate.add_argument(
        "--max-recording-seconds",
        type=float,
        default=env_float("JARVIS_MAX_RECORDING_SECONDS", 26.0),
        help="Wall-clock cap from wake/follow-up listen start. Keep this above --max-speech-seconds.",
    )
    gate.add_argument("--no-adaptive-volume", action="store_true")
    gate.add_argument("--noise-floor-percentile", type=float, default=env_float("JARVIS_NOISE_FLOOR_PERCENTILE", 75.0))
    gate.add_argument("--speech-start-margin", type=int, default=env_int("JARVIS_SPEECH_START_MARGIN", 350))
    gate.add_argument("--silence-margin", type=int, default=env_int("JARVIS_SILENCE_MARGIN", 650))
    gate.add_argument("--silence-peak-ratio", type=float, default=env_float("JARVIS_SILENCE_PEAK_RATIO", 0.35))
    gate.add_argument("--pre-speech-seconds", type=float, default=env_float("JARVIS_PRE_SPEECH_SECONDS", 0.35))
    gate.add_argument("--rms-threshold", type=float, default=env_float("JARVIS_RMS_THRESHOLD", 0.008))

    session = parser.add_argument_group("conversation session")
    session.add_argument("--turn-listen-timeout", type=float, default=env_float("JARVIS_TURN_LISTEN_TIMEOUT", 10.0))
    session.add_argument("--session-idle-timeout", type=float, default=env_float("JARVIS_SESSION_IDLE_TIMEOUT", 45.0))
    session.add_argument("--max-session-turns", type=int, default=env_int("JARVIS_MAX_SESSION_TURNS", 20))

    tts = parser.add_argument_group("tts")
    tts.add_argument("--no-tts", action="store_true")
    tts.add_argument("--require-tts", action="store_true")
    tts.add_argument("--no-tts-preflight", action="store_true")
    tts.add_argument(
        "--tts-async",
        dest="tts_async",
        action="store_true",
        default=True,
        help="Use /speak_async and poll /queue. This matches the main bridge default.",
    )
    tts.add_argument(
        "--tts-blocking",
        dest="tts_async",
        action="store_false",
        help="Use /speak blocking instead of /speak_async.",
    )
    tts.add_argument("--tts-timeout", type=float, default=env_float("JARVIS_TTS_TIMEOUT", 5.0))
    tts.add_argument("--tts-playback-timeout", type=float, default=env_float("JARVIS_TTS_PLAYBACK_TIMEOUT", 45.0))
    tts.add_argument("--tts-poll-interval", type=float, default=env_float("JARVIS_TTS_POLL_INTERVAL", 0.75))
    tts.add_argument("--tts-voice", default=os.getenv("JARVIS_TTS_VOICE", ""))
    tts.add_argument("--tts-length-scale", type=float, default=None)
    tts.add_argument("--tts-debug", action="store_true")
    tts.add_argument("--post-tts-settle-seconds", type=float, default=env_float("JARVIS_POST_TTS_SETTLE_SECONDS", 0.35))
    tts.add_argument(
        "--speak-end-reply",
        action="store_true",
        help="Speak the AI farewell reply before returning to wake-only standby. Default is to skip it for faster exit.",
    )

    compat = parser.add_argument_group("main bridge compatibility args (accepted but not executed here)")
    compat.add_argument("--device-preflight-verbose", action="store_true")
    compat.add_argument("--no-device-preflight", action="store_true")
    compat.add_argument("--device-preflight-only", action="store_true")
    compat.add_argument("--device-preflight-dry-run", action="store_true")
    compat.add_argument("--device-preflight-keep-music", action="store_true")
    compat.add_argument("--kill-audio-servers", action="store_true")
    compat.add_argument("--uart-port", default="auto")
    compat.add_argument("--uart-baudrate", type=int, default=115200)
    compat.add_argument("--uart-line-ending", default="crlf")
    compat.add_argument("--uart-debug", action="store_true")
    compat.add_argument("--uart-dry-run", action="store_true")
    compat.add_argument("--no-uart", action="store_true")
    compat.add_argument("--camera-id", default="auto")
    compat.add_argument("--camera-width", type=int, default=320)
    compat.add_argument("--camera-height", type=int, default=240)
    compat.add_argument("--camera-jpeg-quality", type=int, default=70)
    compat.add_argument("--camera-latest-timeout", type=float, default=1.0)
    compat.add_argument("--camera-frame-max-age", type=float, default=2.0)
    compat.add_argument("--no-camera", action="store_true")
    compat.add_argument("--force-vision", action="store_true")
    compat.add_argument("--no-vision", action="store_true")
    compat.add_argument("--no-beep", action="store_true")
    compat.add_argument("--beep-device", type=int, default=None)
    compat.add_argument("--beep-keyword", default="UACDemo")
    compat.add_argument("--beep-duration-ms", type=int, default=120)
    compat.add_argument("--beep-frequency", type=float, default=880.0)
    compat.add_argument("--beep-volume", type=float, default=0.14)
    compat.add_argument("--music-url", default="http://127.0.0.1:8788/music")
    compat.add_argument("--music-backend", choices=["auto", "browser", "mpv"], default="auto")
    compat.add_argument("--music-timeout", type=float, default=5.0)
    compat.add_argument("--music-wake-pause-timeout", type=float, default=0.6)
    compat.add_argument("--music-debug", action="store_true")
    compat.add_argument("--no-music", action="store_true")
    compat.add_argument("--weather-url", default="http://127.0.0.1:8788/weather")
    compat.add_argument("--weather-default-location", default="Taipei")
    compat.add_argument("--weather-timeout", type=float, default=6.0)
    compat.add_argument("--weather-api-timeout", type=float, default=5.0)
    compat.add_argument("--weather-debug", action="store_true")
    compat.add_argument("--no-weather", action="store_true")
    compat.add_argument("--motor-step-delay", type=float, default=0.35)
    compat.add_argument("--motor-reset-repeats", type=int, default=4)
    compat.add_argument("--motor-reset-delay", type=float, default=0.22)
    compat.add_argument("--motor-join-timeout", type=float, default=6.0)
    return parser


def apply_latency_preset(args: argparse.Namespace) -> None:
    if args.turbo_response:
        args.silence_duration = 0.55
        args.max_speech_seconds = 5.0
        args.max_recording_seconds = 7.0
        args.turn_listen_timeout = 4.0
        args.session_idle_timeout = 18.0
        args.audio_read_timeout = 0.35
        args.recording_progress_interval = 0.75
        args.tts_poll_interval = 0.2
        args.post_tts_settle_seconds = 0.05
        args.pre_speech_seconds = 0.25
        if args.tts_length_scale is None:
            args.tts_length_scale = 0.86
        print(
            "Turbo response preset enabled: "
            "silence=0.55s, max_speech=5s, max_recording=7s, "
            "turn_timeout=4s, tts_poll=0.2s, tts_length_scale=0.86."
        )
        return

    if not args.fast_response:
        return
    args.silence_duration = 0.75
    args.max_speech_seconds = 6.0
    args.max_recording_seconds = 9.0
    args.turn_listen_timeout = 6.0
    args.session_idle_timeout = 24.0
    args.audio_read_timeout = 0.6
    args.recording_progress_interval = 0.5
    args.tts_poll_interval = 0.35
    args.post_tts_settle_seconds = 0.15
    print(
        "Fast response preset enabled: "
        "silence=0.75s, max_speech=6s, max_recording=9s, "
        "turn_timeout=6s, tts_poll=0.35s."
    )


def main() -> int:
    args = build_arg_parser().parse_args()
    apply_latency_preset(args)
    args._manual_input_device = args.device is not None
    args.server_url = normalize_server_url(args.server_url)
    args.tts_url = normalize_tts_url(args.tts_url, async_tts=args.tts_async)

    if args.device_preflight_only:
        print("Standalone compatibility mode: --device-preflight-only only lists microphones here.")
        try:
            list_microphones()
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        return 0

    if args.list_mics:
        try:
            list_microphones()
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        return 0

    if not preflight_server(args):
        return 1
    if not preflight_tts(args):
        return 1

    try:
        args.device = choose_input_device(args)
        input_sample_rate = choose_input_sample_rate(args.device, args.input_sample_rate)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    recorder = WakeVolumeRecorder(args, sample_rate=input_sample_rate)
    try:
        recorder.load_wake_model()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    device_label = "default" if args.device is None else str(args.device)
    print()
    print("Jarvis conversation mode ready.")
    print("Recorder backbone: frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py style callback queue + adaptive gate.")
    print("Standalone scope: camera/music/weather/UART args are accepted for CLI compatibility but not executed here.")
    print(f"Server URL: {args.server_url}")
    print(f"TTS URL: {'disabled' if args.no_tts else args.tts_url}")
    print(f"Microphone: device={device_label}, sample_rate={input_sample_rate} Hz")
    print(f"Wake word: {'disabled' if args.no_wake_word else args.wake_word}")
    print(
        "Adaptive recording gate: "
        f"{'off' if args.no_adaptive_volume else 'on'}, "
        f"volume_min={args.volume_min}, speech_margin={args.speech_start_margin}, "
        f"silence_margin={args.silence_margin}, peak_ratio={args.silence_peak_ratio:g}"
    )
    print(
        "Session: "
        f"turn_timeout={args.turn_listen_timeout:g}s, "
        f"idle_timeout={args.session_idle_timeout:g}s, max_turns={args.max_session_turns}"
    )
    print("Press Ctrl+C to quit.")

    try:
        while True:
            audio, meta = recorder.record_once()
            if audio is None:
                continue
            run_conversation_session(args, recorder, audio, meta)
            if args.no_wake_word:
                print("No-wake-word testing session ended; exiting so later speech is not recorded without wake.")
                return 0
            print("Wake-only standby restored. Say Hey Jarvis before speaking again.")
    except KeyboardInterrupt:
        print()
        print("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
