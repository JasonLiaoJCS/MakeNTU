#!/usr/bin/env python3
"""
Jarvis IR remote learner/sender for Jetson.

This folder is intentionally standalone. It does not import or modify the
existing frdm_uart_context_sender bridge; it only follows the same local-tool
style: CLI first, JSON state, and an optional localhost HTTP endpoint.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from typing import Any


THIS_DIR = Path(__file__).resolve().parent

DEFAULT_CODES_PATH = Path(os.getenv("JARVIS_IR_CODES_PATH", str(THIS_DIR / "ir_codes.json")))
DEFAULT_RX_PIN = int(os.getenv("JARVIS_IR_RX_PIN", "18"))
DEFAULT_TX_PIN = int(os.getenv("JARVIS_IR_TX_PIN", "32"))
DEFAULT_PIN_MODE = os.getenv("JARVIS_IR_PIN_MODE", "BOARD")
DEFAULT_FREQUENCY_HZ = int(os.getenv("JARVIS_IR_FREQUENCY_HZ", "38000"))
DEFAULT_DUTY_CYCLE = float(os.getenv("JARVIS_IR_DUTY_CYCLE", "0.33"))
DEFAULT_HTTP_PORT = int(os.getenv("JARVIS_IR_PORT", "8790"))

DEFAULT_CAPTURE_TIMEOUT_SEC = float(os.getenv("JARVIS_IR_CAPTURE_TIMEOUT_SEC", "10.0"))
DEFAULT_IDLE_GAP_US = int(os.getenv("JARVIS_IR_IDLE_GAP_US", "35000"))
DEFAULT_MAX_CAPTURE_MS = int(os.getenv("JARVIS_IR_MAX_CAPTURE_MS", "800"))
DEFAULT_MIN_TIMINGS = int(os.getenv("JARVIS_IR_MIN_TIMINGS", "8"))
DEFAULT_REPEAT_GAP_MS = int(os.getenv("JARVIS_IR_REPEAT_GAP_MS", "90"))

PUNCT_RE = re.compile(r"[\s，。！？!?,.、：:；;「」『』\"'`~（）()\[\]{}<>《》]+")
SPACED_PUNCT_RE = re.compile(r"[，。！？!?,.、：:；;「」『』\"'`~（）()\[\]{}<>《》]+")

WAKE_WORDS = (
    "hey jarvis",
    "hi jarvis",
    "ok jarvis",
    "jarvis",
    "javis",
    "hey javis",
    "賈維斯",
    "甲維斯",
    "加維斯",
)

POLITE_WORDS = (
    "請你",
    "請",
    "麻煩你",
    "麻煩",
    "幫我",
    "可以",
    "可不可以",
    "一下子",
    "一下",
    "拜託",
)

LEARN_MARKERS = (
    "這個按鈕",
    "這顆按鈕",
    "這按鈕",
    "此按鈕",
    "這個鍵",
    "這顆鍵",
    "學習",
    "記住",
    "錄製",
    "新增",
    "新增一個",
    "紅外線學習",
)

LIST_WORDS = ("列出", "有哪些", "所有", "清單", "已學", "學過")

LEADING_LABEL_WORDS = (
    "是用來控制",
    "用來控制",
    "負責控制",
    "是控制",
    "控制",
    "叫做",
    "記成",
    "設定成",
    "設成",
    "命名為",
    "命名成",
    "是",
    "為",
)

SEND_LEADING_WORDS = (
    "幫我把",
    "幫我",
    "請你把",
    "請你",
    "請把",
    "請",
    "把",
    "打開",
    "開啟",
    "開",
    "關掉",
    "關閉",
    "關",
    "啟動",
    "停止",
    "控制",
    "按一下",
    "按",
    "切換",
)

TRAILING_WORDS = (
    "這個按鈕",
    "這顆按鈕",
    "的按鈕",
    "按鈕",
    "遙控器",
    "紅外線",
    "訊號",
    "信號",
    "的",
    "了",
    "一下",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_spaces(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def strip_wake_words(text: str) -> str:
    cleaned = str(text or "")
    lowered = cleaned.lower()
    for word in WAKE_WORDS:
        if word in lowered:
            cleaned = re.sub(re.escape(word), "", cleaned, flags=re.IGNORECASE)
            lowered = cleaned.lower()
    return normalize_spaces(cleaned)


def strip_known_words(text: str, words: tuple[str, ...], *, leading: bool = False, trailing: bool = False) -> str:
    value = normalize_spaces(text)
    changed = True
    while changed:
        changed = False
        for word in words:
            if not word:
                continue
            if leading and value.startswith(word):
                value = normalize_spaces(value[len(word) :])
                changed = True
            elif trailing and value.endswith(word):
                value = normalize_spaces(value[: -len(word)])
                changed = True
            elif not leading and not trailing and word in value:
                value = normalize_spaces(value.replace(word, ""))
                changed = True
    return value


def clean_display_text(text: str) -> str:
    value = strip_wake_words(text)
    value = SPACED_PUNCT_RE.sub(" ", value)
    value = strip_known_words(value, POLITE_WORDS)
    return normalize_spaces(value)


def text_key(text: str) -> str:
    value = clean_display_text(text).lower()
    return PUNCT_RE.sub("", value)


def normalize_label(raw: str) -> str:
    value = clean_display_text(raw)
    value = strip_known_words(value, LEADING_LABEL_WORDS, leading=True)
    value = strip_known_words(value, SEND_LEADING_WORDS, leading=True)
    value = strip_known_words(value, TRAILING_WORDS, trailing=True)
    value = strip_known_words(value, LEADING_LABEL_WORDS, leading=True)
    value = strip_known_words(value, TRAILING_WORDS, trailing=True)
    return normalize_spaces(value)


def normalize_send_label(raw: str) -> str:
    value = clean_display_text(raw)
    value = strip_known_words(value, SEND_LEADING_WORDS, leading=True)
    value = strip_known_words(value, TRAILING_WORDS, trailing=True)
    return normalize_label(value)


@dataclass(frozen=True)
class TextIntent:
    action: str
    label: str = ""
    reason: str = ""

    def to_json(self) -> dict[str, str]:
        return {"action": self.action, "label": self.label, "reason": self.reason}


def detect_text_intent(text: str) -> TextIntent:
    cleaned = clean_display_text(text)
    if not cleaned:
        return TextIntent("unknown", reason="empty text")

    if any(word in cleaned for word in LIST_WORDS) and ("紅外線" in cleaned or "按鈕" in cleaned or "遙控" in cleaned):
        return TextIntent("list", reason="list words")

    learnish = any(marker in cleaned for marker in LEARN_MARKERS)
    if learnish:
        patterns = (
            r"(?:把)?(?:這個|這顆|這|此)?(?:遙控器)?(?:按鈕|鍵)(?:是|叫做|記成|設定成|設成|命名為|命名成|用來)?(?P<label>.+)$",
            r"(?:學習|記住|錄製|新增|新增一個)(?P<label>.+?)(?:的)?(?:按鈕|鍵|紅外線|訊號|信號)?$",
            r"(?:紅外線學習)(?P<label>.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, cleaned)
            if match:
                label = normalize_label(match.group("label"))
                if label:
                    return TextIntent("learn", label=label, reason="learn pattern")
        label = normalize_label(cleaned)
        if label:
            return TextIntent("learn", label=label, reason="learn fallback")
        return TextIntent("learn", reason="learn words without label")

    label = normalize_send_label(cleaned)
    if label:
        return TextIntent("send", label=label, reason="default send")
    return TextIntent("unknown", reason="no label")


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = normalize_spaces(value)
        key = text_key(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def build_aliases(label: str, extra_aliases: list[str] | None = None) -> list[str]:
    base = normalize_label(label)
    variants = [label, base]
    if base:
        variants.extend(
            [
                f"控制{base}",
                f"開{base}",
                f"打開{base}",
                f"開啟{base}",
                f"關{base}",
                f"關掉{base}",
                f"關閉{base}",
                f"幫我開{base}",
                f"幫我關{base}",
                f"幫我控制{base}",
                f"按一下{base}",
            ]
        )
        if base.startswith("電"):
            variants.append(base[1:])
        if base.endswith("電風扇"):
            variants.append("風扇")
        if base == "電風扇":
            variants.extend(["開風扇", "關風扇", "打開風扇", "關掉風扇"])
    if extra_aliases:
        variants.extend(extra_aliases)
    return unique_strings(variants)


@dataclass
class IrSignal:
    id: str
    label: str
    aliases: list[str]
    timings_us: list[int]
    protocol: str = "raw"
    frequency_hz: int = DEFAULT_FREQUENCY_HZ
    duty_cycle: float = DEFAULT_DUTY_CYCLE
    repeat_gap_ms: int = DEFAULT_REPEAT_GAP_MS
    default_repeats: int = 1
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "aliases": self.aliases,
            "protocol": self.protocol,
            "frequency_hz": self.frequency_hz,
            "duty_cycle": self.duty_cycle,
            "repeat_gap_ms": self.repeat_gap_ms,
            "default_repeats": self.default_repeats,
            "timings_us": self.timings_us,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "IrSignal":
        label = str(data.get("label") or data.get("id") or "").strip()
        signal_id = str(data.get("id") or text_key(label)).strip()
        aliases = data.get("aliases") if isinstance(data.get("aliases"), list) else []
        timings = data.get("timings_us") if isinstance(data.get("timings_us"), list) else []
        return cls(
            id=signal_id,
            label=label,
            aliases=unique_strings([str(item) for item in aliases] or build_aliases(label)),
            protocol=str(data.get("protocol") or "raw"),
            frequency_hz=int(data.get("frequency_hz") or DEFAULT_FREQUENCY_HZ),
            duty_cycle=float(data.get("duty_cycle") or DEFAULT_DUTY_CYCLE),
            repeat_gap_ms=int(data.get("repeat_gap_ms") or DEFAULT_REPEAT_GAP_MS),
            default_repeats=max(1, int(data.get("default_repeats") or 1)),
            timings_us=[max(1, int(float(value))) for value in timings],
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self._lock = threading.Lock()
        self.data = self._load()

    def _empty(self) -> dict[str, Any]:
        timestamp = now_iso()
        return {
            "version": 1,
            "created_at": timestamp,
            "updated_at": timestamp,
            "signals": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cannot read IR code store {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"IR code store {self.path} is not a JSON object")
        if not isinstance(data.get("signals"), dict):
            data["signals"] = {}
        data.setdefault("version", 1)
        data.setdefault("created_at", now_iso())
        data.setdefault("updated_at", now_iso())
        return data

    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = now_iso()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                delete=False,
            ) as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                temp_path = Path(handle.name)
            temp_path.replace(self.path)

    def signals(self) -> list[IrSignal]:
        raw = self.data.get("signals", {})
        if not isinstance(raw, dict):
            return []
        signals: list[IrSignal] = []
        for value in raw.values():
            if isinstance(value, dict):
                signals.append(IrSignal.from_json(value))
        signals.sort(key=lambda item: item.label)
        return signals

    def add(self, signal: IrSignal, *, overwrite: bool = False) -> None:
        if not signal.id:
            raise ValueError("Signal id is empty")
        signals = self.data.setdefault("signals", {})
        if not isinstance(signals, dict):
            raise RuntimeError("IR code store signals field is corrupted")
        if signal.id in signals and not overwrite:
            raise RuntimeError(f"IR label already exists: {signal.label!r}; use --overwrite to replace it")
        existing = signals.get(signal.id)
        if isinstance(existing, dict) and existing.get("created_at"):
            signal.created_at = str(existing.get("created_at"))
        signal.updated_at = now_iso()
        signals[signal.id] = signal.to_json()
        self.save()

    def delete(self, label_or_query: str) -> IrSignal:
        signal = self.resolve(label_or_query)
        if signal is None:
            raise RuntimeError(f"No learned IR signal matches {label_or_query!r}")
        signals = self.data.get("signals", {})
        if isinstance(signals, dict):
            signals.pop(signal.id, None)
        self.save()
        return signal

    def resolve(self, query: str) -> IrSignal | None:
        query_forms = unique_strings([query, normalize_send_label(query), normalize_label(query)])
        query_keys = [text_key(item) for item in query_forms if text_key(item)]
        if not query_keys:
            return None

        best: tuple[int, IrSignal] | None = None
        for signal in self.signals():
            aliases = unique_strings([signal.label, signal.id] + signal.aliases)
            alias_keys = [(alias, text_key(alias)) for alias in aliases if text_key(alias)]
            for query_key in query_keys:
                for _alias, alias_key in alias_keys:
                    score = 0
                    if query_key == alias_key:
                        score = 1000 + len(alias_key)
                    elif alias_key and alias_key in query_key:
                        score = 100 + len(alias_key)
                    elif query_key and query_key in alias_key:
                        score = 50 + len(query_key)
                    if score and (best is None or score > best[0]):
                        best = (score, signal)
        return best[1] if best else None

    def summary(self) -> dict[str, Any]:
        signals = self.signals()
        return {
            "path": str(self.path),
            "count": len(signals),
            "signals": [
                {
                    "id": signal.id,
                    "label": signal.label,
                    "aliases": signal.aliases,
                    "timing_count": len(signal.timings_us),
                    "duration_us": sum(signal.timings_us),
                    "updated_at": signal.updated_at,
                }
                for signal in signals
            ],
        }


def import_gpio() -> Any:
    try:
        import Jetson.GPIO as GPIO  # type: ignore

        return GPIO
    except ImportError:
        try:
            import RPi.GPIO as GPIO  # type: ignore

            return GPIO
        except ImportError as exc:
            raise RuntimeError(
                "Missing GPIO dependency. On Jetson, install Jetson.GPIO and run with GPIO permission "
                "or sudo if your pin setup requires it."
            ) from exc


def set_gpio_mode(GPIO: Any, pin_mode: str) -> None:
    mode_name = str(pin_mode or "BOARD").strip().upper()
    if not hasattr(GPIO, mode_name):
        valid = ", ".join(name for name in ("BOARD", "BCM", "TEGRA_SOC", "CVM") if hasattr(GPIO, name))
        raise RuntimeError(f"GPIO mode {mode_name!r} is not available. Available modes: {valid or 'unknown'}")
    GPIO.setmode(getattr(GPIO, mode_name))
    if hasattr(GPIO, "setwarnings"):
        GPIO.setwarnings(False)


def precise_sleep_us(duration_us: int) -> None:
    if duration_us <= 0:
        return
    end_ns = time.perf_counter_ns() + int(duration_us * 1000)
    while True:
        remaining_ns = end_ns - time.perf_counter_ns()
        if remaining_ns <= 0:
            return
        if remaining_ns > 1_500_000:
            time.sleep(max(0.0, (remaining_ns - 700_000) / 1_000_000_000.0))


class GpioIrBackend:
    def __init__(self, *, pin_mode: str = DEFAULT_PIN_MODE) -> None:
        self.pin_mode = pin_mode

    def capture(
        self,
        *,
        rx_pin: int,
        active_low: bool,
        timeout_sec: float,
        idle_gap_us: int,
        max_capture_ms: int,
        min_timings: int,
    ) -> list[int]:
        GPIO = import_gpio()
        set_gpio_mode(GPIO, self.pin_mode)
        idle_level = 1 if active_low else 0

        try:
            pull_up = getattr(GPIO, "PUD_UP", None)
            try:
                if pull_up is not None:
                    GPIO.setup(rx_pin, GPIO.IN, pull_up_down=pull_up)
                else:
                    GPIO.setup(rx_pin, GPIO.IN)
            except TypeError:
                GPIO.setup(rx_pin, GPIO.IN)

            print(f"Waiting for IR signal on pin {rx_pin} ({self.pin_mode}, active_low={active_low})...")
            deadline = time.monotonic() + max(0.1, timeout_sec)
            while time.monotonic() < deadline:
                if int(GPIO.input(rx_pin)) != idle_level:
                    break
                time.sleep(0.0001)
            else:
                raise RuntimeError(f"No IR signal detected within {timeout_sec:g}s")

            start_ns = time.perf_counter_ns()
            last_edge_ns = start_ns
            last_level = int(GPIO.input(rx_pin))
            timings: list[int] = []
            max_capture_us = max(1, int(max_capture_ms * 1000))

            while True:
                now_ns = time.perf_counter_ns()
                level = int(GPIO.input(rx_pin))
                if level != last_level:
                    duration_us = max(1, int((now_ns - last_edge_ns) / 1000))
                    timings.append(duration_us)
                    last_edge_ns = now_ns
                    last_level = level

                elapsed_us = int((now_ns - start_ns) / 1000)
                idle_for_us = int((now_ns - last_edge_ns) / 1000)
                if last_level == idle_level and idle_for_us >= idle_gap_us:
                    break
                if elapsed_us >= max_capture_us:
                    break

            if len(timings) < min_timings:
                raise RuntimeError(f"Captured only {len(timings)} timing entries; expected at least {min_timings}")
            return timings
        finally:
            try:
                GPIO.cleanup(rx_pin)
            except Exception:
                pass

    def transmit(
        self,
        *,
        tx_pin: int,
        timings_us: list[int],
        frequency_hz: int,
        duty_cycle: float,
        repeats: int,
        repeat_gap_ms: int,
        active_high: bool,
    ) -> None:
        if not timings_us:
            raise RuntimeError("Signal has no timings to transmit")

        GPIO = import_gpio()
        set_gpio_mode(GPIO, self.pin_mode)
        on_level = GPIO.HIGH if active_high else GPIO.LOW
        off_level = GPIO.LOW if active_high else GPIO.HIGH
        frequency_hz = max(1, int(frequency_hz))
        duty_cycle = clamp(float(duty_cycle), 0.05, 0.95)
        period_ns = max(1, int(1_000_000_000 / frequency_hz))
        on_ns = max(1, int(period_ns * duty_cycle))

        def wait_until_ns(target_ns: int) -> None:
            while True:
                remaining = target_ns - time.perf_counter_ns()
                if remaining <= 0:
                    return
                if remaining > 1_500_000:
                    time.sleep(max(0.0, (remaining - 700_000) / 1_000_000_000.0))

        def carrier_mark(duration_us: int) -> None:
            end_ns = time.perf_counter_ns() + int(duration_us * 1000)
            while time.perf_counter_ns() < end_ns:
                cycle_start = time.perf_counter_ns()
                GPIO.output(tx_pin, on_level)
                wait_until_ns(min(end_ns, cycle_start + on_ns))
                GPIO.output(tx_pin, off_level)
                wait_until_ns(min(end_ns, cycle_start + period_ns))
            GPIO.output(tx_pin, off_level)

        try:
            GPIO.setup(tx_pin, GPIO.OUT, initial=off_level)
            for repeat_index in range(max(1, repeats)):
                for index, duration_us in enumerate(timings_us):
                    duration = max(1, int(duration_us))
                    if index % 2 == 0:
                        carrier_mark(duration)
                    else:
                        GPIO.output(tx_pin, off_level)
                        precise_sleep_us(duration)
                GPIO.output(tx_pin, off_level)
                if repeat_index < repeats - 1:
                    precise_sleep_us(max(0, repeat_gap_ms) * 1000)
        finally:
            try:
                GPIO.output(tx_pin, off_level)
            except Exception:
                pass
            try:
                GPIO.cleanup(tx_pin)
            except Exception:
                pass

    def monitor(
        self,
        *,
        rx_pin: int,
        seconds: float,
        sample_interval_ms: float,
    ) -> dict[str, Any]:
        GPIO = import_gpio()
        set_gpio_mode(GPIO, self.pin_mode)
        samples = 0
        highs = 0
        lows = 0
        edges = 0
        first_level: int | None = None
        last_level: int | None = None
        start = time.monotonic()
        next_print = start
        interval_sec = max(0.0005, sample_interval_ms / 1000.0)
        try:
            GPIO.setup(rx_pin, GPIO.IN)
            print(f"Monitoring IR receiver pin {rx_pin} ({self.pin_mode}) for {seconds:g}s.")
            print("Idle is usually HIGH for common 3-pin IR receiver modules.")
            while time.monotonic() - start < max(0.1, seconds):
                level = int(GPIO.input(rx_pin))
                if first_level is None:
                    first_level = level
                if last_level is not None and level != last_level:
                    edges += 1
                    print(f"edge {edges}: {last_level} -> {level} at {time.monotonic() - start:.4f}s")
                last_level = level
                samples += 1
                if level:
                    highs += 1
                else:
                    lows += 1
                now = time.monotonic()
                if now >= next_print:
                    print(f"level={level} samples={samples} highs={highs} lows={lows} edges={edges}")
                    next_print = now + 0.5
                time.sleep(interval_sec)
            return {
                "ok": True,
                "pin": rx_pin,
                "pin_mode": self.pin_mode,
                "seconds": seconds,
                "samples": samples,
                "first_level": first_level,
                "last_level": last_level,
                "highs": highs,
                "lows": lows,
                "edges": edges,
            }
        finally:
            try:
                GPIO.cleanup(rx_pin)
            except Exception:
                pass


def play_beep(
    *,
    duration_ms: int = 180,
    frequency_hz: float = 1320.0,
    volume: float = 0.55,
    device: int | None = None,
) -> bool:
    if duration_ms <= 0 or volume <= 0:
        return True
    try:
        import numpy as np
        import sounddevice as sd

        sample_rate = 48_000
        sample_count = max(1, int(round(sample_rate * duration_ms / 1000.0)))
        t = np.arange(sample_count, dtype=np.float32) / float(sample_rate)
        tone = np.sin(2.0 * np.pi * float(frequency_hz) * t).astype(np.float32)
        tone += 0.25 * np.sin(2.0 * np.pi * float(frequency_hz) * 2.0 * t).astype(np.float32)
        tone /= max(1.0, float(np.max(np.abs(tone))))
        tone *= float(clamp(volume, 0.0, 1.0))
        fade = max(1, int(round(sample_rate * 0.005)))
        if sample_count > fade * 2:
            ramp = np.linspace(0.0, 1.0, num=fade, dtype=np.float32)
            tone[:fade] *= ramp
            tone[-fade:] *= ramp[::-1]
        sd.play(tone, samplerate=sample_rate, device=device, blocking=True)
        return True
    except Exception as exc:
        print(f"WARNING: audio beep failed ({exc}); using terminal bell.")
        sys.stdout.write("\a")
        sys.stdout.flush()
        return False


class IrController:
    def __init__(
        self,
        *,
        store: SignalStore,
        backend: GpioIrBackend,
        rx_pin: int,
        tx_pin: int,
        receiver_active_low: bool,
        transmitter_active_high: bool,
        frequency_hz: int,
        duty_cycle: float,
    ) -> None:
        self.store = store
        self.backend = backend
        self.rx_pin = rx_pin
        self.tx_pin = tx_pin
        self.receiver_active_low = receiver_active_low
        self.transmitter_active_high = transmitter_active_high
        self.frequency_hz = frequency_hz
        self.duty_cycle = duty_cycle
        self._hardware_lock = threading.Lock()

    def learn(
        self,
        text_or_label: str,
        *,
        label: str | None = None,
        aliases: list[str] | None = None,
        overwrite: bool = False,
        beep: bool = True,
        beep_device: int | None = None,
        timeout_sec: float = DEFAULT_CAPTURE_TIMEOUT_SEC,
        idle_gap_us: int = DEFAULT_IDLE_GAP_US,
        max_capture_ms: int = DEFAULT_MAX_CAPTURE_MS,
        min_timings: int = DEFAULT_MIN_TIMINGS,
        rx_pin: int | None = None,
    ) -> dict[str, Any]:
        intent = detect_text_intent(text_or_label)
        clean_label = normalize_label(label or intent.label or text_or_label)
        if not clean_label:
            raise RuntimeError("No IR label found. Example: 這個按鈕是控制電風扇的")

        if beep:
            print("Beep: press the remote button now.")
            play_beep(device=beep_device)

        with self._hardware_lock:
            timings = self.backend.capture(
                rx_pin=rx_pin if rx_pin is not None else self.rx_pin,
                active_low=self.receiver_active_low,
                timeout_sec=timeout_sec,
                idle_gap_us=idle_gap_us,
                max_capture_ms=max_capture_ms,
                min_timings=min_timings,
            )

        signal = IrSignal(
            id=text_key(clean_label),
            label=clean_label,
            aliases=build_aliases(clean_label, aliases),
            protocol="raw",
            frequency_hz=self.frequency_hz,
            duty_cycle=self.duty_cycle,
            repeat_gap_ms=DEFAULT_REPEAT_GAP_MS,
            default_repeats=1,
            timings_us=timings,
            metadata={
                "captured_at": now_iso(),
                "source_text": text_or_label,
                "receive_pin": rx_pin if rx_pin is not None else self.rx_pin,
                "pin_mode": self.backend.pin_mode,
                "receiver_active_low": self.receiver_active_low,
                "timing_count": len(timings),
                "duration_us": sum(timings),
            },
        )
        self.store.add(signal, overwrite=overwrite)
        return {
            "ok": True,
            "action": "learn",
            "handled": True,
            "label": signal.label,
            "id": signal.id,
            "aliases": signal.aliases,
            "timing_count": len(timings),
            "duration_us": sum(timings),
            "store": str(self.store.path),
        }

    def send(
        self,
        text_or_label: str,
        *,
        label: str | None = None,
        repeats: int | None = None,
        dry_run: bool = False,
        tx_pin: int | None = None,
    ) -> dict[str, Any]:
        query = label or text_or_label
        signal = self.store.resolve(query)
        if signal is None:
            raise RuntimeError(f"No learned IR signal matches {query!r}. Run the learn command first.")

        repeat_count = max(1, int(repeats if repeats is not None else signal.default_repeats))
        target_pin = tx_pin if tx_pin is not None else self.tx_pin
        if dry_run:
            return {
                "ok": True,
                "action": "send",
                "handled": True,
                "dry_run": True,
                "query": query,
                "matched_label": signal.label,
                "id": signal.id,
                "tx_pin": target_pin,
                "pin_mode": self.backend.pin_mode,
                "repeats": repeat_count,
                "timing_count": len(signal.timings_us),
                "duration_us": sum(signal.timings_us),
            }

        with self._hardware_lock:
            self.backend.transmit(
                tx_pin=target_pin,
                timings_us=signal.timings_us,
                frequency_hz=signal.frequency_hz,
                duty_cycle=signal.duty_cycle,
                repeats=repeat_count,
                repeat_gap_ms=signal.repeat_gap_ms,
                active_high=self.transmitter_active_high,
            )
        return {
            "ok": True,
            "action": "send",
            "handled": True,
            "dry_run": False,
            "query": query,
            "matched_label": signal.label,
            "id": signal.id,
            "tx_pin": target_pin,
            "pin_mode": self.backend.pin_mode,
            "repeats": repeat_count,
            "timing_count": len(signal.timings_us),
            "duration_us": sum(signal.timings_us),
        }

    def handle_text(
        self,
        text: str,
        *,
        overwrite: bool = False,
        dry_run: bool = False,
        beep: bool = True,
        repeats: int | None = None,
    ) -> dict[str, Any]:
        intent = detect_text_intent(text)
        if intent.action == "learn":
            return self.learn(text, label=intent.label, overwrite=overwrite, beep=beep)
        if intent.action == "send":
            return self.send(text, label=intent.label, repeats=repeats, dry_run=dry_run)
        if intent.action == "list":
            return {"ok": True, "action": "list", "handled": True, **self.store.summary()}
        return {"ok": False, "handled": False, "action": intent.action, "intent": intent.to_json(), "error": "No IR intent detected"}


def bool_value(data: dict[str, Any], key: str, default: bool) -> bool:
    if key not in data:
        return default
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def int_or_none(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value)))


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_request(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        content_length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError as exc:
        raise RuntimeError("invalid Content-Length") from exc
    if content_length > 64_000:
        raise RuntimeError("request too large")
    raw = handler.rfile.read(content_length).decode("utf-8") if content_length else "{}"
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("request JSON must be an object")
    return parsed


def make_handler(controller: IrController, *, default_dry_run: bool) -> type[BaseHTTPRequestHandler]:
    class JarvisIrHandler(BaseHTTPRequestHandler):
        server_version = "JarvisIRRemote/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:
            if self.path.startswith("/health"):
                json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "jarvis_ir_remote",
                        "codes_path": str(controller.store.path),
                        "pin_mode": controller.backend.pin_mode,
                        "rx_pin": controller.rx_pin,
                        "tx_pin": controller.tx_pin,
                        "receiver_active_low": controller.receiver_active_low,
                        "transmitter_active_high": controller.transmitter_active_high,
                        "signal_count": len(controller.store.signals()),
                    },
                )
                return
            if self.path.startswith("/signals"):
                json_response(self, 200, {"ok": True, **controller.store.summary()})
                return
            json_response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            try:
                data = read_json_request(self)
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
                return

            text = str(data.get("text") or data.get("transcript") or data.get("label") or "").strip()
            label = str(data.get("label") or "").strip() or None
            aliases = data.get("aliases") if isinstance(data.get("aliases"), list) else None
            try:
                if self.path.startswith("/learn"):
                    result = controller.learn(
                        text or label or "",
                        label=label,
                        aliases=[str(item) for item in aliases] if aliases else None,
                        overwrite=bool_value(data, "overwrite", False),
                        beep=bool_value(data, "beep", True),
                        beep_device=int_or_none(data.get("beep_device")),
                        timeout_sec=float(data.get("timeout_sec") or DEFAULT_CAPTURE_TIMEOUT_SEC),
                        idle_gap_us=int(data.get("idle_gap_us") or DEFAULT_IDLE_GAP_US),
                        max_capture_ms=int(data.get("max_capture_ms") or DEFAULT_MAX_CAPTURE_MS),
                        min_timings=int(data.get("min_timings") or DEFAULT_MIN_TIMINGS),
                        rx_pin=int_or_none(data.get("rx_pin")),
                    )
                    json_response(self, 200, result)
                    return
                if self.path.startswith("/send"):
                    result = controller.send(
                        text or label or "",
                        label=label,
                        repeats=int_or_none(data.get("repeats")),
                        dry_run=bool_value(data, "dry_run", default_dry_run),
                        tx_pin=int_or_none(data.get("tx_pin")),
                    )
                    json_response(self, 200, result)
                    return
                if self.path.startswith("/text") or self.path.startswith("/ir"):
                    result = controller.handle_text(
                        text,
                        overwrite=bool_value(data, "overwrite", False),
                        dry_run=bool_value(data, "dry_run", default_dry_run),
                        beep=bool_value(data, "beep", True),
                        repeats=int_or_none(data.get("repeats")),
                    )
                    json_response(self, 200 if result.get("ok") else 400, result)
                    return
                json_response(self, 404, {"ok": False, "error": "not found"})
            except Exception as exc:
                json_response(self, 500, {"ok": False, "handled": True, "error": str(exc)})

    return JarvisIrHandler


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--codes-path", type=Path, default=DEFAULT_CODES_PATH, help="JSON file used to store learned IR codes.")
    parser.add_argument("--rx-pin", type=int, default=DEFAULT_RX_PIN, help="IR receiver GPIO pin.")
    parser.add_argument("--tx-pin", type=int, default=DEFAULT_TX_PIN, help="IR transmitter GPIO pin.")
    parser.add_argument("--pin-mode", default=DEFAULT_PIN_MODE, help="Jetson.GPIO pin mode: BOARD, BCM, TEGRA_SOC, or CVM.")
    parser.add_argument("--rx-active-high", action="store_true", help="Use this if the IR receiver idles low and pulses high.")
    parser.add_argument("--tx-active-low", action="store_true", help="Use this if the transmitter input turns on when driven low.")
    parser.add_argument("--frequency-hz", type=int, default=DEFAULT_FREQUENCY_HZ, help="IR carrier frequency for transmit.")
    parser.add_argument("--duty-cycle", type=float, default=DEFAULT_DUTY_CYCLE, help="IR carrier duty cycle for transmit.")


def build_controller(args: argparse.Namespace) -> IrController:
    store = SignalStore(Path(args.codes_path))
    backend = GpioIrBackend(pin_mode=args.pin_mode)
    return IrController(
        store=store,
        backend=backend,
        rx_pin=args.rx_pin,
        tx_pin=args.tx_pin,
        receiver_active_low=not bool(args.rx_active_high),
        transmitter_active_high=not bool(args.tx_active_low),
        frequency_hz=args.frequency_hz,
        duty_cycle=args.duty_cycle,
    )


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_server(args: argparse.Namespace) -> int:
    controller = build_controller(args)
    try:
        server = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(controller, default_dry_run=args.default_dry_run),
        )
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"ERROR: IR remote tool port {args.port} is already in use.")
            print(f"Check it with: curl http://{args.host}:{args.port}/health")
            return 1
        raise

    print("Jarvis IR remote tool started")
    print(f"  health : http://{args.host}:{args.port}/health")
    print(f"  text   : http://{args.host}:{args.port}/text")
    print(f"  store  : {controller.store.path}")
    print(f"  pins   : rx={controller.rx_pin}, tx={controller.tx_pin}, mode={controller.backend.pin_mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nIR remote tool stopped.")
    finally:
        server.server_close()
    return 0


def sample_nec_timings() -> list[int]:
    return [
        9000,
        4500,
        560,
        560,
        560,
        1690,
        560,
        560,
        560,
        1690,
        560,
        560,
        560,
        1690,
        560,
        560,
        560,
        1690,
        560,
        560,
        560,
    ]


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SignalStore(Path(temp_dir) / "ir_codes.json")
        signal = IrSignal(
            id=text_key("電風扇"),
            label="電風扇",
            aliases=build_aliases("電風扇"),
            timings_us=sample_nec_timings(),
        )
        store.add(signal, overwrite=True)
        assert detect_text_intent("這個按鈕是控制電風扇的").action == "learn"
        assert detect_text_intent("這個按鈕是控制電風扇的").label == "電風扇"
        assert detect_text_intent("幫我開電風扇").action == "send"
        assert detect_text_intent("幫我開電風扇").label == "電風扇"
        assert store.resolve("幫我開電風扇") is not None
        controller = IrController(
            store=store,
            backend=GpioIrBackend(pin_mode="BOARD"),
            rx_pin=18,
            tx_pin=32,
            receiver_active_low=True,
            transmitter_active_high=True,
            frequency_hz=38_000,
            duty_cycle=0.33,
        )
        dry = controller.send("幫我開電風扇", dry_run=True)
        assert dry["ok"] and dry["matched_label"] == "電風扇"
    print("Self-test passed.")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    add_common_args(common)

    parser = argparse.ArgumentParser(description="Learn and replay IR remote buttons from Jetson GPIO.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    learn = subparsers.add_parser("learn", parents=[common], help="Capture one remote button and store it.")
    learn.add_argument("text", nargs="*", help="Natural label text, e.g. 這個按鈕是控制電風扇的")
    learn.add_argument("--label", default=None, help="Explicit stored label.")
    learn.add_argument("--alias", action="append", default=[], help="Extra alias that can trigger this signal.")
    learn.add_argument("--overwrite", action="store_true", help="Replace an existing label.")
    learn.add_argument("--no-beep", action="store_true", help="Do not play the learn cue beep.")
    learn.add_argument("--beep-device", type=int, default=None, help="Optional sounddevice output index.")
    learn.add_argument("--timeout-sec", type=float, default=DEFAULT_CAPTURE_TIMEOUT_SEC)
    learn.add_argument("--idle-gap-us", type=int, default=DEFAULT_IDLE_GAP_US)
    learn.add_argument("--max-capture-ms", type=int, default=DEFAULT_MAX_CAPTURE_MS)
    learn.add_argument("--min-timings", type=int, default=DEFAULT_MIN_TIMINGS)

    send = subparsers.add_parser("send", parents=[common], help="Replay a learned IR button.")
    send.add_argument("text", nargs="*", help="Natural command text, e.g. 幫我開電風扇")
    send.add_argument("--label", default=None, help="Explicit stored label.")
    send.add_argument("--repeats", type=int, default=None, help="Override transmit repeat count.")
    send.add_argument("--dry-run", action="store_true", help="Resolve the signal without touching GPIO.")

    text = subparsers.add_parser("text", parents=[common], help="Auto-route a transcript to learn, send, or list.")
    text.add_argument("text", nargs="+", help="Transcript text from Jarvis.")
    text.add_argument("--overwrite", action="store_true")
    text.add_argument("--no-beep", action="store_true")
    text.add_argument("--dry-run", action="store_true")
    text.add_argument("--repeats", type=int, default=None)

    list_cmd = subparsers.add_parser("list", parents=[common], help="List learned IR buttons.")
    list_cmd.set_defaults(command="list")

    delete = subparsers.add_parser("delete", parents=[common], help="Delete one learned IR button.")
    delete.add_argument("label", help="Label or phrase to delete.")

    monitor = subparsers.add_parser("monitor", parents=[common], help="Print raw IR receiver pin levels for wiring diagnostics.")
    monitor.add_argument("--seconds", type=float, default=8.0)
    monitor.add_argument("--sample-interval-ms", type=float, default=0.2)

    server = subparsers.add_parser("server", parents=[common], help="Run a localhost HTTP IR tool.")
    server.add_argument("--host", default=os.getenv("JARVIS_IR_HOST", "127.0.0.1"))
    server.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    server.add_argument("--default-dry-run", action="store_true", help="Make /send and /text resolve only unless dry_run=false is posted.")

    subparsers.add_parser("self-test", help="Run parser/store tests without GPIO.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "self-test":
            return run_self_test()
        if args.command == "server":
            return run_server(args)

        controller = build_controller(args)
        if args.command == "learn":
            phrase = " ".join(args.text).strip()
            result = controller.learn(
                phrase or args.label or "",
                label=args.label,
                aliases=args.alias,
                overwrite=args.overwrite,
                beep=not args.no_beep,
                beep_device=args.beep_device,
                timeout_sec=args.timeout_sec,
                idle_gap_us=args.idle_gap_us,
                max_capture_ms=args.max_capture_ms,
                min_timings=args.min_timings,
            )
            print_json(result)
            return 0
        if args.command == "send":
            phrase = " ".join(args.text).strip()
            result = controller.send(phrase or args.label or "", label=args.label, repeats=args.repeats, dry_run=args.dry_run)
            print_json(result)
            return 0
        if args.command == "text":
            phrase = " ".join(args.text).strip()
            result = controller.handle_text(
                phrase,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                beep=not args.no_beep,
                repeats=args.repeats,
            )
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.command == "list":
            print_json({"ok": True, **controller.store.summary()})
            return 0
        if args.command == "delete":
            signal = controller.store.delete(args.label)
            print_json({"ok": True, "deleted": signal.to_json(), "store": str(controller.store.path)})
            return 0
        if args.command == "monitor":
            result = controller.backend.monitor(
                rx_pin=args.rx_pin,
                seconds=args.seconds,
                sample_interval_ms=args.sample_interval_ms,
            )
            print_json(result)
            return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
