"""
Desktop remote voice server for Windows 11 + RTX 4090.

Architecture:
    Jetson Orin Nano records a 16 kHz mono WAV chunk
    -> POST WAV to this Windows desktop server
    -> this server runs Qwen3-ASR-1.7B with qwen-asr
    -> this server asks Ollama qwen35-fast:latest for strict JSON intent
    -> this server returns transcript + command JSON to Jetson

Windows PowerShell setup:
    py -3.12 -m venv .venv
    .venv\\Scripts\\Activate.ps1
    python -m pip install --upgrade pip
    pip install flask qwen-asr numpy
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    ollama pull qwen35-fast:latest

Run on desktop:
    python desktop_voice_server.py --host 0.0.0.0 --port 8765 --ollama-model qwen35-fast:latest --no-think

Test from Jetson:
    curl http://DESKTOP_IP:8765/health
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from desk_voice_controller import (
    DEFAULT_ASR_MODEL,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_NO_THINK,
    DEFAULT_OLLAMA_URL,
    OllamaCommandParser,
    QwenASRAdapter,
    apply_safety_language_guard,
    safe_unknown,
)


app = Flask(__name__)
asr_adapter: QwenASRAdapter | None = None
ollama_parser: OllamaCommandParser | None = None
min_confidence = DEFAULT_CONFIDENCE_THRESHOLD


def command_response(command: Any, elapsed_ms: int, ok: bool = True, error: str | None = None) -> dict[str, Any]:
    should_execute = bool(
        ok
        and command.intent != "UNKNOWN"
        and command.confidence >= min_confidence
    )
    reason = ""
    if command.intent == "UNKNOWN":
        reason = "intent is UNKNOWN"
    elif command.confidence < min_confidence:
        reason = f"confidence {command.confidence:.2f} < {min_confidence:.2f}"
    elif error:
        reason = error

    return {
        "ok": ok,
        "error": error,
        "transcript": command.transcript,
        "command": command.to_json_dict(),
        "wire_command": command.wire_command,
        "should_execute": should_execute,
        "skip_reason": reason,
        "elapsed_ms": elapsed_ms,
    }


@app.get("/health")
def health() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "desktop_voice_server",
            "asr_loaded": asr_adapter is not None and asr_adapter.model is not None,
            "ollama_ready": ollama_parser is not None,
        }
    )


@app.post("/text-command")
def text_command() -> Any:
    started = time.monotonic()
    data = request.get_json(silent=True) or {}
    transcript = str(data.get("text", "")).strip()
    if not transcript:
        command = safe_unknown("", "missing text")
        return jsonify(command_response(command, elapsed_ms(started), ok=False, error="missing text")), 400

    if ollama_parser is None:
        command = safe_unknown(transcript, "server Ollama parser not initialized")
        return jsonify(command_response(command, elapsed_ms(started), ok=False, error="server not ready")), 503

    command = ollama_parser.parse_text(transcript)
    command = apply_safety_language_guard(command)
    return jsonify(command_response(command, elapsed_ms(started)))


@app.post("/voice-command")
def voice_command() -> Any:
    started = time.monotonic()
    if asr_adapter is None or asr_adapter.model is None or ollama_parser is None:
        command = safe_unknown("", "server ASR/Ollama not initialized")
        return jsonify(command_response(command, elapsed_ms(started), ok=False, error="server not ready")), 503

    upload = request.files.get("audio")
    if upload is None:
        command = safe_unknown("", "missing multipart file field: audio")
        return jsonify(command_response(command, elapsed_ms(started), ok=False, error="missing audio")), 400

    temp_path = save_upload_to_temp_wav(upload)
    try:
        transcript = asr_adapter.transcribe(temp_path).strip()
        if not transcript:
            command = safe_unknown("", "empty ASR transcript")
            return jsonify(command_response(command, elapsed_ms(started)))

        command = ollama_parser.parse_text(transcript)
        command = apply_safety_language_guard(command)
        return jsonify(command_response(command, elapsed_ms(started)))
    except Exception as exc:
        command = safe_unknown("", f"server pipeline failed: {exc}")
        return jsonify(command_response(command, elapsed_ms(started), ok=False, error=str(exc))), 500
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def save_upload_to_temp_wav(upload: Any) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="jetson_voice_", suffix=".wav", delete=False)
    temp_path = Path(handle.name)
    handle.close()
    upload.save(temp_path)
    return temp_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows desktop Qwen3-ASR + Ollama remote voice command server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.set_defaults(no_think=DEFAULT_OLLAMA_NO_THINK)
    parser.add_argument("--no-think", dest="no_think", action="store_true", help="Prefix Ollama prompts with /no_think. Default is on.")
    parser.add_argument("--think", dest="no_think", action="store_false", help="Allow thinking mode for models that support it.")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    parser.add_argument("--skip-asr-load", action="store_true", help="Debug only: do not load ASR; /text-command still works.")
    return parser


def main() -> int:
    global asr_adapter, ollama_parser, min_confidence

    args = build_arg_parser().parse_args()
    min_confidence = args.min_confidence

    ollama_parser = OllamaCommandParser(args.ollama_url, args.ollama_model, no_think=args.no_think)
    ollama_parser.warm_up()

    if not args.skip_asr_load:
        asr_adapter = QwenASRAdapter(args.asr_model)
        asr_adapter.load()
    else:
        print("ASR load skipped. /voice-command will return server not ready.")

    print()
    print(f"Desktop voice server listening on http://{args.host}:{args.port}")
    print("Use Jetson client --server-url http://DESKTOP_IP:8765/voice-command")
    app.run(host=args.host, port=args.port, threaded=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
