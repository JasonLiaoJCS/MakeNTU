"""
Windows 11 voice controller: microphone -> Qwen3-ASR -> Ollama JSON command -> backend.

Install, Windows PowerShell:
    py -3.12 -m venv .venv
    .venv\\Scripts\\Activate.ps1
    python -m pip install --upgrade pip
    pip install sounddevice numpy qwen-asr pyserial
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

Ollama:
    ollama pull qwen35-fast:latest
    ollama serve

Usage:
    python desk_voice_controller.py --list-mics
    python desk_voice_controller.py --check-deps
    python desk_voice_controller.py --text "幫我開電風扇"
    python desk_voice_controller.py
    python desk_voice_controller.py --device 2
    python desk_voice_controller.py --seconds 4
    python desk_voice_controller.py --backend print
    python desk_voice_controller.py --backend serial
    python desk_voice_controller.py --backend http

Environment overrides:
    OLLAMA_URL=http://localhost:11434/api/chat
    OLLAMA_MODEL=qwen35-fast:latest
    OLLAMA_NO_THINK=1
    QWEN_ASR_MODEL=Qwen/Qwen3-ASR-1.7B
    SERIAL_PORT=COM5
    SERIAL_BAUDRATE=115200
    HTTP_COMMAND_URL=http://127.0.0.1:5000/command
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any


SAMPLE_RATE = 16_000
CHANNELS = 1
DEFAULT_SECONDS = 4.0
DEFAULT_RMS_THRESHOLD = 0.008
DEFAULT_CONFIDENCE_THRESHOLD = 0.58

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen35-fast:latest")
DEFAULT_OLLAMA_NO_THINK = os.getenv("OLLAMA_NO_THINK", "1").strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_ASR_MODEL = os.getenv("QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
DEFAULT_SERIAL_PORT = os.getenv("SERIAL_PORT", "COM5")
DEFAULT_SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))
DEFAULT_HTTP_URL = os.getenv("HTTP_COMMAND_URL", "http://127.0.0.1:5000/command")


ALLOWED_INTENTS = [
    "HOME.FAN.ON",
    "HOME.FAN.OFF",
    "HOME.LIGHT.ON",
    "HOME.LIGHT.OFF",
    "ROBOT.CRUISE",
    "ROBOT.QUIET",
    "ROBOT.STOP",
    "VACUUM.OFF",
    "VACUUM.LOW",
    "VACUUM.HIGH",
    "UNKNOWN",
]

INTENT_DEFAULTS: dict[str, tuple[str, str]] = {
    "HOME.FAN.ON": ("fan", "on"),
    "HOME.FAN.OFF": ("fan", "off"),
    "HOME.LIGHT.ON": ("light", "on"),
    "HOME.LIGHT.OFF": ("light", "off"),
    "ROBOT.CRUISE": ("robot", "cruise"),
    "ROBOT.QUIET": ("robot", "quiet"),
    "ROBOT.STOP": ("robot", "stop"),
    "VACUUM.OFF": ("vacuum", "off"),
    "VACUUM.LOW": ("vacuum", "low"),
    "VACUUM.HIGH": ("vacuum", "high"),
    "UNKNOWN": ("unknown", "unknown"),
}

COMMAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": ALLOWED_INTENTS},
        "target": {"type": "string"},
        "action": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "transcript": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["intent", "target", "action", "confidence", "transcript", "reply"],
}

SYSTEM_PROMPT = f"""
你是桌面裝置語音控制指令解析器。
你只能輸出一個 JSON object，必須符合提供的 JSON Schema，不可以輸出 markdown 或額外文字。

允許 intent:
{", ".join(ALLOWED_INTENTS)}

判斷規則:
1. 使用者要開電風扇、打開風扇、風扇開一下 -> HOME.FAN.ON。
2. 使用者要關電風扇、停止風扇、風扇關掉 -> HOME.FAN.OFF。
3. 使用者要開燈 -> HOME.LIGHT.ON；關燈 -> HOME.LIGHT.OFF。
4. 使用者要桌面助手巡航、開始巡航、自己移動看看 -> ROBOT.CRUISE。
5. 使用者要安靜、降低存在感、不要吵 -> ROBOT.QUIET。
6. 使用者要停下、不要動、先不要動、緊急停止 -> ROBOT.STOP。
7. 吸塵器關閉 -> VACUUM.OFF；低速 -> VACUUM.LOW；高速/強力 -> VACUUM.HIGH。
8. 無法明確判斷、不是控制指令、只是聊天、或設備不在清單內 -> UNKNOWN。
9. reply 欄位一定要回應使用者。若是控制指令，用自然中文簡短確認；若是 UNKNOWN 但使用者在聊天或問問題，也要自然回答，但不能假裝已執行硬體控制。

否定語氣必須特別小心:
- 「不要開風扇」不可以判成 HOME.FAN.ON，應該 UNKNOWN 或 HOME.FAN.OFF，保守用 UNKNOWN。
- 「不要巡航」不可以判成 ROBOT.CRUISE，保守用 ROBOT.STOP 或 UNKNOWN。
- 「先不要動」應該是 ROBOT.STOP。
- 「不是叫你開燈」不可以判成 HOME.LIGHT.ON。

confidence:
- 明確直接控制指令: 0.85 到 0.98。
- 有點模糊但仍可判斷: 0.60 到 0.80。
- 不確定或 UNKNOWN: 0.0 到 0.55。
""".strip()

CHAT_SYSTEM_PROMPT = """
你是桌面助手的簡短中文回覆模組。
你會收到語音轉文字內容。
請用繁體中文回答，1 到 2 句即可。
如果使用者是在測試你有沒有聽到，請明確說你聽到了。
如果使用者是在聊天或問問題，正常回答。
如果使用者問你對某個人或狀況的看法，回答要像人一樣自然、有溫度，聚焦在行為和情境，不要攻擊身分、性傾向、性別、族群或外貌。
如果使用者像是在下硬體控制指令，請只用簡短語氣確認，不要輸出 JSON。
不要說你已經控制硬體，除非文字明確是控制指令。
""".strip()


@dataclasses.dataclass
class Command:
    intent: str
    target: str
    action: str
    confidence: float
    transcript: str
    reply: str

    @property
    def wire_command(self) -> str:
        return self.intent

    def to_json_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def safe_unknown(transcript: str, reason: str) -> Command:
    return Command(
        intent="UNKNOWN",
        target="unknown",
        action="unknown",
        confidence=0.0,
        transcript=transcript,
        reply=f"我不太確定這個指令，所以先不執行。({reason})",
    )


def replace_reply(command: Command, reply: str) -> Command:
    return Command(
        intent=command.intent,
        target=command.target,
        action=command.action,
        confidence=command.confidence,
        transcript=command.transcript,
        reply=reply.strip() or command.reply,
    )


def command_for_intent(intent: str, transcript: str, confidence: float, reply: str) -> Command:
    target, action = INTENT_DEFAULTS[intent]
    return Command(
        intent=intent,
        target=target,
        action=action,
        confidence=confidence,
        transcript=transcript,
        reply=reply,
    )


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return max(0.0, min(1.0, number))


def validate_command(data: Any, transcript: str) -> Command:
    if not isinstance(data, dict):
        return safe_unknown(transcript, "JSON 不是 object")

    intent = str(data.get("intent", "UNKNOWN")).strip().upper()
    if intent not in ALLOWED_INTENTS:
        return safe_unknown(transcript, f"未知 intent: {intent}")

    target, action = INTENT_DEFAULTS[intent]
    confidence = clamp_confidence(data.get("confidence", 0.0))
    reply = str(data.get("reply", "")).strip() or "收到。"
    model_transcript = str(data.get("transcript", transcript)).strip() or transcript

    return Command(
        intent=intent,
        target=target,
        action=action,
        confidence=confidence,
        transcript=model_transcript,
        reply=reply,
    )


def normalize_command_text(text: str) -> str:
    return text.lower().replace(" ", "").replace("，", ",").replace("。", ".")


def rule_based_command(transcript: str, reason: str = "rule-based fallback") -> Command:
    text = normalize_command_text(transcript)
    has_negation = contains_any(text, NEGATION_PATTERNS)

    fan = contains_any(text, ["風扇", "风扇", "電風扇", "电风扇", "fan"])
    light = contains_any(text, ["燈", "灯", "light"])
    vacuum = contains_any(text, ["吸塵", "吸尘", "vacuum"])

    open_word = contains_any(text, ["打開", "打开", "開啟", "开启", "開", "开", "啟動", "启动", "on", "start"])
    close_word = contains_any(text, ["關閉", "关闭", "關掉", "关掉", "關", "关", "off", "停止", "停掉"])
    high_word = contains_any(text, ["高速", "強力", "强力", "大力", "high", "max", "最大"])
    low_word = contains_any(text, ["低速", "小力", "弱", "low", "最小"])

    if contains_any(text, ["不要動", "先不要動", "別動", "别动", "停下", "停止", "緊急停止", "急停", "stop"]):
        return command_for_intent("ROBOT.STOP", transcript, 0.92, "好的，我先停止動作。")

    if fan:
        if close_word:
            return command_for_intent("HOME.FAN.OFF", transcript, 0.9, "好的，幫你關風扇。")
        if open_word and not has_negation:
            return command_for_intent("HOME.FAN.ON", transcript, 0.9, "好的，幫你開風扇。")
        if open_word and has_negation:
            return safe_unknown(transcript, "偵測到否定語氣，已阻擋開風扇指令")

    if light:
        if close_word:
            return command_for_intent("HOME.LIGHT.OFF", transcript, 0.9, "好的，幫你關燈。")
        if open_word and not has_negation:
            return command_for_intent("HOME.LIGHT.ON", transcript, 0.9, "好的，幫你開燈。")
        if open_word and has_negation:
            return safe_unknown(transcript, "偵測到否定語氣，已阻擋開燈指令")

    if contains_any(text, ["不要巡航", "別巡航", "别巡航"]):
        return command_for_intent("ROBOT.STOP", transcript, 0.88, "好的，我不巡航，先停止。")
    if contains_any(text, ["巡航", "cruise"]) and not has_negation:
        return command_for_intent("ROBOT.CRUISE", transcript, 0.86, "好的，開始巡航。")
    if contains_any(text, ["安靜", "安静", "小聲", "小声", "quiet", "不要吵"]):
        return command_for_intent("ROBOT.QUIET", transcript, 0.86, "好的，我會安靜一點。")

    if vacuum:
        if close_word:
            return command_for_intent("VACUUM.OFF", transcript, 0.9, "好的，關閉吸塵。")
        if high_word:
            return command_for_intent("VACUUM.HIGH", transcript, 0.86, "好的，切到強力吸塵。")
        if low_word:
            return command_for_intent("VACUUM.LOW", transcript, 0.86, "好的，切到低速吸塵。")

    return safe_unknown(transcript, f"不是明確控制指令；{reason}")


def fallback_chat_reply(transcript: str) -> str:
    text = normalize_command_text(transcript)
    if contains_any(text, ["聽得到", "听得到", "聽得懂", "听得懂", "有聽到", "有听到"]):
        return "我聽到了，也看得懂你剛剛說的內容。你可以直接對我說要開風扇、關燈或停止動作。"
    if contains_any(text, ["你好", "hello", "hi", "嗨"]):
        return "你好，我在。你可以直接跟我說要控制什麼，或先跟我聊天測試。"
    if contains_any(text, ["同性戀", "同性恋", "gay", "同志"]) and contains_any(text, ["講話", "讲话", "說話", "说话", "吵", "旁邊", "旁边"]):
        return "我會把這件事分開看：他的性傾向不是問題，真正影響你的可能是他一直講話、音量或場合不合適。如果你被打擾，可以平靜地請他小聲一點，或先換個位置，這樣比較不會把事情變成人身評價。"
    if contains_any(text, ["你有什麼看法", "你有什么看法", "怎麼看", "怎么看", "覺得呢", "觉得呢"]):
        return "我會先看具體行為和情境，而不是急著評價某個人。如果那件事讓你不舒服，我們可以一起想一個比較不衝突、也能保護你感受的處理方式。"
    if not transcript.strip():
        return "我沒有聽到清楚的內容，可以再說一次。"
    return "我聽到了。這句比較像聊天或提問，我可以正常回你；如果你想控制設備，也可以直接說要開風扇、關燈或停止動作。"


NEGATION_PATTERNS = [
    "不要",
    "別",
    "先不要",
    "不要再",
    "別再",
    "不是叫你",
    "不用",
    "暫時不要",
]


def contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def apply_safety_language_guard(command: Command) -> Command:
    text = command.transcript.replace(" ", "")
    has_negation = contains_any(text, NEGATION_PATTERNS)

    if contains_any(text, ["不要動", "先不要動", "別動", "停下", "停止", "緊急停止"]):
        return Command(
            intent="ROBOT.STOP",
            target="robot",
            action="stop",
            confidence=max(command.confidence, 0.9),
            transcript=command.transcript,
            reply="好的，我先停止動作。",
        )

    risky_positive_intents = {
        "HOME.FAN.ON",
        "HOME.LIGHT.ON",
        "ROBOT.CRUISE",
        "VACUUM.LOW",
        "VACUUM.HIGH",
    }
    if has_negation and command.intent in risky_positive_intents:
        return safe_unknown(command.transcript, "偵測到否定語氣，已阻擋正向啟動指令")

    if "不要巡航" in text and command.intent == "ROBOT.CRUISE":
        return Command(
            intent="ROBOT.STOP",
            target="robot",
            action="stop",
            confidence=max(command.confidence, 0.85),
            transcript=command.transcript,
            reply="好的，我不巡航，先停止。",
        )

    return command


def extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def strip_thinking_text(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^\s*/?no_think\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


class OllamaCommandParser:
    def __init__(self, url: str, model: str, no_think: bool = DEFAULT_OLLAMA_NO_THINK) -> None:
        self.url = url
        self.model = model
        self.no_think = no_think

    def user_content(self, content: str) -> str:
        if self.no_think:
            return "/no_think\n" + content
        return content

    def warm_up(self) -> None:
        print(f"Ollama warm-up: model={self.model}, url={self.url}, no_think={self.no_think}")
        command = self.parse_text("暖機測試，這不是控制指令。", warmup=True)
        print(f"Ollama warm-up done: {command.intent}")

    def generate_reply(self, transcript: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": self.user_content(transcript)},
            ],
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.2,
                "num_ctx": 2048,
                "num_predict": 128,
            },
        }
        response = post_json(self.url, payload, timeout_sec=120)
        content = response.get("message", {}).get("content", "")
        if not isinstance(content, str):
            return ""
        return strip_thinking_text(content)

    def attach_chat_reply_if_needed(self, command: Command, warmup: bool = False) -> Command:
        if warmup:
            return command
        if command.intent != "UNKNOWN":
            return command
        try:
            reply = self.generate_reply(command.transcript)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"WARN: Ollama chat reply failed: {exc}")
            reply = fallback_chat_reply(command.transcript)
        return replace_reply(command, reply or fallback_chat_reply(command.transcript))

    def parse_text(self, transcript: str, warmup: bool = False) -> Command:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self.user_content(
                        "請解析以下中文語音轉文字內容，輸出符合 schema 的 JSON。\n"
                        f"transcript: {transcript}"
                    ),
                },
            ],
            "stream": False,
            "format": COMMAND_SCHEMA,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.0,
                "num_ctx": 2048,
                "num_predict": 256,
            },
        }

        try:
            response = post_json(self.url, payload, timeout_sec=120)
            content = response.get("message", {}).get("content", "")
            if not isinstance(content, str):
                return safe_unknown(transcript, "Ollama response content 不是字串")
            content = strip_thinking_text(content)
            data = extract_json_object(content)
            if data is None:
                if not warmup:
                    print("WARN: Ollama did not return valid JSON:")
                    print(content)
                command = apply_safety_language_guard(rule_based_command(transcript, "Ollama JSON 解析失敗"))
                return self.attach_chat_reply_if_needed(command, warmup=warmup)
            command = validate_command(data, transcript)
            command = apply_safety_language_guard(command)
            return self.attach_chat_reply_if_needed(command, warmup=warmup)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if not warmup:
                print(f"WARN: Ollama request failed: {exc}")
            return safe_unknown(transcript, "Ollama 連線或解析失敗")


class QwenASRAdapter:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.model: Any | None = None
        self.device = "cpu"
        self.torch_dtype_name = "float32"

    def load(self) -> None:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("請先安裝 torch。Windows CUDA 版建議依 PyTorch 官網指令安裝。") from exc

        has_cuda = bool(torch.cuda.is_available())
        self.device = "cuda:0" if has_cuda else "cpu"
        torch_dtype = torch.bfloat16 if has_cuda else torch.float32
        self.torch_dtype_name = "bfloat16" if has_cuda else "float32"

        print(f"Loading Qwen ASR: {self.model_id}")
        print(f"torch.cuda.is_available() = {has_cuda}")
        print(f"ASR device={self.device}, dtype={self.torch_dtype_name}")

        try:
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:
            raise RuntimeError("找不到 qwen_asr。請先執行: pip install qwen-asr") from exc

        load_errors: list[str] = []
        for kwargs in (
            {"dtype": torch_dtype, "device_map": self.device},
            {"torch_dtype": torch_dtype, "device_map": self.device},
            {"device": self.device, "dtype": torch_dtype},
            {},
        ):
            try:
                self.model = Qwen3ASRModel.from_pretrained(self.model_id, **kwargs)
                return
            except TypeError as exc:
                load_errors.append(f"kwargs={kwargs}: {exc}")
            except Exception:
                raise

        detail = "\n".join(load_errors)
        raise RuntimeError(f"Qwen3ASRModel.from_pretrained 參數不相容:\n{detail}")

    def transcribe(self, wav_path: str | Path) -> str:
        if self.model is None:
            self.load()
        assert self.model is not None

        path = str(wav_path)
        result: Any
        errors: list[str] = []
        for call in (
            lambda: self.model.transcribe(audio=path),
            lambda: self.model.transcribe(path),
            lambda: self.model(path),
        ):
            try:
                result = call()
                return normalize_asr_result(result)
            except TypeError as exc:
                errors.append(str(exc))
                continue

        raise RuntimeError("Qwen ASR transcribe 呼叫失敗: " + " | ".join(errors))


def normalize_asr_result(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for key in ("text", "transcript", "sentence"):
            if key in result:
                return str(result[key]).strip()
    if isinstance(result, list) or isinstance(result, tuple):
        if not result:
            return ""
        texts = [normalize_asr_result(item) for item in result]
        return " ".join(text for text in texts if text).strip()
    for attr in ("text", "transcript", "sentence"):
        if hasattr(result, attr):
            return str(getattr(result, attr)).strip()
    return str(result).strip()


def post_json(url: str, payload: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("HTTP response JSON is not an object")
    return parsed


def list_microphones() -> None:
    try:
        import sounddevice as sd
    except ImportError:
        print("找不到 sounddevice。請先執行: pip install sounddevice")
        return

    print("Microphone devices:")
    devices = sd.query_devices()
    default_input = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None
    for index, device in enumerate(devices):
        max_inputs = int(device.get("max_input_channels", 0))
        if max_inputs <= 0:
            continue
        marker = "  <-- default" if index == default_input else ""
        print(
            f"[{index:2d}] inputs={max_inputs} "
            f"default_sr={device.get('default_samplerate')} "
            f"name={device.get('name')}{marker}"
        )


def check_dependencies() -> int:
    print("Dependency check")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {sys.platform}")

    checks = [
        ("numpy", "numpy"),
        ("sounddevice", "sounddevice"),
        ("serial", "pyserial"),
        ("torch", "torch"),
        ("qwen_asr", "qwen-asr"),
    ]
    missing: list[str] = []
    for import_name, package_name in checks:
        try:
            module = __import__(import_name)
            version = getattr(module, "__version__", "installed")
            print(f"OK      {package_name}: {version}")
        except ImportError:
            print(f"MISSING {package_name}")
            missing.append(package_name)

    if "torch" not in missing:
        try:
            import torch

            print(f"torch.cuda.is_available(): {torch.cuda.is_available()}")
            if torch.cuda.is_available():
                print(f"torch cuda device: {torch.cuda.get_device_name(0)}")
        except Exception as exc:
            print(f"WARN    torch import worked but CUDA check failed: {exc}")

    if missing:
        print()
        print("Install missing packages before voice mode.")
        print("For Ollama text-only mode, torch/qwen-asr/sounddevice are not required.")
        print("Text-only test:")
        print('    python desk_voice_controller.py --text "幫我開電風扇"')
        return 1

    return 0


def record_audio(seconds: float, device: int | None) -> Any:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("請先安裝 sounddevice 與 numpy: pip install sounddevice numpy") from exc

    frames = int(seconds * SAMPLE_RATE)
    print(f"Recording {seconds:.1f}s at {SAMPLE_RATE} Hz mono...")
    audio = sd.rec(
        frames,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return np.asarray(audio, dtype="float32").reshape(-1)


def rms_level(audio: Any) -> float:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("請先安裝 numpy: pip install numpy") from exc
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


def write_temp_wav(audio: Any) -> Path:
    import numpy as np

    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype("<i2")
    handle = tempfile.NamedTemporaryFile(prefix="desk_voice_", suffix=".wav", delete=False)
    path = Path(handle.name)
    handle.close()

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16.tobytes())
    return path


class ControlBackend:
    def send(self, command: Command) -> None:
        raise NotImplementedError


class PrintBackend(ControlBackend):
    def send(self, command: Command) -> None:
        payload = {
            "wire_command": command.wire_command,
            "command": command.to_json_dict(),
        }
        print("PRINT BACKEND payload:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))


class SerialBackend(ControlBackend):
    def __init__(self, port: str, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate

    def send(self, command: Command) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("請先安裝 pyserial: pip install pyserial") from exc

        line = command.wire_command + "\n"
        print(f"SERIAL TX {self.port}@{self.baudrate}: {line.strip()}")
        with serial.Serial(self.port, self.baudrate, timeout=1.0, write_timeout=1.0) as ser:
            ser.write(line.encode("utf-8"))
            ser.flush()


class HttpBackend(ControlBackend):
    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, command: Command) -> None:
        payload = {
            "wire_command": command.wire_command,
            "command": command.to_json_dict(),
        }
        print(f"HTTP POST {self.url}: {command.wire_command}")
        post_json(self.url, payload, timeout_sec=10)


def make_backend(args: argparse.Namespace) -> ControlBackend:
    if args.backend == "print":
        return PrintBackend()
    if args.backend == "serial":
        return SerialBackend(args.serial_port, args.serial_baudrate)
    if args.backend == "http":
        return HttpBackend(args.http_url)
    raise ValueError(f"Unsupported backend: {args.backend}")


def maybe_execute(command: Command, backend: ControlBackend, min_confidence: float) -> bool:
    print("Command JSON:")
    print(json.dumps(command.to_json_dict(), ensure_ascii=False, indent=2))
    print(f"Reply: {command.reply}")

    if command.intent == "UNKNOWN":
        print("SKIP: intent is UNKNOWN, hardware control disabled.")
        return False
    if command.confidence < min_confidence:
        print(f"SKIP: confidence {command.confidence:.2f} < {min_confidence:.2f}.")
        return False

    try:
        backend.send(command)
        print("EXECUTED.")
        return True
    except Exception as exc:
        print(f"ERROR: backend failed: {exc}")
        return False


def run_text_mode(args: argparse.Namespace) -> int:
    parser = OllamaCommandParser(args.ollama_url, args.ollama_model, no_think=args.no_think)
    parser.warm_up()
    backend = make_backend(args)
    command = parser.parse_text(args.text)
    maybe_execute(command, backend, args.min_confidence)
    return 0


def run_voice_mode(args: argparse.Namespace) -> int:
    parser = OllamaCommandParser(args.ollama_url, args.ollama_model, no_think=args.no_think)
    parser.warm_up()
    asr = QwenASRAdapter(args.asr_model)
    asr.load()
    backend = make_backend(args)

    print()
    print("Voice control ready.")
    print("Press Enter to record one chunk. Type q then Enter to quit.")
    print(f"Backend={args.backend}, seconds={args.seconds}, RMS threshold={args.rms_threshold}")

    while True:
        user = input("\nPress Enter to record> ").strip().lower()
        if user in {"q", "quit", "exit"}:
            return 0

        try:
            audio = record_audio(args.seconds, args.device)
            rms = rms_level(audio)
            print(f"RMS={rms:.5f}")
            if rms < args.rms_threshold:
                print("SKIP: audio RMS too low, probably silence/background noise.")
                continue

            wav_path = write_temp_wav(audio)
            try:
                transcript = asr.transcribe(wav_path)
            finally:
                try:
                    wav_path.unlink(missing_ok=True)
                except OSError:
                    pass

            transcript = transcript.strip()
            print(f"ASR transcript: {transcript!r}")
            if not transcript:
                print("SKIP: empty ASR transcript.")
                continue

            command = parser.parse_text(transcript)
            maybe_execute(command, backend, args.min_confidence)
        except KeyboardInterrupt:
            print()
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}")
            print("This chunk was skipped; press Enter to try again.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Desk voice controller with Qwen3-ASR and Ollama JSON intent parsing.")
    parser.add_argument("--list-mics", action="store_true", help="List microphone input devices and exit.")
    parser.add_argument("--check-deps", action="store_true", help="Check Python package dependencies and CUDA availability.")
    parser.add_argument("--text", help="Parse this text with Ollama only; do not run ASR.")
    parser.add_argument("--device", type=int, default=None, help="sounddevice input device index.")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="Seconds per push-to-record chunk.")
    parser.add_argument("--rms-threshold", type=float, default=DEFAULT_RMS_THRESHOLD, help="Skip audio below this RMS level.")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD, help="Do not execute below this confidence.")
    parser.add_argument("--backend", choices=["print", "serial", "http"], default="print")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT)
    parser.add_argument("--serial-baudrate", type=int, default=DEFAULT_SERIAL_BAUDRATE)
    parser.add_argument("--http-url", default=DEFAULT_HTTP_URL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.set_defaults(no_think=DEFAULT_OLLAMA_NO_THINK)
    parser.add_argument("--no-think", dest="no_think", action="store_true", help="Prefix Ollama prompts with /no_think. Default is on.")
    parser.add_argument("--think", dest="no_think", action="store_false", help="Allow thinking mode for models that support it.")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.list_mics:
        list_microphones()
        return 0

    if args.check_deps:
        return check_dependencies()

    if args.text:
        return run_text_mode(args)

    return run_voice_mode(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(0)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        print()
        print("Useful checks:")
        print("    python desk_voice_controller.py --check-deps")
        print('    python desk_voice_controller.py --text "幫我開電風扇"')
        raise SystemExit(1)
