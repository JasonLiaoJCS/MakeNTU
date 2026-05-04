"""
Jetson Orin Nano remote voice client.

Architecture:
    Jetson records audio at the microphone's supported sample rate
    -> resamples to 16 kHz mono WAV
    -> sends a temporary WAV chunk to Windows desktop
    -> desktop runs Qwen3-ASR + Ollama qwen3.5:27b
    -> desktop returns JSON
    -> Jetson prints reply and optionally sends command to serial/HTTP backend

Jetson setup:
    cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
    source ../.venv/bin/activate
    pip install sounddevice numpy pyserial

Usage:
    python jetson_remote_voice_client.py --list-mics
    python jetson_remote_voice_client.py --server-url http://DESKTOP_IP:8765/voice-command
    python jetson_remote_voice_client.py --server-url http://DESKTOP_IP:8765/voice-command --device 2
    python jetson_remote_voice_client.py --server-url http://DESKTOP_IP:8765/voice-command --backend serial --serial-port /dev/ttyTHS1
    python jetson_remote_voice_client.py --server-url http://DESKTOP_IP:8765/voice-command --text "幫我開電風扇"
    python jetson_remote_voice_client.py --record-mode enter-stop --server-url http://DESKTOP_IP:8765/voice-command
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any

from desk_voice_controller import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_RMS_THRESHOLD,
    DEFAULT_SECONDS,
    SAMPLE_RATE,
    CHANNELS,
    Command,
    HttpBackend,
    PrintBackend,
    SerialBackend,
    list_microphones,
    microphone_device_lines,
    rms_level,
    validate_command,
)


DEFAULT_SERVER_URL = "http://127.0.0.1:8765/voice-command"


def choose_input_sample_rate(device: int | None, requested_rate: int | None) -> int:
    if requested_rate:
        return requested_rate

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("請先安裝 sounddevice: pip install sounddevice") from exc

    try:
        info = sd.query_devices(device, "input")
    except ValueError as exc:
        lines = microphone_device_lines(sd)
        available = "\n".join(f"  {line}" for line in lines) if lines else "  (no input devices found)"
        selected = "default input" if device is None else f"--device {device}"
        raise ValueError(
            f"{exc}\n\n"
            f"Selected {selected} is not available as a microphone input.\n"
            "Run `python jetson_fast_voice_chat.py --list-mics` and choose an index with inputs > 0, "
            "or omit `--device` to use the default input.\n"
            f"Available input devices:\n{available}"
        ) from exc
    default_rate = int(round(float(info.get("default_samplerate", 48_000))))
    if default_rate <= 0:
        default_rate = 48_000
    return default_rate


def record_audio_for_device(seconds: float, device: int | None, sample_rate: int) -> Any:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("請先安裝 sounddevice 與 numpy: pip install sounddevice numpy") from exc

    frames = int(seconds * sample_rate)
    print(f"Recording {seconds:.1f}s at {sample_rate} Hz mono...")
    audio = sd.rec(
        frames,
        samplerate=sample_rate,
        channels=CHANNELS,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return np.asarray(audio, dtype="float32").reshape(-1)


def record_audio_until_enter(device: int | None, sample_rate: int, max_seconds: float) -> Any:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("請先安裝 sounddevice 與 numpy: pip install sounddevice numpy") from exc

    chunks: list[Any] = []
    started = time.monotonic()

    def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            print(f"Audio status: {status}")
        chunks.append(indata.copy())

    print(f"Recording at {sample_rate} Hz mono. Press Enter again to stop.")
    with sd.InputStream(
        samplerate=sample_rate,
        channels=CHANNELS,
        dtype="float32",
        device=device,
        callback=callback,
    ):
        try:
            input()
        except EOFError:
            pass

    elapsed = time.monotonic() - started
    if elapsed > max_seconds:
        print(f"NOTE: recording length {elapsed:.1f}s exceeded max hint {max_seconds:.1f}s; keeping full audio.")
    if not chunks:
        return np.asarray([], dtype="float32")
    audio = np.concatenate(chunks, axis=0).astype("float32").reshape(-1)
    print(f"Recorded {len(audio) / float(sample_rate):.2f}s")
    return audio


def resample_audio(audio: Any, source_rate: int, target_rate: int = SAMPLE_RATE) -> Any:
    import numpy as np

    if source_rate == target_rate:
        return np.asarray(audio, dtype="float32")
    if len(audio) == 0:
        return np.asarray(audio, dtype="float32")

    duration = len(audio) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    resampled = np.interp(target_x, source_x, audio).astype("float32")
    return resampled


def write_temp_wav_16k(audio: Any, source_rate: int) -> Path:
    import numpy as np

    audio_16k = resample_audio(audio, source_rate, SAMPLE_RATE)
    clipped = np.clip(audio_16k, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype("<i2")

    handle = tempfile.NamedTemporaryFile(prefix="jetson_voice_16k_", suffix=".wav", delete=False)
    path = Path(handle.name)
    handle.close()

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16.tobytes())
    return path


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


def post_multipart_file(url: str, field_name: str, file_path: Path, timeout_sec: float) -> dict[str, Any]:
    boundary = "----JetsonVoiceBoundary" + uuid.uuid4().hex
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()

    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode("ascii"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
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


def format_http_error(exc: urllib.error.HTTPError, url: str) -> str:
    raw = exc.read().decode("utf-8", errors="replace")
    body = " ".join(raw.split())
    if len(body) > 500:
        body = body[:499] + "..."
    return f"HTTP {exc.code} {exc.reason} from {url}; body={body or '(empty)'}"


def decode_json_response(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("server JSON response is not an object")
    return parsed


def command_from_server_response(response: dict[str, Any]) -> tuple[Command, bool, str]:
    command_data = response.get("command")
    transcript = str(response.get("transcript", ""))
    command = validate_command(command_data, transcript)
    should_execute = bool(response.get("should_execute", False))
    skip_reason = str(response.get("skip_reason", ""))
    return command, should_execute, skip_reason


class NoExecuteBackend:
    def send(self, command: Command) -> None:
        print(f"NOEXEC backend: {command.wire_command}")


def make_backend(args: argparse.Namespace) -> Any:
    if args.backend == "none":
        return NoExecuteBackend()
    if args.backend == "print":
        return PrintBackend()
    if args.backend == "serial":
        return SerialBackend(args.serial_port, args.serial_baudrate)
    if args.backend == "http":
        return HttpBackend(args.http_url)
    raise ValueError(f"Unsupported backend: {args.backend}")


def handle_server_response(response: dict[str, Any], args: argparse.Namespace, backend: Any) -> None:
    command, server_should_execute, server_skip_reason = command_from_server_response(response)
    print()
    print("Server response:")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    print(f"Transcript: {command.transcript}")
    print(f"Reply: {command.reply}")

    local_should_execute = (
        server_should_execute
        and command.intent != "UNKNOWN"
        and command.confidence >= args.min_confidence
    )
    if not local_should_execute:
        reason = server_skip_reason or "local safety gate blocked this command"
        print(f"SKIP hardware control: {reason}")
        return

    try:
        backend.send(command)
        print("EXECUTED on Jetson backend.")
    except Exception as exc:
        print(f"ERROR: Jetson backend failed: {exc}")


def run_text_mode(args: argparse.Namespace) -> int:
    text_url = args.server_url.replace("/voice-command", "/text-command")
    backend = make_backend(args)
    print(f"POST text to {text_url}")
    try:
        response = post_json(text_url, {"text": args.text}, timeout_sec=args.timeout)
    except Exception as exc:
        print(f"ERROR: server text request failed: {exc}")
        return 1
    handle_server_response(response, args, backend)
    return 0


def run_voice_loop(args: argparse.Namespace) -> int:
    backend = make_backend(args)
    try:
        input_sample_rate = choose_input_sample_rate(args.device, args.input_sample_rate)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Remote voice client ready.")
    print(f"Server URL: {args.server_url}")
    print(f"Backend: {args.backend}")
    print(f"Input sample rate: {input_sample_rate} Hz; upload WAV sample rate: {SAMPLE_RATE} Hz")
    if args.record_mode == "enter-stop":
        print("Type q then Enter to quit. Otherwise press Enter to start, then press Enter again to stop.")
    else:
        print("Press Enter to record one audio chunk. Type q then Enter to quit.")

    while True:
        try:
            user = input("\nPress Enter to record> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if user in {"q", "quit", "exit"}:
            return 0

        wav_path: Path | None = None
        try:
            if args.record_mode == "enter-stop":
                audio = record_audio_until_enter(args.device, input_sample_rate, args.max_seconds)
            else:
                audio = record_audio_for_device(args.seconds, args.device, input_sample_rate)
            rms = rms_level(audio)
            print(f"RMS={rms:.5f}")
            if rms < args.rms_threshold:
                print("SKIP: audio RMS too low; not sending to desktop.")
                continue

            wav_path = write_temp_wav_16k(audio, input_sample_rate)
            print(f"POST audio to {args.server_url}")
            started = time.monotonic()
            response = post_multipart_file(args.server_url, "audio", wav_path, timeout_sec=args.timeout)
            print(f"Round trip: {int((time.monotonic() - started) * 1000)} ms")
            handle_server_response(response, args, backend)
        except KeyboardInterrupt:
            print()
            return 0
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: remote request failed: {exc}")
        except Exception as exc:
            print(f"ERROR: {exc}")
        finally:
            if wav_path is not None:
                try:
                    wav_path.unlink(missing_ok=True)
                except OSError:
                    pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jetson microphone client that sends WAV chunks to a desktop Qwen ASR/Ollama server.")
    parser.add_argument("--list-mics", action="store_true")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--text", help="Send text to desktop /text-command instead of recording audio.")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--record-mode", choices=["fixed", "enter-stop"], default="enter-stop", help="fixed: record --seconds. enter-stop: Enter starts, next Enter stops.")
    parser.add_argument("--max-seconds", type=float, default=30.0, help="Hint for enter-stop mode; long recordings are allowed but may be slow to upload/transcribe.")
    parser.add_argument("--input-sample-rate", type=int, default=None, help="Hardware recording sample rate. Default uses selected microphone default rate.")
    parser.add_argument("--rms-threshold", type=float, default=DEFAULT_RMS_THRESHOLD)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    parser.add_argument("--backend", choices=["none", "print", "serial", "http"], default="print")
    parser.add_argument("--serial-port", default="/dev/ttyTHS1")
    parser.add_argument("--serial-baudrate", type=int, default=115200)
    parser.add_argument("--http-url", default="http://127.0.0.1:5000/command")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.list_mics:
        list_microphones()
        return 0
    if args.text:
        return run_text_mode(args)
    return run_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
