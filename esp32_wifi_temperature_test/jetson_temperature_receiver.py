#!/usr/bin/env python3
"""
Minimal Jetson-side WiFi temperature receiver for ESP32 tests.

The server accepts:
  POST /temperature  {"temperature_c":25.4}
  GET  /temperature?temperature_c=25.4

By default, each valid reading prints only:
  25.4 C
"""

from __future__ import annotations

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit


TEMPERATURE_KEYS = ("temperature_c", "temp_c", "temperature", "temp", "value")


def normalize_path(path: str) -> str:
    normalized = str(path or "/temperature").strip()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized.rstrip("/") or "/temperature"


def coerce_temperature_c(value: Any) -> float | None:
    try:
        temperature_c = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    if -55.0 <= temperature_c <= 125.0:
        return temperature_c
    return None


def extract_temperature_c(payload: Any) -> float | None:
    if isinstance(payload, dict):
        for key in TEMPERATURE_KEYS:
            if key in payload:
                temperature_c = coerce_temperature_c(payload[key])
                if temperature_c is not None:
                    return temperature_c
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
        if match:
            return coerce_temperature_c(match.group(0))
        return None

    return coerce_temperature_c(payload)


class TemperatureHandler(BaseHTTPRequestHandler):
    server_version = "JetsonTemperatureReceiver/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:  # type: ignore[attr-defined]
            print("HTTP " + (fmt % args), flush=True)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def is_temperature_path(self) -> bool:
        parsed = urlsplit(self.path)
        return normalize_path(parsed.path) == self.server.temperature_path  # type: ignore[attr-defined]

    def print_temperature(self, temperature_c: float) -> None:
        if self.server.with_time:  # type: ignore[attr-defined]
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{timestamp}  {temperature_c:.1f} C", flush=True)
        else:
            print(f"{temperature_c:.1f} C", flush=True)

    def handle_temperature(self, payload: Any) -> None:
        temperature_c = extract_temperature_c(payload)
        if temperature_c is None:
            self.send_json(400, {"ok": False, "error": "invalid_temperature"})
            return

        self.print_temperature(temperature_c)
        self.send_json(200, {"ok": True, "temperature_c": temperature_c})

    def do_GET(self) -> None:
        if not self.is_temperature_path():
            self.send_json(404, {"ok": False, "error": "not_found"})
            return

        parsed = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
        if not query:
            self.send_json(200, {"ok": True, "message": "send temperature_c"})
            return

        self.handle_temperature(query)

    def do_POST(self) -> None:
        if not self.is_temperature_path():
            self.send_json(404, {"ok": False, "error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0

        raw_body = self.rfile.read(min(max(length, 0), 4096)).decode("utf-8", errors="replace")
        self.handle_temperature(raw_body)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive ESP32 WiFi temperature readings on Jetson.")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host. Use 0.0.0.0 for ESP32 LAN access.")
    parser.add_argument("--port", type=int, default=8790, help="Listen port.")
    parser.add_argument("--path", default="/temperature", help="Temperature HTTP path.")
    parser.add_argument("--with-time", action="store_true", help="Print timestamp before each temperature.")
    parser.add_argument("--verbose", action="store_true", help="Print HTTP request logs.")
    parser.add_argument("--self-test", action="store_true", help="Run parser checks and exit.")
    return parser


def run_self_test() -> int:
    samples = [
        ({"temperature_c": 25.4}, 25.4),
        ({"temp": "26.7"}, 26.7),
        ('{"temperature_c":24.9}', 24.9),
        ("temperature_c=23.5", 23.5),
        ("23.1 C", 23.1),
    ]
    for payload, expected in samples:
        actual = extract_temperature_c(payload)
        if actual is None or abs(actual - expected) > 0.001:
            raise AssertionError(f"{payload!r} -> {actual!r}, expected {expected!r}")
    print("self-test OK")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        return run_self_test()

    server = ReusableThreadingHTTPServer((args.host, args.port), TemperatureHandler)
    server.temperature_path = normalize_path(args.path)  # type: ignore[attr-defined]
    server.with_time = bool(args.with_time)  # type: ignore[attr-defined]
    server.verbose = bool(args.verbose)  # type: ignore[attr-defined]

    print(f"Listening on http://{args.host}:{args.port}{server.temperature_path}")
    print("Valid readings will print as: 25.4 C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
