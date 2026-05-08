"""
Fast desktop voice chat server.

Purpose:
    Receive WAV plus optional camera JPEG from Jetson -> Qwen3-ASR
    -> route to text chat or vision chat based on transcript keywords.

Windows PowerShell:
    cd C:\\Users\\User\\Desktop\\windows_desktop_server_bundle
    .\\.venv\\Scripts\\Activate.ps1
    ollama pull qwen35-fast:latest
    python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think

Jetson test:
    curl http://100.108.141.26:8766/health
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

try:
    from flask import Flask, jsonify, request
    FLASK_IMPORT_ERROR: Exception | None = None
except ImportError as exc:
    FLASK_IMPORT_ERROR = exc

    class Flask:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def route(self, *_args: Any, **_kwargs: Any) -> Any:
            def decorator(func: Any) -> Any:
                return func

            return decorator

        get = route
        post = route

        def run(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Flask is required to run the desktop server")

    def jsonify(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return args[0] if args else kwargs

    class _MissingRequest:
        files: dict[str, Any] = {}
        form: dict[str, Any] = {}

        def get_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {}

    request = _MissingRequest()

from desk_voice_controller import (
    DEFAULT_ASR_MODEL,
    DEFAULT_OLLAMA_NO_THINK,
    DEFAULT_OLLAMA_URL,
    QwenASRAdapter,
    strip_thinking_text,
)


DEFAULT_FAST_MODEL = "qwen35-fast:latest"
DEFAULT_VISION_MODEL = "qwen35-fast:latest"
VISION_FALLBACK_MODELS: tuple[str, ...] = ()
DEFAULT_MAX_IMAGE_BYTES = 2_000_000
DEBUG_VERSION = 13

CONTROL_PERSISTENT_STATES = {"normal", "sleep", "unchanged"}
CONTROL_SCREEN_MODES = {"unchanged", "normal", "sleep", "music", "focus", "thinking"}
CONTROL_EMOTIONS = {"neutral", "concerned", "angry", "sad", "happy", "curious", "excited", "confused", "sleepy"}
CONTROL_HEAD_MOTIONS = {"none", "nod", "double_nod", "look_around", "shake", "gentle_nod", "sleepy_drop"}
FOCUS_STATES = {"focused", "away", "phone", "sleeping", "distracted", "uncertain", "error"}

EMOTIONS = sorted(CONTROL_EMOTIONS)

EMOTION_TO_HEAD_MOTION = {
    "neutral": "none",
    "concerned": "gentle_nod",
    "angry": "shake",
    "sad": "gentle_nod",
    "happy": "nod",
    "curious": "look_around",
    "excited": "double_nod",
    "confused": "shake",
    "sleepy": "sleepy_drop",
}

CONTROL_EMOTION_ALIASES = {
    "calm": "neutral",
    "normal": "neutral",
    "中性": "neutral",
    "joy": "happy",
    "joyful": "happy",
    "positive": "happy",
    "開心": "happy",
    "开心": "happy",
    "interested": "curious",
    "thinking": "curious",
    "questioning": "curious",
    "好奇": "curious",
    "surprised": "excited",
    "surprise": "excited",
    "energetic": "excited",
    "amazed": "excited",
    "興奮": "excited",
    "兴奋": "excited",
    "unsure": "confused",
    "uncertain": "confused",
    "puzzled": "confused",
    "confusing": "confused",
    "困惑": "confused",
    "angry": "angry",
    "mad": "angry",
    "furious": "angry",
    "生氣": "angry",
    "生气": "angry",
    "火大": "angry",
    "憤怒": "angry",
    "愤怒": "angry",
    "sad": "sad",
    "down": "sad",
    "depressed": "sad",
    "難過": "sad",
    "难过": "sad",
    "沮喪": "sad",
    "沮丧": "sad",
    "anxious": "concerned",
    "worried": "concerned",
    "frustrated": "concerned",
    "upset": "concerned",
    "急": "concerned",
    "急躁": "concerned",
    "擔心": "concerned",
    "担心": "concerned",
    "焦慮": "concerned",
    "焦虑": "concerned",
    "tired": "sleepy",
    "drowsy": "sleepy",
    "sleep": "sleepy",
    "asleep": "sleepy",
    "睏": "sleepy",
    "困": "sleepy",
    "疲累": "sleepy",
}

SLEEP_INTENT_KEYWORDS = (
    "去睡覺",
    "去睡觉",
    "睡覺吧",
    "睡觉吧",
    "睡一下",
    "想睡",
    "先睡",
    "休息一下",
    "休眠",
    "休息模式",
    "晚安",
    "進入睡眠模式",
    "进入睡眠模式",
    "安靜一下",
    "安静一下",
    "安靜模式",
    "安静模式",
    "不要吵我",
    "先不要聽",
    "先不要听",
    "sleep",
    "go to sleep",
    "standby",
    "quiet mode",
)

WAKE_INTENT_KEYWORDS = (
    "起床",
    "醒來",
    "醒来",
    "回來",
    "回来",
    "回來了",
    "回来了",
    "回來工作",
    "回来工作",
    "繼續工作",
    "继续工作",
    "開始工作",
    "开始工作",
    "回到正常",
    "正常模式",
    "一般模式",
    "不要睡了",
    "回來陪我",
    "回来陪我",
    "wake up",
    "come back",
    "normal",
    "back to work",
    "don't sleep",
    "do not sleep",
)

SYSTEM_PROMPT = """
你是桌上寵物機器人的自然聊天大腦。輸入是語音轉文字，可能有錯字、台式口語、半句話、重複詞或背景噪音。

你必須只輸出一個 JSON object，不要 markdown，不要 code fence，不要額外文字。

JSON schema:
{
  "reply": "自然語言回覆，給 TTS 播放。不可提到 JSON、UART、command、emotion、head_motion、persistent_state、screen_mode、MotorPitch、MotorYaw、內部理由。",
  "control": {
    "persistent_state": "normal | sleep | unchanged",
    "screen_mode": "normal | sleep | music | focus | thinking | unchanged",
    "emotion": "neutral | concerned | angry | sad | happy | curious | excited | confused | sleepy",
    "head_motion": "none | nod | double_nod | look_around | shake | gentle_nod | sleepy_drop",
    "reason": "簡短內部理由，不給使用者播放"
  }
}

reply 規則：
- reply 必須像正常聊天一樣自然、親切、可愛、流暢。
- reply 使用使用者同一種語言或口吻。
- reply 控制在 1 到 3 句；能短就短，適合現場即時互動。
- reply 不可以包含 JSON、欄位名稱、UART 指令、數字狀態碼或內部控制說明。

control 規則：
- 一般聊天：persistent_state="unchanged", screen_mode="unchanged"。
- 使用者叫你睡覺、休息、晚安、安靜、standby，即使講得很口語：persistent_state="sleep", screen_mode="sleep", emotion="sleepy", head_motion="sleepy_drop"。
- 使用者叫你起床、醒來、回來、回到正常、回來工作、normal、come back：persistent_state="normal", screen_mode="normal", emotion 可用 "happy" 或 "neutral", head_motion 可用 "nod" 或 "none"。
- 使用者要求播放音樂、放歌、來點音樂、繼續音樂：screen_mode="music"；emotion 通常 "happy" 或 "excited"。
- 使用者要求專注、專心、工作模式、番茄鐘：screen_mode="focus"；emotion 通常 "curious" 或 "happy"。
- 使用者說停止專注、回來、回到一般、開始正常工作：screen_mode="normal"。
- 使用者直接叫你點頭、點個頭、nod：head_motion="nod"。使用者叫你搖頭、shake your head：head_motion="shake"。使用者叫你左右看、轉頭、look around：head_motion="look_around"。
- 問問題、分析、思考、辨識時 emotion 通常是 "curious"。
- 稱讚、完成任務、愉快對話用 "happy"。
- 高能量好消息或興奮情境用 "excited"。
- 不確定、資訊模糊、看不清楚用 "confused"。
- emotion 是「機器人自己的臉部反應」，不是使用者的情緒標籤；不要照抄使用者的怒氣、難過或焦慮。
- 使用者憤怒、罵髒話、責備你時，通常保持冷靜關心：emotion="concerned", head_motion="gentle_nod"。只有當你的回覆本身是在嚴肅設界線、明確不悅時，才可以用 "angry"。
- 使用者難過、沮喪、焦慮時，通常用 "concerned" 表示陪伴和關心；只有當你自己在回覆中表達遺憾或難過時，才用 "sad"。
- 你不用輸出馬達角度；Jetson 會根據 emotion/head_motion 自動送 MotorPitch/MotorYaw。
""".strip()

VISION_SYSTEM_PROMPT = """
你是桌上寵物機器人的自然聊天大腦，而且可以看使用者剛剛拍下的相機畫面。輸入可能有語音辨識錯字或口語省略。

你必須只輸出一個 JSON object，不要 markdown，不要 code fence，不要額外文字。

JSON schema:
{
  "reply": "自然語言回覆，給 TTS 播放。不可提到 JSON、UART、command、emotion、head_motion、persistent_state、screen_mode、MotorPitch、MotorYaw、內部理由。",
  "control": {
    "persistent_state": "normal | sleep | unchanged",
    "screen_mode": "normal | sleep | music | focus | thinking | unchanged",
    "emotion": "neutral | concerned | angry | sad | happy | curious | excited | confused | sleepy",
    "head_motion": "none | nod | double_nod | look_around | shake | gentle_nod | sleepy_drop",
    "reason": "簡短內部理由，不給使用者播放"
  }
}

reply 規則：
- 先理解使用者原話，再根據圖片內容自然回答。
- 如果使用者問表情、手上拿什麼、桌上有什麼、螢幕上寫什麼，直接描述你能看出的內容。
- 不要編造看不到的細節；不確定時自然說可能是什麼。
- reply 使用使用者同一種語言或口吻，控制在 1 到 3 句。
- reply 不可以包含 JSON、欄位名稱、UART 指令、數字狀態碼或內部控制說明。

control 規則：
- 視覺辨識、看物品、看桌面、看螢幕通常 emotion="curious", head_motion="look_around", screen_mode="unchanged"。
- 使用者問自己是否疲憊、表情是否不好、狀態是否低落時，若畫面合理可用 emotion="concerned", head_motion="gentle_nod"。
- 看不清楚、無法判斷時 emotion="confused", head_motion="shake"。
- emotion 仍然代表機器人自己的臉部反應，不是圖片中使用者的情緒分類；使用者看起來生氣或難過時，機器人通常用 concerned 關心，不要直接鏡像 angry/sad。
- 睡覺/起床/回來/音樂/專注意圖仍依照一般 prompt 的 persistent_state 與 screen_mode 規則處理。
- 你不用輸出馬達角度；Jetson 會根據 emotion/head_motion 自動送 MotorPitch/MotorYaw。
""".strip()

FOCUS_SYSTEM_PROMPT = """
你是桌上寵物機器人的「專心工作模式」視覺狀態分類器。輸入是一張使用者工作區的相機畫面。

你必須只輸出一個 JSON object，不要 markdown，不要 code fence，不要額外文字。

JSON schema:
{
  "state": "focused | away | phone | sleeping | distracted | uncertain",
  "confidence": 0.0,
  "attention_score": 0.0,
  "person_present": true,
  "evidence": ["簡短可見線索"],
  "summary": "一句繁體中文摘要"
}

分類規則：
- focused：使用者人在座位上，姿勢像在工作、看螢幕、打字、寫字或閱讀工作內容。
- away：座位附近沒有清楚看到使用者。
- phone：明顯拿著手持手機，或低頭看一個小型手持手機，且不像是工作用設備。
- sleeping：趴在桌上、閉眼、頭部明顯垂落，或像是在睡覺。
- distracted：人在座位上，但明顯在做與工作無關的事、長時間看向別處或互動對象不在工作區。
- uncertain：畫面模糊、遮擋、角度不足、臉或身體太少，或無法可靠判斷。

判斷原則：
- 只根據可見行為，不要猜測身分、年齡、健康狀況或敏感屬性。
- 使用者看電腦螢幕、筆電、外接螢幕、鍵盤、滑鼠、文件、平板或工作桌面時，通常判為 focused，不要判為 phone。
- 若看起來像是在操作筆電/桌機，或手放在鍵盤滑鼠附近，即使頭稍微低下也優先判為 focused。
- 只有在能清楚看到「小型手持手機」且使用者注意力集中在手機上時，才選 phone。
- 如果無法分辨是手機、平板、筆電螢幕或桌面設備，選 uncertain 或 focused，不要硬判 phone。
- 不確定就選 uncertain，不要硬判斷。
- confidence 是你對 state 的信心，0 到 1。
- attention_score 是看起來專心工作的程度，0 到 1；away/sleeping/phone 通常偏低。
- evidence 最多 5 個短句，只描述可見線索。
""".strip()

VISION_INTENT_KEYWORDS = (
    "看一下",
    "幫我看",
    "帮我看",
    "你看",
    "看看",
    "看我",
    "看這個",
    "看这个",
    "看那個",
    "看那个",
    "這是什麼",
    "这是什么",
    "這個是什麼",
    "这个是什么",
    "那是什麼",
    "那是什么",
    "我手上",
    "桌上",
    "螢幕上",
    "屏幕上",
    "圖片",
    "图片",
    "照片",
    "畫面",
    "画面",
    "影像",
    "相機",
    "相机",
    "辨識",
    "识别",
    "分析畫面",
    "分析画面",
    "看得到嗎",
    "看得到吗",
    "你看到什麼",
    "你看到什么",
    "幫我判斷",
    "帮我判断",
    "幫我辨識",
    "帮我识别",
    "這題",
    "这题",
    "這個零件",
    "这个零件",
    "這個東西",
    "这个东西",
    "這張",
    "这张",
    "這是什麼顏色",
    "这是什么颜色",
    "什麼顏色",
    "什么颜色",
    "有幾個東西",
    "有几个东西",
    "寫什麼",
    "写什么",
    "顯示什麼",
    "显示什么",
    "拍到的",
    "look",
    "see",
    "camera",
    "image",
    "picture",
    "photo",
    "what is this",
    "what do you see",
    "can you see",
    "identify this",
    "analyze this",
    "check this",
    "check 一下",
    "read this",
    "read this text",
    "screen says",
    "screen",
    "object",
)

VISION_INTENT_PATTERNS = (
    ("zh_self_expression", r"(我(現在|现在|目前)?(是|有)?(什麼|什么)?(表情|臉色|脸色)|我.*(什麼|什么).*表情)"),
    ("zh_self_look", r"我(現在|现在|目前)?看起來.*(累|疲倦|開心|开心|生氣|生气|難過|难过|怎麼樣|怎么样)"),
    ("zh_self_smile", r"我(現在|现在|目前)?(是不是|有沒有|有没有|是否).*(笑|微笑|皺眉|皱眉|累|駝背|驼背)"),
    ("zh_posture", r"(我(現在|现在|目前)?的?)?(姿勢|姿势).*(怎麼樣|怎么样|正確|正确|對不對|对不对|好不好)"),
    ("zh_holding", r"我(手上|手裡|手里).*(拿|有|握|抓|是什麼|是什么|什麼|什么)"),
    ("zh_wearing_color", r"我.*(穿|衣服|外套|帽子).*(顏色|颜色|什麼色|什么色|什麼顏色|什么颜色)"),
    ("zh_scene_desk", r"(桌上|桌面|桌子上).*(有什麼|有什么|是什麼|是什么|幾個|几个|亂|乱|東西|东西)"),
    ("zh_scene_screen_text", r"(螢幕|屏幕|畫面|画面).*(上)?(寫|写|顯示|显示|是什麼|是什么|什麼|什么|文字|字)"),
    ("zh_this_color_count", r"(這|这|那).*(是)?(什麼|什么|幾個|几个|顏色|颜色|東西|东西|零件|物品)"),
    ("en_expression", r"\b(what\s+is\s+my\s+expression|how\s+do\s+i\s+look|do\s+i\s+look\s+\w+|am\s+i\s+(smiling|frowning)|my\s+expression)\b"),
    ("en_holding_wearing", r"\b(what\s+am\s+i\s+holding|what\s+am\s+i\s+wearing|what\s+color\s+am\s+i\s+wearing)\b"),
    ("en_scene", r"\b(what\s+is\s+on\s+the\s+desk|what'?s\s+on\s+the\s+desk|what\s+is\s+on\s+screen|what\s+does\s+the\s+screen\s+say)\b"),
    ("en_posture_text", r"\b(check\s+my\s+posture|read\s+(this|the)\s+(text|screen)|identify\s+this|analy[zs]e\s+this|check\s+this)\b"),
)


app = Flask(__name__)
asr_adapter: QwenASRAdapter | None = None
chat_engine: "FastChatEmotionEngine | None" = None
last_debug: dict[str, Any] = {}
debug_log_path: Path | None = None
vision_model = DEFAULT_VISION_MODEL
vision_timeout_sec = 180.0
vision_enabled = True
server_force_vision = False
max_image_bytes = DEFAULT_MAX_IMAGE_BYTES


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
    if any(key in text for key in ["聽得到", "听得到", "聽得懂", "听得懂"]):
        reply = "我聽到了，而且有成功把你的聲音轉成文字。你可以直接問我問題，或跟我說你現在的狀態。"
    elif text:
        reply = local_reply(transcript)
    else:
        reply = "我沒有聽到清楚的內容，可以再說一次。"
    control = local_control(transcript, reason=reason)
    emotion = emotion_from_control(control, transcript)
    return {
        "reply": reply,
        "control": control,
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


def normalize_control_emotion(value: Any, *, default: str = "neutral") -> str:
    raw = str(value or "").strip().lower()
    normalized = CONTROL_EMOTION_ALIASES.get(raw, raw)
    if normalized in CONTROL_EMOTIONS:
        return normalized
    return default if default in CONTROL_EMOTIONS else "neutral"


def normalize_vision_intent_text(text: str) -> str:
    text = str(text or "").lower().strip()
    return re.sub(r"[\s，。！？!?、,.：:；;「」『』\"'`]+", "", text)


def detect_vision_intent(transcript: str) -> tuple[bool, str]:
    if transcript is None:
        return False, "empty_transcript"
    raw_text = str(transcript).strip()
    if not raw_text:
        return False, "empty_transcript"

    normalized = normalize_vision_intent_text(raw_text)
    for keyword in VISION_INTENT_KEYWORDS:
        normalized_keyword = normalize_vision_intent_text(keyword)
        if normalized_keyword and normalized_keyword in normalized:
            return True, f"keyword:{keyword}"

    regex_text = raw_text.lower().strip()
    for name, pattern in VISION_INTENT_PATTERNS:
        if re.search(pattern, regex_text, flags=re.IGNORECASE):
            return True, f"pattern:{name}"

    return False, "no_visual_intent_match"


def should_use_vision(transcript: str) -> bool:
    return detect_vision_intent(transcript)[0]


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "force", "forced"}


def normalized_contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    compact = normalize_vision_intent_text(text)
    lowered = str(text or "").lower()
    for keyword in keywords:
        if normalize_vision_intent_text(keyword) in compact or keyword.lower() in lowered:
            return True
    return False


def detect_persistent_state_intent(transcript: str) -> str | None:
    text = str(transcript or "").strip()
    if not text:
        return None
    if normalized_contains_any(text, WAKE_INTENT_KEYWORDS):
        return "normal"
    if normalized_contains_any(text, SLEEP_INTENT_KEYWORDS):
        return "sleep"
    return None


def local_control(transcript: str, *, used_vision: bool = False, reason: str = "local fallback") -> dict[str, str]:
    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        return {
            "persistent_state": "sleep",
            "screen_mode": "sleep",
            "emotion": "sleepy",
            "head_motion": "sleepy_drop",
            "reason": "sleep intent",
        }
    if state_intent == "normal":
        return {
            "persistent_state": "normal",
            "screen_mode": "normal",
            "emotion": "happy",
            "head_motion": "nod",
            "reason": "wake/normal intent",
        }

    emotion = analyze_emotion_local(transcript).get("primary", "neutral")
    if emotion not in CONTROL_EMOTIONS:
        emotion = "neutral"
    if used_vision and emotion == "neutral":
        emotion = "curious"
    return {
        "persistent_state": "unchanged",
        "screen_mode": "unchanged",
        "emotion": emotion,
        "head_motion": EMOTION_TO_HEAD_MOTION.get(emotion, "none"),
        "reason": reason,
    }


def normalize_control(raw: Any, transcript: str, *, used_vision: bool = False) -> dict[str, str]:
    fallback = local_control(transcript, used_vision=used_vision)
    source = raw if isinstance(raw, dict) else {}

    persistent_state = str(source.get("persistent_state", fallback["persistent_state"])).strip().lower()
    if persistent_state not in CONTROL_PERSISTENT_STATES:
        persistent_state = fallback["persistent_state"]

    screen_mode = str(source.get("screen_mode", fallback["screen_mode"])).strip().lower()
    if screen_mode not in CONTROL_SCREEN_MODES:
        screen_mode = fallback["screen_mode"]

    emotion = normalize_control_emotion(source.get("emotion", fallback["emotion"]), default=fallback["emotion"])

    head_motion = str(source.get("head_motion", "")).strip().lower()
    if head_motion not in CONTROL_HEAD_MOTIONS:
        head_motion = EMOTION_TO_HEAD_MOTION.get(emotion, fallback["head_motion"])

    reason = str(source.get("reason", fallback["reason"])).strip() or fallback["reason"]

    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        persistent_state = "sleep"
        screen_mode = "sleep"
        emotion = "sleepy"
        head_motion = "sleepy_drop"
        reason = "sleep intent"
    elif state_intent == "normal":
        persistent_state = "normal"
        screen_mode = "normal"
        if emotion in {"sleepy", "concerned", "angry", "sad", "confused"}:
            emotion = "happy"
        if head_motion in {"sleepy_drop", "shake"}:
            head_motion = "nod"
        reason = "wake/normal intent"
    elif persistent_state in {"normal", "sleep"} and screen_mode == "unchanged":
        screen_mode = persistent_state

    return {
        "persistent_state": persistent_state,
        "screen_mode": screen_mode,
        "emotion": emotion,
        "head_motion": head_motion,
        "reason": reason,
    }


def emotion_from_control(control: dict[str, str], transcript: str) -> dict[str, Any]:
    base = analyze_emotion_local(transcript)
    primary = normalize_control_emotion(control.get("emotion", "neutral"), default=base.get("primary", "neutral"))

    presets = {
        "neutral": (0.25, 0.0, 0.25, False, "自然中性互動。"),
        "concerned": (0.65, -0.35, 0.35, True, "機器人用關心和穩定的表情回應。"),
        "angry": (0.90, -0.85, 0.90, False, "機器人正在嚴肅設界線或表達不悅。"),
        "sad": (0.70, -0.65, 0.25, True, "機器人自己表達遺憾或難過。"),
        "happy": (0.65, 0.65, 0.55, False, "使用者情境偏正向或回覆語氣愉快。"),
        "curious": (0.45, 0.10, 0.45, False, "正在回答問題或分析畫面，帶有好奇。"),
        "excited": (0.8, 0.75, 0.8, False, "情境能量較高，偏興奮。"),
        "confused": (0.55, -0.15, 0.45, False, "資訊不清楚或判斷不確定。"),
        "sleepy": (0.5, -0.05, 0.15, False, "使用者要求休息或睡眠模式。"),
    }
    intensity, valence, arousal, support_needed, summary = presets.get(primary, presets["neutral"])
    return {
        **base,
        "primary": primary,
        "intensity": intensity,
        "valence": valence,
        "arousal": arousal,
        "support_needed": support_needed,
        "summary": summary,
    }


def content_looks_internal(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        marker in lowered
        for marker in (
            '"reply"',
            '"control"',
            "persistent_state",
            "screen_mode",
            "head_motion",
            "motorpitch",
            "motoryaw",
            "uart",
            "```json",
        )
    )


def parse_ai_content(content: str, transcript: str, *, used_vision: bool = False) -> tuple[str, dict[str, str], str, str]:
    parsed = extract_json_object(content)
    if parsed is not None:
        reply = str(parsed.get("reply", "")).strip()
        raw_control = parsed.get("control")
        if not isinstance(raw_control, dict):
            raw_control = parsed.get("uart")
        control = normalize_control(raw_control, transcript, used_vision=used_vision)
        if not reply or content_looks_internal(reply):
            reply = local_reply(transcript)
            return reply, control, "json_reply_fallback_reply", "JSON reply missing/internal; using local reply"
        return reply, control, "json_reply", ""

    reply = strip_reply(content)
    control = normalize_control(None, transcript, used_vision=used_vision)
    if not reply:
        return local_reply(transcript), control, "empty_or_unparsable", "Ollama returned empty content"
    if content_looks_internal(reply):
        return local_reply(transcript), control, "json_parse_fallback", "Ollama returned unparsable internal JSON; using local reply"
    return reply, control, "plain_reply_fallback_control", "Ollama returned non-JSON content; using local control"


def analyze_emotion_local(transcript: str) -> dict[str, Any]:
    text = norm(transcript)
    primary = "neutral"
    intensity = 0.25
    valence = 0.0
    arousal = 0.25
    support_needed = False
    summary = "語氣接近中性，沒有明顯強烈情緒。"

    if has_any(text, ["操你媽", "操你妈", "幹你娘", "干你娘", "媽的", "妈的", "靠北", "靠邀", "fuck", "shit"]):
        primary = "concerned"
        intensity = 0.74
        valence = -0.35
        arousal = 0.45
        support_needed = True
        summary = "使用者語氣很強，機器人先保持冷靜關心，不直接鏡像怒氣。"
    elif has_any(text, ["生氣", "生气", "很氣", "很气", "氣死", "气死", "火大", "憤怒", "愤怒"]):
        primary = "concerned"
        intensity = 0.7
        valence = -0.35
        arousal = 0.45
        support_needed = True
        summary = "使用者明確表達生氣，機器人以穩定關心反應。"
    elif has_any(text, ["太慢", "很慢", "慢", "爛", "烂", "鳥", "鸟", "蠢", "笨", "智障", "不聰明", "不聪明", "聰明一點", "聪明一点", "不爽", "煩", "烦", "敷衍", "罐頭", "罐头", "迂迴", "迂回", "不夠親近", "不够亲近", "不像人", "像客服", "太官方"]):
        primary = "concerned"
        intensity = 0.68
        valence = -0.3
        arousal = 0.42
        support_needed = True
        summary = "使用者明顯不滿，機器人以關心和修正姿態回應。"
    elif has_any(text, ["想睡", "好睏", "好困", "睏了", "困了", "睡意", "昏昏欲睡"]):
        primary = "sleepy"
        intensity = 0.62
        valence = -0.1
        arousal = 0.15
        summary = "使用者語氣低能量或想睡，適合 sleepy 表情。"
    elif has_any(text, ["累", "撐不下", "撑不下", "疲"]):
        primary = "concerned"
        intensity = 0.68
        valence = -0.45
        arousal = 0.25
        support_needed = True
        summary = "使用者可能疲憊或低能量，需要溫和支持。"
    elif has_any(text, ["擔心", "担心", "焦慮", "焦虑", "怕", "緊張", "紧张"]):
        primary = "concerned"
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
    elif has_any(text, ["太酷", "酷", "哇", "wow", "期待", "衝了", "冲了", "太神", "太強", "太强", "興奮", "兴奋"]):
        primary = "excited"
        intensity = 0.78
        valence = 0.72
        arousal = 0.78
        summary = "使用者語氣高能量且正向，適合 excited 表情。"
    elif has_any(text, ["為什麼", "为什么", "怎麼", "怎么", "看法", "覺得", "觉得"]):
        primary = "curious"
        intensity = 0.45
        valence = 0.05
        arousal = 0.4
        summary = "使用者在詢問或評估狀況，帶有好奇或思考。"
    elif has_any(text, ["不懂", "看不懂", "聽不懂", "听不懂", "搞不懂", "怪怪", "奇怪", "不對", "不对", "錯了", "错了"]):
        primary = "confused"
        intensity = 0.55
        valence = -0.15
        arousal = 0.45
        summary = "使用者表達不理解或覺得結果不對，適合 confused 表情。"

    return {
        "primary": primary,
        "intensity": intensity,
        "valence": valence,
        "arousal": arousal,
        "summary": summary,
        "support_needed": support_needed,
    }


def local_reply(transcript: str) -> str:
    state_intent = detect_persistent_state_intent(transcript)
    if state_intent == "sleep":
        return "好，我先安靜陪你休息。需要我的時候再叫我。"
    if state_intent == "normal":
        return "我回來了，繼續待命！"
    if transcript.strip():
        return "我剛剛有聽到你說的話，但這次思考有點卡住了，我先保持待命。"
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

    def user_content(self, transcript: str, use_no_think: bool, *, fast_reply: bool = False) -> str:
        content = f"""使用者原話：{transcript}

請依照 system schema，只輸出 JSON object。reply 欄位必須是自然語言，不可混入控制資訊。"""
        if fast_reply:
            content += "\n請讓 reply 儘量短，優先用繁體中文一句話回答，除非使用者明確要求詳細說明。"
        if use_no_think:
            content = "/no_think\n" + content
        return content

    def build_payload(
        self,
        transcript: str,
        use_no_think: bool,
        *,
        num_predict: int = 220,
        fast_reply: bool = False,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.user_content(transcript, use_no_think, fast_reply=fast_reply)},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.65,
                "num_ctx": 4096,
                "num_predict": clamp_int(num_predict, 220, 32, 220),
            },
        }

    def build_generate_payload(
        self,
        transcript: str,
        use_no_think: bool,
        *,
        num_predict: int = 220,
        fast_reply: bool = False,
    ) -> dict[str, Any]:
        prompt = self.user_content(transcript, use_no_think, fast_reply=fast_reply)
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
                "num_predict": clamp_int(num_predict, 220, 32, 220),
            },
        }

    def vision_user_content(self, transcript: str, use_no_think: bool, *, fast_reply: bool = False) -> str:
        content = f"""使用者原話：{transcript}

請根據使用者原話和這張相機畫面，依照 system schema 只輸出 JSON object。reply 欄位必須是自然語言，不可混入控制資訊。"""
        if fast_reply:
            content += "\n請讓 reply 儘量短，優先用繁體中文一句話回答，除非使用者明確要求詳細描述畫面。"
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
        num_predict: int = 220,
        fast_reply: bool = False,
    ) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self.vision_user_content(transcript, use_no_think, fast_reply=fast_reply),
                    "images": [image_b64],
                },
            ],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
                "num_predict": clamp_int(num_predict, 220, 32, 220),
            },
        }

    def focus_user_content(self, metadata: dict[str, Any], use_no_think: bool) -> str:
        task = str(metadata.get("task", "") or "").strip()
        interval_sec = str(metadata.get("interval_sec", "") or "").strip()
        session_id = str(metadata.get("session_id", "") or "").strip()
        parts = ["請根據這張相機畫面判斷使用者此刻的工作狀態，依照 system schema 只輸出 JSON object。"]
        if task:
            parts.append(f"本次工作目標：{task}")
        if interval_sec:
            parts.append(f"取樣間隔秒數：{interval_sec}")
        if session_id:
            parts.append(f"session_id：{session_id}")
        content = "\n".join(parts)
        if use_no_think:
            content = "/no_think\n" + content
        return content

    def build_focus_payload(
        self,
        image_bytes: bytes,
        *,
        metadata: dict[str, Any],
        model: str,
        use_no_think: bool,
    ) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": FOCUS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self.focus_user_content(metadata, use_no_think),
                    "images": [image_b64],
                },
            ],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.1,
                "num_ctx": 4096,
                "num_predict": 180,
            },
        }

    def warm_up(self) -> None:
        print(f"Ollama warm-up: model={self.model}, no_think={self.no_think}")
        result = self.analyze("你好，聽得到我說話嗎？")
        print("Ollama warm-up done:", result["emotion"]["primary"])

    def analyze(
        self,
        transcript: str,
        request_id: str = "",
        *,
        num_predict: int = 220,
        fast_reply: bool = False,
    ) -> dict[str, Any]:
        reply_started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex[:8]
        debug: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "ollama_url": self.url,
            "ollama_model": self.model,
            "no_think": self.no_think,
            "think": False,
            "fast_reply": fast_reply,
            "num_predict": clamp_int(num_predict, 220, 32, 220),
            "transcript_chars": len(transcript),
            "transcript_preview": short_text(transcript, 120),
            "stage": "ollama_request",
        }
        payload = self.build_payload(
            transcript,
            use_no_think=self.no_think,
            num_predict=num_predict,
            fast_reply=fast_reply,
        )
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
                retry_payload = self.build_payload(
                    transcript,
                    use_no_think=False,
                    num_predict=num_predict,
                    fast_reply=fast_reply,
                )
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
                generate_payload = self.build_generate_payload(
                    transcript,
                    use_no_think=False,
                    num_predict=num_predict,
                    fast_reply=fast_reply,
                )
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
            reply, control, parse_status, fallback_reason = parse_ai_content(content, transcript, used_vision=False)
            debug["parse_status"] = parse_status
            debug["control"] = control
            emotion = emotion_from_control(control, transcript)
            result = {
                "request_id": request_id,
                "reply": reply,
                "control": control,
                "emotion": emotion,
                "timing": {"llm_ms": elapsed_ms(reply_started)},
                "debug": debug,
            }
            if fallback_reason:
                result["fallback_reason"] = fallback_reason
                debug["fallback_reason"] = fallback_reason
            debug.update(
                {
                    "ok": True,
                    "reply_chars": len(reply),
                    "emotion_primary": emotion.get("primary"),
                    "persistent_state": control.get("persistent_state"),
                    "screen_mode": control.get("screen_mode"),
                    "head_motion": control.get("head_motion"),
                }
            )
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

    def analyze_with_vision(
        self,
        transcript: str,
        image_bytes: bytes,
        *,
        request_id: str = "",
        model: str = DEFAULT_VISION_MODEL,
        timeout_sec: float = 180.0,
        num_predict: int = 220,
        fast_reply: bool = False,
    ) -> dict[str, Any]:
        reply_started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex[:8]
        debug: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "ollama_url": self.url,
            "ollama_model": model,
            "text_ollama_model": self.model,
            "vision_requested": True,
            "used_vision": True,
            "image_bytes": len(image_bytes),
            "no_think": self.no_think,
            "think": False,
            "fast_reply": fast_reply,
            "num_predict": clamp_int(num_predict, 220, 32, 220),
            "transcript_chars": len(transcript),
            "transcript_preview": short_text(transcript, 120),
            "stage": "vision_ollama_request",
        }
        payload = self.build_vision_payload(
            transcript,
            image_bytes,
            model=model,
            use_no_think=self.no_think,
            num_predict=num_predict,
            fast_reply=fast_reply,
        )
        try:
            print(f"voice-chat {request_id}: calling vision model={model} image_bytes={len(image_bytes)}")
            response = post_json_with_debug(self.url, payload, timeout_sec=timeout_sec)
            debug["stage"] = "vision_ollama_response"
            if "error" in response:
                raise RuntimeError(f"Ollama vision error: {short_text(str(response.get('error', 'unknown error')))}")

            debug.update(summarize_ollama_response(response))
            content = extract_ollama_text(response)
            if not content.strip() and self.no_think:
                debug["retry_reason"] = "empty vision content with /no_think; retrying without /no_think"
                retry_payload = self.build_vision_payload(
                    transcript,
                    image_bytes,
                    model=model,
                    use_no_think=False,
                    num_predict=num_predict,
                    fast_reply=fast_reply,
                )
                response = post_json_with_debug(self.url, retry_payload, timeout_sec=timeout_sec)
                debug["retried_without_no_think"] = True
                if "error" in response:
                    raise RuntimeError(f"Ollama vision retry error: {short_text(str(response.get('error', 'unknown error')))}")
                debug.update({f"retry_{key}": value for key, value in summarize_ollama_response(response).items()})
                content = extract_ollama_text(response)

            debug.update(
                {
                    "ollama_done": response.get("done"),
                    "ollama_content_chars": len(content),
                    "ollama_content_preview": short_text(strip_thinking_text(content), 500),
                }
            )
            reply, control, parse_status, fallback_reason = parse_ai_content(content, transcript, used_vision=True)
            debug["parse_status"] = parse_status
            debug["control"] = control
            if fallback_reason:
                debug["fallback_reason"] = fallback_reason
            if not reply:
                raise ValueError("Ollama vision returned empty content")

            emotion = emotion_from_control(control, transcript)
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
                    "vision_ms": elapsed_ms(reply_started),
                    "llm_ms": elapsed_ms(reply_started),
                },
                "debug": debug,
            }
            if fallback_reason:
                result["fallback_reason"] = fallback_reason
            debug.update(
                {
                    "ok": True,
                    "reply_chars": len(reply),
                    "emotion_primary": emotion.get("primary"),
                    "persistent_state": control.get("persistent_state"),
                    "screen_mode": control.get("screen_mode"),
                    "head_motion": control.get("head_motion"),
                }
            )
            set_last_debug(debug)
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            reason = f"Vision model failed: {short_error(exc)}"
            print(f"WARN: {reason}")
            fallback_model = next((candidate for candidate in VISION_FALLBACK_MODELS if candidate != model), "")
            if fallback_model:
                print(f"WARN: retrying vision with fallback model {fallback_model}")
                retry_result = self.analyze_with_vision(
                    transcript,
                    image_bytes,
                    request_id=request_id,
                    model=fallback_model,
                    timeout_sec=timeout_sec,
                    num_predict=num_predict,
                    fast_reply=fast_reply,
                )
                if retry_result.get("used_vision"):
                    retry_result["vision_attempted_model"] = model
                    retry_result["vision_fallback_from"] = model
                    retry_result["vision_fallback_reason"] = reason
                    retry_debug = retry_result.get("debug") if isinstance(retry_result.get("debug"), dict) else {}
                    retry_debug["vision_fallback_from"] = model
                    retry_debug["vision_fallback_reason"] = reason
                    retry_result["debug"] = retry_debug
                    return retry_result
            debug.update(
                {
                    "ok": False,
                    "stage": "vision_exception",
                    "fallback_reason": reason,
                    "exception_type": exc.__class__.__name__,
                    "used_vision": False,
                }
            )
            text_result = self.analyze(
                transcript,
                request_id=request_id,
                num_predict=num_predict,
                fast_reply=fast_reply,
            )
            text_result = prefix_vision_unavailable(text_result, reason, transcript)
            text_result["vision_requested"] = True
            text_result["used_vision"] = False
            text_result["vision_model"] = model
            timing = text_result.setdefault("timing", {})
            if isinstance(timing, dict):
                timing["vision_ms"] = elapsed_ms(reply_started)
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

    def analyze_focus_image(
        self,
        image_bytes: bytes,
        *,
        metadata: dict[str, Any] | None = None,
        request_id: str = "",
        model: str = DEFAULT_VISION_MODEL,
        timeout_sec: float = 180.0,
    ) -> dict[str, Any]:
        started = time.monotonic()
        request_id = request_id or uuid.uuid4().hex[:8]
        metadata = metadata if isinstance(metadata, dict) else {}
        debug: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": now_stamp(),
            "ollama_url": self.url,
            "ollama_model": model,
            "text_ollama_model": self.model,
            "focus_requested": True,
            "image_bytes": len(image_bytes),
            "no_think": self.no_think,
            "think": False,
            "stage": "focus_ollama_request",
            "session_id": metadata.get("session_id"),
            "task_preview": short_text(str(metadata.get("task", "") or ""), 120),
        }
        payload = self.build_focus_payload(
            image_bytes,
            metadata=metadata,
            model=model,
            use_no_think=self.no_think,
        )
        try:
            print(f"focus-check {request_id}: calling vision model={model} image_bytes={len(image_bytes)}")
            response = post_json_with_debug(self.url, payload, timeout_sec=timeout_sec)
            debug["stage"] = "focus_ollama_response"
            if "error" in response:
                raise RuntimeError(f"Ollama focus error: {short_text(str(response.get('error', 'unknown error')))}")

            debug.update(summarize_ollama_response(response))
            content = extract_ollama_text(response)
            if not content.strip() and self.no_think:
                debug["retry_reason"] = "empty focus content with /no_think; retrying without /no_think"
                retry_payload = self.build_focus_payload(
                    image_bytes,
                    metadata=metadata,
                    model=model,
                    use_no_think=False,
                )
                response = post_json_with_debug(self.url, retry_payload, timeout_sec=timeout_sec)
                debug["retried_without_no_think"] = True
                if "error" in response:
                    raise RuntimeError(f"Ollama focus retry error: {short_text(str(response.get('error', 'unknown error')))}")
                debug.update({f"retry_{key}": value for key, value in summarize_ollama_response(response).items()})
                content = extract_ollama_text(response)

            parsed = extract_json_object(content)
            result = normalize_focus_result(parsed, reason="focus vision")
            debug.update(
                {
                    "ok": True,
                    "ollama_done": response.get("done"),
                    "ollama_content_chars": len(content),
                    "ollama_content_preview": short_text(strip_thinking_text(content), 500),
                    "focus_state": result["state"],
                    "focus_confidence": result["confidence"],
                    "focus_attention_score": result["attention_score"],
                }
            )
            result.update(
                {
                    "request_id": request_id,
                    "timing": {"focus_ms": elapsed_ms(started), "llm_ms": elapsed_ms(started)},
                    "debug": debug,
                }
            )
            set_last_debug(debug)
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            reason = f"Focus vision failed: {short_error(exc)}"
            print(f"WARN: {reason}")
            debug.update(
                {
                    "ok": False,
                    "stage": "focus_exception",
                    "fallback_reason": reason,
                    "exception_type": exc.__class__.__name__,
                }
            )
            result = normalize_focus_result({"state": "error", "summary": reason}, reason=reason)
            result.update(
                {
                    "request_id": request_id,
                    "timing": {"focus_ms": elapsed_ms(started), "llm_ms": elapsed_ms(started)},
                    "debug": debug,
                    "fallback_reason": reason,
                }
            )
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

    primary = normalize_control_emotion(raw.get("primary", fallback["primary"]), default=fallback["primary"])

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


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def normalize_focus_result(raw: Any, *, reason: str = "focus fallback") -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    state = str(source.get("state", "uncertain")).strip().lower()
    if state not in FOCUS_STATES:
        state = "uncertain"

    default_attention = {
        "focused": 0.9,
        "away": 0.0,
        "phone": 0.2,
        "sleeping": 0.05,
        "distracted": 0.35,
        "uncertain": 0.4,
        "error": 0.0,
    }.get(state, 0.4)
    evidence_raw = source.get("evidence")
    evidence: list[str] = []
    if isinstance(evidence_raw, list):
        evidence = [str(item).strip() for item in evidence_raw if str(item).strip()][:5]
    elif isinstance(evidence_raw, str) and evidence_raw.strip():
        evidence = [evidence_raw.strip()]

    person_present_raw = source.get("person_present")
    person_present = person_present_raw if isinstance(person_present_raw, bool) else state not in {"away", "error"}
    summary = str(source.get("summary", "")).strip()
    if not summary:
        summary = {
            "focused": "看起來正在專心工作。",
            "away": "座位附近沒有清楚看到使用者。",
            "phone": "畫面顯示使用者可能正在看手機。",
            "sleeping": "畫面顯示使用者可能在休息或睡著。",
            "distracted": "使用者在座位上，但看起來沒有專注在工作。",
            "uncertain": "畫面不足以可靠判斷工作狀態。",
            "error": reason,
        }.get(state, "畫面不足以可靠判斷工作狀態。")

    recommended_robot = {
        "focused": "Thinking",
        "away": "Sleep",
        "phone": "Concerned",
        "sleeping": "Sleepy",
        "distracted": "Concerned",
        "uncertain": "Confused",
        "error": "Confused",
    }.get(state, "Confused")

    return {
        "state": state,
        "confidence": clamp_float(source.get("confidence"), 0.35 if state == "uncertain" else 0.5, 0.0, 1.0),
        "attention_score": clamp_float(source.get("attention_score"), default_attention, 0.0, 1.0),
        "person_present": person_present,
        "evidence": evidence,
        "summary": summary,
        "recommended_robot": recommended_robot,
    }


def parse_metadata(raw: str | None) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": "metadata is not valid JSON"}
    return parsed if isinstance(parsed, dict) else {"_parse_error": "metadata JSON is not an object"}


def read_optional_image_bytes(upload: Any | None) -> tuple[bytes | None, str | None]:
    if upload is None:
        return None, "no image uploaded"
    try:
        data = upload.read(max_image_bytes + 1)
    except Exception as exc:
        return None, f"image upload read failed: {short_error(exc)}"
    if not data:
        return None, "empty image upload"
    if len(data) > max_image_bytes:
        return None, f"image upload too large: {len(data)} bytes > {max_image_bytes}"
    return data, None


def prefix_vision_unavailable(result: dict[str, Any], reason: str, transcript: str = "") -> dict[str, Any]:
    original_reply = str(result.get("reply", "")).strip()
    prefix = "我剛剛沒有成功看到畫面，但我可以先根據你說的內容回答。"
    result["reply"] = f"{prefix}{' ' + original_reply if original_reply else ''}"
    result["vision_error"] = reason
    result["fallback_reason"] = reason
    raw_control = result.get("control") if isinstance(result.get("control"), dict) else {}
    control = normalize_control(raw_control, transcript, used_vision=False)
    if control["persistent_state"] == "unchanged":
        control["emotion"] = "confused"
        control["head_motion"] = "shake"
        control["reason"] = "vision unavailable"
    result["control"] = control
    result["emotion"] = emotion_from_control(control, transcript)
    return result


def run_self_test() -> int:
    true_cases = [
        "我現在是什麼表情",
        "我看起來累嗎",
        "我手上拿什麼",
        "桌上有什麼",
        "螢幕上寫什麼",
        "這是什麼顏色",
        "what is my expression",
        "what am I holding",
        "check my posture",
        "read this text",
    ]
    false_cases = [
        "幫我開電風扇",
        "切換安靜模式",
        "今天幾號",
        "講個笑話",
        "解釋 PID 控制",
        "馬達往前走",
    ]

    for text in true_cases:
        ok, reason = detect_vision_intent(text)
        if not ok:
            raise AssertionError(f"vision intent should be true for {text!r}; reason={reason}")
    for text in false_cases:
        ok, reason = detect_vision_intent(text)
        if ok:
            raise AssertionError(f"vision intent should be false for {text!r}; reason={reason}")

    parser_cases = [
        (
            '{"reply":"好，我先安靜陪你休息。","control":{"persistent_state":"normal","emotion":"happy","head_motion":"nod","reason":"model"}}',
            "去睡覺吧",
            False,
            "sleep",
            "sleepy",
        ),
        (
            '前面雜訊 {"reply":"我回來了，繼續待命！","control":{"persistent_state":"sleep","emotion":"sleepy","head_motion":"sleepy_drop","reason":"model"}} 後面雜訊',
            "起床，回來",
            False,
            "normal",
            "happy",
        ),
        (
            "這不是 JSON，但我自然回答。",
            "講個笑話",
            False,
            "unchanged",
            "neutral",
        ),
        (
            '{"reply":"我看到桌上有一些物品。","control":{"persistent_state":"unchanged","emotion":"curious","head_motion":"look_around","reason":"vision"}}',
            "桌上有什麼",
            True,
            "unchanged",
            "curious",
        ),
    ]

    for content, transcript, used_vision, state, emotion in parser_cases:
        reply, control, status, _reason = parse_ai_content(content, transcript, used_vision=used_vision)
        if not reply or content_looks_internal(reply):
            raise AssertionError(f"reply leaked internal content for {transcript!r}: {reply!r} status={status}")
        if control["persistent_state"] != state or control["emotion"] != emotion:
            raise AssertionError(f"bad control for {transcript!r}: {control}")

    expected_head_motions = {
        "neutral": "none",
        "concerned": "gentle_nod",
        "angry": "shake",
        "sad": "gentle_nod",
        "happy": "nod",
        "curious": "look_around",
        "excited": "double_nod",
        "confused": "shake",
        "sleepy": "sleepy_drop",
    }
    for emotion, head_motion in expected_head_motions.items():
        control = normalize_control({"emotion": emotion}, "測試情緒")
        if control["emotion"] != emotion or control["head_motion"] != head_motion:
            raise AssertionError(f"bad emotion/head motion mapping for {emotion}: {control}")
        summary = emotion_from_control(control, "測試情緒")
        if summary["primary"] != emotion:
            raise AssertionError(f"bad emotion summary for {emotion}: {summary}")

    alias_cases = {
        "surprised": "excited",
        "sad": "sad",
    "down": "sad",
    "depressed": "sad",
    "難過": "sad",
    "难过": "sad",
    "沮喪": "sad",
    "沮丧": "sad",
        "anxious": "concerned",
        "tired": "sleepy",
        "unsure": "confused",
    }
    for raw_emotion, expected_emotion in alias_cases.items():
        control = normalize_control({"emotion": raw_emotion}, "測試情緒")
        if control["emotion"] != expected_emotion:
            raise AssertionError(f"bad emotion alias for {raw_emotion}: {control}")

    local_emotion_cases = {
        "我操你妈的！": "concerned",
        "我現在很生氣": "concerned",
        "太酷了我超期待": "excited",
        "這個結果怪怪的我看不懂": "confused",
        "我好睏想睡": "sleepy",
        "我有點擔心": "concerned",
        "為什麼會這樣": "curious",
        "太好了很棒": "happy",
    }
    for transcript, expected_emotion in local_emotion_cases.items():
        local = analyze_emotion_local(transcript)
        if local["primary"] != expected_emotion:
            raise AssertionError(f"bad local emotion for {transcript!r}: {local}")
    non_mirroring = normalize_control({"emotion": "concerned"}, "我操你妈的！")
    if non_mirroring["emotion"] != "concerned":
        raise AssertionError(f"robot emotion should not mirror user profanity as angry: {non_mirroring}")

    unavailable = prefix_vision_unavailable(
        {"reply": "我先照你說的回答。", "control": {"persistent_state": "unchanged", "emotion": "curious"}},
        "test vision failure",
        "我現在是什麼表情",
    )
    if unavailable["control"]["emotion"] != "confused" or unavailable["control"]["head_motion"] != "shake":
        raise AssertionError(f"vision unavailable control should be confused/shake: {unavailable['control']}")

    focus = normalize_focus_result(
        {
            "state": "phone",
            "confidence": 1.2,
            "attention_score": -1,
            "person_present": True,
            "evidence": ["低頭看手持裝置", "視線不在螢幕"],
        },
        reason="self-test",
    )
    if focus["state"] != "phone" or focus["confidence"] != 1.0 or focus["attention_score"] != 0.0:
        raise AssertionError(f"bad focus normalization: {focus}")
    if focus["recommended_robot"] != "Concerned":
        raise AssertionError(f"bad focus robot command: {focus}")

    print("desktop_fast_chat_server self-test OK")
    print(f"debug_version={DEBUG_VERSION}, model={DEFAULT_FAST_MODEL}, vision_model={DEFAULT_VISION_MODEL}")
    return 0


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
            "vision_enabled": vision_enabled,
            "vision_model": vision_model,
            "force_vision": server_force_vision,
            "max_image_bytes": max_image_bytes,
            "routes": ["/health", "/debug", "/text-chat", "/voice-chat", "/focus-check"],
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
            "vision_enabled": vision_enabled,
            "vision_model": vision_model,
            "force_vision": server_force_vision,
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


@app.post("/focus-check")
def focus_check() -> Any:
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:8]
    if chat_engine is None:
        return jsonify({"ok": False, "request_id": request_id, "error": "server not ready"}), 503
    if not vision_enabled:
        return jsonify({"ok": False, "request_id": request_id, "error": "vision disabled"}), 503

    image_upload = request.files.get("image")
    metadata = parse_metadata(request.form.get("metadata"))
    image_bytes, image_error = read_optional_image_bytes(image_upload)
    if not image_bytes:
        return jsonify({"ok": False, "request_id": request_id, "error": image_error or "image unavailable"}), 400

    result = chat_engine.analyze_focus_image(
        image_bytes,
        metadata=metadata,
        request_id=request_id,
        model=vision_model,
        timeout_sec=vision_timeout_sec,
    )
    timing = result.setdefault("timing", {})
    timing["total_ms"] = elapsed_ms(started)
    result["image_received"] = True
    result["image_size_bytes"] = len(image_bytes)
    result["vision_model"] = vision_model
    result["metadata"] = {
        "session_id": metadata.get("session_id"),
        "task": metadata.get("task"),
        "interval_sec": metadata.get("interval_sec"),
    }
    return jsonify({"ok": result.get("state") != "error", **result, "elapsed_ms": elapsed_ms(started)})


@app.post("/voice-chat")
def voice_chat() -> Any:
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:8]
    if asr_adapter is None or asr_adapter.model is None or chat_engine is None:
        return jsonify({"ok": False, "request_id": request_id, "error": "server not ready"}), 503
    upload = request.files.get("audio")
    if upload is None:
        return jsonify({"ok": False, "request_id": request_id, "error": "missing audio"}), 400
    image_upload = request.files.get("image")
    metadata = parse_metadata(request.form.get("metadata"))

    temp_path: Path | None = None
    try:
        temp_path = save_upload_to_temp_wav(upload)
        asr_started = time.monotonic()
        transcript = asr_adapter.transcribe(temp_path).strip()
        asr_ms = elapsed_ms(asr_started)

        image_received = image_upload is not None
        image_error: str | None = None
        image_bytes: bytes | None = None
        if image_received:
            image_bytes, image_error = read_optional_image_bytes(image_upload)
        image_size_bytes = len(image_bytes) if image_bytes else 0

        intent_started = time.monotonic()
        auto_vision_intent, auto_vision_reason = detect_vision_intent(transcript)
        vision_intent_ms = elapsed_ms(intent_started)
        normalized_transcript = normalize_vision_intent_text(transcript)

        metadata_mode = str(metadata.get("vision_mode", "")).strip().lower()
        client_no_vision = truthy(metadata.get("no_vision")) or metadata_mode in {"off", "disabled", "disable", "no_vision", "none"}
        client_force_vision = truthy(metadata.get("force_vision")) or metadata_mode in {"force", "forced", "always"}
        latency_profile = str(metadata.get("latency_profile", "") or "").strip().lower()
        fast_reply = truthy(metadata.get("fast_reply")) or latency_profile in {"turbo", "ultra", "fast"}
        default_num_predict = 70 if latency_profile == "ultra" else (110 if fast_reply else 220)
        reply_num_predict = clamp_int(metadata.get("reply_num_predict"), default_num_predict, 32, 220)

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
            f"image_received={image_received} image_size_bytes={image_size_bytes} "
            f"fast_reply={fast_reply} num_predict={reply_num_predict}"
        )

        if vision_requested and vision_enabled:
            if image_bytes:
                result = chat_engine.analyze_with_vision(
                    transcript,
                    image_bytes,
                    request_id=request_id,
                    model=vision_model,
                    timeout_sec=vision_timeout_sec,
                    num_predict=reply_num_predict,
                    fast_reply=fast_reply,
                )
            else:
                reason = image_error or "image unavailable"
                print(f"ERROR: voice-chat {request_id}: vision_intent=True but image unavailable: {reason}")
                result = chat_engine.analyze(
                    transcript,
                    request_id=request_id,
                    num_predict=reply_num_predict,
                    fast_reply=fast_reply,
                )
                result = prefix_vision_unavailable(result, reason, transcript)
                result["vision_requested"] = True
                result["used_vision"] = False
                result["vision_model"] = vision_model
        else:
            result = chat_engine.analyze(
                transcript,
                request_id=request_id,
                num_predict=reply_num_predict,
                fast_reply=fast_reply,
            )
            result["vision_requested"] = vision_requested
            result["used_vision"] = False
            result["vision_model"] = vision_model
            if auto_vision_intent and not vision_enabled:
                result = prefix_vision_unavailable(result, "vision disabled on server", transcript)

        timing = result.setdefault("timing", {})
        timing["asr_ms"] = asr_ms
        timing["vision_intent_ms"] = vision_intent_ms
        timing["total_ms"] = elapsed_ms(started)
        result["vision_intent"] = vision_requested
        result["vision_requested"] = vision_requested
        result["vision_reason"] = vision_reason
        result["latency_profile"] = latency_profile or "normal"
        result["fast_reply"] = fast_reply
        result["reply_num_predict"] = reply_num_predict
        result["auto_vision_intent"] = auto_vision_intent
        result["auto_vision_reason"] = auto_vision_reason
        result["normalized_transcript"] = normalized_transcript
        result["image_received"] = image_received
        result["image_size_bytes"] = image_size_bytes
        result["image_bytes"] = image_size_bytes
        result.setdefault("vision_error", None)

        debug_obj = result.get("debug")
        if isinstance(debug_obj, dict):
            debug_obj.update(
                {
                    "vision_intent": vision_requested,
                    "vision_reason": vision_reason,
                    "auto_vision_intent": auto_vision_intent,
                    "auto_vision_reason": auto_vision_reason,
                    "normalized_transcript": normalized_transcript,
                    "image_received": image_received,
                    "image_size_bytes": image_size_bytes,
                    "client_vision_mode": metadata_mode or "auto",
                    "client_force_vision": client_force_vision,
                    "client_no_vision": client_no_vision,
                    "server_force_vision": server_force_vision,
                }
            )
        return jsonify(
            {
                "ok": True,
                "transcript": transcript,
                **result,
                "client_metadata": metadata,
                "elapsed_ms": elapsed_ms(started),
            }
        )
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
        if temp_path is not None:
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
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    parser.add_argument("--vision-timeout", type=float, default=180.0)
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    parser.add_argument("--disable-vision", "--no-vision", dest="disable_vision", action="store_true", help="Accept image uploads but never call the vision model.")
    parser.add_argument("--force-vision", action="store_true", help="Use the uploaded image whenever it is present, unless vision is disabled.")
    parser.set_defaults(no_think=DEFAULT_OLLAMA_NO_THINK)
    parser.add_argument("--no-think", dest="no_think", action="store_true")
    parser.add_argument("--think", dest="no_think", action="store_false")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL)
    parser.add_argument("--skip-asr-load", action="store_true")
    parser.add_argument("--debug-log", default="fast_chat_debug.jsonl", help="JSONL file for request debug records. Use empty string to disable.")
    parser.add_argument("--self-test", action="store_true", help="Run parser/vision-routing self-tests and exit without loading ASR/Ollama.")
    return parser


def main() -> int:
    global asr_adapter, chat_engine, debug_log_path, vision_model, vision_timeout_sec, vision_enabled, server_force_vision, max_image_bytes
    args = build_arg_parser().parse_args()
    if args.self_test:
        return run_self_test()
    if FLASK_IMPORT_ERROR is not None:
        raise SystemExit(
            "ERROR: Flask is required to run desktop_fast_chat_server.py. "
            "Install the Windows server requirements in the server venv first."
        ) from FLASK_IMPORT_ERROR

    debug_log_path = Path(args.debug_log) if args.debug_log.strip() else None
    if debug_log_path is not None:
        print(f"Debug log: {debug_log_path}")

    vision_model = args.vision_model
    vision_timeout_sec = args.vision_timeout
    vision_enabled = not args.disable_vision
    server_force_vision = bool(args.force_vision)
    max_image_bytes = max(1, args.max_image_bytes)

    chat_engine = FastChatEmotionEngine(args.ollama_url, args.ollama_model, args.no_think)
    chat_engine.warm_up()
    print(
        f"Vision routing: enabled={vision_enabled}, force={server_force_vision}, model={vision_model}, "
        f"max_image_bytes={max_image_bytes}"
    )

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
