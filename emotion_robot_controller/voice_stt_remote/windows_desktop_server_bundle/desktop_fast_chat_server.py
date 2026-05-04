"""
Fast desktop voice chat server.

Purpose:
    Receive WAV from Jetson -> Qwen3-ASR -> one fast Ollama plain chat call + local emotion.

Windows PowerShell:
    cd C:\\Users\\User\\Desktop\\windows_desktop_server_bundle
    .\\.venv\\Scripts\\Activate.ps1
    ollama pull qwen35-fast:latest
    python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think

Jetson test:
    curl http://100.108.141.26:8766/health
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from desk_voice_controller import (
    DEFAULT_ASR_MODEL,
    DEFAULT_OLLAMA_NO_THINK,
    DEFAULT_OLLAMA_URL,
    QwenASRAdapter,
    strip_thinking_text,
)


DEFAULT_FAST_MODEL = "qwen35-fast:latest"
DEBUG_VERSION = 6

EMOTIONS = [
    "neutral",
    "happy",
    "excited",
    "sad",
    "tired",
    "angry",
    "surprised",
    "curious",
    "confused",
    "thinking",
    "concerned",
    "anxious",
    "stressed",
    "frustrated",
    "lonely",
    "calm",
]

SYSTEM_PROMPT = """
你是自然聊天助手。輸入是語音轉文字，可能有錯字、口頭禪或重複詞。

任務：
- 只回覆使用者當下這句話，像平常對話一樣。
- 不要輸出 JSON、markdown、標題、分析欄位或條列。
- 不要說「我先針對這句回」、「你可以再補一句」、「請提供更多資訊」。
- 如果使用者問問題，直接回答；如果資訊不足，可以自然地猜測或給最可能的方向。
- 如果使用者在抱怨你，就正常承認並調整，不要防衛。
- 使用使用者同一種語言或口吻回覆。
- 回覆控制在 1 到 3 句。
""".strip()


app = Flask(__name__)
asr_adapter: QwenASRAdapter | None = None
chat_engine: "FastChatEmotionEngine | None" = None
last_debug: dict[str, Any] = {}
debug_log_path: Path | None = None


def set_last_debug(data: dict[str, Any]) -> None:
    global last_debug
    last_debug = data
    write_debug_log(data)


def write_debug_log(data: dict[str, Any]) -> None:
    if debug_log_path is None:
        return
    try:
        with debug_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"WARN: failed to write debug log {debug_log_path}: {exc}")


def fallback_result(transcript: str, reason: str) -> dict[str, Any]:
    text = transcript.strip()
    emotion = analyze_emotion_local(transcript)
    if any(key in text for key in ["聽得到", "听得到", "聽得懂", "听得懂"]):
        reply = "我聽到了，而且有成功把你的聲音轉成文字。你可以直接問我問題，或跟我說你現在的狀態。"
    elif text:
        reply = local_reply(transcript)
    else:
        reply = "我沒有聽到清楚的內容，可以再說一次。"
    return {
        "reply": reply,
        "fallback_reason": reason,
        "emotion": {
            **emotion,
            "summary": f"{emotion['summary']} ({reason})",
        },
    }


def norm(text: str) -> str:
    return text.lower().replace(" ", "").replace("，", ",").replace("。", ".")


def has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def analyze_emotion_local(transcript: str) -> dict[str, Any]:
    text = norm(transcript)
    primary = "neutral"
    intensity = 0.25
    valence = 0.0
    arousal = 0.25
    support_needed = False
    summary = "語氣接近中性，沒有明顯強烈情緒。"

    if has_any(text, ["操你媽", "操你妈", "幹你娘", "干你娘", "媽的", "妈的", "靠北", "靠邀", "fuck", "shit"]):
        primary = "angry"
        intensity = 0.9
        valence = -0.85
        arousal = 0.9
        summary = "使用者使用強烈髒話，情緒明顯偏憤怒或強烈挫折。"
    elif has_any(text, ["生氣", "生气", "很氣", "很气", "氣死", "气死", "火大", "憤怒", "愤怒"]):
        primary = "angry"
        intensity = 0.82
        valence = -0.75
        arousal = 0.82
        summary = "使用者明確表達生氣或火大，情緒偏高強度負向。"
    elif has_any(text, ["太慢", "很慢", "慢", "爛", "烂", "鳥", "鸟", "蠢", "笨", "智障", "不聰明", "不聪明", "聰明一點", "聪明一点", "不爽", "煩", "烦", "敷衍", "罐頭", "罐头", "迂迴", "迂回", "不夠親近", "不够亲近", "不像人", "像客服", "太官方"]):
        primary = "frustrated"
        intensity = 0.75
        valence = -0.55
        arousal = 0.7
        summary = "使用者明顯對速度或品質不滿，帶有挫折和急迫感。"
    elif has_any(text, ["累", "撐不下", "撑不下", "疲", "睏", "困"]):
        primary = "tired"
        intensity = 0.68
        valence = -0.45
        arousal = 0.25
        support_needed = True
        summary = "使用者可能疲憊或低能量，需要溫和支持。"
    elif has_any(text, ["擔心", "担心", "焦慮", "焦虑", "怕", "緊張", "紧张"]):
        primary = "anxious"
        intensity = 0.68
        valence = -0.45
        arousal = 0.7
        support_needed = True
        summary = "使用者可能有焦慮或擔憂，喚醒程度偏高。"
    elif has_any(text, ["開心", "开心", "太好了", "讚", "赞", "棒"]):
        primary = "happy"
        intensity = 0.7
        valence = 0.65
        arousal = 0.55
        summary = "使用者情緒偏正向，可能感到開心或滿意。"
    elif has_any(text, ["為什麼", "为什么", "怎麼", "怎么", "看法", "覺得", "觉得"]):
        primary = "curious"
        intensity = 0.45
        valence = 0.05
        arousal = 0.4
        summary = "使用者在詢問或評估狀況，帶有好奇或思考。"

    return {
        "primary": primary,
        "intensity": intensity,
        "valence": valence,
        "arousal": arousal,
        "summary": summary,
        "support_needed": support_needed,
    }


def local_reply(transcript: str) -> str:
    if transcript.strip():
        return "Ollama 這次沒有產生文字回覆，我先不硬編。下面的 Warning 會顯示原因。"
    return "我沒有聽到清楚的內容，你可以再說一次。"


def short_text(text: str, limit: int = 80) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def short_error(exc: BaseException, limit: int = 180) -> str:
    return short_text(str(exc) or exc.__class__.__name__, limit)


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def post_json_with_debug(url: str, payload: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_obj = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {short_text(raw, 500)}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"HTTP response is not JSON from {url}: {short_text(raw, 500)}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"HTTP response JSON is not an object from {url}: {short_text(raw, 500)}")
    return parsed


def ollama_generate_url(chat_url: str) -> str:
    if chat_url.endswith("/api/chat"):
        return chat_url[: -len("/api/chat")] + "/api/generate"
    if chat_url.endswith("/api/generate"):
        return chat_url
    return chat_url.rstrip("/") + "/api/generate"


def extract_ollama_text(response: dict[str, Any]) -> str:
    message = response.get("message")
    if isinstance(message, dict):
        for key in ("content", "response", "text"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
    for key in ("response", "content", "text", "output"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def summarize_ollama_response(response: dict[str, Any]) -> dict[str, Any]:
    message = response.get("message")
    summary: dict[str, Any] = {
        "ollama_response_keys": list(response.keys()),
        "ollama_done": response.get("done"),
    }
    if isinstance(message, dict):
        summary["ollama_message_keys"] = list(message.keys())
        summary["ollama_message_role"] = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            summary["ollama_message_content_chars"] = len(content)
        thinking = message.get("thinking") or message.get("reasoning_content")
        if isinstance(thinking, str):
            summary["ollama_message_thinking_chars"] = len(thinking)
    top_response = response.get("response")
    if isinstance(top_response, str):
        summary["ollama_response_chars"] = len(top_response)
    return summary


class FastChatEmotionEngine:
    def __init__(self, url: str, model: str, no_think: bool) -> None:
        self.url = url
        self.model = model
        self.no_think = no_think

    def user_content(self, transcript: str, use_no_think: bool) -> str:
        content = f"""使用者原話：{transcript}

請直接自然回覆上面的原話。不要輸出 JSON。"""
        if use_no_think:
            content = "/no_think\n" + content
        return content

    def build_payload(self, transcript: str, use_no_think: bool) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.user_content(transcript, use_no_think)},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.65,
                "num_ctx": 4096,
                "num_predict": 220,
            },
        }

    def build_generate_payload(self, transcript: str, use_no_think: bool) -> dict[str, Any]:
        prompt = self.user_content(transcript, use_no_think)
        return {
            "model": self.model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.65,
                "num_ctx": 4096,
                "num_predict": 220,
            },
        }

    def warm_up(self) -> None:
        print(f"Ollama warm-up: model={self.model}, no_think={self.no_think}")
        result = self.analyze("你好，聽得到我說話嗎？")
        print("Ollama warm-up done:", result["emotion"]["primary"])

    def analyze(self, transcript: str, request_id: str = "") -> dict[str, Any]:
        reply_started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex[:8]
        debug: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "ollama_url": self.url,
            "ollama_model": self.model,
            "no_think": self.no_think,
            "think": False,
            "transcript_chars": len(transcript),
            "transcript_preview": short_text(transcript, 120),
            "stage": "ollama_request",
        }
        payload = self.build_payload(transcript, use_no_think=self.no_think)
        try:
            response = post_json_with_debug(self.url, payload, timeout_sec=120)
            debug["stage"] = "ollama_response"
            if "error" in response:
                reason = f"Ollama error: {short_text(str(response.get('error', 'unknown error')))}"
                debug.update({"ok": False, "fallback_reason": reason})
                result = fallback_result(transcript, reason)
                result["debug"] = debug
                result["request_id"] = request_id
                set_last_debug(debug)
                return result
            debug.update(summarize_ollama_response(response))
            content = extract_ollama_text(response)
            if not content.strip() and self.no_think:
                debug["retry_reason"] = "empty content with /no_think; retrying without /no_think"
                retry_payload = self.build_payload(transcript, use_no_think=False)
                retry_response = post_json_with_debug(self.url, retry_payload, timeout_sec=120)
                debug["retried_without_no_think"] = True
                debug["retry_done"] = retry_response.get("done")
                if "error" in retry_response:
                    debug["retry_error"] = str(retry_response.get("error", "unknown error"))
                else:
                    response = retry_response
                    debug.update({f"retry_{key}": value for key, value in summarize_ollama_response(response).items()})
                    content = extract_ollama_text(response)
            if not content.strip():
                generate_url = ollama_generate_url(self.url)
                debug["generate_retry_reason"] = "chat returned empty content; trying /api/generate"
                debug["generate_url"] = generate_url
                generate_payload = self.build_generate_payload(transcript, use_no_think=False)
                generate_response = post_json_with_debug(generate_url, generate_payload, timeout_sec=120)
                debug["generate_done"] = generate_response.get("done")
                if "error" in generate_response:
                    debug["generate_error"] = str(generate_response.get("error", "unknown error"))
                else:
                    response = generate_response
                    debug.update({f"generate_{key}": value for key, value in summarize_ollama_response(response).items()})
                    content = extract_ollama_text(response)
            debug.update(
                {
                    "ollama_done": response.get("done"),
                    "ollama_content_chars": len(content),
                    "ollama_content_preview": short_text(strip_thinking_text(content), 500),
                }
            )
            parsed = extract_json_object(content)
            fallback_reason = ""
            if parsed is not None:
                debug["parse_status"] = "json_reply"
                reply = str(parsed.get("reply", "")).strip()
                if not reply:
                    reply = local_reply(transcript)
                    fallback_reason = "Ollama JSON missing reply; using local reply"
                emotion = analyze_emotion_local(transcript)
            else:
                reply = strip_reply(content)
                if not reply:
                    reply = local_reply(transcript)
                    fallback_reason = "Ollama returned empty or unparsable content; using local reply/emotion"
                    debug["parse_status"] = "empty_or_unparsable"
                else:
                    debug["parse_status"] = "plain_reply"
                emotion = analyze_emotion_local(transcript)
            result = {
                "request_id": request_id,
                "reply": reply,
                "emotion": emotion,
                "timing": {"llm_ms": elapsed_ms(reply_started)},
                "debug": debug,
            }
            if fallback_reason:
                result["fallback_reason"] = fallback_reason
                debug["fallback_reason"] = fallback_reason
            debug.update({"ok": True, "reply_chars": len(reply), "emotion_primary": emotion.get("primary")})
            set_last_debug(debug)
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            print(f"WARN: Ollama chat/emotion failed: {exc}")
            reason = f"Ollama request failed: {short_error(exc)}"
            debug.update({"ok": False, "stage": "ollama_exception", "fallback_reason": reason, "exception_type": exc.__class__.__name__})
            result = fallback_result(transcript, reason)
            result["debug"] = debug
            result["request_id"] = request_id
            set_last_debug(debug)
            return result


def strip_reply(content: str) -> str:
    text = strip_thinking_text(content)
    text = re.sub(r"^```(?:text)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text.strip())
    text = text.strip().strip('"')
    text = re.sub(r"^(reply|回答|回覆)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = strip_thinking_text(text).strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


def normalize_emotion(raw: Any, transcript: str) -> dict[str, Any]:
    fallback = analyze_emotion_local(transcript)
    if not isinstance(raw, dict):
        return fallback

    primary = str(raw.get("primary", fallback["primary"])).strip().lower()
    if primary not in EMOTIONS:
        primary = fallback["primary"]

    return {
        "primary": primary,
        "intensity": clamp_float(raw.get("intensity"), fallback["intensity"], 0.0, 1.0),
        "valence": clamp_float(raw.get("valence"), fallback["valence"], -1.0, 1.0),
        "arousal": clamp_float(raw.get("arousal"), fallback["arousal"], 0.0, 1.0),
        "summary": str(raw.get("summary", fallback["summary"])).strip() or fallback["summary"],
        "support_needed": raw.get("support_needed") if isinstance(raw.get("support_needed"), bool) else fallback["support_needed"],
    }


def clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


@app.get("/health")
def health() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "desktop_fast_chat_server",
            "debug_version": DEBUG_VERSION,
            "asr_loaded": asr_adapter is not None and asr_adapter.model is not None,
            "chat_ready": chat_engine is not None,
            "ollama_url": chat_engine.url if chat_engine is not None else None,
            "ollama_model": chat_engine.model if chat_engine is not None else None,
            "no_think": chat_engine.no_think if chat_engine is not None else None,
            "routes": ["/health", "/debug", "/text-chat", "/voice-chat"],
            "last_debug": last_debug,
            "debug_log": str(debug_log_path) if debug_log_path is not None else None,
        }
    )


@app.get("/debug")
def debug_status() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "desktop_fast_chat_server",
            "debug_version": DEBUG_VERSION,
            "asr_loaded": asr_adapter is not None and asr_adapter.model is not None,
            "chat_ready": chat_engine is not None,
            "last_debug": last_debug,
            "debug_log": str(debug_log_path) if debug_log_path is not None else None,
        }
    )


@app.post("/text-chat")
def text_chat() -> Any:
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:8]
    data = request.get_json(silent=True) or {}
    transcript = str(data.get("text", "")).strip()
    if chat_engine is None:
        return jsonify({"ok": False, "request_id": request_id, "error": "server not ready"}), 503
    result = chat_engine.analyze(transcript, request_id=request_id)
    timing = result.setdefault("timing", {})
    timing["total_ms"] = elapsed_ms(started)
    return jsonify({"ok": True, "transcript": transcript, **result, "elapsed_ms": elapsed_ms(started)})


@app.post("/voice-chat")
def voice_chat() -> Any:
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:8]
    if asr_adapter is None or asr_adapter.model is None or chat_engine is None:
        return jsonify({"ok": False, "request_id": request_id, "error": "server not ready"}), 503
    upload = request.files.get("audio")
    if upload is None:
        return jsonify({"ok": False, "request_id": request_id, "error": "missing audio"}), 400

    temp_path = save_upload_to_temp_wav(upload)
    try:
        asr_started = time.monotonic()
        transcript = asr_adapter.transcribe(temp_path).strip()
        asr_ms = elapsed_ms(asr_started)
        result = chat_engine.analyze(transcript, request_id=request_id)
        timing = result.setdefault("timing", {})
        timing["asr_ms"] = asr_ms
        timing["total_ms"] = elapsed_ms(started)
        return jsonify({"ok": True, "transcript": transcript, **result, "elapsed_ms": elapsed_ms(started)})
    except Exception as exc:
        debug = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "stage": "voice_chat_exception",
            "exception_type": exc.__class__.__name__,
            "error": short_error(exc, 500),
        }
        set_last_debug(debug)
        return jsonify({"ok": False, "request_id": request_id, "error": str(exc), "debug": debug, "elapsed_ms": elapsed_ms(started)}), 500
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def save_upload_to_temp_wav(upload: Any) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="jetson_fast_chat_", suffix=".wav", delete=False)
    temp_path = Path(handle.name)
    handle.close()
    upload.save(temp_path)
    return temp_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fast desktop ASR + chat/emotion server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_FAST_MODEL)
    parser.set_defaults(no_think=DEFAULT_OLLAMA_NO_THINK)
    parser.add_argument("--no-think", dest="no_think", action="store_true")
    parser.add_argument("--think", dest="no_think", action="store_false")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL)
    parser.add_argument("--skip-asr-load", action="store_true")
    parser.add_argument("--debug-log", default="fast_chat_debug.jsonl", help="JSONL file for request debug records. Use empty string to disable.")
    return parser


def main() -> int:
    global asr_adapter, chat_engine, debug_log_path
    args = build_arg_parser().parse_args()
    debug_log_path = Path(args.debug_log) if args.debug_log.strip() else None
    if debug_log_path is not None:
        print(f"Debug log: {debug_log_path}")

    chat_engine = FastChatEmotionEngine(args.ollama_url, args.ollama_model, args.no_think)
    chat_engine.warm_up()

    if not args.skip_asr_load:
        asr_adapter = QwenASRAdapter(args.asr_model)
        asr_adapter.load()
    else:
        print("ASR load skipped; /text-chat works, /voice-chat will not.")

    print(f"Fast chat server listening on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
