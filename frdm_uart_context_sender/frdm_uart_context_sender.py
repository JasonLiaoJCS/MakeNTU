#!/usr/bin/env python3
"""
Small FRDM MCXN947 UART sender for the current SMONITORCOMMAND table.

It intentionally supports only these commands:
    Sleep
    Normal
    ShowNum <0..999999>
    MotorPitch <0..180>
    MotorYaw <0..180>

The tool can read the current chat context, build uart.json, print the reply,
and then send the selected UART lines to FRDM over USB serial.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUDRATE = 115200
DEFAULT_OUTPUT = "uart.json"
DEFAULT_LINE_ENDING = "crlf"

COMMAND_ARITY = {
    "Sleep": 0,
    "Normal": 0,
    "ShowNum": 1,
    "MotorPitch": 1,
    "MotorYaw": 1,
}

SLEEP_WORDS = {
    "sleep",
    "sleepy",
    "tired",
    "quiet",
    "away",
    "睡",
    "睡覺",
    "睡觉",
    "休息",
    "安靜",
    "安静",
    "離席",
    "离席",
    "累",
    "困",
    "打瞌睡",
}

NORMAL_WORDS = {
    "normal",
    "wake",
    "awake",
    "醒",
    "回來",
    "回来",
    "正常",
    "一般",
    "工作",
    "開始",
    "开始",
    "聊天",
    "你好",
    "hello",
}

LEFT_WORDS = {"left", "左", "左邊", "左边", "往左", "看左"}
RIGHT_WORDS = {"right", "右", "右邊", "右边", "往右", "看右"}
CENTER_WORDS = {"center", "centre", "中間", "中间", "正面", "置中"}
UP_WORDS = {"up", "抬頭", "抬头", "往上", "看上", "上面"}
DOWN_WORDS = {"down", "低頭", "低头", "往下", "看下", "下面"}


@dataclass(frozen=True)
class UartCommand:
    name: str
    value: int | None = None
    reason: str = ""

    def wire(self) -> str:
        if self.value is None:
            return self.name
        return f"{self.name} {self.value}"

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "wire": self.wire(),
        }
        if self.value is not None:
            data["value"] = self.value
        if self.reason:
            data["reason"] = self.reason
        return data


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_command_name(name: str) -> str:
    aliases = {
        "sleep": "Sleep",
        "normal": "Normal",
        "shownum": "ShowNum",
        "show_num": "ShowNum",
        "show-number": "ShowNum",
        "motorpitch": "MotorPitch",
        "pitch": "MotorPitch",
        "motor_yaw": "MotorYaw",
        "motoryaw": "MotorYaw",
        "yaw": "MotorYaw",
    }
    compact = re.sub(r"[\s_-]+", "", name.strip()).lower()
    return aliases.get(compact, name.strip())


def parse_command(raw: str | dict[str, Any], reason: str = "explicit command") -> UartCommand:
    if isinstance(raw, dict):
        name = normalize_command_name(str(raw.get("name", raw.get("command", ""))))
        value = raw.get("value", raw.get("arg", raw.get("args")))
        if isinstance(value, list):
            value = value[0] if value else None
    else:
        parts = str(raw).strip().split()
        name = normalize_command_name(parts[0] if parts else "")
        value = parts[1] if len(parts) > 1 else None

    if name not in COMMAND_ARITY:
        raise ValueError(f"Unsupported FRDM command: {name!r}")

    arity = COMMAND_ARITY[name]
    if arity == 0:
        return UartCommand(name=name, reason=reason)

    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} needs one integer value")

    try:
        number = int(float(str(value).strip()))
    except ValueError as exc:
        raise ValueError(f"{name} value must be an integer: {value!r}") from exc

    if name in {"MotorPitch", "MotorYaw"}:
        number = clamp(number, 0, 180)
    elif name == "ShowNum":
        number = clamp(number, 0, 999999)

    return UartCommand(name=name, value=number, reason=reason)


def payload_text(payload: dict[str, Any]) -> str:
    parts = [
        as_text(payload.get("transcript")),
        as_text(payload.get("reply")),
        as_text(payload.get("text")),
        as_text(payload.get("context")),
    ]
    emotion = payload.get("emotion")
    if isinstance(emotion, dict):
        parts.extend(as_text(emotion.get(key)) for key in ("primary", "summary"))
    else:
        parts.append(as_text(emotion))
    return " ".join(part for part in parts if part).strip()


def emotion_primary(payload: dict[str, Any]) -> str:
    emotion = payload.get("emotion")
    if isinstance(emotion, dict):
        return str(emotion.get("primary", "")).strip().lower()
    return str(emotion or "").strip().lower()


def context_dict(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("context", {})
    return context if isinstance(context, dict) else {}


def first_int_from_keys(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    context = context_dict(payload)
    for source in (context, payload):
        for key in keys:
            if key in source and source[key] is not None and str(source[key]).strip() != "":
                try:
                    return int(float(str(source[key]).strip()))
                except ValueError:
                    return None
    return None


def regex_int(text: str, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def has_any(text: str, words: set[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def has_center_context(text: str) -> bool:
    if has_any(text, CENTER_WORDS):
        return True
    # Avoid matching "回正常" as "回正".
    return re.search(r"回正(?!常)", text) is not None


def add_unique(commands: list[UartCommand], command: UartCommand) -> None:
    if any(existing.name == command.name and existing.value == command.value for existing in commands):
        return
    commands.append(command)


def explicit_commands(payload: dict[str, Any]) -> list[UartCommand]:
    raw_commands = payload.get("commands")
    if raw_commands is None:
        raw_commands = payload.get("uart")
    if raw_commands is None:
        return []
    if isinstance(raw_commands, (str, dict)):
        raw_commands = [raw_commands]
    if not isinstance(raw_commands, list):
        raise ValueError("commands/uart must be a string, object, or list")
    return [parse_command(item) for item in raw_commands]


def decide_commands(payload: dict[str, Any], *, default_normal: bool = True) -> list[UartCommand]:
    manual = explicit_commands(payload)
    if manual:
        return manual

    text = payload_text(payload)
    context = context_dict(payload)
    mode = str(context.get("mode", payload.get("mode", ""))).strip().lower()
    emotion = emotion_primary(payload)
    commands: list[UartCommand] = []

    if mode in {"sleep", "sleepy", "quiet", "away"}:
        add_unique(commands, UartCommand("Sleep", reason="context.mode requests sleep"))
    elif mode in {"normal", "wake", "awake"}:
        add_unique(commands, UartCommand("Normal", reason="context.mode requests normal"))
    elif emotion in {"sleepy", "tired"} or has_any(text, SLEEP_WORDS):
        add_unique(commands, UartCommand("Sleep", reason="sleep/tired context"))
    elif has_any(text, NORMAL_WORDS):
        add_unique(commands, UartCommand("Normal", reason="normal/wake context"))
    elif default_normal:
        add_unique(commands, UartCommand("Normal", reason="default state"))

    pitch = first_int_from_keys(payload, ("pitch", "motor_pitch", "MotorPitch"))
    if pitch is None:
        pitch = regex_int(
            text,
            (
                r"(?:MotorPitch|motor_pitch|pitch|俯仰|抬頭|抬头|低頭|低头)\s*[:= ]\s*(\d{1,3})",
            ),
        )
    if pitch is None:
        if has_any(text, UP_WORDS):
            pitch = 60
        elif has_any(text, DOWN_WORDS):
            pitch = 120
    if pitch is not None:
        add_unique(commands, UartCommand("MotorPitch", clamp(pitch, 0, 180), reason="pitch context"))

    yaw = first_int_from_keys(payload, ("yaw", "motor_yaw", "MotorYaw"))
    if yaw is None:
        yaw = regex_int(
            text,
            (
                r"(?:MotorYaw|motor_yaw|yaw|左右|轉頭|转头)\s*[:= ]\s*(\d{1,3})",
            ),
        )
    if yaw is None:
        if has_any(text, LEFT_WORDS):
            yaw = 60
        elif has_any(text, RIGHT_WORDS):
            yaw = 120
        elif has_center_context(text):
            yaw = 90
    if yaw is not None:
        add_unique(commands, UartCommand("MotorYaw", clamp(yaw, 0, 180), reason="yaw context"))

    show_num = first_int_from_keys(payload, ("show_num", "show_number", "number", "ShowNum"))
    if show_num is None:
        show_num = regex_int(
            text,
            (
                r"(?:ShowNum|show_num|show number|顯示數字|显示数字|顯示|显示)\s*[:= ]?\s*(\d{1,6})",
            ),
        )
    if show_num is not None:
        add_unique(commands, UartCommand("ShowNum", clamp(show_num, 0, 999999), reason="show number context"))

    return commands


def build_uart_json(payload: dict[str, Any], commands: list[UartCommand]) -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": now_iso(),
        "target": "FRDM-MCXN947",
        "transport": "usb-serial",
        "allowed_commands": list(COMMAND_ARITY.keys()),
        "reply": as_text(payload.get("reply")).strip(),
        "transcript": as_text(payload.get("transcript", payload.get("text"))).strip(),
        "emotion": payload.get("emotion"),
        "commands": [command.to_json() for command in commands],
    }


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.input:
        with Path(args.input).open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    elif args.stdin:
        raw = sys.stdin.read().strip()
        loaded = json.loads(raw) if raw else {}
    else:
        loaded = {
            "transcript": args.text or "",
            "reply": args.reply or "",
            "emotion": {"primary": args.emotion} if args.emotion else {},
            "context": {},
        }

    if not isinstance(loaded, dict):
        raise ValueError("input JSON must be an object")

    if args.command:
        loaded["commands"] = args.command
    if args.text:
        loaded["transcript"] = args.text
    if args.reply:
        loaded["reply"] = args.reply
    if args.emotion:
        loaded["emotion"] = {"primary": args.emotion}
    return loaded


def write_uart_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_uart(
    commands: list[UartCommand],
    *,
    port: str,
    baudrate: int,
    timeout: float,
    line_ending: bytes,
    read_ms: int,
    delay_ms: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if dry_run:
        for command in commands:
            results.append({"tx": command.wire(), "rx": [], "dry_run": True})
        return results

    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pyserial. Install with: python -m pip install pyserial") from exc

    with serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout) as ser:
        time.sleep(0.1)
        ser.reset_input_buffer()
        for command in commands:
            wire = command.wire()
            ser.write(wire.encode("utf-8") + line_ending)
            ser.flush()

            rx_lines: list[str] = []
            deadline = time.monotonic() + read_ms / 1000.0
            while time.monotonic() < deadline:
                line = ser.readline()
                if line:
                    rx_lines.append(line.decode("utf-8", errors="replace").rstrip())

            results.append({"tx": wire, "rx": rx_lines})
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decide and send simple FRDM UART commands from current context.")
    parser.add_argument("--input", help="Read context JSON from a file.")
    parser.add_argument("--stdin", action="store_true", help="Read context JSON from stdin.")
    parser.add_argument("--text", help="Transcript/current user text.")
    parser.add_argument("--reply", help="Assistant reply to print before UART TX.")
    parser.add_argument("--emotion", help="Simple emotion primary, e.g. neutral, sleepy, tired.")
    parser.add_argument("--command", action="append", help='Manual command override, e.g. "Sleep" or "MotorPitch 90". Can repeat.')
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Where to write uart.json.")
    parser.add_argument("--no-write-json", action="store_true", help="Do not write uart.json.")
    parser.add_argument("--no-default-normal", action="store_true", help="Do not send Normal when no other state is detected.")
    parser.add_argument("--port", default=DEFAULT_PORT, help="USB serial port, e.g. /dev/ttyACM0 or /dev/ttyUSB0.")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--read-ms", type=int, default=250, help="Read FRDM replies for this many ms after each TX.")
    parser.add_argument("--delay-ms", type=int, default=80, help="Delay between UART commands.")
    parser.add_argument("--line-ending", choices=["lf", "crlf"], default=DEFAULT_LINE_ENDING)
    parser.add_argument("--dry-run", action="store_true", help="Build uart.json and print TX, but do not open serial.")
    parser.add_argument("--quiet", action="store_true", help="Only print JSON result.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = load_payload(args)
        commands = decide_commands(payload, default_normal=not args.no_default_normal)
        uart_doc = build_uart_json(payload, commands)
        if not args.no_write_json:
            write_uart_json(Path(args.output), uart_doc)

        reply = uart_doc.get("reply", "")
        if not args.quiet and reply:
            print("Reply:")
            print(reply)
            print()

        line_ending = b"\r\n" if args.line_ending == "crlf" else b"\n"
        results = send_uart(
            commands,
            port=args.port,
            baudrate=args.baudrate,
            timeout=args.timeout,
            line_ending=line_ending,
            read_ms=args.read_ms,
            delay_ms=args.delay_ms,
            dry_run=args.dry_run,
        )
        uart_doc["serial"] = {
            "port": args.port,
            "baudrate": args.baudrate,
            "line_ending": args.line_ending,
            "dry_run": args.dry_run,
            "results": results,
        }
        if not args.no_write_json:
            write_uart_json(Path(args.output), uart_doc)

        if args.quiet:
            print(json.dumps(uart_doc, ensure_ascii=False))
        else:
            print(f"uart_json: {args.output if not args.no_write_json else '(not written)'}")
            for result in results:
                print(f"TX: {result['tx']}")
                for line in result.get("rx", []):
                    print(f"RX: {line}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
