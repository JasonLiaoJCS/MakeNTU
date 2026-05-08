#!/usr/bin/env python3
"""
Focus work mode for the MakeNTU desktop pet.

Flow:
    Start session -> UART work expression -> every N seconds capture one JPEG
    -> POST to desktop /focus-check -> append JSONL -> generate Markdown report
    -> UART Normal on exit.

Images are memory-only by default. Use --save-images only while debugging.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import glob
import json
import os
from pathlib import Path
import re
import select
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


THIS_DIR = Path(__file__).resolve().parent
DEFAULT_FOCUS_URL = os.getenv("FOCUS_SERVER_URL", "http://100.108.141.26:8766/focus-check")
DEFAULT_LOG_ROOT = THIS_DIR / "logs" / "focus_sessions"
DEFAULT_TODO_LIST_PATH = THIS_DIR / "logs" / "todo_list.json"
DEFAULT_INTERVAL_SEC = 60.0
DEFAULT_DISCORD_WEBHOOK_FILE = Path(os.getenv("DISCORD_WEBHOOK_FILE", "~/.config/makentu/discord_webhook_url")).expanduser()
DISCORD_USER_AGENT = "DiscordBot (https://github.com/asrlab-yian/MakeNTU, 0.1)"
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
FOCUS_STATES = {"focused", "away", "phone", "sleeping", "distracted", "uncertain", "error"}
STATE_UART_COMMANDS = {
    "focused": "Thinking",
    "away": "Sleep",
    "phone": "Concerned",
    "sleeping": "Sleepy",
    "distracted": "Concerned",
    "uncertain": "Confused",
    "error": "Confused",
}
STATE_LABELS = {
    "focused": "專心",
    "away": "離席",
    "phone": "疑似手機",
    "sleeping": "疑似睡覺",
    "distracted": "分心",
    "uncertain": "不確定",
    "error": "錯誤",
}


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def short_text(text: str, limit: int = 160) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def clamp_float(value: Any, default: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


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


def clean_todo_text(text: Any) -> str:
    return " ".join(str(text or "").strip().split())[:160]


def read_secret_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        print(f"WARNING: could not read secret file {path}: {exc}")
        return ""


def default_discord_webhook_url() -> str:
    return (
        os.getenv("FOCUS_DISCORD_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        or read_secret_file(DEFAULT_DISCORD_WEBHOOK_FILE)
    )


def normalize_focus_url(raw_url: str) -> str:
    raw_url = str(raw_url or "").strip() or DEFAULT_FOCUS_URL
    parsed = urllib.parse.urlsplit(raw_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/focus-check"):
        return raw_url
    if path.endswith("/voice-chat") or path.endswith("/text-chat"):
        path = path.rsplit("/", 1)[0]
    path = path.rstrip("/") + "/focus-check"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def parse_camera_id(raw: str) -> str | int:
    value = str(raw).strip()
    if value.lower() in {"", "auto"}:
        return "auto"
    try:
        return int(value)
    except ValueError:
        return value


def camera_candidates(camera_id: str | int) -> list[str | int]:
    if str(camera_id).lower() != "auto":
        return [camera_id]
    candidates: list[str | int] = []
    for path in glob.glob("/dev/video*"):
        match = re.search(r"\d+$", path)
        if match:
            candidates.append(int(match.group()))
        else:
            candidates.append(path)
    return sorted(candidates, key=lambda item: str(item))


def discover_uart_ports() -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        from serial.tools import list_ports

        for port in list_ports.comports():
            device = str(port.device)
            text = " ".join(
                str(value)
                for value in (port.device, port.description, port.manufacturer, port.product, port.hwid)
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


def resolve_uart_port(port: str) -> str:
    requested = str(port or "").strip()
    if requested and requested.lower() != "auto" and Path(requested).exists():
        return requested
    ports = discover_uart_ports()
    preferred = [item for item in ports if item.get("preferred")]
    candidates = preferred or ports
    if len(candidates) == 1:
        selected = str(candidates[0]["device"])
        print(f"Selected FRDM UART {selected}.")
        return selected
    if requested and requested.lower() != "auto":
        return requested
    if not ports:
        raise RuntimeError("No UART serial device is visible. Use --no-uart or plug in FRDM.")
    details = ", ".join(str(item["device"]) for item in ports)
    raise RuntimeError(f"Could not choose a UART port automatically. Candidates: {details}. Pass --uart-port.")


def line_ending_bytes(name: str) -> bytes:
    return b"\r\n" if name == "crlf" else b"\n"


@dataclass
class UartSender:
    port: str
    baudrate: int
    timeout: float
    line_ending: str
    dry_run: bool
    no_uart: bool
    debug: bool

    def send(self, command: str, *, reason: str = "") -> bool:
        command = str(command or "").strip()
        if not command:
            return True
        wire = f"{command} 0 0"
        if self.no_uart:
            print(f"FRDM UART skipped (--no-uart): {wire}")
            return True
        if self.dry_run:
            print(f"FRDM UART dry-run TX: {wire}" + (f" ({reason})" if reason else ""))
            return True
        try:
            import serial

            port = resolve_uart_port(self.port)
            with serial.Serial(
                port=port,
                baudrate=self.baudrate,
                timeout=max(0.005, self.timeout),
                write_timeout=max(0.005, self.timeout),
            ) as ser:
                time.sleep(0.04)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                ser.write(wire.encode("utf-8") + line_ending_bytes(self.line_ending))
                ser.flush()
                print(f"FRDM UART TX: {wire}" + (f" ({reason})" if reason else ""))
                if self.debug:
                    deadline = time.monotonic() + min(max(self.timeout, 0.02), 0.2)
                    while time.monotonic() < deadline:
                        line = ser.readline()
                        if line:
                            print(f"FRDM UART RX: {line.decode('utf-8', errors='replace').rstrip()}")
            return True
        except Exception as exc:
            print(f"WARNING: UART send failed for {wire}: {exc}")
            return False

    def send_raw_line(self, line: str, *, reason: str = "") -> bool:
        wire = str(line or "").strip()
        if not wire:
            return True
        if any(ch in wire for ch in "\r\n"):
            print(f"WARNING: UART raw line rejected because it contains newline: {wire!r}")
            return False
        if len(wire.encode("utf-8")) > 120:
            print(f"WARNING: UART raw line too long ({len(wire.encode('utf-8'))} bytes): {wire!r}")
            return False
        if self.no_uart:
            print(f"FRDM UART skipped (--no-uart): {wire}")
            return True
        if self.dry_run:
            print(f"FRDM UART dry-run TX: {wire}" + (f" ({reason})" if reason else ""))
            return True
        try:
            import serial

            port = resolve_uart_port(self.port)
            with serial.Serial(
                port=port,
                baudrate=self.baudrate,
                timeout=max(0.005, self.timeout),
                write_timeout=max(0.005, self.timeout),
            ) as ser:
                time.sleep(0.04)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                ser.write(wire.encode("utf-8") + line_ending_bytes(self.line_ending))
                ser.flush()
                print(f"FRDM UART TX: {wire}" + (f" ({reason})" if reason else ""))
                if self.debug:
                    deadline = time.monotonic() + max(0.0, self.timeout)
                    while time.monotonic() < deadline:
                        raw = ser.readline()
                        if not raw:
                            break
                        print(f"FRDM UART RX: {raw.decode('utf-8', errors='replace').rstrip()}")
            return True
        except Exception as exc:
            print(f"WARNING: UART send failed for {wire}: {exc}")
            return False


class OneShotCamera:
    def __init__(
        self,
        *,
        camera_id: str | int,
        width: int,
        height: int,
        max_side: int,
        jpeg_quality: int,
        warmup_frames: int,
    ) -> None:
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.max_side = max(0, int(max_side))
        self.jpeg_quality = max(1, min(int(jpeg_quality), 100))
        self.warmup_frames = max(1, int(warmup_frames))

    def capture_jpeg(self) -> tuple[bytes | None, dict[str, Any]]:
        try:
            import cv2

            try:
                cv2.setLogLevel(2)
            except Exception:
                pass
        except ImportError as exc:
            return None, {"ok": False, "error": f"opencv is not installed: {exc}"}

        errors: list[str] = []
        started = time.perf_counter()
        for candidate in camera_candidates(self.camera_id):
            cap = self._open_capture(candidate, cv2)
            if cap is None:
                errors.append(f"{candidate}: open failed")
                continue
            try:
                self._configure_capture(cap, cv2)
                frame = None
                for _ in range(self.warmup_frames):
                    ok, maybe_frame = cap.read()
                    if ok and maybe_frame is not None:
                        frame = maybe_frame
                if frame is None:
                    errors.append(f"{candidate}: read failed")
                    continue

                h, w = frame.shape[:2]
                largest_side = max(w, h)
                if self.max_side > 0 and largest_side > self.max_side:
                    scale = self.max_side / float(largest_side)
                    frame = cv2.resize(
                        frame,
                        (max(1, int(w * scale)), max(1, int(h * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
                if not ok:
                    errors.append(f"{candidate}: encode failed")
                    continue
                data = encoded.tobytes()
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return data, {
                    "ok": True,
                    "camera": candidate,
                    "bytes": len(data),
                    "capture_ms": elapsed_ms,
                    "privacy": "memory_only",
                }
            finally:
                cap.release()
        return None, {"ok": False, "error": "; ".join(errors) or "no camera candidates"}

    def _open_capture(self, candidate: str | int, cv2: Any) -> Any | None:
        if hasattr(cv2, "CAP_V4L2"):
            cap = cv2.VideoCapture(candidate, cv2.CAP_V4L2)
            if cap.isOpened():
                return cap
            cap.release()
        cap = cv2.VideoCapture(candidate)
        if cap.isOpened():
            return cap
        cap.release()
        return None

    def _configure_capture(self, cap: Any, cv2: Any) -> None:
        if hasattr(cv2, "CAP_PROP_FOURCC"):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "CAP_PROP_FPS"):
            cap.set(cv2.CAP_PROP_FPS, 10)


def multipart_focus_request(url: str, image_bytes: bytes, metadata: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    boundary = "----FocusWorkModeBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")

    def add_file(name: str, filename: str, content_type: str, data: bytes) -> None:
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("ascii")
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
        chunks.append(data)
        chunks.append(b"\r\n")

    add_field("metadata", json.dumps(metadata, ensure_ascii=False))
    add_file("image", "focus_sample.jpg", "image/jpeg", image_bytes)
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    body = b"".join(chunks)
    request_obj = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {short_text(raw, 500)}") from exc

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("focus-check response JSON is not an object")
    return parsed


def normalize_focus_response(raw: dict[str, Any] | None, *, fallback_summary: str = "") -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    state = str(data.get("state", "error" if fallback_summary else "uncertain")).strip().lower()
    if state not in FOCUS_STATES:
        state = "uncertain"
    evidence_raw = data.get("evidence")
    if isinstance(evidence_raw, list):
        evidence = [str(item).strip() for item in evidence_raw if str(item).strip()][:5]
    elif isinstance(evidence_raw, str) and evidence_raw.strip():
        evidence = [evidence_raw.strip()]
    else:
        evidence = []
    person_present = data.get("person_present")
    if not isinstance(person_present, bool):
        person_present = state not in {"away", "error"}
    default_attention = {
        "focused": 0.9,
        "away": 0.0,
        "phone": 0.2,
        "sleeping": 0.05,
        "distracted": 0.35,
        "uncertain": 0.4,
        "error": 0.0,
    }.get(state, 0.4)
    summary = str(data.get("summary", "") or fallback_summary).strip()
    if not summary:
        summary = "畫面不足以可靠判斷工作狀態。"
    return {
        "state": state,
        "confidence": clamp_float(data.get("confidence"), 0.35 if state == "uncertain" else 0.5),
        "attention_score": clamp_float(data.get("attention_score"), default_attention),
        "person_present": person_present,
        "evidence": evidence,
        "summary": summary,
        "recommended_robot": str(data.get("recommended_robot", STATE_UART_COMMANDS.get(state, "Confused")) or "Confused"),
        "request_id": data.get("request_id"),
        "vision_model": data.get("vision_model"),
        "timing": data.get("timing") if isinstance(data.get("timing"), dict) else {},
        "ok": bool(data.get("ok", state != "error")),
        "error": data.get("error") or data.get("fallback_reason"),
    }


def append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save_debug_image(session_dir: Path, sample_index: int, image_bytes: bytes) -> str:
    image_dir = session_dir / "debug_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / f"sample_{sample_index:04d}.jpg"
    path.write_bytes(image_bytes)
    return str(path)


def load_todo_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: could not read to-do list {path}: {exc}")
        return []
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = clean_todo_text(item.get("text"))
        if not text:
            continue
        try:
            item_id = int(item.get("id", 0) or 0)
        except (TypeError, ValueError):
            item_id = 0
        status = str(item.get("status", "open") or "open").strip().lower()
        if status not in {"open", "done"}:
            status = "open"
        cleaned.append(
            {
                "id": item_id,
                "text": text,
                "status": status,
                "created_at": str(item.get("created_at", "") or ""),
                "completed_at": str(item.get("completed_at", "") or "") if item.get("completed_at") else "",
                "source": str(item.get("source", "voice") or "voice"),
            }
        )
    return cleaned


def todo_public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id", 0),
        "text": item.get("text", ""),
        "status": item.get("status", "open"),
        "created_at": item.get("created_at", ""),
        "completed_at": item.get("completed_at", ""),
    }


def analyze_todos_for_session(
    *,
    todo_items: list[dict[str, Any]],
    started_at: datetime,
    ended_at: datetime,
) -> dict[str, Any]:
    completed_during: list[dict[str, Any]] = []
    remaining_open: list[dict[str, Any]] = []
    added_during_open: list[dict[str, Any]] = []
    added_during_completed: list[dict[str, Any]] = []

    for item in todo_items:
        status = str(item.get("status", "open") or "open")
        created_at = parse_iso_datetime(item.get("created_at"))
        completed_at = parse_iso_datetime(item.get("completed_at"))
        created_during = created_at is not None and started_at <= created_at <= ended_at
        completed_in_session = completed_at is not None and started_at <= completed_at <= ended_at

        if completed_in_session:
            completed_during.append(todo_public_item(item))
            if created_during:
                added_during_completed.append(todo_public_item(item))
        elif status == "open":
            remaining_open.append(todo_public_item(item))
            if created_during:
                added_during_open.append(todo_public_item(item))

    return {
        "completed_during": completed_during,
        "remaining_open": remaining_open,
        "added_during_open": added_during_open,
        "added_during_completed": added_during_completed,
        "completed_count": len(completed_during),
        "remaining_count": len(remaining_open),
        "added_during_open_count": len(added_during_open),
    }


def state_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {state: 0 for state in sorted(FOCUS_STATES)}
    for entry in entries:
        state = str(entry.get("state", "uncertain"))
        counts[state if state in counts else "uncertain"] += 1
    return counts


def longest_streak(entries: list[dict[str, Any]], target_state: str) -> int:
    best = 0
    current = 0
    for entry in entries:
        if entry.get("state") == target_state:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def build_focus_recommendation(summary: dict[str, Any]) -> str:
    score = float(summary.get("focus_score", 0.0) or 0.0)
    state_stats = summary.get("state_stats") if isinstance(summary.get("state_stats"), dict) else {}
    todo = summary.get("todo") if isinstance(summary.get("todo"), dict) else {}
    notes: list[str] = []

    if score >= 85:
        notes.append("這輪專注品質很穩，可以維持同樣的工作節奏。")
    elif score >= 70:
        notes.append("整體專注狀態不錯，下一輪可以保留 5 分鐘緩衝處理中斷。")
    elif score >= 50:
        notes.append("有進入工作狀態，但中斷偏多；建議把下一輪縮短成 20 分鐘並先收掉通知。")
    else:
        notes.append("這輪比較難維持專心，建議先把工作切成更小步驟，從一個可完成的待辦開始。")

    if int(state_stats.get("phone", {}).get("count", 0) or 0) > 0:
        notes.append("有偵測到疑似手機狀態，下一輪可以把手機放遠或開勿擾。")
    if int(state_stats.get("away", {}).get("count", 0) or 0) > 0:
        notes.append("有離席紀錄，若是必要休息可以改用較短的番茄鐘節奏。")
    if int(state_stats.get("sleeping", {}).get("count", 0) or 0) > 0:
        notes.append("有疑似睡覺狀態，建議先休息再開下一輪，不要硬撐。")
    if int(todo.get("completed_count", 0) or 0) > 0:
        notes.append("這輪有完成待辦，很好，下一輪可以接著處理剩餘清單中風險最高的一項。")
    elif int(todo.get("remaining_count", 0) or 0) > 0:
        notes.append("這輪還沒有完成待辦，下一輪建議先選一個最小可收尾項目。")
    return " ".join(notes)


def build_encouragement(summary: dict[str, Any]) -> str:
    todo = summary.get("todo") if isinstance(summary.get("todo"), dict) else {}
    completed = int(todo.get("completed_count", 0) or 0)
    score = float(summary.get("focus_score", 0.0) or 0.0)
    if completed > 0 and score >= 70:
        return f"做得不錯，這輪不只維持住專注，還完成了 {completed} 個待辦。"
    if completed > 0:
        return f"雖然中間有些波動，但你還是完成了 {completed} 個待辦，這很有價值。"
    if score >= 70:
        return "這輪專注狀態穩定，就算待辦還沒收掉，也是在累積進度。"
    return "這輪先當作校準，下一輪從一個更小的待辦開始會比較容易進入狀態。"


def parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def focus_report_title(summary: dict[str, Any]) -> str:
    started_at = parse_datetime_value(summary.get("started_at"))
    if started_at is not None:
        return f"專心報告：{started_at.strftime('%Y/%m/%d/%H')} 開始的專注時段"

    session_id = str(summary.get("session_id", "") or "")
    match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})", session_id)
    if match:
        year, month, day, hour = match.groups()
        return f"專心報告：{year}/{month}/{day}/{hour} 開始的專注時段"

    return "專心報告：未記錄開始時間的專注時段"


def build_focus_summary(
    *,
    session_id: str,
    task: str,
    started_at: datetime,
    ended_at: datetime,
    interval_sec: float,
    entries: list[dict[str, Any]],
    log_file: Path,
    report_file: Path,
    todo_items: list[dict[str, Any]],
    todo_list_path: Path,
) -> dict[str, Any]:
    counts = state_counts(entries)
    valid_samples = max(1, len([entry for entry in entries if entry.get("state") != "error"]))
    focused = counts.get("focused", 0)
    focus_ratio = focused / valid_samples
    duration_min = max(0.0, (ended_at - started_at).total_seconds() / 60.0)
    sample_min = duration_min / len(entries) if duration_min > 0 and entries else interval_sec / 60.0
    longest_focus_min = longest_streak(entries, "focused") * sample_min
    average_attention = (
        sum(float(entry.get("attention_score", 0.0) or 0.0) for entry in entries) / len(entries)
        if entries
        else 0.0
    )
    focus_score = round((average_attention * 70.0) + (focus_ratio * 30.0))
    focus_score = max(0, min(100, int(focus_score)))
    state_stats = {}
    for state in ("focused", "distracted", "phone", "sleeping", "away", "uncertain", "error"):
        count = counts.get(state, 0)
        state_stats[state] = {
            "label": STATE_LABELS.get(state, state),
            "count": count,
            "estimated_min": round(count * sample_min, 1),
        }
    distracted_count = sum(counts.get(state, 0) for state in ("distracted", "phone", "sleeping", "away"))
    summary: dict[str, Any] = {
        "version": 1,
        "session_id": session_id,
        "task": task,
        "started_at": started_at.isoformat(timespec="seconds"),
        "ended_at": ended_at.isoformat(timespec="seconds"),
        "duration_min": round(duration_min, 1),
        "interval_sec": interval_sec,
        "sample_count": len(entries),
        "valid_sample_count": valid_samples,
        "focus_ratio": round(focus_ratio, 3),
        "focus_percent": round(focus_ratio * 100.0, 1),
        "average_attention": round(average_attention, 3),
        "focus_score": focus_score,
        "focused_min": round(counts.get("focused", 0) * sample_min, 1),
        "distracted_min": round(distracted_count * sample_min, 1),
        "longest_focus_min": round(longest_focus_min, 1),
        "state_stats": state_stats,
        "todo_list_path": str(todo_list_path),
        "todo": analyze_todos_for_session(todo_items=todo_items, started_at=started_at, ended_at=ended_at),
        "log_file": str(log_file),
        "report_file": str(report_file),
    }
    summary["report_title"] = focus_report_title(summary)
    summary["recommendation"] = build_focus_recommendation(summary)
    summary["encouragement"] = build_encouragement(summary)
    return summary


def format_todo_lines(items: list[dict[str, Any]], *, empty: str, limit: int = 8) -> list[str]:
    if not items:
        return [f"- {empty}"]
    lines = [f"- {item.get('text', '')}" for item in items[:limit]]
    if len(items) > limit:
        lines.append(f"- 還有 {len(items) - limit} 個沒有列出")
    return lines


def build_report(
    *,
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> str:
    state_stats = summary.get("state_stats") if isinstance(summary.get("state_stats"), dict) else {}
    todo = summary.get("todo") if isinstance(summary.get("todo"), dict) else {}

    title = str(summary.get("report_title") or focus_report_title(summary))

    lines = [
        f"# {title}",
        "",
        "## 本次摘要",
        "",
        f"- 工作目標：{summary.get('task') or '(未指定)'}",
        f"- 開始時間：{summary.get('started_at')}",
        f"- 結束時間：{summary.get('ended_at')}",
        f"- 總時長：約 {summary.get('duration_min', 0)} 分鐘",
        f"- 取樣間隔：{summary.get('interval_sec', 0):g} 秒",
        f"- 有效取樣：{summary.get('valid_sample_count', 0)} / {summary.get('sample_count', 0)}",
        f"- 專心時間：約 {summary.get('focused_min', 0)} 分鐘",
        f"- 分心時間：約 {summary.get('distracted_min', 0)} 分鐘",
        f"- 專注比例：{summary.get('focus_percent', 0)}%",
        f"- 專注分數：{summary.get('focus_score', 0)} / 100",
        f"- 平均 attention score：{summary.get('average_attention', 0)}",
        f"- 最長連續專心：約 {summary.get('longest_focus_min', 0)} 分鐘",
        f"- JSONL log：{summary.get('log_file')}",
        f"- Summary JSON：{summary.get('summary_file', '')}",
        "",
        "## 建議",
        "",
        str(summary.get("recommendation", "")),
        "",
        "## 完成 To-Do 與鼓勵",
        "",
        *format_todo_lines(todo.get("completed_during", []) if isinstance(todo.get("completed_during"), list) else [], empty="這輪還沒有完成待辦。"),
        "",
        str(summary.get("encouragement", "")),
        "",
        "## 剩餘 To-Do 與下一步",
        "",
        *format_todo_lines(todo.get("remaining_open", []) if isinstance(todo.get("remaining_open"), list) else [], empty="目前沒有剩餘未完成待辦。"),
        "",
        "建議下一輪先挑一個最小、最能降低 demo 風險的項目開始。",
        "",
        "## 狀態統計",
        "",
        "| 狀態 | 次數 | 估計時間 |",
        "| --- | ---: | ---: |",
    ]
    for state in ("focused", "distracted", "phone", "sleeping", "away", "uncertain", "error"):
        stat = state_stats.get(state) if isinstance(state_stats.get(state), dict) else {}
        lines.append(
            f"| {stat.get('label', STATE_LABELS.get(state, state))} | "
            f"{stat.get('count', 0)} | {stat.get('estimated_min', 0)} 分鐘 |"
        )

    lines.extend(["", "## 時間軸", "", "| 時間 | 狀態 | 信心 | 分數 | 摘要 |", "| --- | --- | ---: | ---: | --- |"])
    for entry in entries:
        timestamp = str(entry.get("timestamp", ""))
        state = str(entry.get("state", "uncertain"))
        confidence = float(entry.get("confidence", 0.0) or 0.0)
        attention = float(entry.get("attention_score", 0.0) or 0.0)
        summary = str(entry.get("summary", "")).replace("|", "/")
        lines.append(
            f"| {timestamp} | {STATE_LABELS.get(state, state)} | {confidence:.2f} | {attention:.2f} | {summary} |"
        )
    lines.append("")
    return "\n".join(lines)


def truncate_discord_content(text: str, limit: int = 1900) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 20].rstrip() + "\n...(略)"


def build_discord_message(summary: dict[str, Any]) -> str:
    todo = summary.get("todo") if isinstance(summary.get("todo"), dict) else {}
    completed = todo.get("completed_during") if isinstance(todo.get("completed_during"), list) else []
    remaining = todo.get("remaining_open") if isinstance(todo.get("remaining_open"), list) else []
    completed_text = "、".join(str(item.get("text", "")) for item in completed[:4]) if completed else "這輪還沒有完成待辦"
    remaining_text = "、".join(str(item.get("text", "")) for item in remaining[:4]) if remaining else "沒有剩餘待辦"
    if len(completed) > 4:
        completed_text += f" 等 {len(completed)} 個"
    if len(remaining) > 4:
        remaining_text += f" 等 {len(remaining)} 個"
    title = str(summary.get("report_title") or focus_report_title(summary))
    return truncate_discord_content(
        "\n".join(
            [
                f"**{title}**",
                f"工作目標：{summary.get('task') or '(未指定)'}",
                f"專注分數：{summary.get('focus_score', 0)} / 100",
                f"專心時間：約 {summary.get('focused_min', 0)} 分鐘；分心時間：約 {summary.get('distracted_min', 0)} 分鐘",
                f"完成 To-Do：{completed_text}",
                f"剩餘 To-Do：{remaining_text}",
                f"建議：{summary.get('recommendation', '')}",
                f"報告檔：{summary.get('report_file', '')}",
            ]
        )
    )


def send_discord_notification(
    summary: dict[str, Any],
    *,
    webhook_url: str,
    timeout_sec: float,
    dry_run: bool,
) -> dict[str, Any]:
    content = build_discord_message(summary)
    if dry_run:
        print("Discord notify dry-run:")
        print(content)
        return {"mode": "discord", "ok": True, "dry_run": True, "content_preview": content[:300]}
    payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=max(0.1, timeout_sec)) as response:
        status = int(getattr(response, "status", 0) or 0)
        body = response.read().decode("utf-8", errors="replace")
    ok = 200 <= status < 300
    return {"mode": "discord", "ok": ok, "status": status, "response": short_text(body, 300)}


def send_focus_notification(
    summary: dict[str, Any],
    report: str,
    *,
    mode: str,
    discord_webhook_url: str,
    timeout_sec: float,
    dry_run: bool,
) -> dict[str, Any]:
    del report
    selected = str(mode or "none").strip().lower()
    if selected in {"", "none"}:
        return {"mode": "none", "ok": True}
    if selected != "discord":
        return {"mode": selected, "ok": False, "error": f"unsupported notify mode: {selected}"}
    webhook_url = str(discord_webhook_url or "").strip()
    if not webhook_url:
        print("WARNING: notify-mode=discord but --discord-webhook-url is empty; report was not sent.")
        return {"mode": "discord", "ok": False, "error": "missing discord webhook url"}
    try:
        result = send_discord_notification(
            summary,
            webhook_url=webhook_url,
            timeout_sec=timeout_sec,
            dry_run=dry_run,
        )
        if result.get("ok"):
            print("Focus report notification sent to Discord." if not dry_run else "Focus report Discord dry-run complete.")
        else:
            print(f"WARNING: Discord notification returned non-2xx status: {result}")
        return result
    except Exception as exc:
        print(f"WARNING: Discord notification failed: {exc}")
        return {"mode": "discord", "ok": False, "error": str(exc)}


def stdin_requested_stop() -> bool:
    if not sys.stdin.isatty():
        return False
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if not readable:
        return False
    text = sys.stdin.readline().strip().lower()
    return text in {"q", "quit", "exit", "stop", "end", "結束", "停止"}


class FocusSession:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.session_id = args.session_id or now_local().strftime("focus_%Y%m%d_%H%M%S")
        self.session_dir = Path(args.log_root).expanduser() / self.session_id
        self.log_file = self.session_dir / "focus_log.jsonl"
        self.report_file = self.session_dir / "focus_report.md"
        self.summary_file = self.session_dir / "focus_summary.json"
        self.todo_list_path = Path(str(args.todo_list_path)).expanduser()
        self.entries: list[dict[str, Any]] = []
        self.stop_requested = False
        self.last_uart_command = ""
        self.streak_state = ""
        self.streak_count = 0
        self.deadline_monotonic: float | None = None
        self.camera = OneShotCamera(
            camera_id=parse_camera_id(args.camera_id),
            width=args.camera_width,
            height=args.camera_height,
            max_side=args.camera_max_side,
            jpeg_quality=args.camera_jpeg_quality,
            warmup_frames=args.camera_warmup_frames,
        )
        self.uart = UartSender(
            port=args.uart_port,
            baudrate=args.uart_baudrate,
            timeout=args.uart_timeout,
            line_ending=args.uart_line_ending,
            dry_run=args.uart_dry_run,
            no_uart=args.no_uart,
            debug=args.uart_debug,
        )

    def request_stop(self, _signum: int | None = None, _frame: Any | None = None) -> None:
        self.stop_requested = True

    def run(self) -> int:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        focus_url = normalize_focus_url(self.args.server_url)
        started_at = now_local()
        start_todos = load_todo_items(self.todo_list_path)
        metadata_path = self.session_dir / "session.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "task": self.args.task,
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "interval_sec": self.args.interval_sec,
                    "duration_min": self.args.duration_min,
                    "focus_url": focus_url,
                    "todo_list_path": str(self.todo_list_path),
                    "todo_open_count_at_start": len([item for item in start_todos if item.get("status") == "open"]),
                    "notify_mode": self.args.notify_mode,
                    "privacy": "images are memory-only unless --save-images is set",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print(f"Focus work mode started: {self.session_id}")
        print(f"Task: {self.args.task or '(not specified)'}")
        print(f"Interval: {self.args.interval_sec:g}s")
        print(f"Log: {self.log_file}")
        print(f"Report: {self.report_file}")
        print(f"Summary: {self.summary_file}")
        print(f"To-do list: {self.todo_list_path}")
        print(f"Notify mode: {self.args.notify_mode}")
        print("Image privacy: memory-only; use --save-images only for debugging.")
        if sys.stdin.isatty():
            print("Type q + Enter to end the session.")

        self._send_uart("Thinking", reason="focus session start", force=True)
        deadline = (
            time.monotonic() + max(0.0, float(self.args.duration_min)) * 60.0
            if self.args.duration_min is not None
            else None
        )
        self.deadline_monotonic = deadline
        self._send_focus_dashboard("active", streak=0, reason="focus session start")

        sample_index = 0
        try:
            while not self.stop_requested:
                if deadline is not None and time.monotonic() >= deadline:
                    print("Duration reached; ending focus session.")
                    break
                sample_index += 1
                self._run_sample(sample_index, focus_url)
                if self.args.once:
                    break
                if not self._sleep_until_next_sample(deadline):
                    break
        finally:
            ended_at = now_local()
            self._send_focus_dashboard("idle", remaining_min=0, streak=0, reason="focus session end")
            self._send_uart("Normal", reason="focus session end", force=True)
            todo_items = load_todo_items(self.todo_list_path)
            summary = build_focus_summary(
                session_id=self.session_id,
                task=self.args.task,
                started_at=started_at,
                ended_at=ended_at,
                interval_sec=self.args.interval_sec,
                entries=self.entries,
                log_file=self.log_file,
                report_file=self.report_file,
                todo_items=todo_items,
                todo_list_path=self.todo_list_path,
            )
            summary["summary_file"] = str(self.summary_file)
            self.summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report = build_report(summary=summary, entries=self.entries)
            self.report_file.write_text(report, encoding="utf-8")
            notify_result = send_focus_notification(
                summary,
                report,
                mode=self.args.notify_mode,
                discord_webhook_url=self.args.discord_webhook_url,
                timeout_sec=self.args.notify_timeout,
                dry_run=self.args.notify_dry_run,
            )
            if notify_result:
                summary["notification"] = notify_result
                self.summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Focus summary written: {self.summary_file}")
            print(f"Focus work report written: {self.report_file}")
        return 0

    def _run_sample(self, sample_index: int, focus_url: str) -> None:
        timestamp = now_iso()
        image_saved = False
        image_path = ""
        inference_ms = 0

        if self.args.mock_state:
            capture_ms = 0
            capture_detail = {"ok": True, "camera": "mock", "bytes": 0, "capture_ms": 0, "privacy": "mock_no_image"}
            result = normalize_focus_response(
                {
                    "ok": True,
                    "state": self.args.mock_state,
                    "confidence": 0.9,
                    "attention_score": 0.9 if self.args.mock_state == "focused" else 0.2,
                    "summary": "mock focus result",
                    "evidence": ["self-test mock"],
                }
            )
        else:
            capture_started = time.perf_counter()
            image_bytes, capture_detail = self.camera.capture_jpeg()
            capture_ms = int((time.perf_counter() - capture_started) * 1000)

            if image_bytes is None:
                result = normalize_focus_response(None, fallback_summary=str(capture_detail.get("error", "camera failed")))
            else:
                if self.args.save_images:
                    image_path = save_debug_image(self.session_dir, sample_index, image_bytes)
                    image_saved = True
                    capture_detail["privacy"] = "debug_image_saved"
                metadata = {
                    "session_id": self.session_id,
                    "task": self.args.task,
                    "interval_sec": self.args.interval_sec,
                    "sample_index": sample_index,
                    "capture_timestamp": timestamp,
                    "image_saved": image_saved,
                }
                inference_started = time.perf_counter()
                try:
                    response = multipart_focus_request(focus_url, image_bytes, metadata, self.args.timeout)
                    result = normalize_focus_response(response)
                except Exception as exc:
                    result = normalize_focus_response(None, fallback_summary=f"focus-check failed: {exc}")
                finally:
                    inference_ms = int((time.perf_counter() - inference_started) * 1000)
                    del image_bytes

        timing = result.get("timing") if isinstance(result.get("timing"), dict) else {}
        entry = {
            "timestamp": timestamp,
            "session_id": self.session_id,
            "sample_index": sample_index,
            "state": result["state"],
            "confidence": result["confidence"],
            "attention_score": result["attention_score"],
            "person_present": result["person_present"],
            "evidence": result["evidence"],
            "summary": result["summary"],
            "recommended_robot": result["recommended_robot"],
            "request_id": result.get("request_id"),
            "vision_model": result.get("vision_model"),
            "ok": result.get("ok"),
            "error": result.get("error"),
            "capture": {**capture_detail, "capture_ms": capture_detail.get("capture_ms", capture_ms)},
            "inference_ms": timing.get("focus_ms", inference_ms),
            "image_saved": image_saved,
            "image_path": image_path if image_saved else "",
            "privacy": "image_bytes_deleted_after_analysis" if not image_saved else "debug_image_saved",
        }
        self.entries.append(entry)
        append_jsonl(self.log_file, entry)
        print(
            f"[{sample_index:04d}] {entry['timestamp']} "
            f"{STATE_LABELS.get(entry['state'], entry['state'])} "
            f"confidence={entry['confidence']:.2f} attention={entry['attention_score']:.2f} "
            f"{entry['summary']}"
        )
        self._maybe_update_uart(str(entry["state"]))

    def _maybe_update_uart(self, state: str) -> None:
        if state == self.streak_state:
            self.streak_count += 1
        else:
            self.streak_state = state
            self.streak_count = 1

        self._send_focus_dashboard(state, streak=self.streak_count, reason=f"{state} dashboard")

        if state == "focused":
            self._send_uart("Thinking", reason="focused")
            return
        if self.streak_count < self.args.alert_threshold:
            return
        self._send_uart(STATE_UART_COMMANDS.get(state, "Confused"), reason=f"{state} streak={self.streak_count}")

    def _send_uart(self, command: str, *, reason: str, force: bool = False) -> None:
        if getattr(self.args, "no_active_screen_uart", False) and str(command or "").strip() != "Normal":
            return
        if not force and command == self.last_uart_command:
            return
        if self.uart.send(command, reason=reason):
            self.last_uart_command = command

    def _remaining_minutes(self) -> int:
        deadline = self.deadline_monotonic
        if deadline is None:
            return 0
        remaining_sec = max(0.0, deadline - time.monotonic())
        return int((remaining_sec + 59.0) // 60.0)

    def _send_focus_dashboard(
        self,
        state: str,
        *,
        remaining_min: int | None = None,
        streak: int = 0,
        reason: str,
    ) -> None:
        normalized = str(state or "idle").strip().lower()
        if normalized not in FOCUS_STATES and normalized not in {"active", "idle"}:
            normalized = "uncertain"
        remaining = self._remaining_minutes() if remaining_min is None else max(0, int(remaining_min))
        streak_count = max(0, int(streak))
        self.uart.send_raw_line(f"Focus {normalized},{remaining},{streak_count}", reason=reason)

    def _sleep_until_next_sample(self, deadline: float | None) -> bool:
        wait_sec = max(0.0, float(self.args.interval_sec))
        end_at = time.monotonic() + wait_sec
        while not self.stop_requested and time.monotonic() < end_at:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            if stdin_requested_stop():
                print("Stop command received from terminal.")
                return False
            time.sleep(min(0.5, max(0.0, end_at - time.monotonic())))
        return not self.stop_requested


def run_self_test() -> int:
    response = normalize_focus_response(
        {
            "state": "phone",
            "confidence": 2,
            "attention_score": -1,
            "person_present": True,
            "evidence": ["低頭看手機"],
            "recommended_robot": "Concerned",
        }
    )
    if response["state"] != "phone" or response["confidence"] != 1.0 or response["attention_score"] != 0.0:
        raise AssertionError(f"focus response normalization failed: {response}")
    started = now_local()
    ended = started + timedelta(minutes=25)
    completed_at = started + timedelta(minutes=10)
    todo_items = [
        {
            "id": 1,
            "text": "整理 demo 指令",
            "status": "done",
            "created_at": started.isoformat(timespec="seconds"),
            "completed_at": completed_at.isoformat(timespec="seconds"),
        },
        {
            "id": 2,
            "text": "測試 FRDM 馬達",
            "status": "open",
            "created_at": started.isoformat(timespec="seconds"),
            "completed_at": "",
        },
    ]
    entries = [
        {"timestamp": now_iso(), **normalize_focus_response({"state": "focused", "attention_score": 0.9}), "sample_index": 1},
        {"timestamp": now_iso(), **response, "sample_index": 2},
    ]
    summary = build_focus_summary(
        session_id="focus_self_test",
        task="test task",
        started_at=started,
        ended_at=ended,
        interval_sec=60,
        entries=entries,
        log_file=Path("/tmp/focus_self_test.jsonl"),
        report_file=Path("/tmp/focus_self_test.md"),
        todo_items=todo_items,
        todo_list_path=Path("/tmp/focus_todo_self_test.json"),
    )
    summary["summary_file"] = "/tmp/focus_summary_self_test.json"
    if summary["todo"]["completed_count"] != 1 or summary["todo"]["remaining_count"] != 1:
        raise AssertionError(f"focus to-do summary failed: {summary['todo']}")
    report = build_report(summary=summary, entries=entries)
    if "專注分數" not in report or "疑似手機" not in report or "完成 To-Do" not in report:
        raise AssertionError("report generation failed")
    discord_message = build_discord_message(summary)
    if "專注分數" not in discord_message or "整理 demo 指令" not in discord_message:
        raise AssertionError("discord summary message failed")
    if not normalize_focus_url("http://host:8766").endswith("/focus-check"):
        raise AssertionError("focus URL normalization failed")
    print("focus_work_mode self-test OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Focus work mode: periodic camera focus checks + JSONL log + report.")
    parser.add_argument("--server-url", default=DEFAULT_FOCUS_URL, help="Desktop server /focus-check URL or base server URL.")
    parser.add_argument("--task", default=os.getenv("FOCUS_TASK", ""), help="Optional work goal shown in the report.")
    parser.add_argument("--session-id", default="", help="Optional fixed session id.")
    parser.add_argument("--duration-min", type=float, default=None, help="End automatically after this many minutes.")
    parser.add_argument("--interval-sec", type=float, default=float(os.getenv("FOCUS_INTERVAL_SEC", str(DEFAULT_INTERVAL_SEC))), help="Seconds between checks.")
    parser.add_argument("--once", action="store_true", help="Run one sample and generate a report.")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("FOCUS_TIMEOUT_SEC", "180")), help="HTTP timeout for /focus-check.")
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT), help="Root folder for focus session logs.")
    parser.add_argument("--save-images", action="store_true", help="Debug only: save sampled images instead of memory-only operation.")
    parser.add_argument("--mock-state", choices=sorted(FOCUS_STATES - {"error"}), default="", help="Skip camera/server and use a fixed state.")
    parser.add_argument("--todo-list-path", default=os.getenv("TODO_LIST_PATH", str(DEFAULT_TODO_LIST_PATH)), help="Wake Bridge to-do JSON used in focus summary.")

    notify = parser.add_argument_group("notification")
    notify.add_argument("--notify-mode", choices=["none", "discord"], default=os.getenv("FOCUS_NOTIFY_MODE", "none"))
    notify.add_argument("--discord-webhook-url", default=default_discord_webhook_url())
    notify.add_argument("--notify-timeout", type=float, default=float(os.getenv("FOCUS_NOTIFY_TIMEOUT", "8.0")))
    notify.add_argument("--notify-dry-run", action="store_true", help="Print notification payload without sending it.")

    camera = parser.add_argument_group("camera")
    camera.add_argument("--camera-id", default=os.getenv("FOCUS_CAMERA_ID", "auto"))
    camera.add_argument("--camera-width", type=int, default=int(os.getenv("FOCUS_CAMERA_WIDTH", "640")))
    camera.add_argument("--camera-height", type=int, default=int(os.getenv("FOCUS_CAMERA_HEIGHT", "480")))
    camera.add_argument("--camera-max-side", type=int, default=int(os.getenv("FOCUS_CAMERA_MAX_SIDE", "640")))
    camera.add_argument("--camera-jpeg-quality", type=int, default=int(os.getenv("FOCUS_CAMERA_JPEG_QUALITY", "78")))
    camera.add_argument("--camera-warmup-frames", type=int, default=int(os.getenv("FOCUS_CAMERA_WARMUP_FRAMES", "3")))

    uart = parser.add_argument_group("FRDM UART")
    uart.add_argument("--no-uart", action="store_true", help="Do not send FRDM UART commands.")
    uart.add_argument("--uart-dry-run", action="store_true", help="Print UART commands without opening serial.")
    uart.add_argument("--uart-debug", action="store_true")
    uart.add_argument("--uart-port", default=os.getenv("FOCUS_UART_PORT", "auto"))
    uart.add_argument("--uart-baudrate", type=int, default=int(os.getenv("FOCUS_UART_BAUDRATE", "115200")))
    uart.add_argument("--uart-timeout", type=float, default=float(os.getenv("FOCUS_UART_TIMEOUT", "0.08")))
    uart.add_argument("--uart-line-ending", choices=["lf", "crlf"], default=os.getenv("FOCUS_UART_LINE_ENDING", "crlf"))
    uart.add_argument(
        "--no-active-screen-uart",
        action="store_true",
        help="Do not send active focus screen-state UART commands; still allow Focus dashboard raw updates and final Normal.",
    )
    uart.add_argument(
        "--no-screen-uart",
        action="store_true",
        dest="no_active_screen_uart",
        help="Compatibility alias for --no-active-screen-uart.",
    )
    uart.add_argument("--alert-threshold", type=int, default=int(os.getenv("FOCUS_ALERT_THRESHOLD", "2")), help="Non-focused streak before changing robot state.")

    parser.add_argument("--self-test", action="store_true", help="Run local parser/report tests and exit.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    if args.interval_sec <= 0:
        parser.error("--interval-sec must be > 0")
    if args.alert_threshold < 1:
        parser.error("--alert-threshold must be >= 1")

    session = FocusSession(args)
    signal.signal(signal.SIGINT, session.request_stop)
    signal.signal(signal.SIGTERM, session.request_stop)
    return session.run()


if __name__ == "__main__":
    raise SystemExit(main())
