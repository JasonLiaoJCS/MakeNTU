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
import os
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np


THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
VOICE_DIR = PROJECT_ROOT / "emotion_robot_controller" / "voice_stt_remote"

if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))

import voice_chat_frdm_uart_bridge as bridge  # noqa: E402
import jetson_fast_voice_chat as voice_chat  # noqa: E402


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


class WakeVolumeRecorder:
    def __init__(self, args: argparse.Namespace, sample_rate: int) -> None:
        self.args = args
        self.sample_rate = sample_rate
        self.target_rate = voice_chat.SAMPLE_RATE
        self.frames_per_chunk = max(256, int(round(sample_rate * args.wake_chunk_ms / 1000.0)))
        self.oww = None

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

                    if score >= self.args.wake_threshold:
                        wake_detected_at = now
                        wake_score_at_start = score
                        state = "recording"
                        record_chunks = []
                        speech_started_at = None
                        silence_started_at = None
                        self.reset_wake()
                        print()
                        print(f"Wake detected: {self.args.wake_word} score={score:.2f}")
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
                    }

                if speech_started_at is not None and now - speech_started_at >= self.args.max_speech_seconds:
                    print(f"Max speech length reached ({self.args.max_speech_seconds:.1f}s); sending.")
                    break

        if not record_chunks:
            return None, {"wake_score": wake_score_at_start, "reason": "empty_recording"}

        audio = np.concatenate(record_chunks).astype(np.float32)
        duration = len(audio) / float(self.sample_rate)
        if duration < self.args.min_speech_seconds:
            print(f"Recording too short ({duration:.2f}s); dropping.")
            return None, {
                "wake_score": wake_score_at_start,
                "duration_sec": duration,
                "reason": "too_short",
            }

        return audio, {
            "wake_score": wake_score_at_start,
            "duration_sec": duration,
            "reason": "ok",
        }


def select_input_device(args: argparse.Namespace) -> int | None:
    selected = bridge.resolve_input_device(args)
    if selected is None and str(getattr(args, "mic_keyword", "") or "").strip():
        print("WARNING: using default input because no matching USB microphone was found.")
        list_sounddevice_inputs()
    return selected


def run_wake_voice_loop(args: argparse.Namespace) -> int:
    if not voice_chat.preflight_server(args):
        return 1
    if not voice_chat.preflight_tts(args):
        return 1

    args.device = select_input_device(args)
    try:
        input_sample_rate = voice_chat.choose_input_sample_rate(args.device, args.input_sample_rate)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    recorder = WakeVolumeRecorder(args, sample_rate=input_sample_rate)
    try:
        recorder.load_wake_model()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Wake voice chat + FRDM UART bridge ready.")
    print("AI path: Jetson wake/record locally -> Windows desktop local /voice-chat -> local ASR/Ollama.")
    print("No Gemini/OpenAI cloud API is used by this bridge.")
    print(f"Server URL: {args.server_url}")
    print(f"FRDM UART: {args.uart_port} @ {args.uart_baudrate}, line_ending={args.uart_line_ending}")
    print(f"Input sample rate: {input_sample_rate} Hz; upload WAV sample rate: {voice_chat.SAMPLE_RATE} Hz")
    print(f"Wake word: {'disabled' if args.no_wake_word else args.wake_word}")
    print(f"volume_min={args.volume_min}, silence_duration={args.silence_duration}s")
    print("Press Ctrl+C to quit.")

    while True:
        wav_path: Path | None = None
        try:
            audio, meta = recorder.record_once()
            if audio is None:
                continue

            rms = voice_chat.rms_level(audio)
            print(f"Recorded {meta.get('duration_sec', 0.0):.2f}s; RMS={rms:.5f}")
            if rms < args.rms_threshold:
                print("SKIP: audio RMS too low; not sending.")
                continue

            wav_path = voice_chat.write_temp_wav_16k(audio, input_sample_rate)
            print(f"POST audio to {args.server_url}")
            started = time.monotonic()
            response = voice_chat.post_multipart_file(args.server_url, "audio", wav_path, timeout_sec=args.timeout)
            print(f"Round trip: {int((time.monotonic() - started) * 1000)} ms")
            if not bridge.handle_chat_response(response, args, verbose_debug=args.debug):
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


def add_wake_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.description = "Hands-free wake-word voice chat + FRDM MCXN947 UART bridge."
    group = parser.add_argument_group("wake-word auto recording")
    group.add_argument("--wake-word", default=os.getenv("WAKE_WORD", "hey_jarvis"))
    group.add_argument("--wake-threshold", type=float, default=_env_float("WAKE_THRESHOLD", 0.5))
    group.add_argument("--no-wake-word", action="store_true", help="Start recording by volume only, without openWakeWord.")
    group.add_argument("--volume-min", type=int, default=_env_int("VOLUME_MIN", 14500), help="Mean abs int16 volume needed to count as speech.")
    group.add_argument("--silence-duration", type=float, default=_env_float("SILENCE_DURATION", 1.2))
    group.add_argument("--min-speech-seconds", type=float, default=_env_float("MIN_SPEECH_SECONDS", 0.4))
    group.add_argument("--max-speech-seconds", type=float, default=_env_float("MAX_SPEECH_SECONDS", 15.0))
    group.add_argument("--wake-listen-timeout", type=float, default=_env_float("WAKE_LISTEN_TIMEOUT", 6.0))
    group.add_argument("--wake-chunk-ms", type=int, default=_env_int("WAKE_CHUNK_MS", 80))
    group.add_argument("--idle-volume-print-min", type=int, default=_env_int("IDLE_VOLUME_PRINT_MIN", 100))
    group.add_argument("--listen-debug", action="store_true", help="Print standby/recording volume on every chunk.")
    return parser


def build_arg_parser() -> argparse.ArgumentParser:
    return add_wake_args(bridge.build_arg_parser())


def main() -> int:
    args = build_arg_parser().parse_args()
    args.server_url = voice_chat.normalize_server_url(args.server_url)
    args.tts_url = voice_chat.normalize_tts_url(args.tts_url, blocking=args.tts_blocking)
    bridge.apply_default_tts_voice(args)
    args.tts_interrupt = not args.tts_no_interrupt
    args.tts_stream = False if args.tts_file_playback else None

    if args.list_mics:
        voice_chat.list_microphones()
        list_sounddevice_inputs()
        return 0
    if args.check_server:
        return bridge.run_check_server(args)
    if args.text:
        return bridge.run_text_mode(args)
    return run_wake_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
