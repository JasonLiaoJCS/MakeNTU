#!/usr/bin/env python3
"""
Standalone ESP32 temperature receiver test.

Run this on the Jetson to verify that an ESP32 can send DS18B20 readings over
the same LAN. This script does not touch Weather, UART, FRDM, TTS, or the wake
bridge. It only prints received temperature values.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import urllib.parse
from typing import Any


TEMPERATURE_KEYS = ("temperature_c", "temp_c", "temperatureC", "temperature", "temp", "value")


def normalize_path(path: str) -> str:
    value = str(path or "/temperature").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/") or "/temperature"


def coerce_temperature_c(value: Any) -> float | None:
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
        for key in TEMPERATURE_KEYS:
            if key in payload:
                temp_c = coerce_temperature_c(payload[key])
                if temp_c is not None:
                    return temp_c
        return None

    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            return extract_temperature_c(json.loads(text))
        except json.JSONDecodeError:
            pass
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return coerce_temperature_c(match.group(0)) if match else None

    return coerce_temperature_c(payload)


class TemperatureReceiver(BaseHTTPRequestHandler):
    server_version = "ESP32TemperatureTest/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.debug:  # type: ignore[attr-defined]
            print("HTTP: " + (fmt % args))

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def path_matches(self) -> bool:
        parsed = urllib.parse.urlsplit(self.path)
        return normalize_path(parsed.path) == self.server.temperature_path  # type: ignore[attr-defined]

    def print_temperature(self, temp_c: float, raw: str = "") -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        remote_ip = self.client_address[0]
        suffix = f" raw={raw!r}" if self.server.debug and raw else ""  # type: ignore[attr-defined]
        print(f"[{now}] from {remote_ip}: {temp_c:.1f} C{suffix}", flush=True)

    def do_GET(self) -> None:
        if not self.path_matches():
            self.send_json(404, {"ok": False, "error": "not_found"})
            return

        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        flat_params = {key: values[0] for key, values in params.items() if values}
        temp_c = extract_temperature_c(flat_params)
        if temp_c is None:
            self.send_json(
                200,
                {
                    "ok": True,
                    "message": "POST JSON like {\"ok\":true,\"temperature_c\":25.4}, or GET /temperature?temperature_c=25.4",
                },
            )
            return

        self.print_temperature(temp_c)
        self.send_json(200, {"ok": True, "temperature_c": temp_c})

    def do_POST(self) -> None:
        if not self.path_matches():
            self.send_json(404, {"ok": False, "error": "not_found"})
            return

        try:
            length = min(max(0, int(self.headers.get("Content-Length", "0") or "0")), 4096)
        except ValueError:
            length = 0
        raw = self.rfile.read(length).decode("utf-8", errors="replace").strip()

        content_type = str(self.headers.get("Content-Type", "") or "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            payload: Any = {key: values[0] for key, values in urllib.parse.parse_qs(raw).items() if values}
        else:
            payload = raw

        temp_c = extract_temperature_c(payload)
        if temp_c is None:
            self.send_json(400, {"ok": False, "error": "invalid_temperature"})
            return

        self.print_temperature(temp_c, raw=raw)
        self.send_json(200, {"ok": True, "temperature_c": temp_c})


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def run_self_test() -> int:
    cases = [
        ({"ok": True, "temperature_c": 25.4}, 25.4),
        ({"temp": "26.7"}, 26.7),
        ("{\"ok\":true,\"temperature_c\":24.9}", 24.9),
        ("temperature=23.5", 23.5),
    ]
    for payload, expected in cases:
        actual = extract_temperature_c(payload)
        if actual is None or abs(actual - expected) > 0.001:
            raise AssertionError(f"parse failed: {payload!r} -> {actual!r}, expected {expected!r}")
    print("temperature receiver self-test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive ESP32 DS18B20 temperature POSTs and print them.")
    parser.add_argument("--host", default="0.0.0.0", help="Jetson listen host. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", type=int, default=8790, help="Jetson listen port.")
    parser.add_argument("--path", default="/temperature", help="HTTP path that ESP32 should POST to.")
    parser.add_argument("--debug", action="store_true", help="Print HTTP debug details.")
    parser.add_argument("--self-test", action="store_true", help="Run parser checks and exit.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.self_test:
        return run_self_test()

    server = ReusableThreadingHTTPServer((args.host, args.port), TemperatureReceiver)
    server.temperature_path = normalize_path(args.path)  # type: ignore[attr-defined]
    server.debug = bool(args.debug)  # type: ignore[attr-defined]

    print(f"ESP32 temperature test receiver listening on http://{args.host}:{args.port}{server.temperature_path}")
    print("Waiting for readings. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
