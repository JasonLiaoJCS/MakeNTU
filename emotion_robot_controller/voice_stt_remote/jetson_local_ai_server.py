#!/usr/bin/env python3
"""
Jetson-local AI sidecar for the MakeNTU wake bridge.

This server intentionally mirrors the desktop_fast_chat_server.py HTTP shape so
the existing wake bridge can point at http://127.0.0.1:8766/voice-chat without
knowing whether the AI path is remote or fully local.

v1 scope:
    audio -> whisper.cpp STT -> local Ollama text/vision model -> reply/control JSON
    focus-check still returns disabled unless the wake bridge enables focus mode later
"""

from __future__ import annotations

import argparse
import base64
import cgi
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

import desktop_fast_chat_server as desktop_core


DEBUG_VERSION = 19
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_OLLAMA_URL = os.getenv("JETSON_LOCAL_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
DEFAULT_OLLAMA_MODEL = os.getenv("JETSON_LOCAL_OLLAMA_MODEL", "qwen3:1.7b-q4_K_M")
DEFAULT_FALLBACK_OLLAMA_MODEL = os.getenv("JETSON_LOCAL_OLLAMA_FALLBACK_MODEL", "qwen3:0.6b")
DEFAULT_VISION_MODEL = os.getenv("JETSON_LOCAL_VISION_MODEL", "qwen2.5vl:3b-q4_K_M")
DEFAULT_NUM_CTX = int(os.getenv("JETSON_LOCAL_OLLAMA_NUM_CTX", "2048"))
DEFAULT_NUM_PREDICT = int(os.getenv("JETSON_LOCAL_OLLAMA_NUM_PREDICT", "120"))
DEFAULT_TEMPERATURE = float(os.getenv("JETSON_LOCAL_OLLAMA_TEMPERATURE", "0.5"))
DEFAULT_KEEP_ALIVE = os.getenv("JETSON_LOCAL_OLLAMA_KEEP_ALIVE", "10m")
DEFAULT_MEMORY_TURNS = int(os.getenv("JETSON_LOCAL_MEMORY_TURNS", "0"))
DEFAULT_VISION_TIMEOUT = float(os.getenv("JETSON_LOCAL_VISION_TIMEOUT", "120"))
DEFAULT_MAX_IMAGE_BYTES = int(os.getenv("JETSON_LOCAL_MAX_IMAGE_BYTES", "2000000"))
DEFAULT_STT_LANGUAGE = os.getenv("WHISPER_CPP_LANGUAGE", "zh")
DEFAULT_STT_TIMEOUT = float(os.getenv("WHISPER_CPP_TIMEOUT", "30"))


last_debug: dict[str, Any] = {}
debug_log_path: Path | None = None
chat_engine: "LocalOllamaChatEngine | None" = None
stt_adapter: "WhisperCppAdapter | None" = None
vision_model = DEFAULT_VISION_MODEL
vision_timeout_sec = DEFAULT_VISION_TIMEOUT
vision_enabled = True
server_force_vision = False
max_image_bytes = DEFAULT_MAX_IMAGE_BYTES


GOODBYE_EXACT_PHRASES = {
    "掰掰",
    "拜拜",
    "拜",
    "再見",
    "再见",
    "下次見",
    "下次见",
    "byebye",
    "bye",
    "bye bye",
    "goodbye",
    "good bye",
}

GOODBYE_PATTERNS = [
    re.compile(r"(掰|拜){2,}(了|啦|囉|啰|喔|哦|唷|呀|啊|吧)?$"),
    re.compile(r"白白(了|啦|囉|啰|喔|哦|唷|呀|啊|吧)?$"),
    re.compile(r"八八(了|啦|囉|啰|喔|哦|唷|呀|啊|吧)?$"),
    re.compile(r"再[見见會会]"),
    re.compile(r"\b(bye|by|buy){1,2}\b"),
    re.compile(r"\bgood\s*(bye|by)\b"),
]


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def short_text(text: Any, limit: int = 120) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "..."


def normalize_for_memory_command(text: str) -> str:
    lowered = str(text or "").strip().lower()
    return re.sub(r"[\s，。！？!?、,.~～…]+", "", lowered)


def is_goodbye_for_memory_clear(text: str) -> bool:
    normalized = normalize_for_memory_command(text)
    if not normalized:
        return False
    if normalized in {normalize_for_memory_command(item) for item in GOODBYE_EXACT_PHRASES}:
        return True
    return any(pattern.search(normalized) for pattern in GOODBYE_PATTERNS)


def write_debug_log(data: dict[str, Any]) -> None:
    if debug_log_path is None:
        return
    try:
        with debug_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"WARN: failed to write debug log {debug_log_path}: {exc}")


def set_last_debug(data: dict[str, Any]) -> None:
    global last_debug
    last_debug = data
    write_debug_log(data)


def post_json(url: str, payload: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {short_text(raw, 500)}") from exc

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"HTTP response JSON is not an object from {url}: {short_text(raw, 500)}")
    return parsed


def ollama_base_url(chat_url: str) -> str:
    parsed = urllib.parse.urlsplit(chat_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/api/chat"):
        path = path[: -len("/api/chat")]
    elif path.endswith("/api/generate"):
        path = path[: -len("/api/generate")]
    else:
        path = ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def ollama_generate_url(chat_url: str) -> str:
    if chat_url.endswith("/api/chat"):
        return chat_url[: -len("/api/chat")] + "/api/generate"
    if chat_url.endswith("/api/generate"):
        return chat_url
    return chat_url.rstrip("/") + "/api/generate"


def ollama_tags_url(chat_url: str) -> str:
    return ollama_base_url(chat_url).rstrip("/") + "/api/tags"


def ollama_models(chat_url: str, timeout_sec: float = 3.0) -> set[str]:
    try:
        data = post_json_get(ollama_tags_url(chat_url), timeout_sec=timeout_sec)
    except Exception:
        return set()
    models = data.get("models")
    if not isinstance(models, list):
        return set()
    names: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        for key in ("name", "model"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
    return names


def post_json_get(url: str, timeout_sec: float) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"GET {url} did not return a JSON object")
    return parsed


def resolve_default_stt_bin() -> str:
    env_bin = os.getenv("WHISPER_CPP_BIN", "").strip()
    if env_bin:
        return env_bin
    for name in ("whisper-cli", "main"):
        found = shutil.which(name)
        if found:
            return found
    return "whisper-cli"


def resolve_default_stt_model() -> str:
    env_model = os.getenv("WHISPER_CPP_MODEL", "").strip()
    if env_model:
        return env_model
    candidates = [
        Path.home() / "whisper.cpp" / "models" / "ggml-base.bin",
        Path.home() / ".cache" / "whisper.cpp" / "ggml-base.bin",
        Path("/home/asrlab-yian/MakeNTU/models/whisper/ggml-base.bin"),
        Path.home() / "whisper.cpp" / "models" / "ggml-tiny.bin",
        Path.home() / ".cache" / "whisper.cpp" / "ggml-tiny.bin",
        Path("/home/asrlab-yian/MakeNTU/models/whisper/ggml-tiny.bin"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return str(candidates[0])


class WhisperCppAdapter:
    def __init__(
        self,
        *,
        binary: str,
        model: str,
        language: str = DEFAULT_STT_LANGUAGE,
        timeout_sec: float = DEFAULT_STT_TIMEOUT,
        extra_args: str = "",
    ) -> None:
        self.binary = binary
        self.model = model
        self.language = language
        self.timeout_sec = timeout_sec
        self.extra_args = shlex.split(extra_args) if extra_args.strip() else []

    @property
    def binary_path(self) -> str | None:
        if os.path.sep in self.binary:
            path = Path(self.binary).expanduser()
            return str(path) if path.exists() else None
        return shutil.which(self.binary)

    @property
    def model_path(self) -> Path:
        return Path(self.model).expanduser()

    def ready(self) -> bool:
        return self.binary_path is not None and self.model_path.exists()

    def health(self) -> dict[str, Any]:
        return {
            "engine": "whisper.cpp",
            "binary": self.binary,
            "binary_path": self.binary_path,
            "binary_exists": self.binary_path is not None,
            "model": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "language": self.language,
            "timeout_sec": self.timeout_sec,
            "extra_args": self.extra_args,
            "ready": self.ready(),
        }

    def transcribe(self, wav_path: Path) -> tuple[str, dict[str, Any]]:
        binary_path = self.binary_path
        if not binary_path:
            raise RuntimeError(f"whisper.cpp binary not found: {self.binary}")
        model_path = self.model_path
        if not model_path.exists():
            raise RuntimeError(f"whisper.cpp model not found: {model_path}")

        command = [
            binary_path,
            "-m",
            str(model_path),
            "-f",
            str(wav_path),
            "-l",
            self.language,
            "-nt",
            "-np",
            *self.extra_args,
        ]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_sec,
            check=False,
        )
        transcript = clean_whisper_output(completed.stdout)
        if not transcript:
            transcript = clean_whisper_output(completed.stderr)
        debug = {
            "stt_command": " ".join(shlex.quote(part) for part in command),
            "stt_returncode": completed.returncode,
            "stt_stdout_preview": short_text(completed.stdout, 500),
            "stt_stderr_preview": short_text(completed.stderr, 500),
            "stt_ms": elapsed_ms(started),
        }
        if completed.returncode != 0:
            raise RuntimeError(
                f"whisper.cpp failed with code {completed.returncode}: "
                f"{short_text(completed.stderr or completed.stdout, 300)}"
            )
        return transcript.strip(), debug


def clean_whisper_output(raw: str) -> str:
    lines: list[str] = []
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith(("whisper_", "main:", "system_info:", "ggml_", "whisper_print_timings")):
            continue
        if stripped.startswith("[") and "-->" in stripped:
            stripped = stripped.split("]", 1)[-1].strip() if "]" in stripped else stripped
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


class LocalOllamaChatEngine:
    def __init__(
        self,
        *,
        url: str,
        model: str,
        fallback_model: str,
        no_think: bool,
        memory_turns: int,
        num_ctx: int,
        num_predict: int,
        temperature: float,
        keep_alive: str,
        timeout_sec: float,
    ) -> None:
        self.url = url
        self.model = model
        self.fallback_model = fallback_model
        self.no_think = no_think
        self.memory_turns = max(0, memory_turns)
        self.num_ctx = max(256, num_ctx)
        self.num_predict = max(16, num_predict)
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.timeout_sec = timeout_sec
        self.memory: list[dict[str, Any]] = []

    def available_models(self) -> set[str]:
        return ollama_models(self.url)

    def active_model(self) -> str:
        models = self.available_models()
        if self.model in models:
            return self.model
        if self.fallback_model and self.fallback_model in models:
            return self.fallback_model
        return self.model

    def ready(self) -> bool:
        return self.active_model() in self.available_models()

    def conversation_context(self) -> str:
        if self.memory_turns <= 0 or not self.memory:
            return ""
        # Expose only the assistant's prior replies as short summary items.
        # Do NOT include the user's previous questions.
        lines: list[str] = []
        lines.append("以下為你先前已回覆的要點（僅包含助理回覆，供參考）：")
        for index, turn in enumerate(self.memory[-self.memory_turns :], start=1):
            reply = short_text(turn.get("assistant", ""), 200)
            if not reply:
                continue
            lines.append(f"{index}. {reply}")
        return "\n".join(lines)

    def remember_turn(self, transcript: str, reply: str, *, used_vision: bool = False) -> None:
        if self.memory_turns <= 0:
            return
        user_text = short_text(transcript, 180)
        reply_text = short_text(desktop_core.strip_thinking_text(reply), 240)
        if not user_text or not reply_text:
            return
        self.memory.append({"user": user_text, "assistant": reply_text, "used_vision": bool(used_vision)})
        if len(self.memory) > self.memory_turns:
            del self.memory[: len(self.memory) - self.memory_turns]

    def clear_memory(self) -> int:
        count = len(self.memory)
        self.memory.clear()
        return count

    def user_content(self, transcript: str, use_no_think: bool) -> str:
        context = self.conversation_context()
        context_block = f"{context}\n\n" if context else ""
        content = f"""{context_block}使用者原話：{transcript}

注意：不要重複先前已回答的問題；若使用者提出新話題請直接回答。

請依照 system schema，只輸出 JSON object。reply 欄位必須是自然語言，不可混入控制資訊。"""
        if use_no_think:
            content = "/no_think\n" + content
        return content

    def build_payload(self, transcript: str, *, model: str, use_no_think: bool) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": desktop_core.SYSTEM_PROMPT},
                {"role": "user", "content": self.user_content(transcript, use_no_think)},
            ],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }

    def build_generate_payload(self, transcript: str, *, model: str, use_no_think: bool) -> dict[str, Any]:
        return {
            "model": model,
            "system": desktop_core.SYSTEM_PROMPT,
            "prompt": self.user_content(transcript, use_no_think),
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }

    def vision_user_content(self, transcript: str, use_no_think: bool) -> str:
        context = self.conversation_context()
        context_block = f"{context}\n\n" if context else ""
        content = f"""{context_block}使用者原話：{transcript}

請根據使用者原話和這張相機畫面，依照 system schema 只輸出 JSON object。reply 欄位必須是自然語言，不可混入控制資訊。"""
        if use_no_think:
            content = "/no_think\n" + content
        return content

    def build_vision_payload(
        self,
        transcript: str,
        image_bytes: bytes,
        *,
        model: str,
        use_no_think: bool,
    ) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": desktop_core.VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self.vision_user_content(transcript, use_no_think),
                    "images": [image_b64],
                },
            ],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": min(self.temperature, 0.2),
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }

    def warm_up(self) -> None:
        print(
            "Local Ollama warm-up: "
            f"model={self.active_model()}, no_think={self.no_think}, "
            f"num_ctx={self.num_ctx}, num_predict={self.num_predict}"
        )
        result = self.analyze("你好，聽得到我說話嗎？", remember=False)
        print("Local Ollama warm-up done:", result.get("emotion", {}).get("primary", "unknown"))

    def analyze(
        self,
        transcript: str,
        request_id: str = "",
        *,
        remember: bool = True,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex[:8]
        model = model_override or self.active_model()
        debug: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "stage": "ollama_request",
            "local_ai": True,
            "ollama_url": self.url,
            "ollama_model": model,
            "configured_ollama_model": self.model,
            "fallback_ollama_model": self.fallback_model,
            "model_override": model_override,
            "no_think": self.no_think,
            "think": False,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "temperature": self.temperature,
            "memory_turns": self.memory_turns,
            "memory_items": len(self.memory),
            "transcript_chars": len(transcript),
            "transcript_preview": short_text(transcript, 120),
        }
        clear_memory_after_reply = is_goodbye_for_memory_clear(transcript)
        if clear_memory_after_reply:
            debug["memory_clear_requested"] = True
        if model not in self.available_models():
            reason = f"Ollama model not available locally: {self.model}"
            if self.fallback_model:
                reason += f" (fallback {self.fallback_model} also unavailable)"
            debug.update({"ok": False, "stage": "ollama_model_unavailable", "fallback_reason": reason})
            result = desktop_core.fallback_result(transcript, reason)
            if clear_memory_after_reply:
                cleared = self.clear_memory()
                debug["memory_cleared_after_reply"] = True
                debug["memory_items_cleared"] = cleared
                debug["memory_items_after"] = len(self.memory)
            result["request_id"] = request_id
            result["debug"] = debug
            result.setdefault("timing", {})["llm_ms"] = elapsed_ms(started)
            set_last_debug(debug)
            return result

        payload = self.build_payload(transcript, model=model, use_no_think=self.no_think)
        try:
            response = post_json(self.url, payload, timeout_sec=self.timeout_sec)
            debug["stage"] = "ollama_response"
            if "error" in response:
                raise RuntimeError(str(response.get("error", "unknown error")))
            debug.update(desktop_core.summarize_ollama_response(response))
            content = desktop_core.extract_ollama_text(response)
            if not content.strip() and self.no_think:
                debug["retry_reason"] = "empty content with /no_think; retrying without /no_think"
                retry_payload = self.build_payload(transcript, model=model, use_no_think=False)
                retry_response = post_json(self.url, retry_payload, timeout_sec=self.timeout_sec)
                debug["retried_without_no_think"] = True
                if "error" in retry_response:
                    debug["retry_error"] = str(retry_response.get("error", "unknown error"))
                else:
                    response = retry_response
                    debug.update({f"retry_{key}": value for key, value in desktop_core.summarize_ollama_response(response).items()})
                    content = desktop_core.extract_ollama_text(response)
            if not content.strip():
                generate_url = ollama_generate_url(self.url)
                debug["generate_retry_reason"] = "chat returned empty content; trying /api/generate"
                debug["generate_url"] = generate_url
                generate_payload = self.build_generate_payload(transcript, model=model, use_no_think=False)
                generate_response = post_json(generate_url, generate_payload, timeout_sec=self.timeout_sec)
                if "error" in generate_response:
                    debug["generate_error"] = str(generate_response.get("error", "unknown error"))
                else:
                    response = generate_response
                    debug.update({f"generate_{key}": value for key, value in desktop_core.summarize_ollama_response(response).items()})
                    content = desktop_core.extract_ollama_text(response)
            debug.update(
                {
                    "ollama_done": response.get("done"),
                    "ollama_content_chars": len(content),
                    "ollama_content_preview": short_text(desktop_core.strip_thinking_text(content), 500),
                }
            )
            reply, control, parse_status, fallback_reason = desktop_core.parse_ai_content(content, transcript, used_vision=False)
            emotion = desktop_core.emotion_from_control(control, transcript)
            debug.update(
                {
                    "ok": True,
                    "parse_status": parse_status,
                    "control": control,
                    "reply_chars": len(reply),
                    "emotion_primary": emotion.get("primary"),
                    "persistent_state": control.get("persistent_state"),
                    "screen_mode": control.get("screen_mode"),
                    "head_motion": control.get("head_motion"),
                }
            )
            result = {
                "request_id": request_id,
                "reply": reply,
                "control": control,
                "emotion": emotion,
                "timing": {"llm_ms": elapsed_ms(started)},
                "debug": debug,
            }
            if fallback_reason:
                result["fallback_reason"] = fallback_reason
                debug["fallback_reason"] = fallback_reason
            if clear_memory_after_reply:
                cleared = self.clear_memory()
                debug["memory_cleared_after_reply"] = True
                debug["memory_items_cleared"] = cleared
                debug["memory_items_after"] = len(self.memory)
            elif remember:
                self.remember_turn(transcript, reply, used_vision=False)
                debug["memory_items_after"] = len(self.memory)
            set_last_debug(debug)
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            reason = f"Local Ollama request failed: {short_text(exc, 180)}"
            if (
                model_override is None
                and self.fallback_model
                and self.fallback_model != model
                and self.fallback_model in self.available_models()
            ):
                debug.update(
                    {
                        "ok": False,
                        "stage": "ollama_exception_retry_fallback_model",
                        "fallback_reason": reason,
                        "retry_fallback_model": self.fallback_model,
                        "exception_type": exc.__class__.__name__,
                    }
                )
                set_last_debug(debug)
                retry_result = self.analyze(
                    transcript,
                    request_id=request_id,
                    remember=remember,
                    model_override=self.fallback_model,
                )
                retry_debug = retry_result.get("debug") if isinstance(retry_result.get("debug"), dict) else {}
                retry_debug["text_fallback_from"] = model
                retry_debug["text_fallback_reason"] = reason
                retry_result["debug"] = retry_debug
                retry_result["text_fallback_from"] = model
                retry_result["text_fallback_reason"] = reason
                set_last_debug(retry_debug)
                return retry_result
            debug.update({"ok": False, "stage": "ollama_exception", "fallback_reason": reason, "exception_type": exc.__class__.__name__})
            result = desktop_core.fallback_result(transcript, reason)
            if clear_memory_after_reply:
                cleared = self.clear_memory()
                debug["memory_cleared_after_reply"] = True
                debug["memory_items_cleared"] = cleared
                debug["memory_items_after"] = len(self.memory)
            result["request_id"] = request_id
            result["debug"] = debug
            result.setdefault("timing", {})["llm_ms"] = elapsed_ms(started)
            set_last_debug(debug)
            return result

    def analyze_with_vision(
        self,
        transcript: str,
        image_bytes: bytes,
        *,
        request_id: str = "",
        model: str = DEFAULT_VISION_MODEL,
        timeout_sec: float = DEFAULT_VISION_TIMEOUT,
    ) -> dict[str, Any]:
        started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex[:8]
        debug: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "stage": "vision_ollama_request",
            "local_ai": True,
            "ollama_url": self.url,
            "ollama_model": model,
            "text_ollama_model": self.active_model(),
            "configured_ollama_model": self.model,
            "fallback_ollama_model": self.fallback_model,
            "vision_requested": True,
            "used_vision": True,
            "image_bytes": len(image_bytes),
            "no_think": self.no_think,
            "think": False,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "temperature": min(self.temperature, 0.2),
            "memory_turns": self.memory_turns,
            "memory_items": len(self.memory),
            "transcript_chars": len(transcript),
            "transcript_preview": short_text(transcript, 120),
        }
        if model not in self.available_models():
            reason = f"Ollama vision model not available locally: {model}"
            debug.update({"ok": False, "stage": "vision_model_unavailable", "fallback_reason": reason, "used_vision": False})
            text_result = self.analyze(transcript, request_id=request_id)
            text_result = desktop_core.prefix_vision_unavailable(text_result, reason, transcript)
            text_result["vision_requested"] = True
            text_result["used_vision"] = False
            text_result["vision_model"] = model
            text_result["debug"] = {**debug, **(text_result.get("debug") if isinstance(text_result.get("debug"), dict) else {})}
            set_last_debug(text_result["debug"])
            return text_result

        payload = self.build_vision_payload(transcript, image_bytes, model=model, use_no_think=self.no_think)
        try:
            print(f"voice-chat {request_id}: calling local vision model={model} image_bytes={len(image_bytes)}")
            response = post_json(self.url, payload, timeout_sec=timeout_sec)
            debug["stage"] = "vision_ollama_response"
            if "error" in response:
                raise RuntimeError(f"Ollama vision error: {short_text(str(response.get('error', 'unknown error')), 240)}")
            debug.update(desktop_core.summarize_ollama_response(response))
            content = desktop_core.extract_ollama_text(response)
            if not content.strip() and self.no_think:
                debug["retry_reason"] = "empty vision content with /no_think; retrying without /no_think"
                retry_payload = self.build_vision_payload(transcript, image_bytes, model=model, use_no_think=False)
                response = post_json(self.url, retry_payload, timeout_sec=timeout_sec)
                debug["retried_without_no_think"] = True
                if "error" in response:
                    raise RuntimeError(f"Ollama vision retry error: {short_text(str(response.get('error', 'unknown error')), 240)}")
                debug.update({f"retry_{key}": value for key, value in desktop_core.summarize_ollama_response(response).items()})
                content = desktop_core.extract_ollama_text(response)

            debug.update(
                {
                    "ollama_done": response.get("done"),
                    "ollama_content_chars": len(content),
                    "ollama_content_preview": short_text(desktop_core.strip_thinking_text(content), 500),
                }
            )
            reply, control, parse_status, fallback_reason = desktop_core.parse_ai_content(content, transcript, used_vision=True)
            if not reply:
                raise ValueError("Ollama vision returned empty content")
            emotion = desktop_core.emotion_from_control(control, transcript)
            debug.update(
                {
                    "ok": True,
                    "parse_status": parse_status,
                    "control": control,
                    "reply_chars": len(reply),
                    "emotion_primary": emotion.get("primary"),
                    "persistent_state": control.get("persistent_state"),
                    "screen_mode": control.get("screen_mode"),
                    "head_motion": control.get("head_motion"),
                }
            )
            result = {
                "request_id": request_id,
                "reply": reply,
                "control": control,
                "emotion": emotion,
                "vision_requested": True,
                "used_vision": True,
                "vision_model": model,
                "vision_attempted_model": model,
                "vision_error": None,
                "timing": {
                    "vision_ms": elapsed_ms(started),
                    "llm_ms": elapsed_ms(started),
                },
                "debug": debug,
            }
            if fallback_reason:
                result["fallback_reason"] = fallback_reason
                debug["fallback_reason"] = fallback_reason
            if is_goodbye_for_memory_clear(transcript):
                cleared = self.clear_memory()
                debug["memory_cleared_after_reply"] = True
                debug["memory_items_cleared"] = cleared
                debug["memory_items_after"] = len(self.memory)
            else:
                self.remember_turn(transcript, reply, used_vision=True)
                debug["memory_items_after"] = len(self.memory)
            set_last_debug(debug)
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            reason = f"Vision model failed: {short_text(exc, 180)}"
            print(f"WARN: {reason}")
            debug.update(
                {
                    "ok": False,
                    "stage": "vision_exception",
                    "fallback_reason": reason,
                    "exception_type": exc.__class__.__name__,
                    "used_vision": False,
                }
            )
            text_result = self.analyze(transcript, request_id=request_id)
            text_result = desktop_core.prefix_vision_unavailable(text_result, reason, transcript)
            text_result["vision_requested"] = True
            text_result["used_vision"] = False
            text_result["vision_model"] = model
            timing = text_result.setdefault("timing", {})
            if isinstance(timing, dict):
                timing["vision_ms"] = elapsed_ms(started)
            text_debug = text_result.get("debug") if isinstance(text_result.get("debug"), dict) else {}
            text_debug.update(
                {
                    "vision_requested": True,
                    "used_vision": False,
                    "vision_error": reason,
                    "vision_stage": debug.get("stage"),
                    "vision_model": model,
                }
            )
            text_result["debug"] = text_debug
            set_last_debug(text_debug)
            return text_result


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, bytes], dict[str, str]]:
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
        "CONTENT_LENGTH": handler.headers.get("Content-Length", "0"),
    }
    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ=environ,
        keep_blank_values=True,
    )
    files: dict[str, bytes] = {}
    fields: dict[str, str] = {}
    for key in form.keys():
        item = form[key]
        if isinstance(item, list):
            item = item[0]
        if getattr(item, "filename", None):
            files[key] = item.file.read()
        else:
            value = item.value
            fields[key] = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    return files, fields


def optional_image_bytes(files: dict[str, bytes]) -> tuple[bytes | None, str | None]:
    if "image" not in files:
        return None, "no image uploaded"
    data = files.get("image") or b""
    if not data:
        return None, "empty image upload"
    if len(data) > max_image_bytes:
        return None, f"image upload too large: {len(data)} bytes > {max_image_bytes}"
    return data, None


def response_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class LocalAIHandler(BaseHTTPRequestHandler):
    server_version = "JetsonLocalAI/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = response_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/health":
            self.send_json(build_health())
            return
        if path == "/debug":
            self.send_json(build_debug())
            return
        self.send_json({"ok": False, "error": "not found", "path": path}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/text-chat":
            self.handle_text_chat()
            return
        if path == "/voice-chat":
            self.handle_voice_chat()
            return
        if path == "/memory/clear":
            self.handle_memory_clear()
            return
        if path == "/focus-check":
            self.send_json(
                {
                    "ok": False,
                    "request_id": uuid.uuid4().hex[:8],
                    "error": "focus vision disabled in jetson local launcher",
                    "vision_enabled": vision_enabled,
                    "vision_model": vision_model,
                },
                status=503,
            )
            return
        self.send_json({"ok": False, "error": "not found", "path": path}, status=404)

    def handle_text_chat(self) -> None:
        started = time.monotonic()
        request_id = uuid.uuid4().hex[:8]
        data = read_json_body(self)
        transcript = str(data.get("text", "")).strip()
        if chat_engine is None or not chat_engine.ready():
            self.send_json({"ok": False, "request_id": request_id, "error": "server not ready", **build_readiness()}, status=503)
            return
        result = chat_engine.analyze(transcript, request_id=request_id)
        timing = result.setdefault("timing", {})
        timing["total_ms"] = elapsed_ms(started)
        self.send_json({"ok": True, "transcript": transcript, **result, "elapsed_ms": elapsed_ms(started)})

    def handle_memory_clear(self) -> None:
        request_id = uuid.uuid4().hex[:8]
        data = read_json_body(self)
        reason = short_text(data.get("reason", ""), 160)
        if chat_engine is None:
            self.send_json({"ok": False, "request_id": request_id, "error": "chat engine not ready"}, status=503)
            return
        cleared = chat_engine.clear_memory()
        debug = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "stage": "memory_clear",
            "ok": True,
            "reason": reason,
            "memory_items_cleared": cleared,
            "memory_items_after": len(chat_engine.memory),
        }
        set_last_debug(debug)
        self.send_json(
            {
                "ok": True,
                "request_id": request_id,
                "cleared": cleared,
                "memory_items_after": len(chat_engine.memory),
                "reason": reason,
                "debug": debug,
            }
        )

    def handle_voice_chat(self) -> None:
        started = time.monotonic()
        request_id = uuid.uuid4().hex[:8]
        if chat_engine is None or stt_adapter is None or not chat_engine.ready() or not stt_adapter.ready():
            self.send_json({"ok": False, "request_id": request_id, "error": "server not ready", **build_readiness()}, status=503)
            return
        try:
            files, fields = parse_multipart(self)
        except Exception as exc:
            self.send_json({"ok": False, "request_id": request_id, "error": f"bad multipart upload: {short_text(exc, 180)}"}, status=400)
            return
        audio_bytes = files.get("audio")
        if not audio_bytes:
            self.send_json({"ok": False, "request_id": request_id, "error": "missing audio"}, status=400)
            return
        metadata = parse_metadata(fields.get("metadata", ""))
        image_received = "image" in files
        image_bytes, image_error = optional_image_bytes(files) if image_received else (None, "no image uploaded")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="jetson_local_ai_", suffix=".wav", delete=False) as handle:
                handle.write(audio_bytes)
                temp_path = Path(handle.name)
            stt_started = time.monotonic()
            transcript, stt_debug = stt_adapter.transcribe(temp_path)
            asr_ms = elapsed_ms(stt_started)
            if not transcript.strip():
                reason = "empty local STT transcript"
                result = desktop_core.fallback_result("", reason)
                result["request_id"] = request_id
                debug = {
                    "request_id": request_id,
                    "timestamp": now_stamp(),
                    "stage": "stt_empty",
                    "ok": True,
                    "local_ai": True,
                    "fallback_reason": reason,
                    **stt_debug,
                }
                result["debug"] = debug
                result["timing"] = {"asr_ms": asr_ms, "llm_ms": 0, "total_ms": elapsed_ms(started)}
                set_last_debug(debug)
            else:
                intent_started = time.monotonic()
                auto_vision_intent, auto_vision_reason = desktop_core.detect_vision_intent(transcript)
                vision_intent_ms = elapsed_ms(intent_started)
                normalized_transcript = desktop_core.normalize_vision_intent_text(transcript)

                metadata_mode = str(metadata.get("vision_mode", "")).strip().lower()
                client_no_vision = desktop_core.truthy(metadata.get("no_vision")) or metadata_mode in {
                    "off",
                    "disabled",
                    "disable",
                    "no_vision",
                    "none",
                }
                client_force_vision = desktop_core.truthy(metadata.get("force_vision")) or metadata_mode in {
                    "force",
                    "forced",
                    "always",
                }
                if client_no_vision:
                    vision_requested = False
                    vision_reason = "disabled_by_client_no_vision"
                elif not vision_enabled:
                    vision_requested = False
                    vision_reason = "disabled_by_server_no_vision"
                elif client_force_vision:
                    vision_requested = True
                    vision_reason = "forced_by_client_metadata"
                elif server_force_vision:
                    vision_requested = True
                    vision_reason = "forced_by_server_flag"
                else:
                    vision_requested = auto_vision_intent
                    vision_reason = auto_vision_reason

                print(f"voice-chat {request_id}: transcript={transcript!r}")
                print(f"voice-chat {request_id}: normalized_transcript={normalized_transcript!r}")
                print(
                    f"voice-chat {request_id}: vision_intent={vision_requested} "
                    f"reason={vision_reason} auto={auto_vision_intent}:{auto_vision_reason} "
                    f"image_received={image_received} image_size_bytes={len(image_bytes) if image_bytes else 0}"
                )

                if vision_requested and vision_enabled:
                    if image_bytes:
                        result = chat_engine.analyze_with_vision(
                            transcript,
                            image_bytes,
                            request_id=request_id,
                            model=vision_model,
                            timeout_sec=vision_timeout_sec,
                        )
                    else:
                        reason = image_error or "image unavailable"
                        print(f"ERROR: voice-chat {request_id}: vision_intent=True but image unavailable: {reason}")
                        result = chat_engine.analyze(transcript, request_id=request_id)
                        result = desktop_core.prefix_vision_unavailable(result, reason, transcript)
                        result["vision_requested"] = True
                        result["used_vision"] = False
                        result["vision_model"] = vision_model
                else:
                    result = chat_engine.analyze(transcript, request_id=request_id)
                    result["vision_requested"] = vision_requested
                    result["used_vision"] = False
                    result["vision_model"] = vision_model
                    if auto_vision_intent and not vision_enabled:
                        result = desktop_core.prefix_vision_unavailable(result, "vision disabled on server", transcript)

                result_debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
                result_debug.update(
                    {
                        "image_received": image_received,
                        "image_size_bytes": len(image_bytes) if image_bytes else 0,
                        "image_error": image_error,
                        "vision_intent": vision_requested,
                        "vision_reason": vision_reason,
                        "auto_vision_intent": auto_vision_intent,
                        "auto_vision_reason": auto_vision_reason,
                        "normalized_transcript": normalized_transcript,
                        "metadata": {
                            "vision_mode": metadata.get("vision_mode"),
                            "force_vision": metadata.get("force_vision"),
                            "no_vision": metadata.get("no_vision"),
                            "turn_source": metadata.get("turn_source"),
                            "latency_profile": metadata.get("latency_profile"),
                        },
                        **stt_debug,
                    }
                )
                result["debug"] = result_debug
                timing = result.setdefault("timing", {})
                timing["asr_ms"] = asr_ms
                timing["vision_intent_ms"] = vision_intent_ms
                timing["total_ms"] = elapsed_ms(started)
                result["vision_intent"] = vision_requested
                result["vision_requested"] = vision_requested
                result["vision_reason"] = vision_reason
                result["auto_vision_intent"] = auto_vision_intent
                result["auto_vision_reason"] = auto_vision_reason
                result["normalized_transcript"] = normalized_transcript
                set_last_debug(result_debug)

            payload = {
                "ok": True,
                "request_id": request_id,
                "transcript": transcript,
                "image_received": image_received,
                "image_size_bytes": len(image_bytes) if image_bytes else 0,
                "client_metadata": metadata,
                **result,
                "elapsed_ms": elapsed_ms(started),
            }
            self.send_json(payload)
        except subprocess.TimeoutExpired as exc:
            self.send_json({"ok": False, "request_id": request_id, "error": f"local STT timeout after {exc.timeout}s"}, status=504)
        except Exception as exc:
            self.send_json({"ok": False, "request_id": request_id, "error": short_text(exc, 300)}, status=500)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass


def parse_metadata(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_readiness() -> dict[str, Any]:
    active_model = chat_engine.active_model() if chat_engine is not None else None
    available = chat_engine.available_models() if chat_engine is not None else set()
    return {
        "chat_ready": chat_engine is not None and active_model in available,
        "asr_loaded": stt_adapter is not None and stt_adapter.ready(),
        "ollama_model": active_model,
        "ollama_model_available": active_model in available if active_model else False,
        "vision_model_available": vision_model in available if vision_model else False,
        "available_ollama_models": sorted(available),
        "stt_ready": stt_adapter.ready() if stt_adapter is not None else False,
    }


def build_health() -> dict[str, Any]:
    readiness = build_readiness()
    stt_health = stt_adapter.health() if stt_adapter is not None else {}
    return {
        "ok": True,
        "service": "jetson_local_ai_server",
        "debug_version": DEBUG_VERSION,
        "local_ai": True,
        "ollama_url": chat_engine.url if chat_engine is not None else None,
        "configured_ollama_model": chat_engine.model if chat_engine is not None else None,
        "fallback_ollama_model": chat_engine.fallback_model if chat_engine is not None else None,
        "no_think": chat_engine.no_think if chat_engine is not None else None,
        "num_ctx": chat_engine.num_ctx if chat_engine is not None else None,
        "num_predict": chat_engine.num_predict if chat_engine is not None else None,
        "memory_turns": chat_engine.memory_turns if chat_engine is not None else None,
        "memory_items": len(chat_engine.memory) if chat_engine is not None else None,
        "vision_enabled": vision_enabled,
        "vision_model": vision_model,
        "force_vision": server_force_vision,
        "max_image_bytes": max_image_bytes,
        "routes": ["/health", "/debug", "/text-chat", "/voice-chat", "/memory/clear", "/focus-check"],
        "stt": stt_health,
        "last_debug": last_debug,
        "debug_log": str(debug_log_path) if debug_log_path is not None else None,
        **readiness,
    }


def build_debug() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "jetson_local_ai_server",
        "debug_version": DEBUG_VERSION,
        "local_ai": True,
        "vision_enabled": vision_enabled,
        "vision_model": vision_model,
        "force_vision": server_force_vision,
        "max_image_bytes": max_image_bytes,
        "last_debug": last_debug,
        "debug_log": str(debug_log_path) if debug_log_path is not None else None,
        **build_readiness(),
    }


def run_self_test() -> int:
    content = json.dumps(
        {
            "reply": "收到，我在 Jetson 本地運作。",
            "control": {
                "persistent_state": "unchanged",
                "screen_mode": "unchanged",
                "emotion": "happy",
                "head_motion": "nod",
                "reason": "self-test",
            },
        },
        ensure_ascii=False,
    )
    reply, control, status, reason = desktop_core.parse_ai_content(content, "測試", used_vision=False)
    if reply != "收到，我在 Jetson 本地運作。" or control["emotion"] != "happy" or status != "json_reply" or reason:
        raise AssertionError(f"bad parser self-test: reply={reply!r} control={control!r} status={status!r} reason={reason!r}")
    malformed = '{ "reply": "我有正常收到，這次直接回答你。", "control": { "persistent_state": "unchanged", "emotion": "curious", "head_motion": "curious_peek" }'
    reply, control, status, _reason = desktop_core.parse_ai_content(malformed, "你有聽到嗎", used_vision=False)
    if reply != "我有正常收到，這次直接回答你。" or control["emotion"] != "curious" or status != "loose_json_reply":
        raise AssertionError(f"bad malformed parser self-test: reply={reply!r} control={control!r} status={status!r}")
    cleaned = clean_whisper_output("[00:00:00.000 --> 00:00:01.000] 你好\nwhisper_print_timings: x")
    if cleaned != "你好":
        raise AssertionError(f"bad whisper output cleanup: {cleaned!r}")
    print("jetson_local_ai_server self-test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jetson-local whisper.cpp + Ollama /voice-chat compatible server.")
    parser.add_argument("--host", default=os.getenv("JETSON_LOCAL_AI_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("JETSON_LOCAL_AI_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--fallback-ollama-model", default=DEFAULT_FALLBACK_OLLAMA_MODEL)
    parser.add_argument("--ollama-timeout", type=float, default=float(os.getenv("JETSON_LOCAL_OLLAMA_TIMEOUT", "90")))
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    parser.add_argument("--vision-timeout", type=float, default=DEFAULT_VISION_TIMEOUT)
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    parser.add_argument("--disable-vision", "--no-vision", dest="disable_vision", action="store_true")
    parser.add_argument("--force-vision", action="store_true")
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    parser.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--keep-alive", default=DEFAULT_KEEP_ALIVE)
    parser.add_argument("--memory-turns", type=int, default=DEFAULT_MEMORY_TURNS)
    parser.add_argument("--no-think", dest="no_think", action="store_true", default=True)
    parser.add_argument("--think", dest="no_think", action="store_false")
    parser.add_argument("--stt-bin", default=resolve_default_stt_bin())
    parser.add_argument("--stt-model", default=resolve_default_stt_model())
    parser.add_argument("--stt-language", default=DEFAULT_STT_LANGUAGE)
    parser.add_argument("--stt-timeout", type=float, default=DEFAULT_STT_TIMEOUT)
    parser.add_argument("--stt-extra-args", default=os.getenv("WHISPER_CPP_EXTRA_ARGS", ""))
    parser.add_argument("--debug-log", default=os.getenv("JETSON_LOCAL_AI_DEBUG_LOG", "jetson_local_ai_debug.jsonl"))
    parser.add_argument("--no-warm-up", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    global chat_engine, stt_adapter, debug_log_path, vision_model, vision_timeout_sec, vision_enabled, server_force_vision, max_image_bytes
    args = build_arg_parser().parse_args()
    if args.self_test:
        return run_self_test()

    debug_log_path = Path(args.debug_log) if str(args.debug_log).strip() else None
    if debug_log_path is not None:
        print(f"Debug log: {debug_log_path}")
    vision_model = args.vision_model
    vision_timeout_sec = args.vision_timeout
    vision_enabled = not args.disable_vision
    server_force_vision = bool(args.force_vision)
    max_image_bytes = max(1, args.max_image_bytes)

    stt_adapter = WhisperCppAdapter(
        binary=args.stt_bin,
        model=args.stt_model,
        language=args.stt_language,
        timeout_sec=args.stt_timeout,
        extra_args=args.stt_extra_args,
    )
    print("Local STT:", json.dumps(stt_adapter.health(), ensure_ascii=False))

    chat_engine = LocalOllamaChatEngine(
        url=args.ollama_url,
        model=args.ollama_model,
        fallback_model=args.fallback_ollama_model,
        no_think=args.no_think,
        memory_turns=args.memory_turns,
        num_ctx=args.num_ctx,
        num_predict=args.num_predict,
        temperature=args.temperature,
        keep_alive=args.keep_alive,
        timeout_sec=args.ollama_timeout,
    )
    print(
        "Local Ollama config:",
        json.dumps(
            {
                "url": args.ollama_url,
                "model": args.ollama_model,
                "fallback_model": args.fallback_ollama_model,
                "active_model": chat_engine.active_model(),
                "num_ctx": args.num_ctx,
                "num_predict": args.num_predict,
                "temperature": args.temperature,
                "keep_alive": args.keep_alive,
                "vision_enabled": vision_enabled,
                "vision_model": vision_model,
                "force_vision": server_force_vision,
                "max_image_bytes": max_image_bytes,
            },
            ensure_ascii=False,
        ),
    )
    if not args.no_warm_up:
        chat_engine.warm_up()

    server = ThreadingHTTPServer((args.host, args.port), LocalAIHandler)
    print(f"Jetson local AI server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJetson local AI server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
