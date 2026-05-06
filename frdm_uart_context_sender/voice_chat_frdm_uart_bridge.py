#!/usr/bin/env python3
"""
Standalone Jetson voice chat -> FRDM UART bridge.

This file is intentionally placed in frdm_uart_context_sender/ so the original
voice_stt_remote code stays untouched.

Flow:
    Jetson record / text input
    -> Windows desktop /voice-chat or /text-chat
    -> print Transcript / Reply / Emotion / Timing
    -> decide the current FRDM UART commands
    -> write uart.json
    -> send UART to FRDM MCXN947
    -> optionally send reply to jetson_piper_tts
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
VOICE_DIR = PROJECT_ROOT / "emotion_robot_controller" / "voice_stt_remote"

if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

try:
    import jetson_fast_voice_chat as voice_chat
except ImportError as exc:
    raise SystemExit(
        "ERROR: cannot import emotion_robot_controller/voice_stt_remote/jetson_fast_voice_chat.py. "
        f"Expected it at: {VOICE_DIR}"
    ) from exc

from frdm_uart_context_sender import (  # noqa: E402
    DEFAULT_BAUDRATE,
    DEFAULT_LINE_ENDING,
    DEFAULT_PORT,
    build_uart_json,
    decide_commands,
    send_uart,
    write_uart_json,
)


DEFAULT_UART_OUTPUT = str(THIS_DIR / "uart.json")
DEFAULT_TTS_VOICE = "zh_CN-xiao_ya-medium"
DEFAULT_MIC_KEYWORD = os.getenv("MIC_DEVICE_KEYWORD", "UACDemo")
UART_PREFERRED_KEYWORDS = (
    "frdm",
    "mcu",
    "cmsis",
    "dap",
    "nxp",
    "j-link",
    "linkserver",
    "mbed",
)


def _input_device_info(device_index: int | None) -> dict[str, Any] | None:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Missing dependency: sounddevice. Install it in the voice venv.") from exc

    try:
        info = sd.query_devices(device_index, "input")
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def find_input_device_by_keyword(keyword: str) -> int | None:
    if not keyword.strip():
        return None
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Missing dependency: sounddevice. Install it in the voice venv.") from exc

    lowered = keyword.strip().lower()
    for index, device in enumerate(sd.query_devices()):
        if not isinstance(device, dict):
            continue
        name = str(device.get("name", ""))
        if lowered in name.lower() and int(device.get("max_input_channels", 0)) > 0:
            return index
    return None


def resolve_input_device(args: argparse.Namespace) -> int | None:
    """Prefer the named USB mic so ALSA index changes do not break demos."""
    keyword = str(getattr(args, "mic_keyword", DEFAULT_MIC_KEYWORD) or "").strip()
    no_fallback = bool(getattr(args, "no_mic_fallback", False))

    if args.device is not None:
        info = _input_device_info(args.device)
        if info is not None:
            return args.device

        print(f"WARNING: --device {args.device} is not a microphone input.")
        if no_fallback:
            return args.device

    if keyword and not no_fallback:
        found = find_input_device_by_keyword(keyword)
        if found is not None:
            if args.device is None:
                print(f"Selected input device {found} by keyword {keyword!r}.")
            else:
                print(f"Falling back to input device {found} by keyword {keyword!r}.")
            return found
        if args.device is not None:
            print(f"WARNING: no input device contains keyword {keyword!r}; keeping --device {args.device}.")
            return args.device

    return args.device


def response_to_uart_payload(response: dict[str, Any]) -> dict[str, Any]:
    """Keep only the current context that matters to FRDM decision making."""
    payload: dict[str, Any] = {
        "request_id": response.get("request_id"),
        "transcript": str(response.get("transcript", "")).strip(),
        "reply": str(response.get("reply", "")).strip(),
        "emotion": response.get("emotion") if isinstance(response.get("emotion"), dict) else {},
        "timing": response.get("timing") if isinstance(response.get("timing"), dict) else {},
    }

    # Allow the desktop server or a test JSON to explicitly request a simple FRDM
    # command without adding another layer of AI/rule logic here.
    for key in ("context", "mode", "commands", "uart", "pitch", "yaw", "show_num", "number"):
        if key in response:
            payload[key] = response[key]
    return payload


def line_ending_bytes(name: str) -> bytes:
    return b"\r\n" if name == "crlf" else b"\n"


def discover_uart_ports() -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        from serial.tools import list_ports

        for port in list_ports.comports():
            device = str(port.device)
            text = " ".join(
                str(value)
                for value in (
                    port.device,
                    port.description,
                    port.manufacturer,
                    port.product,
                    port.hwid,
                )
                if value
            )
            lowered = text.lower()
            ports.append(
                {
                    "device": device,
                    "description": port.description or "",
                    "hwid": port.hwid or "",
                    "preferred": any(keyword in lowered for keyword in UART_PREFERRED_KEYWORDS),
                }
            )
            seen.add(device)
    except Exception:
        pass

    for pattern in ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            device = str(path)
            if device in seen:
                continue
            ports.append({"device": device, "description": "", "hwid": "", "preferred": False})
            seen.add(device)

    return sorted(ports, key=lambda item: (not bool(item.get("preferred")), str(item.get("device", ""))))


def print_uart_ports() -> None:
    ports = discover_uart_ports()
    print("UART serial ports:")
    if not ports:
        print("  (none)")
        print("  Plug the FRDM debug/USB-serial cable into the Jetson, then run this again.")
        return
    for port in ports:
        marker = "*" if port.get("preferred") else " "
        description = str(port.get("description", "")).strip()
        hwid = str(port.get("hwid", "")).strip()
        detail = " ".join(part for part in (description, hwid) if part)
        suffix = f"  {detail}" if detail else ""
        print(f" {marker} {port['device']}{suffix}")


def resolve_uart_port(port: str) -> str:
    requested = str(port or "").strip()
    if requested and requested.lower() != "auto" and Path(requested).exists():
        return requested

    ports = discover_uart_ports()
    preferred = [item for item in ports if item.get("preferred")]
    candidates = preferred or ports

    if len(candidates) == 1:
        selected = str(candidates[0]["device"])
        reason = "preferred match" if preferred else "only serial port"
        if requested.lower() == "auto" or requested != selected:
            print(f"Selected FRDM UART {selected} ({reason}).")
        return selected

    if requested and requested.lower() != "auto" and not requested.startswith("/dev/"):
        return requested

    if not ports:
        raise RuntimeError(
            f"No UART serial device is visible; requested {requested or 'auto'}. "
            "Plug the FRDM debug/USB-serial cable into the Jetson, then check "
            "`python3 wake_voice_chat_frdm_bridge.py --list-uarts`."
        )

    details = ", ".join(str(item["device"]) for item in ports)
    raise RuntimeError(
        f"Could not choose a UART port automatically; requested {requested or 'auto'}. "
        f"Candidates: {details}. Pass the right one with --uart-port."
    )


def print_uart_summary(
    uart_doc: dict[str, Any],
    *,
    output: str,
    wrote_json: bool,
    debug: bool,
) -> None:
    print()
    print("FRDM UART:")
    print(f"  uart_json   : {output if wrote_json else '(not written)'}")
    serial = uart_doc.get("serial") if isinstance(uart_doc.get("serial"), dict) else {}
    print(f"  port        : {serial.get('port', 'unknown')}")
    print(f"  baudrate    : {serial.get('baudrate', 'unknown')}")
    print(f"  line_ending : {serial.get('line_ending', 'unknown')}")
    print(f"  dry_run     : {serial.get('dry_run', False)}")

    commands = uart_doc.get("commands") if isinstance(uart_doc.get("commands"), list) else []
    if not commands:
        print("  commands    : (none)")
        return

    by_wire = {
        item.get("wire"): item
        for item in commands
        if isinstance(item, dict) and isinstance(item.get("wire"), str)
    }
    results = serial.get("results") if isinstance(serial.get("results"), list) else []
    for result in results:
        if not isinstance(result, dict):
            continue
        tx = str(result.get("tx", "")).strip()
        print(f"  TX          : {tx}")
        if debug:
            command = by_wire.get(tx, {})
            reason = command.get("reason") if isinstance(command, dict) else ""
            if reason:
                print(f"    reason    : {reason}")
        for line in result.get("rx", []):
            print(f"  RX          : {line}")


def send_frdm_for_response(response: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.no_uart:
        return True

    payload = response_to_uart_payload(response)
    commands = decide_commands(payload, default_normal=not args.no_default_normal)
    uart_doc = build_uart_json(payload, commands)
    output_path = Path(args.uart_output)

    try:
        if not args.no_write_uart_json:
            write_uart_json(output_path, uart_doc)

        started = time.monotonic()
        results: list[dict[str, Any]]
        if commands:
            uart_port = args.uart_port if args.uart_dry_run else resolve_uart_port(args.uart_port)
            results = send_uart(
                commands,
                port=uart_port,
                baudrate=args.uart_baudrate,
                timeout=args.uart_timeout,
                line_ending=line_ending_bytes(args.uart_line_ending),
                read_ms=args.uart_read_ms,
                delay_ms=args.uart_delay_ms,
                dry_run=args.uart_dry_run,
            )
        else:
            uart_port = args.uart_port
            results = []

        uart_doc["serial"] = {
            "port": uart_port,
            "baudrate": args.uart_baudrate,
            "line_ending": args.uart_line_ending,
            "dry_run": args.uart_dry_run,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "results": results,
        }
        if not args.no_write_uart_json:
            write_uart_json(output_path, uart_doc)

        print_uart_summary(
            uart_doc,
            output=str(output_path),
            wrote_json=not args.no_write_uart_json,
            debug=args.uart_debug,
        )
        return True
    except Exception as exc:
        print()
        print(f"WARNING: FRDM UART failed: {exc}")
        if args.require_uart:
            return False
        return True


def handle_chat_response(response: dict[str, Any], args: argparse.Namespace, *, verbose_debug: bool = False) -> bool:
    voice_chat.print_result(response, verbose_debug=verbose_debug)
    if not send_frdm_for_response(response, args):
        return False
    voice_chat.speak_reply(response, args)
    return True


def print_zero_rms_hint(args: argparse.Namespace) -> None:
    print("HINT: RMS is exactly zero, so the selected microphone is probably not receiving audio.")
    if args.device is None:
        print("      Run `python3 voice_chat_frdm_uart_bridge.py --list-mics` and use `--mic-keyword UACDemo`, or choose an input index.")
    else:
        print(f"      Current --device is {args.device}; check mic mute/gain/cable, or retry with `--mic-keyword UACDemo`.")


def run_text_mode(args: argparse.Namespace) -> int:
    if not voice_chat.preflight_server(args):
        return 1
    if not voice_chat.preflight_tts(args):
        return 1

    text_url = args.server_url.replace("/voice-chat", "/text-chat")
    print(f"POST text to {text_url}")
    try:
        response = voice_chat.post_json(text_url, {"text": args.text}, timeout_sec=args.timeout)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0 if handle_chat_response(response, args, verbose_debug=args.debug) else 1


def apply_default_tts_voice(args: argparse.Namespace) -> None:
    if not getattr(args, "tts_voice", None):
        args.tts_voice = DEFAULT_TTS_VOICE


def run_voice_loop(args: argparse.Namespace) -> int:
    if not voice_chat.preflight_server(args):
        return 1
    if not voice_chat.preflight_tts(args):
        return 1

    args.device = resolve_input_device(args)
    try:
        input_sample_rate = voice_chat.choose_input_sample_rate(args.device, args.input_sample_rate)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Fast voice chat + FRDM UART bridge ready.")
    print("AI path: Jetson record -> Windows desktop local /voice-chat -> local ASR/Ollama.")
    print("No Gemini/OpenAI cloud API is used by this bridge.")
    print(f"Server URL: {args.server_url}")
    print(f"FRDM UART: {args.uart_port} @ {args.uart_baudrate}, line_ending={args.uart_line_ending}")
    print(f"Input sample rate: {input_sample_rate} Hz; upload WAV sample rate: {voice_chat.SAMPLE_RATE} Hz")
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
            audio = voice_chat.record_audio_until_enter(args.device, input_sample_rate, args.max_seconds)
            rms = voice_chat.rms_level(audio)
            print(f"RMS={rms:.5f}")
            if rms == 0:
                print_zero_rms_hint(args)
            if rms < args.rms_threshold:
                print("SKIP: audio RMS too low; not sending.")
                continue

            wav_path = voice_chat.write_temp_wav_16k(audio, input_sample_rate)
            print(f"POST audio to {args.server_url}")
            started = time.monotonic()
            response = voice_chat.post_multipart_file(args.server_url, "audio", wav_path, timeout_sec=args.timeout)
            print(f"Round trip: {int((time.monotonic() - started) * 1000)} ms")
            if not handle_chat_response(response, args, verbose_debug=args.debug):
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


def run_check_server(args: argparse.Namespace) -> int:
    health_url = voice_chat.endpoint_url(args.server_url, "/health")
    debug_url = voice_chat.endpoint_url(args.server_url, "/debug")
    text_url = voice_chat.endpoint_url(args.server_url, "/text-chat")

    try:
        health = voice_chat.get_json(health_url, timeout_sec=min(args.timeout, 8.0))
        voice_chat.print_server_summary(health)
    except Exception as exc:
        print(f"ERROR: /health failed: {exc}")
        return 1

    try:
        debug = voice_chat.get_json(debug_url, timeout_sec=min(args.timeout, 8.0))
        last = debug.get("last_debug") if isinstance(debug, dict) else None
        voice_chat.print_debug_summary(last, verbose=True)
    except Exception as exc:
        print(f"WARNING: /debug failed: {exc}")

    if not voice_chat.preflight_tts(args):
        return 1

    print()
    print(f"POST text smoke test to {text_url}")
    try:
        response = voice_chat.post_json(text_url, {"text": args.text or "debug ping：自然回我一句話。"}, timeout_sec=args.timeout)
    except Exception as exc:
        print(f"ERROR: text smoke test failed: {exc}")
        return 1
    return 0 if handle_chat_response(response, args, verbose_debug=True) else 1


def add_uart_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.description = "Standalone Jetson fast voice chat + FRDM MCXN947 UART bridge."
    mic_group = parser.add_argument_group("microphone selection")
    mic_group.add_argument(
        "--mic-keyword",
        default=DEFAULT_MIC_KEYWORD,
        help="Auto-select the first input device whose name contains this keyword. Default: UACDemo.",
    )
    mic_group.add_argument(
        "--no-mic-fallback",
        action="store_true",
        help="Do not fall back to --mic-keyword when --device is missing or invalid.",
    )

    group = parser.add_argument_group("FRDM UART bridge")
    group.add_argument("--no-uart", action="store_true", help="Do not write uart.json or send UART.")
    group.add_argument("--require-uart", action="store_true", help="Exit with error if UART send fails.")
    group.add_argument("--uart-dry-run", action="store_true", help="Build uart.json and print TX, but do not open serial.")
    group.add_argument("--uart-port", default=DEFAULT_PORT, help="FRDM USB serial port, e.g. /dev/ttyACM0, /dev/ttyUSB0, or auto.")
    group.add_argument("--list-uarts", action="store_true", help="List visible UART serial ports and exit.")
    group.add_argument("--uart-baudrate", type=int, default=DEFAULT_BAUDRATE)
    group.add_argument("--uart-timeout", type=float, default=1.0)
    group.add_argument("--uart-read-ms", type=int, default=300, help="Read FRDM replies for this many ms after each TX.")
    group.add_argument("--uart-delay-ms", type=int, default=80, help="Delay between multiple UART commands.")
    group.add_argument("--uart-line-ending", choices=["lf", "crlf"], default=DEFAULT_LINE_ENDING)
    group.add_argument("--uart-output", default=DEFAULT_UART_OUTPUT, help="Where to write uart.json.")
    group.add_argument("--no-write-uart-json", action="store_true", help="Do not write uart.json.")
    group.add_argument("--no-default-normal", action="store_true", help="Do not send Normal when no other state is detected.")
    group.add_argument("--uart-debug", action="store_true", help="Print command reasons in the UART summary.")
    return parser


def build_arg_parser() -> argparse.ArgumentParser:
    return add_uart_args(voice_chat.build_arg_parser())


def main() -> int:
    args = build_arg_parser().parse_args()
    args.server_url = voice_chat.normalize_server_url(args.server_url)
    args.tts_url = voice_chat.normalize_tts_url(args.tts_url, blocking=args.tts_blocking)
    apply_default_tts_voice(args)
    args.tts_interrupt = not args.tts_no_interrupt
    args.tts_stream = False if args.tts_file_playback else None

    if args.list_uarts:
        print_uart_ports()
        return 0
    if args.list_mics:
        voice_chat.list_microphones()
        return 0
    if args.check_server:
        return run_check_server(args)
    if args.text:
        return run_text_mode(args)
    return run_voice_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
