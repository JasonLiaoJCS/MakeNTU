#!/usr/bin/env python3
"""
Jetson BLE controller for ESP32-S3 fan, LED matrix, and DS18B20 temperature.

This script can be used in three ways:
  1. Direct CLI BLE commands such as FAN_ON, FAN_SPEED:180, LED_ON, TEMP?.
  2. FRDM UART fan events such as Fan 1,50 or FanSpeed 75, mapped from
     0..100 percent to ESP32 PWM 0..255.
  3. Text/voice-like commands such as "voice open fan" or "voice led off".

The ESP32-S3 is expected to expose:
  device name: ESP32S3_FAN_LED_TEMP
  service:     12345678-1234-1234-1234-1234567890ab
  command:     12345678-1234-1234-1234-1234567890ac
  status:      12345678-1234-1234-1234-1234567890ad
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable


DEVICE_NAME = "ESP32S3_FAN_LED_TEMP"
SERVICE_UUID = "12345678-1234-1234-1234-1234567890ab"
COMMAND_UUID = "12345678-1234-1234-1234-1234567890ac"
STATUS_UUID = "12345678-1234-1234-1234-1234567890ad"
DEFAULT_MIN_NONZERO_PWM = 96

DIRECT_COMMANDS = {
    "FAN_ON",
    "FAN_OFF",
    "FAN_TOGGLE",
    "LED_ON",
    "LED_OFF",
    "LED_TOGGLE",
    "TEMP?",
    "ALL_OFF",
}

FRDM_UART_KEYWORDS = ("frdm", "mcu", "cmsis", "dap", "nxp", "j-link", "linkserver", "mbed")


@dataclass
class Esp32Status:
    raw: str
    temp_c: float | None = None
    fan: str | None = None
    speed: int | None = None
    led: str | None = None
    received_at: datetime | None = None

    @property
    def fan_is_on(self) -> bool:
        return str(self.fan or "").upper() == "ON" and int(self.speed or 0) > 0

    def summary(self) -> str:
        temp = "--" if self.temp_c is None else f"{self.temp_c:.2f} C"
        fan = self.fan or "--"
        speed = "--" if self.speed is None else str(self.speed)
        led = self.led or "--"
        return f"TEMP={temp}, FAN={fan}, SPEED={speed}, LED={led}"


def parse_status(text: str) -> Esp32Status:
    status = Esp32Status(raw=text, received_at=datetime.now().astimezone())
    for part in str(text or "").strip().split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().upper()
        value = value.strip()
        if key == "TEMP":
            try:
                status.temp_c = float(value)
            except ValueError:
                pass
        elif key == "FAN":
            status.fan = value.upper()
        elif key == "SPEED":
            try:
                status.speed = max(0, min(255, int(float(value))))
            except ValueError:
                pass
        elif key == "LED":
            status.led = value.upper()
    return status


def temperature_c_to_uart_x10(temp_c: float) -> int:
    return clamp_int(float(temp_c) * 10.0, -550, 1250)


def format_temp_room_uart_line(temp_c: float | None) -> str | None:
    if temp_c is None:
        return None
    try:
        value = float(temp_c)
    except (TypeError, ValueError):
        return None
    if value < -55.0 or value > 125.0:
        return None
    return f"TempRoom {temperature_c_to_uart_x10(value)}"


def clamp_int(value: Any, low: int, high: int) -> int:
    return max(low, min(high, int(round(float(value)))))


def min_nonzero_pwm() -> int:
    try:
        return clamp_int(os.getenv("FAN_MIN_PWM", str(DEFAULT_MIN_NONZERO_PWM)), 0, 255)
    except (TypeError, ValueError):
        return DEFAULT_MIN_NONZERO_PWM


def apply_min_nonzero_pwm(pwm: int, *, minimum: int | None = None) -> int:
    value = clamp_int(pwm, 0, 255)
    if value <= 0:
        return 0
    floor = min_nonzero_pwm() if minimum is None else clamp_int(minimum, 0, 255)
    return max(value, floor)


def percent_to_pwm(percent: Any) -> int:
    return apply_min_nonzero_pwm(clamp_int(float(percent) * 255.0 / 100.0, 0, 255))


def pwm_to_percent(pwm: Any) -> int:
    return clamp_int(float(pwm) * 100.0 / 255.0, 0, 100)


def normalize_ble_command(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    compact = text.replace(" ", "_").upper()
    if compact in DIRECT_COMMANDS:
        return compact
    speed_match = re.fullmatch(r"FAN(?:_|\s+)SPEED\s*[:= ]\s*(-?\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if speed_match:
        return f"FAN_SPEED:{clamp_int(speed_match.group(1), 0, 255)}"
    return None


def frdm_event_parts(line: str) -> list[str]:
    text = str(line or "").strip()
    if not text:
        return []
    if text.startswith("$") and "*" in text:
        text = text[1:].split("*", 1)[0]
    text = re.sub(r"[,=:]+", " ", text)
    parts = text.split()
    if parts and parts[0].upper() == "EVT":
        parts = parts[1:]
    return parts


def parse_bool_token(value: str) -> bool | None:
    token = str(value or "").strip().lower()
    if token in {"1", "on", "true", "yes", "y", "open", "enable", "enabled"}:
        return True
    if token in {"0", "off", "false", "no", "n", "close", "closed", "disable", "disabled"}:
        return False
    if token in {"開", "开", "開啟", "开启", "打開", "打开"}:
        return True
    if token in {"關", "关", "關閉", "关闭", "關掉", "关掉"}:
        return False
    return None


def parse_frdm_fan_event(line: str) -> dict[str, Any] | None:
    parts = frdm_event_parts(line)
    if not parts:
        return None
    command = parts[0].strip().lower()
    power: bool | None = None
    percent: int | None = None

    if command in {"fan", "fanset", "fancontrol"}:
        if len(parts) < 2:
            return None
        power = parse_bool_token(parts[1])
        if power is None:
            try:
                percent = clamp_int(parts[1], 0, 100)
            except (TypeError, ValueError):
                return None
            power = percent > 0
        if len(parts) >= 3:
            try:
                percent = clamp_int(parts[2], 0, 100)
            except (TypeError, ValueError):
                percent = None
    elif command in {"fanpower", "fanswitch", "fanonoff"}:
        if len(parts) < 2:
            return None
        power = parse_bool_token(parts[1])
        if power is None:
            return None
    elif command in {"fanspeed", "fanlevel", "fanpercent"}:
        if len(parts) < 2:
            return None
        try:
            percent = clamp_int(parts[1], 0, 100)
        except (TypeError, ValueError):
            return None
        power = percent > 0
    else:
        return None

    if percent is None:
        percent = 100 if power else 0
    if not power:
        percent = 0
    return {
        "power": bool(power),
        "state": "on" if power else "off",
        "percent": percent,
        "pwm": percent_to_pwm(percent),
        "raw": str(line or "").strip(),
    }


def frdm_event_to_ble_commands(line: str) -> list[str] | None:
    event = parse_frdm_fan_event(line)
    if event is None:
        return None
    if not event["power"]:
        return ["FAN_OFF"]
    return ["FAN_ON", f"FAN_SPEED:{event['pwm']}"]


def percent_command_to_ble(percent: Any) -> list[str]:
    value = clamp_int(percent, 0, 100)
    if value <= 0:
        return ["FAN_OFF"]
    return ["FAN_ON", f"FAN_SPEED:{percent_to_pwm(value)}"]


def voice_text_to_ble_commands(text: str, status: Esp32Status | None, *, speed_step: int = 32) -> list[str] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    compact = re.sub(r"\s+", "", lowered)

    is_led = "led" in lowered or "燈" in raw or "灯" in raw or "matrix" in lowered
    is_fan = "fan" in lowered or "風扇" in raw or "风扇" in raw or "電扇" in raw or "电扇" in raw

    off_words = ("off", "close", "stop", "turn off", "shut", "關", "关", "關掉", "关掉", "關閉", "关闭", "停止")
    on_words = ("on", "open", "start", "turn on", "開", "开", "打開", "打开", "啟動", "启动")
    faster_words = (
        "faster", "speed up", "stronger", "more wind",
        "快一點", "快一点", "轉快", "转快", "加速",
        "大一點", "大一点", "調高", "调高", "提高",
        "調大", "调大", "加大", "開大", "开大",
        "強一點", "强一点", "更強", "更强", "風量大", "风量大",
    )
    slower_words = (
        "slower", "slow down", "weaker", "less wind",
        "慢一點", "慢一点", "轉慢", "转慢", "減速", "减速",
        "小一點", "小一点", "調低", "调低", "降低",
        "調小", "调小", "減小", "减小", "開小", "开小",
        "弱一點", "弱一点", "更弱", "風量小", "风量小",
    )
    hot_words = ("hot", "too warm", "好熱", "好热", "太熱", "太热", "很熱", "很热", "悶", "闷")
    cold_words = ("cold", "too cold", "好冷", "太冷", "很冷", "涼", "凉")

    def has_any(words: Iterable[str]) -> bool:
        return any(word in lowered or word in compact or word in raw for word in words)

    if "all off" in lowered or "全部關" in raw or "全部关" in raw:
        return ["ALL_OFF"]

    commands: list[str] = []

    if is_led:
        if has_any(off_words):
            commands.append("LED_OFF")
        elif has_any(on_words):
            commands.append("LED_ON")
        elif "toggle" in lowered or "切換" in raw or "切换" in raw:
            commands.append("LED_TOGGLE")

    current_speed = int(status.speed) if status and status.speed is not None else 0
    if is_fan or has_any(faster_words + slower_words + hot_words + cold_words):
        floor = min_nonzero_pwm()
        if has_any(off_words):
            commands.append("FAN_OFF")
        elif has_any(faster_words):
            next_speed = max(floor, current_speed + speed_step) if current_speed <= 0 else current_speed + speed_step
            commands.extend(["FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(next_speed)}"])
        elif has_any(slower_words):
            next_speed = current_speed - speed_step if current_speed > 0 else 0
            if next_speed < floor:
                commands.append("FAN_OFF")
            else:
                commands.extend(["FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(next_speed)}"])
        elif has_any(hot_words):
            next_speed = max(current_speed, 180)
            commands.extend(["FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(next_speed)}"])
        elif has_any(cold_words):
            next_speed = current_speed - max(speed_step, 64)
            if next_speed < floor:
                commands.append("FAN_OFF")
            else:
                commands.extend(["FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(next_speed)}"])
        elif has_any(on_words):
            next_speed = current_speed if current_speed > 0 else 180
            commands.extend(["FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(next_speed)}"])
        elif "toggle" in lowered or "切換" in raw or "切换" in raw:
            commands.append("FAN_TOGGLE")

    if commands:
        return commands

    return None


def resolve_input_to_ble_commands(line: str, status: Esp32Status | None, *, speed_step: int = 32) -> list[str] | None:
    text = str(line or "").strip()
    if not text:
        return None

    direct = normalize_ble_command(text)
    if direct is not None:
        return [direct]

    percent_match = re.fullmatch(
        r"(?:PERCENT|FAN_PERCENT|FRDM_SPEED|FRDM_PERCENT|PWM_PERCENT)\s*[:= ]\s*(-?\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if percent_match:
        return percent_command_to_ble(percent_match.group(1))

    frdm_commands = frdm_event_to_ble_commands(text)
    if frdm_commands is not None:
        return frdm_commands

    voice_match = re.fullmatch(r"(?:voice|say|asr)\s*[: ]\s*(.+)", text, flags=re.IGNORECASE)
    if voice_match:
        return voice_text_to_ble_commands(voice_match.group(1), status, speed_step=speed_step)

    return voice_text_to_ble_commands(text, status, speed_step=speed_step)


def short_status_line(status: Esp32Status) -> str:
    stamp = status.received_at.strftime("%H:%M:%S") if status.received_at else "--:--:--"
    return f"[{stamp}] ESP32 status: {status.summary()}"


def advertised_name(device: Any) -> str:
    metadata = getattr(device, "metadata", {}) or {}
    return str(getattr(device, "name", None) or metadata.get("local_name") or "")


def advertisement_name(advertisement: Any) -> str:
    return str(getattr(advertisement, "local_name", "") or "")


def device_service_uuids(device: Any, advertisement: Any | None = None) -> list[str]:
    metadata = getattr(device, "metadata", {}) or {}
    raw = (
        (getattr(advertisement, "service_uuids", None) if advertisement is not None else None)
        or getattr(device, "service_uuids", None)
        or metadata.get("uuids")
        or metadata.get("service_uuids")
        or []
    )
    return [str(item).lower() for item in raw if str(item or "").strip()]


def normalize_ble_address(value: Any) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).upper()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def clean_bluetoothctl_output(text: str) -> str:
    # bluetoothctl can emit ANSI/control bytes when its prompt races with events.
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", str(text or ""))
    return "".join(ch for ch in text.replace("\r", "\n") if ch in "\n\t" or ord(ch) >= 32)


def bluetoothctl_output(*args: str, timeout: float = 6.0) -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    return clean_bluetoothctl_output((result.stdout or "") + (result.stderr or ""))


def bluez_cached_devices() -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for line in bluetoothctl_output("devices", timeout=8.0).splitlines():
        match = re.match(r"\s*Device\s+([0-9A-Fa-f:]{17})\s+(.+?)\s*$", line)
        if match:
            devices.append((match.group(1).upper(), match.group(2).strip()))
    return devices


def bluez_device_info(address: str) -> str:
    if not normalize_ble_address(address):
        return ""
    return bluetoothctl_output("info", address, timeout=5.0)


def bluez_primary_info(info: str) -> str:
    lines: list[str] = []
    for line in str(info or "").splitlines():
        if re.match(r"\s*\[[A-Z]+\]", line):
            break
        lines.append(line)
    return "\n".join(lines)


def bluez_info_name(info: str) -> str:
    for line in bluez_primary_info(info).splitlines():
        match = re.match(r"\s*(?:Name|Alias):\s*(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return ""


def bluez_info_connected(info: str) -> bool:
    for line in bluez_primary_info(info).splitlines():
        match = re.match(r"\s*Connected:\s*(yes|no)\s*$", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).lower() == "yes"
    return False


def bluez_info_matches_target(info: str, expected_name: str) -> bool:
    text = bluez_primary_info(info)
    if SERVICE_UUID.lower() in text.lower():
        return True
    return bluez_info_name(text) == expected_name


def recover_bluez_stale_connections(args: argparse.Namespace) -> None:
    if not bool(getattr(args, "recover_bluez_stale", True)):
        return

    expected_name = str(getattr(args, "name", DEVICE_NAME) or DEVICE_NAME)
    requested_address = str(getattr(args, "address", "") or "").strip()
    candidates: list[str] = []
    if requested_address:
        candidates.append(requested_address.upper())
    for address, name in bluez_cached_devices():
        if name == expected_name:
            candidates.append(address)

    seen: set[str] = set()
    for address in candidates:
        normalized = normalize_ble_address(address)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        info = bluez_device_info(address)
        if not bluez_info_connected(info):
            continue
        requested_match = bool(requested_address) and normalize_ble_address(requested_address) == normalized
        if not requested_match and not bluez_info_matches_target(info, expected_name):
            continue

        label = bluez_info_name(info) or expected_name
        print(f"BLE: clearing stale BlueZ connection to {label} at {address}", flush=True)
        bluetoothctl_output("disconnect", address, timeout=5.0)
        time.sleep(0.7)


def ble_device_rssi(device: Any, advertisement: Any | None = None) -> int | None:
    value = getattr(advertisement, "rssi", None) if advertisement is not None else getattr(device, "rssi", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def describe_ble_device(device: Any, advertisement: Any | None = None) -> str:
    name = advertisement_name(advertisement) or advertised_name(device) or "(unnamed)"
    address = str(getattr(device, "address", "") or "?")
    rssi = ble_device_rssi(device, advertisement)
    uuids = device_service_uuids(device, advertisement)
    manufacturer_data = getattr(advertisement, "manufacturer_data", None) if advertisement is not None else None
    manufacturer_text = f" mfg={','.join(hex(int(key)) for key in manufacturer_data.keys())}" if manufacturer_data else ""
    uuid_text = f" uuids={','.join(uuids[:3])}" if uuids else ""
    rssi_text = f" rssi={rssi}" if rssi is not None else ""
    return f"{address:20s} {name}{rssi_text}{uuid_text}{manufacturer_text}"


def bleak_discover_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    bluez_args: dict[str, Any] = {}
    adapter = str(getattr(args, "adapter", "") or "").strip()
    if adapter:
        bluez_args["adapter"] = adapter
    if bool(getattr(args, "scan_duplicates", False)):
        bluez_args["filters"] = {"DuplicateData": True}

    kwargs: dict[str, Any] = {
        "scanning_mode": str(getattr(args, "scan_mode", "active") or "active"),
    }
    if bluez_args:
        kwargs["bluez"] = bluez_args
    if bool(getattr(args, "scan_filter_service", False)):
        kwargs["service_uuids"] = [SERVICE_UUID]
    return kwargs


async def discover_with_advertisements(args: argparse.Namespace) -> list[tuple[Any, Any | None]]:
    try:
        from bleak import BleakScanner
    except Exception as exc:
        raise RuntimeError("bleak is not installed. Run: python3 -m pip install bleak") from exc
    kwargs = bleak_discover_kwargs(args)
    try:
        discovered = await BleakScanner.discover(timeout=args.scan_timeout, return_adv=True, **kwargs)
    except TypeError:
        kwargs.pop("return_adv", None)
        return [(device, None) for device in await BleakScanner.discover(timeout=args.scan_timeout, **kwargs)]
    if isinstance(discovered, dict):
        items: list[tuple[Any, Any | None]] = []
        for value in discovered.values():
            if isinstance(value, tuple) and len(value) >= 2:
                items.append((value[0], value[1]))
            else:
                items.append((value, None))
        return items
    return [(device, None) for device in discovered]


def is_target_device(device: Any, advertisement: Any | None, expected_name: str) -> bool:
    names = {advertised_name(device), advertisement_name(advertisement)}
    return expected_name in names or SERVICE_UUID.lower() in device_service_uuids(device, advertisement)


def is_address_device(device: Any, expected_address: str) -> bool:
    expected = normalize_ble_address(expected_address)
    if not expected:
        return False
    return normalize_ble_address(getattr(device, "address", "")) == expected


async def scan_only(args: argparse.Namespace) -> int:
    recover_bluez_stale_connections(args)
    print(f"BLE scan-only: scanning for {args.scan_timeout:g}s...")
    discovered = await discover_with_advertisements(args)
    if not discovered:
        print("No BLE devices found.")
        return 1
    target_only = bool(getattr(args, "scan_target_only", False))
    print("Target BLE candidates:" if target_only else "Nearby BLE devices:")
    target_seen = False
    min_rssi = getattr(args, "min_rssi", None)
    if min_rssi is not None:
        discovered = [
            item for item in discovered
            if ble_device_rssi(item[0], item[1]) is None or ble_device_rssi(item[0], item[1]) >= int(min_rssi)
        ]
    if str(getattr(args, "scan_sort", "name") or "name") == "rssi":
        discovered = sorted(discovered, key=lambda item: ble_device_rssi(item[0], item[1]) if ble_device_rssi(item[0], item[1]) is not None else -999, reverse=True)
    else:
        discovered = sorted(
            discovered,
            key=lambda item: (advertisement_name(item[1]) or advertised_name(item[0]) or "~", str(getattr(item[0], "address", ""))),
        )
    for device, advertisement in discovered:
        is_target = is_target_device(device, advertisement, args.name)
        if target_only and not is_target:
            continue
        line = describe_ble_device(device, advertisement)
        marker = "* " if is_target else "  "
        print(f"{marker}{line}")
        if is_target:
            target_seen = True
    if target_seen:
        print(f"Target candidate found. Use --address <MAC> if the name is missing or unstable.")
        return 0
    print(f"Target {args.name!r} was not seen.")
    return 1


def post_tts_async(base_url: str, text: str, *, timeout_sec: float = 1.5) -> bool:
    if not base_url:
        return False
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parsed.path.rstrip("/") in {"/speak", "/speak_async"}:
        endpoint = base_url.rstrip("/")
    else:
        endpoint = base_url.rstrip("/") + "/speak_async"
    payload = json.dumps({"text": text, "blocking": False, "interrupt": False}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            response.read()
        return True
    except Exception as exc:
        print(f"WARNING: TTS reminder failed: {exc}", flush=True)
        return False


def find_frdm_uart_port() -> str | None:
    try:
        import serial.tools.list_ports
    except Exception:
        return None
    ports = list(serial.tools.list_ports.comports())
    for port in ports:
        haystack = " ".join(
            str(item or "")
            for item in (port.device, port.description, port.manufacturer, port.product, port.hwid)
        ).lower()
        if any(keyword in haystack for keyword in FRDM_UART_KEYWORDS):
            return port.device
    return ports[0].device if ports else None


class FrdmUartFanReader:
    def __init__(
        self,
        *,
        port: str,
        baudrate: int,
        command_queue: asyncio.Queue[str],
        tx_queue: "queue.Queue[str] | None",
        loop: asyncio.AbstractEventLoop,
        reconnect_sec: float,
        stop_event: threading.Event,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.command_queue = command_queue
        self.tx_queue = tx_queue
        self.loop = loop
        self.reconnect_sec = reconnect_sec
        self.stop_event = stop_event
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="frdm-uart-fan-reader", daemon=True)
        self.thread.start()

    def join(self, timeout: float = 1.0) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def _resolve_port(self) -> str | None:
        if self.port.lower() == "auto":
            return find_frdm_uart_port()
        return self.port

    def _write_pending(self, ser: Any) -> None:
        tx_queue = self.tx_queue
        if tx_queue is None:
            return
        while not self.stop_event.is_set():
            try:
                line = tx_queue.get_nowait()
            except queue.Empty:
                return
            wire = str(line or "").strip()
            if not wire:
                continue
            try:
                ser.write((wire + "\r\n").encode("utf-8"))
                ser.flush()
                print(f"FRDM UART TX: {wire}", flush=True)
            except Exception as exc:
                print(f"WARNING: FRDM UART write failed for {wire!r}: {exc}", flush=True)
                raise

    def _run(self) -> None:
        try:
            import serial
        except Exception as exc:
            print(f"WARNING: pyserial is required for --frdm-uart-port: {exc}", flush=True)
            return
        while not self.stop_event.is_set():
            port = self._resolve_port()
            if not port:
                print("FRDM UART: no port found; retrying...", flush=True)
                self.stop_event.wait(self.reconnect_sec)
                continue
            try:
                with serial.Serial(port, self.baudrate, timeout=0.25) as ser:
                    print(f"FRDM UART: listening on {port} @ {self.baudrate}", flush=True)
                    while not self.stop_event.is_set():
                        self._write_pending(ser)
                        raw = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode("utf-8", errors="replace").strip()
                        commands = frdm_event_to_ble_commands(line)
                        if commands is None:
                            continue
                        event = parse_frdm_fan_event(line) or {}
                        print(
                            "FRDM fan -> BLE: "
                            f"percent={event.get('percent')} pwm={event.get('pwm')} raw={line!r}",
                            flush=True,
                        )
                        for command in commands:
                            asyncio.run_coroutine_threadsafe(self.command_queue.put(command), self.loop)
            except Exception as exc:
                if not self.stop_event.is_set():
                    print(f"WARNING: FRDM UART disconnected/error: {exc}; retrying...", flush=True)
                    self.stop_event.wait(self.reconnect_sec)


class Esp32BleController:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.command_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.connected = asyncio.Event()
        self.stop = asyncio.Event()
        self.latest_status: Esp32Status | None = None
        self.client: Any | None = None
        self.frdm_uart_tx_queue: queue.Queue[str] | None = None
        self._last_passive_reminder_at = 0.0
        self._last_temp_room_uart_at = 0.0
        self._printed_tts_warning = False

    async def scan(self) -> Any:
        try:
            from bleak import BleakScanner
        except Exception as exc:
            raise RuntimeError("bleak is not installed. Run: python3 -m pip install bleak") from exc

        recover_bluez_stale_connections(self.args)
        address = str(getattr(self.args, "address", "") or "").strip()
        target_label = f"{address} or {self.args.name!r}" if address else repr(self.args.name)
        print(f"BLE: scanning for {target_label} ({self.args.scan_timeout:g}s)...", flush=True)
        discovered = await discover_with_advertisements(self.args)
        for device, advertisement in discovered:
            name = advertisement_name(advertisement) or advertised_name(device)
            matched_address = address and is_address_device(device, address)
            matched_target = is_target_device(device, advertisement, self.args.name)
            if matched_address or matched_target:
                if address and not matched_address:
                    print(
                        f"BLE: requested address {address} was not seen; using target at {device.address}",
                        flush=True,
                    )
                print(f"BLE: found {name or '(unnamed target)'} at {device.address}", flush=True)
                return device
        nearby = "; ".join(describe_ble_device(device, advertisement) for device, advertisement in discovered[:8])
        raise RuntimeError(f"device {target_label} not found. Nearby: {nearby or 'none'}")

    async def run_ble_forever(self) -> None:
        try:
            from bleak import BleakClient
        except Exception as exc:
            raise RuntimeError("bleak is not installed. Run: python3 -m pip install bleak") from exc

        while not self.stop.is_set():
            try:
                target = await self.scan()
                disconnected = asyncio.Event()

                def on_disconnect(_: Any) -> None:
                    print("BLE: disconnected.", flush=True)
                    self.connected.clear()
                    disconnected.set()

                print(f"BLE: connecting to {target}...", flush=True)
                async with BleakClient(target, disconnected_callback=on_disconnect, timeout=self.args.connect_timeout) as client:
                    try:
                        self.client = client
                        self.connected.set()
                        print("BLE: connected. Subscribing status notify...", flush=True)
                        await client.start_notify(STATUS_UUID, self._handle_notify)
                        if self.args.read_status_on_connect:
                            await self.read_status_once(client)
                        await self._connected_command_loop(client, disconnected)
                    finally:
                        self.connected.clear()
                        self.client = None
            except asyncio.CancelledError:
                self.connected.clear()
                self.client = None
                raise
            except Exception as exc:
                self.connected.clear()
                self.client = None
                if not self.stop.is_set():
                    print(f"WARNING: BLE connection failed: {exc}", flush=True)
            if not self.stop.is_set():
                print(f"BLE: reconnecting in {self.args.reconnect_sec:g}s...", flush=True)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.args.reconnect_sec)
                except asyncio.TimeoutError:
                    pass

    async def _connected_command_loop(self, client: Any, disconnected: asyncio.Event) -> None:
        while not self.stop.is_set() and not disconnected.is_set():
            get_task = asyncio.create_task(self.command_queue.get())
            disconnect_task = asyncio.create_task(disconnected.wait())
            done, pending = await asyncio.wait({get_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if get_task in done:
                command = get_task.result()
                if command is None:
                    self.stop.set()
                    return
                if disconnect_task in done or disconnected.is_set():
                    await self.command_queue.put(command)
                    return
                try:
                    await self.write_command(client, command)
                except Exception:
                    await self.command_queue.put(command)
                    raise
                continue
            if disconnect_task in done:
                return

    async def write_command(self, client: Any, command: str) -> None:
        command = normalize_ble_command(command) or command
        data = command.encode("ascii", errors="strict")
        try:
            if self.args.write_response_auto:
                await client.write_gatt_char(COMMAND_UUID, data)
            else:
                await client.write_gatt_char(COMMAND_UUID, data, response=bool(self.args.write_with_response))
            print(f"BLE TX: {command}", flush=True)
        except Exception as exc:
            print(f"WARNING: BLE write failed for {command!r}: {exc}", flush=True)
            raise

    async def read_status_once(self, client: Any) -> None:
        try:
            raw = await client.read_gatt_char(STATUS_UUID)
        except Exception as exc:
            print(f"WARNING: BLE status read failed: {exc}", flush=True)
            return
        self._accept_status_bytes(raw)

    async def disconnect_current_client(self) -> None:
        client = self.client
        self.client = None
        self.connected.clear()
        if client is None:
            return
        try:
            if bool(getattr(client, "is_connected", False)):
                try:
                    await client.stop_notify(STATUS_UUID)
                except Exception:
                    pass
                await client.disconnect()
        except Exception as exc:
            print(f"WARNING: BLE cleanup disconnect failed: {exc}", flush=True)

    def _handle_notify(self, _: Any, data: bytearray) -> None:
        self._accept_status_bytes(bytes(data))

    def _accept_status_bytes(self, data: bytes) -> None:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return
        status = parse_status(text)
        self.latest_status = status
        print(short_status_line(status), flush=True)
        self._maybe_passive_reminder(status)
        self._maybe_queue_temp_room_uart(status)

    def _maybe_queue_temp_room_uart(self, status: Esp32Status) -> None:
        tx_queue = self.frdm_uart_tx_queue
        if tx_queue is None or bool(getattr(self.args, "no_frdm_temp_room_uart", False)):
            return
        interval_sec = max(0.0, float(getattr(self.args, "frdm_temp_room_interval_sec", 10.0) or 0.0))
        if interval_sec <= 0.0:
            return
        now = time.monotonic()
        if now - self._last_temp_room_uart_at < interval_sec:
            return
        line = format_temp_room_uart_line(status.temp_c)
        if line is None:
            return
        if tx_queue.qsize() >= 5:
            return
        self._last_temp_room_uart_at = now
        tx_queue.put_nowait(line)

    def _maybe_passive_reminder(self, status: Esp32Status) -> None:
        if self.args.no_passive_reminder or status.temp_c is None:
            return
        threshold = float(self.args.passive_threshold)
        if status.temp_c <= threshold:
            return
        if status.fan_is_on:
            return
        now = time.monotonic()
        if now - self._last_passive_reminder_at < float(self.args.passive_cooldown_sec):
            return
        self._last_passive_reminder_at = now
        message = self.args.passive_message.format(temp=status.temp_c, threshold=threshold)
        print(f"PASSIVE REMINDER: {message}", flush=True)
        if self.args.no_tts_reminder:
            return
        ok = post_tts_async(self.args.tts_url, message, timeout_sec=self.args.tts_timeout)
        if not ok and not self._printed_tts_warning:
            self._printed_tts_warning = True
            print("TIP: use --no-tts-reminder if the Piper TTS server is not running.", flush=True)

    async def input_loop(self) -> None:
        self.print_help()
        loop = asyncio.get_running_loop()
        while not self.stop.is_set():
            try:
                line = await loop.run_in_executor(None, input, "ble> ")
            except (EOFError, KeyboardInterrupt):
                self.stop.set()
                await self.command_queue.put(None)
                return
            text = line.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in {"quit", "exit", "q"}:
                self.stop.set()
                await self.command_queue.put(None)
                return
            if lowered in {"help", "h", "?"}:
                self.print_help()
                continue
            if lowered in {"status", "last"}:
                if self.latest_status is None:
                    print("No status received yet.", flush=True)
                else:
                    print(short_status_line(self.latest_status), flush=True)
                continue
            if lowered == "connected":
                print(f"BLE connected: {self.connected.is_set()}", flush=True)
                continue

            commands = resolve_input_to_ble_commands(text, self.latest_status, speed_step=self.args.voice_speed_step)
            if not commands:
                print(f"Unknown command: {text!r}. Type help for examples.", flush=True)
                continue

            if not self.connected.is_set():
                print("BLE is not connected yet; command queued.", flush=True)
            for command in commands:
                await self.command_queue.put(command)

    def print_help(self) -> None:
        print(
            "\nCommands:\n"
            "  FAN_ON | FAN_OFF | FAN_TOGGLE | FAN_SPEED:0..255\n"
            "  LED_ON | LED_OFF | LED_TOGGLE | TEMP? | ALL_OFF\n"
            f"  PERCENT:0..100              # FRDM/manual percent -> FAN_SPEED:0..255, nonzero min {min_nonzero_pwm()}\n"
            "  Fan 1,50 | Fan 0,0          # FRDM event format; 50 -> PWM 128\n"
            "  FanSpeed 75                 # FRDM slider percent; 75 -> PWM 191\n"
            "  voice open fan / voice fan faster / voice led off\n"
            "  status | connected | help | quit\n",
            flush=True,
        )


async def async_main(args: argparse.Namespace) -> int:
    controller = Esp32BleController(args)
    loop = asyncio.get_running_loop()
    stop_threads = threading.Event()
    frdm_tx_queue: queue.Queue[str] | None = queue.Queue() if args.frdm_uart_port else None
    controller.frdm_uart_tx_queue = frdm_tx_queue

    def request_stop() -> None:
        controller.stop.set()
        stop_threads.set()
        try:
            controller.command_queue.put_nowait(None)
        except Exception:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    frdm_reader: FrdmUartFanReader | None = None
    if args.frdm_uart_port:
        frdm_reader = FrdmUartFanReader(
            port=args.frdm_uart_port,
            baudrate=args.frdm_uart_baudrate,
            command_queue=controller.command_queue,
            tx_queue=frdm_tx_queue,
            loop=loop,
            reconnect_sec=args.frdm_uart_reconnect_sec,
            stop_event=stop_threads,
        )
        frdm_reader.start()

    ble_task = asyncio.create_task(controller.run_ble_forever())
    input_task = asyncio.create_task(controller.input_loop())
    done: set[asyncio.Task[Any]] = set()
    pending: set[asyncio.Task[Any]] = {ble_task, input_task}
    try:
        done, pending = await asyncio.wait({ble_task, input_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        request_stop()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await controller.disconnect_current_client()
        stop_threads.set()
        if frdm_reader is not None:
            frdm_reader.join()
    for task in done:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
    return 0


def run_self_test() -> None:
    low_nonzero_pwm = apply_min_nonzero_pwm(41)
    half_pwm = apply_min_nonzero_pwm(128)
    three_quarter_pwm = apply_min_nonzero_pwm(191)
    voice_faster_pwm = apply_min_nonzero_pwm(128)
    cases = {
        "FAN_SPEED:180": ["FAN_SPEED:180"],
        "fan speed 999": ["FAN_SPEED:255"],
        "PERCENT:50": ["FAN_ON", f"FAN_SPEED:{half_pwm}"],
        "Fan 1,50": ["FAN_ON", f"FAN_SPEED:{half_pwm}"],
        "FanSpeed 16": ["FAN_ON", f"FAN_SPEED:{low_nonzero_pwm}"],
        "EVT,Fan,1,100": ["FAN_ON", "FAN_SPEED:255"],
        "$EVT,Fan,0,0*00": ["FAN_OFF"],
        "FanSpeed 75": ["FAN_ON", f"FAN_SPEED:{three_quarter_pwm}"],
        "voice led off": ["LED_OFF"],
        "voice fan faster": ["FAN_ON", f"FAN_SPEED:{voice_faster_pwm}"],
        "voice 調高風扇": ["FAN_ON", f"FAN_SPEED:{voice_faster_pwm}"],
        "voice 開燈以及開風扇": ["LED_ON", "FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(96)}"],
    }
    status = Esp32Status(raw="TEMP:27.31,FAN:ON,SPEED:96,LED:ON", temp_c=27.31, fan="ON", speed=96, led="ON")
    for raw, expected in cases.items():
        actual = resolve_input_to_ble_commands(raw, status)
        if actual != expected:
            raise AssertionError(f"{raw!r}: expected {expected}, got {actual}")
    off_status = Esp32Status(raw="TEMP:24.00,FAN:OFF,SPEED:0,LED:OFF", temp_c=24.0, fan="OFF", speed=0, led="OFF")
    off_faster = resolve_input_to_ble_commands("調高風扇", off_status)
    expected_off_faster = ["FAN_ON", f"FAN_SPEED:{apply_min_nonzero_pwm(32)}"]
    if off_faster != expected_off_faster:
        raise AssertionError(f"fan faster from off failed: expected {expected_off_faster}, got {off_faster}")
    parsed = parse_status("TEMP:27.31,FAN:ON,SPEED:180,LED:ON")
    if parsed.temp_c != 27.31 or parsed.fan != "ON" or parsed.speed != 180 or parsed.led != "ON":
        raise AssertionError(f"status parse failed: {parsed}")
    if format_temp_room_uart_line(parsed.temp_c) != "TempRoom 273":
        raise AssertionError(f"TempRoom formatting failed: {format_temp_room_uart_line(parsed.temp_c)}")
    print("self-test OK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control ESP32S3_FAN_LED_TEMP over BLE from Jetson Nano.")
    parser.add_argument("--name", default=os.getenv("ESP32S3_BLE_NAME", DEVICE_NAME), help="BLE device name to scan for.")
    parser.add_argument("--address", default=os.getenv("ESP32S3_BLE_ADDRESS", ""), help="Optional BLE MAC/address; skips name scan.")
    parser.add_argument("--adapter", default=os.getenv("BLE_ADAPTER", ""), help="Optional BlueZ adapter, e.g. hci0.")
    parser.add_argument("--scan-mode", choices=["active", "passive"], default=os.getenv("BLE_SCAN_MODE", "active"), help="BLE scanning mode. active requests scan responses.")
    parser.add_argument("--scan-duplicates", action="store_true", help="Ask BlueZ to report duplicate advertisement data during scans.")
    parser.add_argument("--scan-filter-service", action="store_true", help="Ask BlueZ to filter scan results by the ESP32 service UUID.")
    parser.add_argument("--scan-timeout", type=float, default=float(os.getenv("ESP32S3_BLE_SCAN_TIMEOUT", "8.0")))
    parser.add_argument("--connect-timeout", type=float, default=float(os.getenv("ESP32S3_BLE_CONNECT_TIMEOUT", "12.0")))
    parser.add_argument("--reconnect-sec", type=float, default=float(os.getenv("ESP32S3_BLE_RECONNECT_SEC", "3.0")))
    parser.add_argument("--write-with-response", action="store_true", help="Force BLE write with response.")
    parser.add_argument(
        "--write-response-auto",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Let bleak choose write response mode from characteristic properties.",
    )
    parser.add_argument("--read-status-on-connect", action=argparse.BooleanOptionalAction, default=True)

    parser.add_argument("--frdm-uart-port", default=os.getenv("FRDM_UART_PORT", ""), help="Optional FRDM UART port, or auto.")
    parser.add_argument("--frdm-uart-baudrate", type=int, default=int(os.getenv("FRDM_UART_BAUDRATE", "115200")))
    parser.add_argument("--frdm-uart-reconnect-sec", type=float, default=float(os.getenv("FRDM_UART_RECONNECT_SEC", "1.0")))
    parser.add_argument("--frdm-temp-room-interval-sec", type=float, default=float(os.getenv("TEMP_ROOM_UART_INTERVAL_SEC", "10.0")), help="When --frdm-uart-port is set, send TempRoom <Celsius*10> to FRDM at this interval. Use 0 to disable.")
    parser.add_argument("--no-frdm-temp-room-uart", action="store_true", help="Do not write periodic TempRoom UART updates to FRDM.")

    parser.add_argument("--min-fan-pwm", type=int, default=min_nonzero_pwm(), help="Minimum nonzero PWM sent for percent/FRDM fan speeds.")
    parser.add_argument("--voice-speed-step", type=int, default=int(os.getenv("FAN_VOICE_SPEED_STEP", "32")))
    parser.add_argument("--passive-threshold", type=float, default=float(os.getenv("FAN_PASSIVE_THRESHOLD_C", "25.0")))
    parser.add_argument("--passive-cooldown-sec", type=float, default=float(os.getenv("FAN_PASSIVE_COOLDOWN_SEC", "120.0")))
    parser.add_argument("--no-passive-reminder", action="store_true", help="Do not remind when temperature is above threshold.")
    parser.add_argument(
        "--passive-message",
        default=os.getenv("FAN_PASSIVE_MESSAGE", "現在溫度 {temp:.1f} 度，有點熱，要不要幫你開風扇？"),
        help="Reminder text. Supports {temp} and {threshold}.",
    )
    parser.add_argument("--tts-url", default=os.getenv("TTS_BASE_URL", "http://127.0.0.1:8777"))
    parser.add_argument("--tts-timeout", type=float, default=float(os.getenv("TTS_TIMEOUT", "1.5")))
    parser.add_argument("--no-tts-reminder", action="store_true", help="Print passive reminders without calling Piper TTS.")
    parser.add_argument("--scan-only", action="store_true", help="List nearby BLE devices and exit without connecting.")
    parser.add_argument("--scan-target-only", action="store_true", help="With --scan-only, print only devices matching the expected name/service UUID.")
    parser.add_argument("--scan-sort", choices=["name", "rssi"], default="name", help="Sort --scan-only output.")
    parser.add_argument("--min-rssi", type=int, default=None, help="With --scan-only, hide devices weaker than this RSSI, e.g. -65.")
    parser.add_argument(
        "--recover-bluez-stale",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ESP32S3_BLE_RECOVER_BLUEZ_STALE", True),
        help="Disconnect stale local BlueZ ESP32 connections before scanning.",
    )
    parser.add_argument("--self-test", action="store_true", help="Run parser/conversion tests and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.min_fan_pwm <= 255:
        print("ERROR: --min-fan-pwm must be 0..255.", file=sys.stderr)
        return 2
    os.environ["FAN_MIN_PWM"] = str(args.min_fan_pwm)
    if args.self_test:
        run_self_test()
        return 0
    if args.scan_timeout <= 0 or args.connect_timeout <= 0 or args.reconnect_sec <= 0:
        print("ERROR: BLE timeouts/reconnect values must be > 0.", file=sys.stderr)
        return 2
    if args.frdm_uart_baudrate <= 0:
        print("ERROR: --frdm-uart-baudrate must be > 0.", file=sys.stderr)
        return 2
    if args.frdm_temp_room_interval_sec < 0:
        print("ERROR: --frdm-temp-room-interval-sec must be >= 0.", file=sys.stderr)
        return 2
    if args.scan_only:
        try:
            return asyncio.run(scan_only(args))
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
