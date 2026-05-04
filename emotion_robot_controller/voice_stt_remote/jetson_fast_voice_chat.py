"""
Jetson fast voice chat client.

Purpose:
    Press Enter to start recording, press Enter again to stop.
    Send WAV to desktop_fast_chat_server.py.
    Send the returned reply to jetson_piper_tts for local playback.
    Print:
        1. detailed chat reply
        2. emotion analysis

Run:
    python jetson_fast_voice_chat.py --server-url http://100.108.141.26:8766/voice-chat
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from desk_voice_controller import (
    DEFAULT_RMS_THRESHOLD,
    SAMPLE_RATE,
    list_microphones,
    rms_level,
)
from jetson_remote_voice_client import (
    choose_input_sample_rate,
    format_http_error,
    post_json,
    post_multipart_file,
    record_audio_until_enter,
    write_temp_wav_16k,
)


DEFAULT_SERVER_URL = "http://127.0.0.1:8766/voice-chat"
DEFAULT_TTS_URL = "http://127.0.0.1:8777/speak_async"


def normalize_server_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.endswith("~"):
        fixed = cleaned.rstrip("~")
        print(f"WARNING: --server-url ends with '~'; using {fixed}")
        cleaned = fixed
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned
    path = parsed.path.rstrip("/")
    if path == "":
        fixed = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/voice-chat", "", ""))
        print(f"WARNING: --server-url has no path; using {fixed}")
        return fixed
    if path != "/voice-chat":
        fixed = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        print(f"WARNING: expected --server-url path /voice-chat; current path is {path}")
        return fixed
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def endpoint_url(server_url: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(server_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_tts_url(url: str, *, blocking: bool = False) -> str:
    cleaned = url.strip()
    if cleaned.endswith("~"):
        fixed = cleaned.rstrip("~")
        print(f"WARNING: --tts-url ends with '~'; using {fixed}")
        cleaned = fixed
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned
    desired_path = "/speak" if blocking else "/speak_async"
    path = parsed.path.rstrip("/")
    if path in {"", "/"}:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, desired_path, "", ""))
    if blocking and path == "/speak_async":
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/speak", "", ""))
    if (not blocking) and path == "/speak":
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/speak_async", "", ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def tts_base_url(tts_url: str) -> str:
    parsed = urllib.parse.urlsplit(tts_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def get_json(url: str, timeout_sec: float) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(format_http_error(exc, url)) from exc
    text = raw.decode("utf-8", errors="replace")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"GET {url} did not return a JSON object")
    return parsed


def print_server_summary(health: dict[str, Any]) -> None:
    print("Server health:")
    print(f"  service      : {health.get('service', 'unknown')}")
    print(f"  debug_version: {health.get('debug_version', 'old/unknown')}")
    print(f"  chat_ready   : {health.get('chat_ready', 'unknown')}")
    print(f"  asr_loaded   : {health.get('asr_loaded', 'unknown')}")
    print(f"  ollama_url   : {health.get('ollama_url', 'unknown')}")
    print(f"  ollama_model : {health.get('ollama_model', 'unknown')}")
    last = health.get("last_debug")
    if isinstance(last, dict) and last:
        print(f"  last_request : {last.get('request_id', 'unknown')} stage={last.get('stage', 'unknown')} ok={last.get('ok', 'unknown')}")
        reason = str(last.get("fallback_reason", "")).strip()
        if reason:
            print(f"  last_warning : {reason}")
    if health.get("debug_version") != 6:
        print("  warning      : desktop server is not the latest debug build; copy the new desktop_fast_chat_server.py to Windows and restart it.")


def print_debug_summary(debug: Any, verbose: bool) -> None:
    if not isinstance(debug, dict) or not debug:
        return
    print()
    print("Debug:")
    fields = [
        "request_id",
        "stage",
        "ok",
        "parse_status",
        "fallback_reason",
        "retry_reason",
        "retried_without_no_think",
        "retry_error",
        "generate_retry_reason",
        "generate_url",
        "generate_done",
        "generate_error",
        "ollama_url",
        "ollama_model",
        "think",
        "ollama_response_keys",
        "ollama_message_keys",
        "ollama_message_thinking_chars",
        "ollama_done",
        "retry_done",
        "ollama_content_chars",
        "transcript_chars",
    ]
    for field in fields:
        if field in debug:
            print(f"  {field}: {debug.get(field)}")
    preview = str(debug.get("ollama_content_preview", "")).strip()
    if verbose and preview:
        print("  ollama_content_preview:")
        print(f"    {preview}")


def preflight_server(args: argparse.Namespace) -> bool:
    if args.no_preflight:
        return True
    health_url = endpoint_url(args.server_url, "/health")
    try:
        health = get_json(health_url, timeout_sec=min(args.timeout, 8.0))
    except Exception as exc:
        print(f"ERROR: server health check failed: {exc}")
        print(f"Check Windows server, then try: curl {health_url}")
        return False
    print_server_summary(health)
    if health.get("chat_ready") is not True:
        print("ERROR: desktop server is not chat_ready.")
        return False
    return True


def preflight_tts(args: argparse.Namespace) -> bool:
    if args.no_tts or args.no_tts_preflight:
        return True

    health_url = urllib.parse.urljoin(tts_base_url(args.tts_url) + "/", "health")
    try:
        health = get_json(health_url, timeout_sec=min(args.tts_timeout, 4.0))
    except Exception as exc:
        message = f"TTS health check failed: {exc}"
        if args.require_tts:
            print(f"ERROR: {message}")
            print("Start it with: cd /home/asrlab-yian/MakeNTU/jetson_piper_tts && source .venv/bin/activate && python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777")
            return False
        print(f"WARNING: {message}")
        print("Voice chat will continue, but replies will not be spoken until jetson_piper_tts is running.")
        return True

    ready = bool(health.get("ready"))
    engine = health.get("engine") if isinstance(health.get("engine"), dict) else {}
    audio = health.get("audio") if isinstance(health.get("audio"), dict) else {}
    print("TTS health:")
    print(f"  service : {health.get('service', 'jetson_piper_tts')}")
    print(f"  ready   : {ready}")
    print(f"  url     : {args.tts_url}")
    print(f"  model   : {engine.get('model', 'unknown')}")
    print(f"  audio   : {audio.get('backend', 'unknown')} device={audio.get('device', 'unknown')}")
    if not ready:
        if args.require_tts:
            print("ERROR: TTS server is not ready.")
            return False
        print("WARNING: TTS server is not ready; voice chat will continue without spoken replies.")
    return True


def print_result(response: dict[str, Any], *, verbose_debug: bool = False) -> None:
    transcript = str(response.get("transcript", "")).strip()
    reply = str(response.get("reply", "")).strip()
    request_id = str(response.get("request_id", "")).strip()
    emotion = response.get("emotion", {})
    if not isinstance(emotion, dict):
        emotion = {}
    timing = response.get("timing", {})
    if not isinstance(timing, dict):
        timing = {}

    print()
    print("Transcript:")
    if request_id:
        print(f"request_id={request_id}")
    print(transcript or "(empty)")
    print()
    print("Reply:")
    print(reply or "(no reply)")
    print()
    print("Emotion:")
    print(f"  primary        : {emotion.get('primary', 'unknown')}")
    print(f"  intensity      : {emotion.get('intensity', 'unknown')}")
    print(f"  valence        : {emotion.get('valence', 'unknown')}")
    print(f"  arousal        : {emotion.get('arousal', 'unknown')}")
    print(f"  support_needed : {emotion.get('support_needed', 'unknown')}")
    print(f"  summary        : {emotion.get('summary', '')}")
    fallback_reason = str(response.get("fallback_reason", "")).strip()
    if fallback_reason:
        print()
        print(f"Warning: {fallback_reason}")
    print_debug_summary(response.get("debug"), verbose_debug)
    print()
    print("Timing:")
    if "asr_ms" in timing:
        print(f"  asr_ms   : {timing.get('asr_ms')}")
    if "llm_ms" in timing:
        print(f"  llm_ms   : {timing.get('llm_ms')}")
    print(f"  total_ms : {timing.get('total_ms', response.get('elapsed_ms', 'unknown'))}")


def build_tts_payload(reply: str, args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": reply,
        "interrupt": args.tts_interrupt,
    }
    if args.tts_blocking:
        payload["blocking"] = True
    if args.tts_voice:
        payload["voice"] = args.tts_voice
    if args.tts_length_scale is not None:
        payload["length_scale"] = args.tts_length_scale
    if args.tts_stream is not None:
        payload["stream"] = args.tts_stream
    return payload


def speak_reply(response: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    if args.no_tts:
        return None
    reply = str(response.get("reply", "")).strip()
    if not reply:
        return None

    payload = build_tts_payload(reply, args)
    started = time.monotonic()
    try:
        result = post_json(args.tts_url, payload, timeout_sec=args.tts_timeout)
    except Exception as exc:
        print(f"WARNING: TTS speak failed: {exc}")
        return None

    elapsed = int((time.monotonic() - started) * 1000)
    result["_client_post_ms"] = elapsed
    if args.tts_debug:
        print()
        print("TTS:")
        print(f"  url          : {args.tts_url}")
        print(f"  post_ms      : {elapsed}")
        print(f"  queued       : {result.get('queued', False)}")
        if "job_id" in result:
            print(f"  job_id       : {result.get('job_id')}")
        playback = result.get("playback") if isinstance(result.get("playback"), dict) else {}
        if playback:
            print(f"  mode         : {playback.get('mode', 'unknown')}")
            print(f"  producer     : {playback.get('producer', 'unknown')}")
            print(f"  streaming    : {playback.get('streaming', 'unknown')}")
    return result


def run_text_mode(args: argparse.Namespace) -> int:
    if not preflight_server(args):
        return 1
    if not preflight_tts(args):
        return 1
    text_url = args.server_url.replace("/voice-chat", "/text-chat")
    print(f"POST text to {text_url}")
    try:
        response = post_json(text_url, {"text": args.text}, timeout_sec=args.timeout)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    speak_reply(response, args)
    print_result(response, verbose_debug=args.debug)
    return 0


def run_voice_loop(args: argparse.Namespace) -> int:
    if not preflight_server(args):
        return 1
    if not preflight_tts(args):
        return 1
    try:
        input_sample_rate = choose_input_sample_rate(args.device, args.input_sample_rate)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Fast voice chat ready.")
    print(f"Server URL: {args.server_url}")
    print(f"Input sample rate: {input_sample_rate} Hz; upload WAV sample rate: {SAMPLE_RATE} Hz")
    print("Type q then Enter to quit. Otherwise press Enter to start, then press Enter again to stop.")

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
            audio = record_audio_until_enter(args.device, input_sample_rate, args.max_seconds)
            rms = rms_level(audio)
            print(f"RMS={rms:.5f}")
            if rms < args.rms_threshold:
                print("SKIP: audio RMS too low; not sending.")
                continue

            wav_path = write_temp_wav_16k(audio, input_sample_rate)
            print(f"POST audio to {args.server_url}")
            started = time.monotonic()
            response = post_multipart_file(args.server_url, "audio", wav_path, timeout_sec=args.timeout)
            print(f"Round trip: {int((time.monotonic() - started) * 1000)} ms")
            speak_reply(response, args)
            print_result(response, verbose_debug=args.debug)
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jetson client for fast remote voice chat + emotion analysis.")
    parser.add_argument("--list-mics", action="store_true")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--text", help="Send text to desktop /text-chat instead of recording audio.")
    parser.add_argument("--device", type=int, default=None, help="sounddevice input device index. Omit to use the default microphone.")
    parser.add_argument("--input-sample-rate", type=int, default=None)
    parser.add_argument("--rms-threshold", type=float, default=DEFAULT_RMS_THRESHOLD)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-seconds", type=float, default=45.0)
    parser.add_argument("--debug", action="store_true", help="Print server debug payloads and Ollama response snippets.")
    parser.add_argument("--no-preflight", action="store_true", help="Skip the startup GET /health check.")
    parser.add_argument("--check-server", action="store_true", help="Run /health, /debug, and a text-chat smoke test, then exit.")
    parser.add_argument("--tts-url", default=DEFAULT_TTS_URL, help="Jetson Piper TTS endpoint. Use /speak_async for low latency.")
    parser.add_argument("--no-tts", action="store_true", help="Do not speak the reply with jetson_piper_tts.")
    parser.add_argument("--require-tts", action="store_true", help="Exit if jetson_piper_tts is not reachable/ready.")
    parser.add_argument("--no-tts-preflight", action="store_true", help="Skip TTS /health check at startup.")
    parser.add_argument("--tts-timeout", type=float, default=5.0, help="Timeout for posting replies to TTS.")
    parser.add_argument("--tts-blocking", action="store_true", help="Use /speak and wait for playback instead of /speak_async.")
    parser.add_argument("--tts-no-interrupt", action="store_true", help="Queue replies instead of interrupting current TTS playback.")
    parser.add_argument("--tts-voice", help="Optional Piper voice name, e.g. zh_CN-xiao_ya-medium.")
    parser.add_argument("--tts-length-scale", type=float, default=None, help="Optional Piper length_scale. Lower is faster.")
    parser.add_argument("--tts-file-playback", action="store_true", help="Ask TTS server to use WAV/file playback instead of raw streaming.")
    parser.add_argument("--tts-debug", action="store_true", help="Print TTS enqueue/playback debug summary.")
    return parser


def run_check_server(args: argparse.Namespace) -> int:
    health_url = endpoint_url(args.server_url, "/health")
    debug_url = endpoint_url(args.server_url, "/debug")
    text_url = endpoint_url(args.server_url, "/text-chat")
    try:
        health = get_json(health_url, timeout_sec=min(args.timeout, 8.0))
        print_server_summary(health)
    except Exception as exc:
        print(f"ERROR: /health failed: {exc}")
        return 1

    try:
        debug = get_json(debug_url, timeout_sec=min(args.timeout, 8.0))
        last = debug.get("last_debug") if isinstance(debug, dict) else None
        print_debug_summary(last, verbose=True)
    except Exception as exc:
        print(f"WARNING: /debug failed: {exc}")

    if not preflight_tts(args):
        return 1

    print()
    print(f"POST text smoke test to {text_url}")
    try:
        response = post_json(text_url, {"text": args.text or "debug ping：自然回我一句話。"}, timeout_sec=args.timeout)
    except Exception as exc:
        print(f"ERROR: text smoke test failed: {exc}")
        return 1
    print_result(response, verbose_debug=True)
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    args.server_url = normalize_server_url(args.server_url)
    args.tts_url = normalize_tts_url(args.tts_url, blocking=args.tts_blocking)
    args.tts_interrupt = not args.tts_no_interrupt
    args.tts_stream = False if args.tts_file_playback else None
    if args.list_mics:
        list_microphones()
        return 0
    if args.check_server:
        return run_check_server(args)
    if args.text:
        return run_text_mode(args)
    return run_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
