#!/usr/bin/env python3
"""Jetson local dashboard API for the MakeNTU smart home demo."""

from __future__ import annotations

import argparse
import errno
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import glob
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
DEFAULT_DATA_DIR = THIS_DIR / "data"
DEFAULT_TODO_LIST_PATH = PROJECT_ROOT / "frdm_uart_context_sender" / "logs" / "todo_list.json"
DEFAULT_FOCUS_ROOT = PROJECT_ROOT / "frdm_uart_context_sender" / "logs" / "focus_sessions"
DEFAULT_FOCUS_TEST_ROOT = Path("/tmp/focus_voice_test")
DEFAULT_MUSIC_URL = os.getenv("MUSIC_TOOL_URL", "http://127.0.0.1:8788/music")
DEFAULT_WEATHER_URL = os.getenv("WEATHER_TOOL_URL", "http://127.0.0.1:8788/weather")
DEFAULT_MUSIC_HEALTH_URL = os.getenv("MUSIC_TOOL_HEALTH_URL", "http://127.0.0.1:8788/health")
DEFAULT_TTS_HEALTH_URL = os.getenv("TTS_HEALTH_URL", "http://127.0.0.1:8777/health")
DEFAULT_AI_HEALTH_URL = os.getenv("AI_SERVER_HEALTH_URL", "http://100.108.141.26:8766/health")
DEFAULT_AI_DEBUG_LOG = os.getenv("AI_DEBUG_LOG", str(PROJECT_ROOT / "frdm_uart_context_sender" / "logs" / "ai_trace.jsonl"))
DEFAULT_WAKE_STATUS_PATH = os.getenv("WAKE_STATUS_PATH", str(PROJECT_ROOT / "frdm_uart_context_sender" / "logs" / "wake_status.json"))
DEFAULT_WEATHER_LOCATION = os.getenv("WEATHER_DEFAULT_LOCATION", "Taipei")
DEFAULT_LOCAL_TEMPERATURE_URL = os.getenv("LOCAL_TEMPERATURE_URL", "http://127.0.0.1:8790/temperature")
DEFAULT_ESP32_STATUS_URL = os.getenv("ESP32_STATUS_URL", "http://127.0.0.1:8791/api/esp32/status")
DEFAULT_ESP32_CONTROL_URL = os.getenv("ESP32_CONTROL_URL", "http://127.0.0.1:8791/api/esp32/control")
DEFAULT_FRDM_POWER_CYCLE_MODE = os.getenv("DASHBOARD_FRDM_POWER_CYCLE_MODE", "usb-host")
DEFAULT_FRDM_USB_CONTROLLER = os.getenv("DASHBOARD_FRDM_USB_CONTROLLER", "3610000.usb")
UART_PREFERRED_KEYWORDS = ("frdm", "mcu", "cmsis", "dap", "nxp", "j-link", "linkserver", "mbed")
MAX_REQUEST_BYTES = 256_000


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def clean_text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def clean_display_text(value: Any, limit: int = 1000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback
    except Exception as exc:
        return {"_error": f"could not read {path}: {exc}", "_fallback": fallback}


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_local().tzinfo)
    return parsed.astimezone()


def request_json(handler: BaseHTTPRequestHandler) -> tuple[dict[str, Any] | None, str | None]:
    try:
        content_length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        return None, "invalid Content-Length"
    if content_length > MAX_REQUEST_BYTES:
        return None, "request too large"
    if content_length <= 0:
        return {}, None
    try:
        raw = handler.rfile.read(content_length).decode("utf-8")
        data = json.loads(raw or "{}")
    except Exception as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "JSON body must be an object"
    return data, None


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def bytes_response(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def quote_dashboard_field(value: Any, *, max_chars: int = 28, max_encoded_chars: int = 72) -> str:
    text = " ".join(str(value or "").strip().split())[:max_chars]
    encoded = urllib.parse.quote(text, safe="-_.~")
    while len(encoded) > max_encoded_chars and text:
        text = text[:-1]
        encoded = urllib.parse.quote(text, safe="-_.~")
    return encoded


def uart_packet_line(payload: str) -> str:
    checksum = 0
    for char in payload:
        checksum ^= ord(char) & 0xFF
    return f"${payload}*{checksum:02X}"


def todo_empty_data() -> dict[str, Any]:
    return {"version": 1, "next_id": 1, "items": []}


def normalize_todo_data(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return todo_empty_data()
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    cleaned: list[dict[str, Any]] = []
    max_id = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get("text"), 160)
        if not text:
            continue
        try:
            item_id = int(item.get("id", 0) or 0)
        except (TypeError, ValueError):
            item_id = 0
        if item_id <= 0:
            item_id = max_id + 1
        max_id = max(max_id, item_id)
        status = str(item.get("status", "open") or "open").strip().lower()
        if status not in {"open", "done"}:
            status = "open"
        cleaned.append(
            {
                "id": item_id,
                "text": text,
                "status": status,
                "created_at": str(item.get("created_at", "") or ""),
                "completed_at": item.get("completed_at") if item.get("completed_at") else None,
                "source": str(item.get("source", "dashboard") or "dashboard"),
            }
        )
    try:
        next_id = int(raw.get("next_id", max_id + 1) or max_id + 1)
    except (TypeError, ValueError):
        next_id = max_id + 1
    return {"version": 1, "next_id": max(next_id, max_id + 1), "items": cleaned}


def read_todo(path: Path) -> dict[str, Any]:
    data = read_json(path, todo_empty_data())
    if isinstance(data, dict) and "_fallback" in data:
        return normalize_todo_data(data.get("_fallback"))
    return normalize_todo_data(data)


def write_todo(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, normalize_todo_data(data))


def todo_counts(data: dict[str, Any]) -> dict[str, int]:
    items = data.get("items") if isinstance(data.get("items"), list) else []
    open_count = sum(1 for item in items if isinstance(item, dict) and item.get("status") == "open")
    done_count = sum(1 for item in items if isinstance(item, dict) and item.get("status") == "done")
    return {"open": open_count, "done": done_count, "total": open_count + done_count}


def todo_public(data: dict[str, Any]) -> dict[str, Any]:
    counts = todo_counts(data)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return {"ok": True, "counts": counts, "items": items, "path": ""}


def add_todo(path: Path, text: str, *, source: str = "dashboard") -> dict[str, Any]:
    item_text = clean_text(text, 160)
    if not item_text:
        return {"ok": False, "error": "todo text is empty"}
    data = read_todo(path)
    item_id = int(data.get("next_id", 1) or 1)
    item = {
        "id": item_id,
        "text": item_text,
        "status": "open",
        "created_at": now_iso(),
        "completed_at": None,
        "source": source,
    }
    data["items"].append(item)
    data["next_id"] = item_id + 1
    write_todo(path, data)
    view = todo_public(data)
    return {"ok": True, "action": "add", "item": item, "todo": view, **view}


def complete_todo(path: Path, item_id: int, *, source: str = "dashboard") -> dict[str, Any]:
    data = read_todo(path)
    target: dict[str, Any] | None = None
    for item in data["items"]:
        if not isinstance(item, dict):
            continue
        try:
            current_id = int(item.get("id", 0) or 0)
        except (TypeError, ValueError):
            current_id = 0
        if current_id == item_id:
            target = item
            break
    if target is None:
        return {"ok": False, "error": f"todo id {item_id} not found"}
    already_done = target.get("status") == "done"
    if not already_done:
        target["status"] = "done"
        target["completed_at"] = now_iso()
        target["source"] = source
        write_todo(path, data)
    view = todo_public(data)
    return {"ok": True, "action": "done", "already_done": already_done, "item": target, "todo": view, **view}


def clear_completed_todos(path: Path) -> dict[str, Any]:
    data = read_todo(path)
    before = len(data["items"])
    data["items"] = [item for item in data["items"] if not isinstance(item, dict) or item.get("status") != "done"]
    removed = before - len(data["items"])
    write_todo(path, data)
    view = todo_public(data)
    return {"ok": True, "action": "clear_completed", "removed": removed, "todo": view, **view}


def default_devices() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": now_iso(),
        "devices": [
            {
                "id": "living_light",
                "name": "LED Light",
                "type": "light",
                "room": "living_room",
                "state": "off",
                "value": 0,
                "unit": "%",
                "online": True,
            },
            {
                "id": "desk_fan",
                "name": "Desk Fan",
                "type": "fan",
                "room": "study",
                "state": "off",
                "value": 0,
                "unit": "%",
                "online": True,
            },
            {
                "id": "air_conditioner",
                "name": "Air Conditioner",
                "type": "ac",
                "room": "living_room",
                "state": "off",
                "value": 26,
                "unit": "C",
                "online": True,
            },
        ],
    }


def default_sensors() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": now_iso(),
        "sensors": [
            {
                "id": "home_temperature",
                "name": "Local Temperature",
                "type": "temperature",
                "room": "desk",
                "value": None,
                "unit": "C",
                "online": False,
                "source": "esp32",
            },
            {
                "id": "home_humidity",
                "name": "Home Humidity",
                "type": "humidity",
                "room": "living_room",
                "value": None,
                "unit": "%",
                "online": False,
                "source": "frdm",
            },
            {
                "id": "ambient_light",
                "name": "Ambient Light",
                "type": "light",
                "room": "living_room",
                "value": None,
                "unit": "lux",
                "online": False,
                "source": "frdm",
            },
            {
                "id": "pet_camera_motion",
                "name": "Pet Camera Motion",
                "type": "motion",
                "room": "home",
                "value": False,
                "unit": "",
                "online": camera_available("auto"),
                "source": "jetson",
            },
        ],
    }


def normalize_devices(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return default_devices()
    devices = raw.get("devices")
    if not isinstance(devices, list):
        return default_devices()
    cleaned: list[dict[str, Any]] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_id = clean_text(device.get("id"), 48)
        if not device_id:
            continue
        cleaned.append(
            {
                "id": device_id,
                "name": clean_text(device.get("name") or device_id, 80),
                "type": clean_text(device.get("type") or "device", 40),
                "room": clean_text(device.get("room") or "home", 48),
                "state": clean_text(device.get("state") or "off", 24).lower(),
                "value": device.get("value", 0),
                "unit": clean_text(device.get("unit") or "", 12),
                "online": bool(device.get("online", True)),
                "updated_at": str(device.get("updated_at", "") or ""),
            }
        )
    return {"version": 1, "updated_at": str(raw.get("updated_at") or now_iso()), "devices": cleaned}


def normalize_sensors(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return default_sensors()
    sensors = raw.get("sensors")
    if not isinstance(sensors, list):
        return default_sensors()
    cleaned: list[dict[str, Any]] = []
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        sensor_id = clean_text(sensor.get("id"), 48)
        if not sensor_id:
            continue
        cleaned.append(
            {
                "id": sensor_id,
                "name": clean_text(sensor.get("name") or sensor_id, 80),
                "type": clean_text(sensor.get("type") or "sensor", 40),
                "room": clean_text(sensor.get("room") or "home", 48),
                "value": sensor.get("value"),
                "unit": clean_text(sensor.get("unit") or "", 12),
                "online": bool(sensor.get("online", False)),
                "source": clean_text(sensor.get("source") or "frdm", 32),
                "updated_at": str(sensor.get("updated_at", "") or ""),
            }
        )
    return {"version": 1, "updated_at": str(raw.get("updated_at") or now_iso()), "sensors": cleaned}


def read_devices(path: Path) -> dict[str, Any]:
    raw = read_json(path, default_devices())
    data = normalize_devices(raw.get("_fallback") if isinstance(raw, dict) and "_fallback" in raw else raw)
    if not path.exists():
        atomic_write_json(path, data)
    return data


def read_sensors(path: Path) -> dict[str, Any]:
    raw = read_json(path, default_sensors())
    data = normalize_sensors(raw.get("_fallback") if isinstance(raw, dict) and "_fallback" in raw else raw)
    if not path.exists():
        atomic_write_json(path, data)
    return data


def write_devices(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = now_iso()
    atomic_write_json(path, normalize_devices(data))


def write_sensors(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = now_iso()
    atomic_write_json(path, normalize_sensors(data))


def set_device(path: Path, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean_id = clean_text(device_id, 48)
    data = read_devices(path)
    target: dict[str, Any] | None = None
    for device in data["devices"]:
        if isinstance(device, dict) and device.get("id") == clean_id:
            target = device
            break
    if target is None:
        return {"ok": False, "error": f"device {clean_id} not found"}
    if "state" in payload:
        state = clean_text(payload.get("state"), 24).lower()
        if state:
            target["state"] = state
    if "value" in payload:
        target["value"] = payload.get("value")
    if "online" in payload:
        target["online"] = bool(payload.get("online"))
    target["updated_at"] = now_iso()
    write_devices(path, data)
    return {"ok": True, "action": "set", "device": target, "devices": data["devices"]}


def update_sensor(path: Path, sensor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean_id = clean_text(sensor_id, 48)
    data = read_sensors(path)
    target: dict[str, Any] | None = None
    for sensor in data["sensors"]:
        if isinstance(sensor, dict) and sensor.get("id") == clean_id:
            target = sensor
            break
    if target is None:
        target = {
            "id": clean_id,
            "name": clean_text(payload.get("name") or clean_id, 80),
            "type": clean_text(payload.get("type") or "sensor", 40),
            "room": clean_text(payload.get("room") or "home", 48),
            "value": None,
            "unit": clean_text(payload.get("unit") or "", 12),
            "online": True,
            "source": clean_text(payload.get("source") or "dashboard", 32),
            "updated_at": "",
        }
        data["sensors"].append(target)
    for key in ("name", "type", "room", "unit", "source"):
        if key in payload:
            target[key] = clean_text(payload.get(key), 80 if key == "name" else 48)
    if "value" in payload:
        target["value"] = payload.get("value")
    if "online" in payload:
        target["online"] = bool(payload.get("online"))
    target["updated_at"] = now_iso()
    write_sensors(path, data)
    return {"ok": True, "action": "update", "sensor": target, "sensors": data["sensors"]}


def append_event(path: Path, event: dict[str, Any], *, limit: int = 300) -> None:
    raw = read_json(path, {"version": 1, "events": []})
    events = raw.get("events") if isinstance(raw, dict) and isinstance(raw.get("events"), list) else []
    record = {"at": now_iso(), **event}
    events.append(record)
    atomic_write_json(path, {"version": 1, "events": events[-limit:]})


def read_events(path: Path) -> dict[str, Any]:
    raw = read_json(path, {"version": 1, "events": []})
    events = raw.get("events") if isinstance(raw, dict) and isinstance(raw.get("events"), list) else []
    return {"ok": True, "events": [public_event(event) for event in reversed(events)]}


def public_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"type": "event", "at": now_iso()}
    output = dict(event)
    item = output.get("item") if isinstance(output.get("item"), dict) else {}
    device = output.get("device") if isinstance(output.get("device"), dict) else {}
    request = output.get("request") if isinstance(output.get("request"), dict) else {}
    result = output.get("result") if isinstance(output.get("result"), dict) else {}

    if output.get("type") in {"todo_add", "todo_done"} and item:
        output.setdefault("text", item.get("text", ""))
        output.setdefault("todo_id", item.get("id"))
    if output.get("type") == "device_set" and device:
        output.setdefault("device_id", device.get("id", ""))
        output.setdefault("name", device.get("name", device.get("id", "")))
        output.setdefault("state", device.get("state", ""))
        output.setdefault("value", device.get("value"))
    if output.get("type") == "music_control":
        output.setdefault("action", request.get("action") or result.get("action") or "")
        output.setdefault("title", music_display_title(result) or request.get("query") or "")
    return output


def http_get_json(url: str, *, timeout_sec: float = 1.2) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        raw = response.read(256_000).decode("utf-8")
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {"ok": False, "error": "response was not an object"}


def http_post_json(url: str, payload: dict[str, Any], *, timeout_sec: float = 5.0) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        raw = response.read(512_000).decode("utf-8")
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {"ok": False, "error": "response was not an object"}


def safe_get_json(url: str, *, timeout_sec: float = 1.2) -> dict[str, Any]:
    if not url:
        return {"ok": False, "error": "url not configured"}
    try:
        return http_get_json(url, timeout_sec=timeout_sec)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}


def safe_post_json(url: str, payload: dict[str, Any], *, timeout_sec: float = 5.0) -> dict[str, Any]:
    if not url:
        return {"ok": False, "error": "url not configured"}
    try:
        return http_post_json(url, payload, timeout_sec=timeout_sec)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            data = json.loads(body or "{}")
            if isinstance(data, dict):
                data.setdefault("ok", False)
                data.setdefault("status", exc.code)
                return data
        except Exception:
            pass
        return {"ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}


def weather_code_text(code: Any) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    if value == 0:
        return "Clear"
    if value in {1, 2, 3}:
        return "Cloudy"
    if value in {45, 48}:
        return "Fog"
    if value in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "Rain"
    if value in {71, 73, 75, 77, 85, 86}:
        return "Snow"
    if value in {95, 96, 99}:
        return "Thunderstorm"
    return "Weather"


def public_weather(data: dict[str, Any], *, default_location: str) -> dict[str, Any]:
    weather = data.get("weather") if isinstance(data.get("weather"), dict) else {}
    temp = weather.get("temperature_c")
    if temp is None:
        temp = weather.get("temperature_min_c")
    high = weather.get("temperature_max_c")
    rain = weather.get("precipitation_probability")
    if rain is None:
        rain = weather.get("precipitation_probability_max")
    code = weather.get("weather_code")
    condition = weather.get("condition") or weather_code_text(code)
    return {
        "ok": bool(data.get("ok", False)),
        "location": data.get("location") or default_location,
        "temperature_c": temp,
        "temperature_high_c": high,
        "condition": condition,
        "rain_probability": rain,
        "humidity": weather.get("relative_humidity") or weather.get("humidity"),
        "source": data.get("source") or "open-meteo",
        "updated_at": now_iso(),
        "raw": data,
    }


def music_display_title(data: dict[str, Any]) -> str:
    for key in ("youtube_title", "media_title", "now_playing_title", "title", "last_title"):
        value = clean_display_text(data.get(key), 180)
        if value:
            return value
    return clean_display_text(data.get("last_query") or data.get("query"), 180)


def music_display_artist(data: dict[str, Any]) -> str:
    for key in ("artist", "channel", "uploader", "creator", "backend", "last_backend"):
        value = clean_display_text(data.get(key), 80)
        if value:
            return value
    return ""


def public_music(data: dict[str, Any]) -> dict[str, Any]:
    active = bool(data.get("active", False))
    paused = bool(data.get("paused", False))
    if paused:
        status = "paused"
    elif active or data.get("action") in {"play", "resume"} and data.get("ok", False):
        status = "playing"
    else:
        status = "stopped"
    title = music_display_title(data)
    raw_volume = (
        data.get("volume_percent")
        if data.get("volume_percent") is not None
        else data.get("mpv_effective_volume")
        if data.get("mpv_effective_volume") is not None
        else data.get("volume")
        if data.get("volume") is not None
        else data.get("mpv_volume")
    )
    try:
        volume_percent = int(round(float(raw_volume)))
    except (TypeError, ValueError):
        volume_percent = None
    try:
        volume_max = int(round(float(data.get("volume_max", data.get("mpv_volume_max", 200)))))
    except (TypeError, ValueError):
        volume_max = 200
    return {
        **data,
        "ok": bool(data.get("ok", False)),
        "status": status,
        "title": title if status != "stopped" else "",
        "artist": music_display_artist(data) if status != "stopped" else "",
        "volume_percent": volume_percent,
        "volume_max": volume_max,
        "requested_query": data.get("last_query") or data.get("query") or "",
        "active": active,
        "paused": paused,
        "health": data,
    }


def numeric_value(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pwm_to_percent(value: Any) -> int:
    pwm = numeric_value(value)
    if pwm is None:
        return 0
    return max(0, min(100, int(round(pwm * 100.0 / 255.0))))


def upsert_sensor_view(sensors: dict[str, Any], sensor: dict[str, Any]) -> dict[str, Any]:
    data = normalize_sensors(sensors)
    sensor_id = clean_text(sensor.get("id"), 48)
    replaced = False
    for index, item in enumerate(data["sensors"]):
        if isinstance(item, dict) and item.get("id") == sensor_id:
            data["sensors"][index] = {**item, **sensor}
            replaced = True
            break
    if not replaced:
        data["sensors"].append(sensor)
    data["updated_at"] = now_iso()
    return normalize_sensors(data)


def upsert_device_view(devices: dict[str, Any], device: dict[str, Any]) -> dict[str, Any]:
    data = normalize_devices(devices)
    device_id = clean_text(device.get("id"), 48)
    replaced = False
    for index, item in enumerate(data["devices"]):
        if isinstance(item, dict) and item.get("id") == device_id:
            data["devices"][index] = {**item, **device}
            replaced = True
            break
    if not replaced:
        data["devices"].append(device)
    data["updated_at"] = now_iso()
    return normalize_devices(data)


def public_local_temperature(data: dict[str, Any]) -> dict[str, Any]:
    temp = numeric_value(data.get("temperature_c"))
    if temp is None:
        temp = numeric_value(data.get("temp_c"))
    return {
        "ok": bool(data.get("ok") is True and temp is not None),
        "temperature_c": temp,
        "unit": "C",
        "source": data.get("source") or "esp32",
        "age_sec": data.get("age_sec"),
        "updated_at": data.get("updated_at") or data.get("received_at_iso") or now_iso(),
        "raw": data,
    }


def public_wake_status(path: Path) -> dict[str, Any]:
    raw = read_json(path, {})
    if not isinstance(raw, dict) or not raw:
        return {"ok": False, "listening": False, "error": "wake status unavailable", "path": str(path)}
    updated_at = parse_iso_datetime(raw.get("updated_at"))
    age_sec = None
    if updated_at is not None:
        age_sec = max(0.0, (now_local() - updated_at).total_seconds())
    stale = age_sec is None or age_sec > 12.0
    phase = clean_text(raw.get("phase") or raw.get("state") or "", 48)
    listening = bool(raw.get("listening", False)) and not stale
    return {
        "ok": listening,
        "listening": listening,
        "stale": stale,
        "phase": phase or ("stale" if stale else "unknown"),
        "volume": raw.get("volume"),
        "recent_peak": raw.get("recent_peak"),
        "wake_score": raw.get("wake_score"),
        "wake_threshold": raw.get("wake_threshold"),
        "wake_volume_threshold": raw.get("wake_volume_threshold"),
        "noise_floor": raw.get("noise_floor"),
        "updated_at": raw.get("updated_at"),
        "age_sec": age_sec,
        "path": str(path),
        "raw": raw,
    }


def parse_first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_reply_from_debug(debug: dict[str, Any]) -> str:
    for key in ("output", "reply", "reply_preview", "model_reply", "model_output"):
        value = clean_display_text(debug.get(key), 1000)
        if value:
            return value

    raw = clean_display_text(debug.get("ollama_content_preview") or debug.get("ollama_content"), 1200)
    parsed = parse_first_json_object(raw) if raw else None
    if isinstance(parsed, dict):
        reply = clean_display_text(parsed.get("reply"), 1000)
        if reply:
            return reply

    match = re.search(r'"reply"\s*:\s*"((?:\\.|[^"\\])*)"', raw, flags=re.DOTALL)
    if match:
        try:
            decoded = json.loads(f'"{match.group(1)}"')
            reply = clean_display_text(decoded, 1000)
            if reply:
                return reply
        except json.JSONDecodeError:
            pass
    return raw


def ai_debug_entry(debug: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    if not isinstance(debug, dict) or not debug:
        return None
    transcript = clean_display_text(
        debug.get("input")
        or debug.get("transcript")
        or debug.get("transcript_text")
        or debug.get("transcript_preview")
        or debug.get("normalized_transcript"),
        1000,
    )
    reply = extract_reply_from_debug(debug)
    lowered_transcript = transcript.lower()
    if lowered_transcript == "pet_idle_reflection" or transcript.startswith("[PET_IDLE_REFLECTION]"):
        return None
    if not transcript and not reply:
        return None
    control = debug.get("control") if isinstance(debug.get("control"), dict) else {}
    return {
        "request_id": clean_text(debug.get("request_id"), 64),
        "timestamp": clean_text(debug.get("timestamp"), 64),
        "source": clean_text(debug.get("turn_source") or source, 80),
        "stage": clean_text(debug.get("stage"), 80),
        "model": clean_text(debug.get("model") or debug.get("ollama_model") or debug.get("vision_model") or debug.get("text_ollama_model"), 80),
        "input": transcript,
        "output": reply,
        "raw_output": clean_display_text(debug.get("raw_output") or debug.get("ollama_content_preview") or debug.get("ollama_content"), 1200),
        "parse_status": clean_text(debug.get("parse_status"), 80),
        "emotion": clean_text(debug.get("emotion") or debug.get("emotion_primary") or control.get("emotion"), 40),
        "screen_mode": clean_text(debug.get("screen_mode") or control.get("screen_mode"), 40),
        "head_motion": clean_text(debug.get("head_motion") or control.get("head_motion"), 40),
        "ok": bool(debug.get("ok", False)),
    }


def jsonl_tail_entries(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []
    entries: list[dict[str, Any]] = []
    for line in reversed(lines[-max(limit * 4, limit):]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entry = ai_debug_entry(parsed, source=str(path))
            if entry is not None:
                entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def load_ai_trace(ai_health: dict[str, Any], *, debug_log_path: str, limit: int) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 100))
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_entry(entry: dict[str, Any] | None) -> None:
        if entry is None:
            return
        key = entry.get("request_id") or f"{entry.get('timestamp')}:{entry.get('input')}:{entry.get('output')}"
        if key in seen:
            return
        seen.add(str(key))
        entries.append(entry)

    candidate_paths: list[Path] = []
    for raw_path in (debug_log_path, str(ai_health.get("debug_log") or ""), "fast_chat_debug.jsonl"):
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            candidate_paths.append(path)
    for path in candidate_paths:
        for entry in jsonl_tail_entries(path, limit=safe_limit):
            add_entry(entry)

    last_debug = ai_health.get("last_debug") if isinstance(ai_health.get("last_debug"), dict) else {}
    add_entry(ai_debug_entry(last_debug, source="ai-health:last_debug"))

    return {
        "ok": bool(ai_health.get("ok") is True or entries),
        "service": ai_health.get("service") or "ai_server",
        "model": ai_health.get("ollama_model") or "",
        "chat_ready": bool(ai_health.get("chat_ready") is True),
        "debug_log": ai_health.get("debug_log") or debug_log_path,
        "entries": entries[:safe_limit],
        "raw_health": ai_health,
    }


def resolve_health_url(tool_url: str, explicit_health_url: str = "") -> str:
    if explicit_health_url:
        return explicit_health_url
    parsed = urllib.parse.urlsplit(tool_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def focus_summary_paths(roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*/focus_summary.json"):
            key = str(path.resolve())
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def range_start(range_name: str) -> datetime | None:
    now = now_local()
    name = str(range_name or "all").lower()
    if name == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "week":
        return now - timedelta(days=7)
    if name == "month":
        return now - timedelta(days=31)
    return None


def focus_report_title(summary: dict[str, Any], *, session_id: str = "") -> str:
    existing = clean_text(summary.get("report_title"), 120)
    if existing:
        return existing

    started_at = parse_iso_datetime(summary.get("started_at"))
    if started_at is not None:
        return f"專心報告：{started_at.strftime('%Y/%m/%d/%H')} 開始的專注時段"

    raw_session_id = str(session_id or summary.get("session_id") or "").strip()
    match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})", raw_session_id)
    if match:
        year, month, day, hour = match.groups()
        return f"專心報告：{year}/{month}/{day}/{hour} 開始的專注時段"

    return "專心報告：未記錄開始時間的專注時段"


def load_focus_summaries(roots: list[Path], *, range_name: str = "all") -> list[dict[str, Any]]:
    start = range_start(range_name)
    summaries: list[dict[str, Any]] = []
    for path in focus_summary_paths(roots):
        raw = read_json(path, {})
        if not isinstance(raw, dict):
            continue
        ended_at = parse_iso_datetime(raw.get("ended_at")) or parse_iso_datetime(raw.get("started_at"))
        if start is not None and ended_at is not None and ended_at < start:
            continue
        item = {
            "session_id": raw.get("session_id") or path.parent.name,
            "report_title": focus_report_title(raw, session_id=str(raw.get("session_id") or path.parent.name)),
            "task": raw.get("task") or "",
            "started_at": raw.get("started_at"),
            "ended_at": raw.get("ended_at"),
            "duration_min": raw.get("duration_min", 0),
            "focus_percent": raw.get("focus_percent", 0),
            "focus_score": raw.get("focus_score", 0),
            "focused_min": raw.get("focused_min", 0),
            "distracted_min": raw.get("distracted_min", 0),
            "longest_focus_min": raw.get("longest_focus_min", 0),
            "state_stats": raw.get("state_stats") if isinstance(raw.get("state_stats"), dict) else {},
            "recommendation": raw.get("recommendation", ""),
            "encouragement": raw.get("encouragement", ""),
            "todo": raw.get("todo") if isinstance(raw.get("todo"), dict) else {},
            "path": str(path),
        }
        summaries.append(item)
    summaries.sort(key=lambda item: str(item.get("ended_at") or item.get("started_at") or ""), reverse=True)
    return summaries


def aggregate_focus(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        today = now_local().date().isoformat()
        return {
            "session_count": 0,
            "duration_min": 0,
            "focused_min": 0,
            "distracted_min": 0,
            "total_focus_min": 0,
            "total_distracted_min": 0,
            "average_focus_score": 0,
            "average_focus_percent": 0,
            "phone_detected_count": 0,
            "away_count": 0,
            "sleeping_count": 0,
            "date": today,
            "month": today[:7],
            "hourly": [],
            "daily": [],
        }
    duration = sum(float(item.get("duration_min", 0) or 0) for item in summaries)
    focused = sum(float(item.get("focused_min", 0) or 0) for item in summaries)
    distracted = sum(float(item.get("distracted_min", 0) or 0) for item in summaries)
    score = sum(float(item.get("focus_score", 0) or 0) for item in summaries) / len(summaries)
    percent = sum(float(item.get("focus_percent", 0) or 0) for item in summaries) / len(summaries)
    phone_count = state_count(summaries, "phone")
    away_count = state_count(summaries, "away")
    sleeping_count = state_count(summaries, "sleeping")
    first_date = summary_date(summaries[-1]) or now_local().date().isoformat()
    return {
        "session_count": len(summaries),
        "duration_min": round(duration, 1),
        "focused_min": round(focused, 1),
        "distracted_min": round(distracted, 1),
        "total_focus_min": round(focused, 1),
        "total_distracted_min": round(distracted, 1),
        "average_focus_score": round(score, 1),
        "average_focus_percent": round(percent, 1),
        "phone_detected_count": phone_count,
        "away_count": away_count,
        "sleeping_count": sleeping_count,
        "date": first_date,
        "month": first_date[:7],
        "hourly": focus_buckets(summaries, by="hour"),
        "daily": focus_buckets(summaries, by="date"),
    }


def summary_date(summary: dict[str, Any]) -> str | None:
    started = parse_iso_datetime(summary.get("started_at")) or parse_iso_datetime(summary.get("ended_at"))
    return started.date().isoformat() if started else None


def state_count(summaries: list[dict[str, Any]], state_name: str) -> int:
    total = 0
    for summary in summaries:
        stats = summary.get("state_stats") if isinstance(summary.get("state_stats"), dict) else {}
        state = stats.get(state_name) if isinstance(stats.get(state_name), dict) else {}
        try:
            total += int(state.get("count", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def focus_buckets(summaries: list[dict[str, Any]], *, by: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        started = parse_iso_datetime(summary.get("started_at")) or parse_iso_datetime(summary.get("ended_at"))
        if started is None:
            continue
        if by == "hour":
            key = f"{started.hour:02d}"
            bucket = buckets.setdefault(key, {"hour": started.hour, "focus_min": 0.0, "distracted_min": 0.0})
        else:
            key = started.date().isoformat()
            bucket = buckets.setdefault(key, {"date": key, "focus_min": 0.0, "distracted_min": 0.0})
        bucket["focus_min"] += float(summary.get("focused_min", 0) or 0)
        bucket["distracted_min"] += float(summary.get("distracted_min", 0) or 0)
    rows = list(buckets.values())
    rows.sort(key=lambda item: str(item.get("date", f"{int(item.get('hour', 0)):02d}")))
    for row in rows:
        row["focus_min"] = round(float(row.get("focus_min", 0) or 0), 1)
        row["distracted_min"] = round(float(row.get("distracted_min", 0) or 0), 1)
    return rows


def find_focus_session(roots: list[Path], session_id: str) -> Path | None:
    safe_id = Path(str(session_id)).name
    for root in roots:
        for session_dir in (root / safe_id,):
            if (session_dir / "focus_summary.json").exists():
                return session_dir
        for summary_path in root.glob("*/focus_summary.json"):
            raw = read_json(summary_path, {})
            if isinstance(raw, dict) and str(raw.get("session_id") or summary_path.parent.name) == safe_id:
                return summary_path.parent
    return None


def load_focus_session(roots: list[Path], session_id: str) -> dict[str, Any]:
    session_dir = find_focus_session(roots, session_id)
    if session_dir is None:
        return {"ok": False, "error": f"focus session {session_id} not found"}
    summary = read_json(session_dir / "focus_summary.json", {})
    report = ""
    log_entries: list[dict[str, Any]] = []
    try:
        report = (session_dir / "focus_report.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except Exception as exc:
        report = f"could not read report: {exc}"
    log_path = session_dir / "focus_log.jsonl"
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines()[-120:]:
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    log_entries.append(entry)
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    except Exception:
        pass
    if isinstance(summary, dict):
        summary.setdefault("report_title", focus_report_title(summary, session_id=session_id))
    return {"ok": True, "session_id": session_id, "summary": summary, "report": report, "entries": log_entries, "path": str(session_dir)}


def camera_candidates(camera_id: str) -> list[str | int]:
    value = str(camera_id or "auto").strip()
    if value.lower() != "auto":
        try:
            return [int(value)]
        except ValueError:
            return [value]
    candidates: list[str | int] = []
    for path in glob.glob("/dev/video*"):
        suffix = "".join(ch for ch in path if ch.isdigit())
        if suffix:
            candidates.append(int(suffix))
        else:
            candidates.append(path)
    return sorted(set(candidates), key=lambda item: str(item))


def camera_available(camera_id: str = "auto") -> bool:
    return bool(camera_candidates(camera_id))


def camera_placeholder_svg(message: str = "Camera unavailable") -> bytes:
    safe_message = clean_text(message, 80).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='960' height='540' viewBox='0 0 960 540'>"
        "<rect width='960' height='540' fill='#16202a'/>"
        "<rect x='70' y='70' width='820' height='400' rx='18' fill='#263542' stroke='#6ac6a5' stroke-width='4'/>"
        "<circle cx='480' cy='270' r='82' fill='none' stroke='#f6d36b' stroke-width='14'/>"
        "<circle cx='480' cy='270' r='28' fill='#f6d36b'/>"
        f"<text x='480' y='410' text-anchor='middle' font-family='Arial' font-size='34' fill='#f3f7fb'>{safe_message}</text>"
        "</svg>"
    )
    return svg.encode("utf-8")


def capture_camera_jpeg(camera_id: str, *, width: int, height: int, quality: int) -> tuple[bytes, str, dict[str, Any]]:
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception as exc:
        return camera_placeholder_svg(f"OpenCV unavailable: {exc}"), "image/svg+xml", {"ok": False, "error": str(exc)}

    last_error = ""
    for candidate in camera_candidates(camera_id):
        cap = cv2.VideoCapture(candidate)
        try:
            if not cap.isOpened():
                last_error = f"could not open {candidate}"
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            ok, frame = cap.read()
            if not ok or frame is None:
                last_error = f"could not read frame from {candidate}"
                continue
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), max(20, min(quality, 95))])
            if not ok:
                last_error = f"could not encode frame from {candidate}"
                continue
            return encoded.tobytes(), "image/jpeg", {"ok": True, "camera": candidate, "width": width, "height": height}
        finally:
            cap.release()
    return camera_placeholder_svg(last_error or "Camera unavailable"), "image/svg+xml", {"ok": False, "error": last_error or "camera unavailable"}


def discover_uart_ports() -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]

        for port in list_ports.comports():
            device = str(port.device)
            text = " ".join(str(value) for value in (port.device, port.description, port.manufacturer, port.product, port.hwid) if value)
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


def resolve_uart_port(port: str) -> str:
    requested = str(port or "").strip()
    if requested and requested.lower() != "auto":
        return requested
    ports = discover_uart_ports()
    preferred = [item for item in ports if item.get("preferred")]
    candidates = preferred or ports
    if len(candidates) == 1:
        return str(candidates[0]["device"])
    if not candidates:
        raise RuntimeError("no UART serial device is visible")
    details = ", ".join(str(item.get("device")) for item in candidates)
    raise RuntimeError(f"could not choose a UART automatically; candidates: {details}")


class FrdmSync:
    def __init__(self, *, enabled: bool, port: str, baudrate: int, todo_item_limit: int) -> None:
        self.enabled = enabled
        self.port = port
        self.baudrate = baudrate
        self.todo_item_limit = todo_item_limit
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "port": self.port or "", "baudrate": self.baudrate}

    def send_lines(self, lines: list[str], *, reason: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "frdm uart sync disabled"}
        if not lines:
            return {"ok": True, "sent": 0}
        try:
            import serial  # type: ignore[import-not-found]
        except Exception as exc:
            return {"ok": False, "error": f"pyserial unavailable: {exc}"}
        with self._lock:
            try:
                resolved_port = resolve_uart_port(self.port)
                with serial.Serial(resolved_port, self.baudrate, timeout=0.3, write_timeout=1.0) as ser:
                    for line in lines:
                        ser.write((line.strip() + "\r\n").encode("utf-8"))
                        ser.flush()
                        time.sleep(0.03)
                return {"ok": True, "sent": len(lines), "port": resolved_port, "reason": reason, "lines": lines}
            except Exception as exc:
                return {"ok": False, "error": str(exc), "reason": reason, "lines": lines}

    def todo_lines(self, todo_data: dict[str, Any]) -> list[str]:
        counts = todo_counts(todo_data)
        items = todo_data.get("items") if isinstance(todo_data.get("items"), list) else []
        open_items = [item for item in items if isinstance(item, dict) and item.get("status") == "open"]
        lines = [f"Todo {counts['open']},{counts['done']}"]
        for slot, item in enumerate(open_items[: max(0, self.todo_item_limit)], start=1):
            try:
                item_id = int(item.get("id", 0) or 0)
            except (TypeError, ValueError):
                item_id = 0
            if item_id <= 0:
                continue
            text = quote_dashboard_field(item.get("text", ""), max_chars=28, max_encoded_chars=72)
            lines.append(f"TodoItem {slot},{item_id},open,{text}")
        lines.append(f"TodoEnd {max(0, len(lines) - 1)}")
        return lines

    def sync_todo(self, todo_data: dict[str, Any], *, reason: str) -> dict[str, Any]:
        return self.send_lines(self.todo_lines(todo_data), reason=reason)

    def sync_device(self, device: dict[str, Any], *, reason: str) -> dict[str, Any]:
        device_id = quote_dashboard_field(device.get("id", ""), max_chars=32, max_encoded_chars=48)
        state = quote_dashboard_field(device.get("state", ""), max_chars=16, max_encoded_chars=24)
        value = quote_dashboard_field(device.get("value", ""), max_chars=16, max_encoded_chars=24)
        return self.send_lines([f"Device {device_id},{state},{value}"], reason=reason)

    def reset(self, *, reason: str) -> dict[str, Any]:
        seq = int(time.time()) % 100000
        lines = [
            "Reset 0 0",
            "Reboot 0 0",
            uart_packet_line(f"RESET,{seq}"),
        ]
        return self.send_lines(lines, reason=reason)


class FrdmPowerCycle:
    def __init__(
        self,
        *,
        mode: str,
        usb_controller: str,
        power_off_sec: float,
        settle_sec: float,
        script: str,
        uhubctl_location: str,
        uhubctl_port: str,
        timeout_sec: float,
    ) -> None:
        self.mode = str(mode or "disabled").strip().lower()
        self.usb_controller = str(usb_controller or DEFAULT_FRDM_USB_CONTROLLER).strip()
        self.power_off_sec = max(0.2, min(float(power_off_sec or 2.0), 20.0))
        self.settle_sec = max(0.0, min(float(settle_sec or 4.0), 30.0))
        self.script = str(script or "").strip()
        self.uhubctl_location = str(uhubctl_location or "").strip()
        self.uhubctl_port = str(uhubctl_port or "").strip()
        self.timeout_sec = max(3.0, min(float(timeout_sec or 20.0), 90.0))
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "usb_controller": self.usb_controller,
            "power_off_sec": self.power_off_sec,
            "settle_sec": self.settle_sec,
            "script": self.script,
            "uhubctl_location": self.uhubctl_location,
            "uhubctl_port": self.uhubctl_port,
        }

    def power_cycle(self, *, reason: str) -> dict[str, Any]:
        with self._lock:
            if self.mode == "disabled":
                return {
                    "ok": False,
                    "action": "frdm_power_cycle",
                    "skipped": True,
                    "reason": "frdm power cycle disabled",
                    **self.status(),
                }
            if self.mode == "script":
                return self._power_cycle_script(reason=reason)
            if self.mode == "uhubctl":
                return self._power_cycle_uhubctl(reason=reason)
            if self.mode == "usb-host":
                return self._power_cycle_usb_host(reason=reason)
            return {
                "ok": False,
                "action": "frdm_power_cycle",
                "error": f"unsupported frdm power cycle mode: {self.mode}",
                **self.status(),
            }

    def _run(self, command: list[str], *, timeout_sec: float | None = None, input_text: str | None = None) -> dict[str, Any]:
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                timeout=timeout_sec or self.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            return {"ok": False, "error": f"command timed out: {exc}", "command": command}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "command": command}
        payload = {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": clean_display_text(result.stdout, 1200),
            "stderr": clean_display_text(result.stderr, 1200),
            "command": command,
        }
        stderr_lower = payload["stderr"].lower()
        if not payload["ok"] and "sudo" in stderr_lower and "password" in stderr_lower:
            payload["error"] = (
                "sudo needs a password; configure passwordless sudo for FRDM power cycle "
                "or run the dashboard as root"
            )
        return payload

    def _power_cycle_script(self, *, reason: str) -> dict[str, Any]:
        if not self.script:
            return {"ok": False, "action": "frdm_power_cycle", "mode": "script", "error": "frdm power cycle script is not configured"}
        script_path = Path(self.script).expanduser()
        if not script_path.exists():
            return {"ok": False, "action": "frdm_power_cycle", "mode": "script", "error": f"script not found: {script_path}"}
        command = [str(script_path)] if os.access(script_path, os.X_OK) else ["bash", str(script_path)]
        env_command = command + [str(self.power_off_sec), str(self.settle_sec)]
        result = self._run(env_command)
        result.update({"action": "frdm_power_cycle", "mode": "script", "reason": reason, "script": str(script_path)})
        return result

    def _power_cycle_uhubctl(self, *, reason: str) -> dict[str, Any]:
        uhubctl = shutil.which("uhubctl")
        if not uhubctl:
            return {"ok": False, "action": "frdm_power_cycle", "mode": "uhubctl", "error": "uhubctl not found"}
        if not self.uhubctl_location or not self.uhubctl_port:
            return {
                "ok": False,
                "action": "frdm_power_cycle",
                "mode": "uhubctl",
                "error": "uhubctl location/port are not configured",
            }
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        off = self._run(prefix + [uhubctl, "-l", self.uhubctl_location, "-p", self.uhubctl_port, "-a", "off"])
        if not off.get("ok"):
            off.update({"action": "frdm_power_cycle", "mode": "uhubctl", "stage": "power_off", "reason": reason})
            return off
        time.sleep(self.power_off_sec)
        on = self._run(prefix + [uhubctl, "-l", self.uhubctl_location, "-p", self.uhubctl_port, "-a", "on"])
        time.sleep(self.settle_sec)
        return {
            "ok": bool(on.get("ok")),
            "action": "frdm_power_cycle",
            "mode": "uhubctl",
            "reason": reason,
            "power_off_sec": self.power_off_sec,
            "settle_sec": self.settle_sec,
            "off": off,
            "on": on,
            "error": "" if on.get("ok") else on.get("stderr") or on.get("error") or "uhubctl power-on failed",
        }

    def _power_cycle_usb_host(self, *, reason: str) -> dict[str, Any]:
        controller = self.usb_controller
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", controller):
            return {"ok": False, "action": "frdm_power_cycle", "mode": "usb-host", "error": f"unsafe usb controller name: {controller!r}"}
        unbind_path = Path("/sys/bus/platform/drivers/tegra-xusb/unbind")
        bind_path = Path("/sys/bus/platform/drivers/tegra-xusb/bind")
        if os.geteuid() == 0:
            try:
                unbind_path.write_text(controller, encoding="utf-8")
                time.sleep(self.power_off_sec)
                bind_path.write_text(controller, encoding="utf-8")
                time.sleep(self.settle_sec)
                return {
                    "ok": True,
                    "action": "frdm_power_cycle",
                    "mode": "usb-host",
                    "reason": reason,
                    "usb_controller": controller,
                    "power_off_sec": self.power_off_sec,
                    "settle_sec": self.settle_sec,
                }
            except Exception as exc:
                return {"ok": False, "action": "frdm_power_cycle", "mode": "usb-host", "error": str(exc), "usb_controller": controller}

        tee = shutil.which("tee") or "/usr/bin/tee"
        off = self._run(["sudo", "-n", tee, str(unbind_path)], timeout_sec=self.timeout_sec, input_text=f"{controller}\n")
        if not off.get("ok"):
            off.update(
                {
                    "action": "frdm_power_cycle",
                    "mode": "usb-host",
                    "stage": "power_off",
                    "reason": reason,
                    "usb_controller": controller,
                    "sudoers_hint": (
                        f"Allow NOPASSWD for: {tee} {unbind_path} and {tee} {bind_path}"
                    ),
                }
            )
            off.setdefault("error", off.get("stderr") or "USB host power-off failed")
            return off

        time.sleep(self.power_off_sec)
        on = self._run(["sudo", "-n", tee, str(bind_path)], timeout_sec=self.timeout_sec, input_text=f"{controller}\n")
        time.sleep(self.settle_sec)
        return {
            "ok": bool(on.get("ok")),
            "action": "frdm_power_cycle",
            "mode": "usb-host",
            "reason": reason,
            "usb_controller": controller,
            "power_off_sec": self.power_off_sec,
            "settle_sec": self.settle_sec,
            "off": off,
            "on": on,
            "sudoers_hint": f"Allow NOPASSWD for: {tee} {unbind_path} and {tee} {bind_path}",
            "error": "" if on.get("ok") else on.get("error") or on.get("stderr") or "USB host power-on failed",
        }


class DashboardState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.data_dir = Path(args.data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.todo_path = Path(args.todo_path).expanduser()
        self.devices_path = self.data_dir / "devices.json"
        self.sensors_path = self.data_dir / "sensors.json"
        self.events_path = self.data_dir / "events.json"
        self.focus_roots = [Path(path).expanduser() for path in args.focus_root]
        self.music_url = args.music_url
        self.weather_url = args.weather_url
        self.music_health_url = resolve_health_url(args.music_url, args.music_health_url)
        self.weather_default_location = args.weather_default_location
        self.tts_health_url = args.tts_health_url
        self.ai_health_url = args.ai_health_url
        self.ai_debug_log = args.ai_debug_log
        self.wake_status_path = Path(args.wake_status_path).expanduser()
        self.local_temperature_url = args.local_temperature_url
        self.esp32_status_url = args.esp32_status_url
        self.esp32_control_url = args.esp32_control_url
        self.camera_id = args.camera_id
        self.camera_width = args.camera_width
        self.camera_height = args.camera_height
        self.camera_jpeg_quality = args.camera_jpeg_quality
        frdm_enabled = bool(args.frdm_uart_port) and not args.no_frdm_uart
        self.frdm = FrdmSync(
            enabled=frdm_enabled,
            port=args.frdm_uart_port,
            baudrate=args.frdm_uart_baudrate,
            todo_item_limit=args.dashboard_todo_item_limit,
        )
        self.frdm_power = FrdmPowerCycle(
            mode=args.frdm_power_cycle_mode,
            usb_controller=args.frdm_usb_controller,
            power_off_sec=args.frdm_power_off_sec,
            settle_sec=args.frdm_power_settle_sec,
            script=args.frdm_power_cycle_script,
            uhubctl_location=args.frdm_uhubctl_location,
            uhubctl_port=args.frdm_uhubctl_port,
            timeout_sec=args.frdm_power_timeout,
        )
        read_devices(self.devices_path)
        read_sensors(self.sensors_path)
        if not self.events_path.exists():
            atomic_write_json(self.events_path, {"version": 1, "events": []})

    def sync_esp32_device(self, device: dict[str, Any]) -> dict[str, Any]:
        if not self.esp32_control_url:
            return {"ok": False, "skipped": True, "reason": "esp32 control url not configured"}
        device_type = clean_text(device.get("type"), 40).lower()
        device_id = clean_text(device.get("id"), 48)
        if device_type not in {"fan", "light"} and device_id not in {"desk_fan", "living_light"}:
            return {"ok": False, "skipped": True, "reason": "device is not an ESP32 appliance"}
        payload = {
            "device_id": device_id,
            "type": device_type,
            "state": clean_text(device.get("state"), 24).lower(),
            "value": device.get("value"),
            "source": "dashboard",
        }
        return safe_post_json(self.esp32_control_url, payload, timeout_sec=2.5)

    def status_payload(self) -> dict[str, Any]:
        todo = read_todo(self.todo_path)
        todo_view = todo_public(todo)
        todo_view["path"] = str(self.todo_path)
        devices = read_devices(self.devices_path)
        sensors = read_sensors(self.sensors_path)
        local_temperature = public_local_temperature(safe_get_json(self.local_temperature_url, timeout_sec=0.5))
        esp32_status = safe_get_json(self.esp32_status_url, timeout_sec=0.5) if self.esp32_status_url else {"ok": False}
        esp32_temperature = numeric_value(esp32_status.get("temperature_c"))
        if esp32_temperature is None:
            esp32_temperature = numeric_value(esp32_status.get("temp_c"))
        if not local_temperature.get("ok") and esp32_status.get("ok") and esp32_temperature is not None:
            local_temperature = {
                "ok": True,
                "temperature_c": esp32_temperature,
                "unit": "C",
                "source": "esp32-ble-status",
                "age_sec": None,
                "updated_at": esp32_status.get("updated_at") or now_iso(),
                "raw": esp32_status,
            }
        if local_temperature.get("ok"):
            sensors = upsert_sensor_view(
                sensors,
                {
                    "id": "home_temperature",
                    "name": "Local Temperature",
                    "type": "temperature",
                    "room": "desk",
                    "value": local_temperature.get("temperature_c"),
                    "unit": "C",
                    "online": True,
                    "source": local_temperature.get("source") or "esp32",
                    "updated_at": local_temperature.get("updated_at") or now_iso(),
                },
            )
        if esp32_status.get("ok"):
            fan_state = str(esp32_status.get("fan") or "").strip().lower()
            fan_percent = pwm_to_percent(esp32_status.get("speed"))
            if fan_state == "on" and fan_percent <= 0:
                fan_percent = 1
            devices = upsert_device_view(
                devices,
                {
                    "id": "desk_fan",
                    "name": "Desk Fan",
                    "type": "fan",
                    "room": "desk",
                    "state": "on" if fan_state == "on" and fan_percent > 0 else "off",
                    "value": fan_percent,
                    "unit": "%",
                    "online": True,
                    "updated_at": esp32_status.get("updated_at") or now_iso(),
                },
            )
            led_state = str(esp32_status.get("led") or "").strip().lower()
            if led_state in {"on", "off"}:
                devices = upsert_device_view(
                    devices,
                    {
                        "id": "living_light",
                        "name": "LED Light",
                        "type": "light",
                        "room": "desk",
                        "state": led_state,
                        "value": 100 if led_state == "on" else 0,
                        "unit": "%",
                        "online": True,
                        "updated_at": esp32_status.get("updated_at") or now_iso(),
                    },
                )
        focus = load_focus_summaries(self.focus_roots, range_name="today")
        music_health = safe_get_json(self.music_health_url, timeout_sec=0.8)
        weather_current = safe_post_json(
            self.weather_url,
            {"text": f"{self.weather_default_location} weather", "default_location": self.weather_default_location},
            timeout_sec=3.0,
        )
        weather_view = public_weather(weather_current, default_location=self.weather_default_location)
        tts_health = safe_get_json(self.tts_health_url, timeout_sec=0.8)
        ai_health = safe_get_json(self.ai_health_url, timeout_sec=0.9)
        wake_status = public_wake_status(self.wake_status_path)
        camera_ok = camera_available(self.camera_id)
        ai_ok = bool(ai_health.get("chat_ready") is True or ai_health.get("ok") is True)
        tts_ok = bool(tts_health.get("ready") is True or tts_health.get("ok") is True)
        wake_ok = bool(wake_status.get("ok"))
        music_ok = bool(music_health.get("ok") is True)
        weather_ok = bool(weather_view.get("ok") is True or music_health.get("weather_available") is True)
        frdm_status = self.frdm.status()
        frdm_seen = bool(frdm_status.get("enabled")) or bool(discover_uart_ports())
        return {
            "ok": True,
            "service": "make_ntu_smart_home_dashboard",
            "time": {
                "iso": now_iso(),
                "date": now_local().strftime("%Y%m%d"),
                "time": now_local().strftime("%H%M%S"),
                "weekday": now_local().isoweekday(),
                "utc_offset_min": int(now_local().utcoffset().total_seconds() // 60) if now_local().utcoffset() else 0,
            },
            "home": {
                "mode": "Home" if camera_ok else "Unknown",
                "person_detected": None,
                "last_update": now_iso(),
            },
            "devices": devices.get("devices", []),
            "device_state": devices,
            "sensors": sensors.get("sensors", []),
            "sensor_state": sensors,
            "local_temperature": local_temperature,
            "esp32": esp32_status,
            "wake": wake_status,
            "todo": todo_view,
            "focus": {"today": aggregate_focus(focus), "recent": focus[:8]},
            "music": public_music(music_health),
            "weather": weather_view,
            "health": {
                "ai": ai_ok,
                "ai_server": ai_ok,
                "tts": tts_ok,
                "wake": wake_ok,
                "music": music_ok,
                "weather": weather_ok,
                "camera": camera_ok,
                "frdm_panel": frdm_seen,
                "frdm_uart": frdm_status,
                "frdm_power": self.frdm_power.status(),
                "details": {"ai": ai_health, "tts": tts_health, "wake": wake_status, "music": music_health, "weather": weather_current},
            },
        }


class DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], state: DashboardState) -> None:
        super().__init__(server_address, handler)
        self.state = state


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "MakeNTUSmartHomeDashboard/0.1"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    @property
    def state(self) -> DashboardState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def parsed(self) -> urllib.parse.SplitResult:
        return urllib.parse.urlsplit(self.path)

    def query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(self.parsed().query)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        path = self.parsed().path.rstrip("/") or "/"
        if path in {"/", "/dashboard"}:
            self.handle_dashboard_page()
            return
        if path.startswith("/static/"):
            self.handle_static(path)
            return
        if path == "/api/status":
            json_response(self, 200, self.state.status_payload())
            return
        if path == "/api/devices":
            json_response(self, 200, {"ok": True, **read_devices(self.state.devices_path)})
            return
        if path == "/api/sensors":
            json_response(self, 200, {"ok": True, **read_sensors(self.state.sensors_path)})
            return
        if path == "/api/todo":
            todo = todo_public(read_todo(self.state.todo_path))
            todo["path"] = str(self.state.todo_path)
            json_response(self, 200, todo)
            return
        if path == "/api/events":
            json_response(self, 200, read_events(self.state.events_path))
            return
        if path == "/api/ai/trace":
            query = self.query()
            try:
                limit = int(query.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            ai_health = safe_get_json(self.state.ai_health_url, timeout_sec=1.2)
            payload = load_ai_trace(ai_health, debug_log_path=self.state.ai_debug_log, limit=limit)
            json_response(self, 200 if payload.get("ok") else 503, payload)
            return
        if path == "/api/focus/summaries":
            range_name = self.query().get("range", ["all"])[0]
            summaries = load_focus_summaries(self.state.focus_roots, range_name=range_name)
            json_response(self, 200, {"ok": True, "range": range_name, "summary": aggregate_focus(summaries), "sessions": summaries})
            return
        if path.startswith("/api/focus/session/"):
            session_id = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            payload = load_focus_session(self.state.focus_roots, session_id)
            json_response(self, 200 if payload.get("ok") else 404, payload)
            return
        if path == "/api/music/status":
            payload = safe_get_json(self.state.music_health_url, timeout_sec=1.2)
            json_response(self, 200 if payload.get("ok") else 503, public_music(payload))
            return
        if path == "/api/weather":
            query = self.query()
            location = query.get("location", [self.state.weather_default_location])[0]
            text = query.get("text", [f"{location} weather"])[0]
            payload = safe_post_json(
                self.state.weather_url,
                {"text": text, "default_location": location, "location": location},
                timeout_sec=6.0,
            )
            view = public_weather(payload, default_location=location)
            json_response(self, 200 if payload.get("ok") else 503, view)
            return
        if path == "/api/camera/latest":
            self.handle_camera_latest()
            return
        if path == "/api/camera/stream":
            self.handle_camera_stream()
            return
        json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = self.parsed().path.rstrip("/") or "/"
        data, error = request_json(self)
        if error:
            json_response(self, 400, {"ok": False, "error": error})
            return
        assert data is not None

        if path == "/api/todo":
            result = add_todo(self.state.todo_path, str(data.get("text") or ""), source=str(data.get("source") or "dashboard"))
            if result.get("ok"):
                sync = self.state.frdm.sync_todo(read_todo(self.state.todo_path), reason="dashboard todo add")
                result["frdm_sync"] = sync
                append_event(self.state.events_path, {"type": "todo_add", "item": result.get("item"), "frdm_sync": sync})
            json_response(self, 200 if result.get("ok") else 400, result)
            return
        if path.startswith("/api/todo/") and path.endswith("/done"):
            parts = path.split("/")
            try:
                item_id = int(parts[3])
            except (IndexError, ValueError):
                json_response(self, 400, {"ok": False, "error": "invalid todo id"})
                return
            result = complete_todo(self.state.todo_path, item_id, source=str(data.get("source") or "dashboard"))
            if result.get("ok"):
                sync = self.state.frdm.sync_todo(read_todo(self.state.todo_path), reason="dashboard todo done")
                result["frdm_sync"] = sync
                append_event(self.state.events_path, {"type": "todo_done", "item": result.get("item"), "frdm_sync": sync})
            json_response(self, 200 if result.get("ok") else 404, result)
            return
        if path == "/api/todo/clear-completed":
            result = clear_completed_todos(self.state.todo_path)
            sync = self.state.frdm.sync_todo(read_todo(self.state.todo_path), reason="dashboard todo clear completed")
            result["frdm_sync"] = sync
            append_event(self.state.events_path, {"type": "todo_clear_completed", "removed": result.get("removed"), "frdm_sync": sync})
            json_response(self, 200, result)
            return
        if path.startswith("/api/devices/") and path.endswith("/set"):
            parts = path.split("/")
            try:
                device_id = urllib.parse.unquote(parts[3])
            except IndexError:
                json_response(self, 400, {"ok": False, "error": "invalid device id"})
                return
            result = set_device(self.state.devices_path, device_id, data)
            if result.get("ok"):
                esp32_control = self.state.sync_esp32_device(result["device"])
                result["esp32_control"] = esp32_control
                sync = self.state.frdm.sync_device(result["device"], reason="dashboard device set")
                result["frdm_sync"] = sync
                append_event(
                    self.state.events_path,
                    {
                        "type": "device_set",
                        "device": result.get("device"),
                        "frdm_sync": sync,
                        "esp32_control": esp32_control,
                    },
                )
            json_response(self, 200 if result.get("ok") else 404, result)
            return
        if path.startswith("/api/sensors/") and path.endswith("/update"):
            parts = path.split("/")
            try:
                sensor_id = urllib.parse.unquote(parts[3])
            except IndexError:
                json_response(self, 400, {"ok": False, "error": "invalid sensor id"})
                return
            result = update_sensor(self.state.sensors_path, sensor_id, data)
            append_event(self.state.events_path, {"type": "sensor_update", "sensor": result.get("sensor")})
            json_response(self, 200 if result.get("ok") else 400, result)
            return
        if path in {"/api/frdm/reset", "/api/frdm/power-cycle"}:
            result = self.state.frdm_power.power_cycle(reason="dashboard frdm power cycle")
            append_event(self.state.events_path, {"type": "frdm_power_cycle", "result": result})
            status = 200 if result.get("ok") else 503
            error = "" if result.get("ok") else str(result.get("error") or result.get("stderr") or result.get("reason") or "FRDM power cycle failed")
            json_response(
                self,
                status,
                {"ok": bool(result.get("ok")), "action": "frdm_power_cycle", "error": error, "frdm_power": result, **result},
            )
            return
        if path == "/api/music/control":
            action = clean_text(data.get("action"), 24).lower()
            payload = dict(data)
            if action:
                payload["action"] = action
            if action == "play" and not clean_text(payload.get("query"), 120):
                payload["query"] = os.getenv("DASHBOARD_DEFAULT_MUSIC_QUERY", "lofi study")
            if "text" not in payload and action == "play" and payload.get("query"):
                payload["text"] = f"play {payload.get('query')}"
            result = safe_post_json(self.state.music_url, payload, timeout_sec=6.0)
            view = public_music(result)
            append_event(self.state.events_path, {"type": "music_control", "request": payload, "result": view})
            json_response(self, 200 if result.get("ok") else 503, view)
            return
        if path == "/api/weather":
            payload = {
                "text": data.get("text") or f"{data.get('location') or self.state.weather_default_location} weather",
                "default_location": data.get("default_location") or data.get("location") or self.state.weather_default_location,
                **data,
            }
            result = safe_post_json(self.state.weather_url, payload, timeout_sec=6.0)
            view = public_weather(result, default_location=str(payload.get("default_location") or self.state.weather_default_location))
            append_event(self.state.events_path, {"type": "weather_lookup", "request": payload, "result": view})
            json_response(self, 200 if result.get("ok") else 503, view)
            return
        json_response(self, 404, {"ok": False, "error": "not found"})

    def handle_dashboard_page(self) -> None:
        index_path = THIS_DIR / "static" / "index.html"
        if index_path.exists():
            self.serve_file(index_path)
            return
        html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MakeNTU Smart Home Dashboard API</title>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #101820; color: #eef6f8; }
    main { max-width: 880px; margin: 0 auto; padding: 40px 24px; }
    code { background: #22313d; padding: 2px 6px; border-radius: 4px; }
    li { margin: 10px 0; }
  </style>
</head>
<body>
  <main>
    <h1>MakeNTU Smart Home Dashboard API</h1>
    <p>This Jetson service is ready. The website can use these endpoints:</p>
    <ul>
      <li><code>GET /api/status</code></li>
      <li><code>GET /api/camera/latest</code> and <code>GET /api/camera/stream</code></li>
      <li><code>GET/POST /api/todo</code>, <code>POST /api/todo/{id}/done</code></li>
      <li><code>GET /api/focus/summaries?range=today|week|month|all</code></li>
      <li><code>GET /api/ai/trace?limit=20</code></li>
      <li><code>GET /api/devices</code>, <code>POST /api/devices/{id}/set</code></li>
      <li><code>GET /api/sensors</code>, <code>POST /api/sensors/{id}/update</code></li>
      <li><code>GET /api/music/status</code>, <code>POST /api/music/control</code></li>
      <li><code>GET/POST /api/weather</code></li>
      <li><code>POST /api/frdm/power-cycle</code></li>
    </ul>
  </main>
</body>
</html>
"""
        text_response(self, 200, html, "text/html; charset=utf-8")

    def handle_static(self, path: str) -> None:
        rel = path.removeprefix("/static/").strip("/")
        candidate = (THIS_DIR / "static" / rel).resolve()
        root = (THIS_DIR / "static").resolve()
        if root not in candidate.parents and candidate != root:
            json_response(self, 403, {"ok": False, "error": "forbidden"})
            return
        if not candidate.exists() or not candidate.is_file():
            json_response(self, 404, {"ok": False, "error": "not found"})
            return
        self.serve_file(candidate)

    def serve_file(self, path: Path) -> None:
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        bytes_response(self, 200, body, content_type)

    def handle_camera_latest(self) -> None:
        body, content_type, info = capture_camera_jpeg(
            self.state.camera_id,
            width=self.state.camera_width,
            height=self.state.camera_height,
            quality=self.state.camera_jpeg_quality,
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Camera-Status", "ok" if info.get("ok") else "unavailable")
        if info.get("error"):
            self.send_header("X-Camera-Error", urllib.parse.quote(str(info.get("error"))[:160]))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_camera_stream(self) -> None:
        query = self.query()
        try:
            fps = float(query.get("fps", ["2"])[0])
        except ValueError:
            fps = 2.0
        delay = 1.0 / max(0.2, min(fps, 8.0))
        boundary = "makentu-frame"
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.end_headers()
        while True:
            body, content_type, _ = capture_camera_jpeg(
                self.state.camera_id,
                width=self.state.camera_width,
                height=self.state.camera_height,
                quality=self.state.camera_jpeg_quality,
            )
            try:
                self.wfile.write(f"--{boundary}\r\n".encode("ascii"))
                self.wfile.write(f"Content-Type: {content_type}\r\n".encode("ascii"))
                self.wfile.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
                self.wfile.write(body)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(delay)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Jetson smart home dashboard API.")
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8789")))
    parser.add_argument("--data-dir", default=os.getenv("DASHBOARD_DATA_DIR", str(DEFAULT_DATA_DIR)))
    parser.add_argument("--todo-path", default=os.getenv("TODO_LIST_PATH", str(DEFAULT_TODO_LIST_PATH)))
    parser.add_argument(
        "--focus-root",
        action="append",
        default=[str(DEFAULT_FOCUS_ROOT), str(DEFAULT_FOCUS_TEST_ROOT)],
        help="Focus session root. Can be passed multiple times.",
    )
    parser.add_argument("--music-url", default=DEFAULT_MUSIC_URL)
    parser.add_argument("--music-health-url", default=DEFAULT_MUSIC_HEALTH_URL)
    parser.add_argument("--weather-url", default=DEFAULT_WEATHER_URL)
    parser.add_argument("--weather-default-location", default=DEFAULT_WEATHER_LOCATION)
    parser.add_argument("--tts-health-url", default=DEFAULT_TTS_HEALTH_URL)
    parser.add_argument("--ai-health-url", default=DEFAULT_AI_HEALTH_URL)
    parser.add_argument("--ai-debug-log", default=DEFAULT_AI_DEBUG_LOG, help="Optional local fast_chat_debug.jsonl path for AI trace history.")
    parser.add_argument("--wake-status-path", default=DEFAULT_WAKE_STATUS_PATH)
    parser.add_argument("--local-temperature-url", default=DEFAULT_LOCAL_TEMPERATURE_URL)
    parser.add_argument("--esp32-status-url", default=DEFAULT_ESP32_STATUS_URL)
    parser.add_argument("--esp32-control-url", default=DEFAULT_ESP32_CONTROL_URL)
    parser.add_argument("--camera-id", default=os.getenv("DASHBOARD_CAMERA_ID", "auto"))
    parser.add_argument("--camera-width", type=int, default=int(os.getenv("DASHBOARD_CAMERA_WIDTH", "640")))
    parser.add_argument("--camera-height", type=int, default=int(os.getenv("DASHBOARD_CAMERA_HEIGHT", "360")))
    parser.add_argument("--camera-jpeg-quality", type=int, default=int(os.getenv("DASHBOARD_CAMERA_JPEG_QUALITY", "82")))
    parser.add_argument("--frdm-uart-port", default=os.getenv("DASHBOARD_FRDM_UART_PORT", ""))
    parser.add_argument("--frdm-uart-baudrate", type=int, default=int(os.getenv("DASHBOARD_FRDM_UART_BAUDRATE", "115200")))
    parser.add_argument("--no-frdm-uart", action="store_true", help="Do not sync dashboard changes to FRDM over UART.")
    parser.add_argument("--dashboard-todo-item-limit", type=int, default=int(os.getenv("DASHBOARD_TODO_ITEM_LIMIT", "8")))
    parser.add_argument(
        "--frdm-power-cycle-mode",
        choices=["usb-host", "uhubctl", "script", "disabled"],
        default=DEFAULT_FRDM_POWER_CYCLE_MODE,
        help="How the dashboard power-cycles FRDM. usb-host resets Jetson xUSB; script/uhubctl can target dedicated FRDM power hardware.",
    )
    parser.add_argument("--frdm-usb-controller", default=DEFAULT_FRDM_USB_CONTROLLER)
    parser.add_argument("--frdm-power-off-sec", type=float, default=float(os.getenv("DASHBOARD_FRDM_POWER_OFF_SEC", "2.0")))
    parser.add_argument("--frdm-power-settle-sec", type=float, default=float(os.getenv("DASHBOARD_FRDM_POWER_SETTLE_SEC", "4.0")))
    parser.add_argument("--frdm-power-timeout", type=float, default=float(os.getenv("DASHBOARD_FRDM_POWER_TIMEOUT", "20.0")))
    parser.add_argument("--frdm-power-cycle-script", default=os.getenv("DASHBOARD_FRDM_POWER_CYCLE_SCRIPT", ""))
    parser.add_argument("--frdm-uhubctl-location", default=os.getenv("DASHBOARD_FRDM_UHUBCTL_LOCATION", ""))
    parser.add_argument("--frdm-uhubctl-port", default=os.getenv("DASHBOARD_FRDM_UHUBCTL_PORT", ""))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.frdm_uart_port:
        args.no_frdm_uart = True
    state = DashboardState(args)
    try:
        server = DashboardHTTPServer((args.host, args.port), DashboardHandler, state)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"ERROR: smart home dashboard port {args.port} is already in use.")
            print("A smart_home_dashboard server is probably already running.")
            print(f"Open it at: http://127.0.0.1:{args.port}/")
            print(f"Check API status with: curl http://127.0.0.1:{args.port}/api/status")
            print("Stop old dashboard servers with: pkill -f 'smart_home_dashboard/server.py'")
            return 1
        raise
    print("MakeNTU smart home dashboard API")
    print(f"  dashboard : http://{args.host}:{args.port}/dashboard")
    print(f"  API status: http://{args.host}:{args.port}/api/status")
    print(f"  data dir  : {state.data_dir}")
    print(f"  todo path : {state.todo_path}")
    print(f"  FRDM UART : {state.frdm.status()}")
    print(f"  FRDM power: {state.frdm_power.status()}")
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nStopping dashboard API.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
