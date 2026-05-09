#!/usr/bin/env python3
"""Standalone local tool server for the MakeNTU local AI stack.

This file intentionally does not import or modify the existing wake/TTS/UART
bridge. Run it as a sidecar service; the Wake Bridge calls:
  - /music only when the transcript asks to play, pause, resume, stop, or change music
  - /weather only when the transcript asks about local or named-location weather
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python fallback
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_HOST = os.getenv("MUSIC_TOOL_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("MUSIC_TOOL_PORT", "8788"))
DEFAULT_BACKEND = os.getenv(
    "MUSIC_PLAYER_BACKEND",
    "mpv" if shutil.which("mpv") and (shutil.which("yt-dlp") or shutil.which("youtube-dl")) else "browser",
)
YOUTUBE_MUSIC_SEARCH_URL = "https://music.youtube.com/search?q="
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_WEATHER_LOCATION = os.getenv("WEATHER_DEFAULT_LOCATION", "Taipei")
DEFAULT_WEATHER_LANGUAGE = os.getenv("WEATHER_LANGUAGE", "zh")
DEFAULT_WEATHER_TIMEOUT_SEC = float(os.getenv("WEATHER_TIMEOUT_SEC", "5.0"))
DEFAULT_WEATHER_CACHE_TTL_SEC = float(os.getenv("WEATHER_CACHE_TTL_SEC", "300.0"))
_WEATHER_CACHE_LOCK = threading.Lock()
_WEATHER_GEOCODE_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_WEATHER_FORECAST_CACHE: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


DEFAULT_MPV_AUDIO_DEVICE = os.getenv("MPV_AUDIO_DEVICE", "auto")
DEFAULT_MPV_AUDIO_DEVICE_KEYWORD = os.getenv("MPV_AUDIO_DEVICE_KEYWORD", "UACDemo")
DEFAULT_MPV_YTDL_COOKIES = os.getenv("MPV_YTDL_COOKIES", os.getenv("YTDLP_COOKIES", ""))
DEFAULT_MPV_YTDL_COOKIES_FROM_BROWSER = os.getenv("MPV_YTDL_COOKIES_FROM_BROWSER", os.getenv("YTDLP_COOKIES_FROM_BROWSER", ""))
DEFAULT_MPV_VOLUME = _env_int("MPV_VOLUME", 100)
DEFAULT_MPV_READY_TIMEOUT_SEC = _env_float("MPV_READY_TIMEOUT_SEC", _env_float("MPV_READY_TIMEOUT", 1.5))

WAKE_WORD_PATTERNS = (
    r"\bhey\s+jarvis\b",
    r"\bhi\s+jarvis\b",
    r"\bjarvis\b",
    r"嘿\s*jarvis",
    r"嗨\s*jarvis",
    r"賈維斯",
    r"贾维斯",
)

STOP_PATTERNS = (
    r"停止(播放)?(音樂|音乐|歌曲|歌)?",
    r"關掉(音樂|音乐|歌曲|歌)",
    r"关掉(音乐|歌曲|歌)",
    r"不要播了",
    r"別播了",
    r"别播了",
    r"停歌",
    r"\bstop\s+(the\s+)?(music|song|audio|playback)\b",
    r"\bstop\b",
)

PAUSE_PATTERNS = (
    r"暫停(播放)?(音樂|音乐|歌曲|歌)?",
    r"暂停(播放)?(音乐|歌曲|歌)?",
    r"\bpause\s+(the\s+)?(music|song|audio|playback)\b",
    r"\bpause\b",
)

RESUME_PATTERNS = (
    r"(?:繼續|继续|接著|接着|恢復|恢复)(?:播放|播|放|音樂|音乐|歌曲|歌)",
    r"(?:繼續|继续|接著|接着|恢復|恢复).{0,4}(?:音樂|音乐|歌曲|歌)",
    r"\bresume\s+(the\s+)?(music|song|audio|playback)\b",
    r"\bcontinue\s+(the\s+)?(music|song|audio|playback)\b",
    r"\bunpause\b",
)

CN_PLAY_PATTERNS = (
    r"(?:請|请|麻煩你|麻烦你|可以)?(?:幫我|帮我)?(?:播放|播一下|播|波一下|波|放一下|放|放首|放一首)\s*(?P<query>.+)",
    r"(?:我想要聽|我想要听|想要聽|想要听|我想聽|我想听|想聽|想听|我要聽|我要听|聽一下|听一下)\s*(?P<query>.+)",
    r"(?:換成|换成|換一首|换一首|改播|切到|換歌成|换歌成)\s*(?P<query>.+)",
    r"(?:來一首|来一首|放點|放点)\s*(?P<query>.*)",
)

EN_PLAY_PATTERNS = (
    r"\bplay(?:\s+me)?\s+(?P<query>.+)",
    r"\bput\s+on\s+(?P<query>.+)",
    r"\bi\s+want\s+to\s+listen\s+to\s+(?P<query>.+)",
    r"\blisten\s+to\s+(?P<query>.+)",
)

TRAILING_FILLERS = (
    "這首歌",
    "这首歌",
    "這首",
    "这首",
    "這個音樂",
    "这个音乐",
    "這個歌曲",
    "这个歌曲",
    "這個歌",
    "这个歌",
    "歌曲",
    "音樂",
    "音乐",
    "給我聽",
    "给我听",
    "給我播",
    "给我播",
    "謝謝",
    "谢谢",
    "please",
)

AUDIO_COMPLAINT_WORDS = (
    "沒聲音",
    "没声音",
    "沒有聲音",
    "没有声音",
    "聲音太小",
    "声音太小",
    "聲音很小",
    "声音很小",
    "聲音超小",
    "声音超小",
    "小聲",
    "小声",
    "音量",
    "聽不到",
    "听不到",
    "聽到聲音",
    "听到声音",
)

EXPLICIT_MUSIC_REQUEST_WORDS = (
    "播放音樂",
    "播放音乐",
    "播放歌曲",
    "播音樂",
    "播音乐",
    "播歌",
    "放音樂",
    "放音乐",
    "放歌",
    "聽歌",
    "听歌",
    "點歌",
    "点歌",
    "play music",
    "play song",
)

WEATHER_KEYWORDS = (
    "天氣",
    "天气",
    "氣溫",
    "气温",
    "溫度",
    "温度",
    "幾度",
    "几度",
    "下雨",
    "會不會下雨",
    "会不会下雨",
    "會下雨",
    "会下雨",
    "降雨",
    "雨傘",
    "雨伞",
    "帶傘",
    "带伞",
    "冷不冷",
    "熱不熱",
    "热不热",
    "weather",
    "forecast",
    "temperature",
    "rain",
    "umbrella",
)

LOCAL_LOCATION_WORDS = (
    "所在地",
    "當地",
    "当地",
    "這裡",
    "这里",
    "這邊",
    "这边",
    "附近",
    "目前位置",
    "現在位置",
    "现在位置",
    "local",
    "here",
    "my location",
)

WEATHER_LOCATION_ALIASES = {
    "台北": "Taipei",
    "臺北": "Taipei",
    "新北": "New Taipei",
    "新竹": "Hsinchu",
    "桃園": "Taoyuan",
    "桃园": "Taoyuan",
    "台中": "Taichung",
    "臺中": "Taichung",
    "台南": "Tainan",
    "臺南": "Tainan",
    "高雄": "Kaohsiung",
    "基隆": "Keelung",
    "宜蘭": "Yilan",
    "宜兰": "Yilan",
    "花蓮": "Hualien",
    "花莲": "Hualien",
    "台東": "Taitung",
    "臺東": "Taitung",
    "東京": "Tokyo",
    "大阪": "Osaka",
    "京都": "Kyoto",
    "首爾": "Seoul",
    "首尔": "Seoul",
    "香港": "Hong Kong",
    "上海": "Shanghai",
    "北京": "Beijing",
    "新加坡": "Singapore",
    "紐約": "New York",
    "纽约": "New York",
    "洛杉磯": "Los Angeles",
    "洛杉矶": "Los Angeles",
    "倫敦": "London",
    "伦敦": "London",
    "巴黎": "Paris",
}

WEATHER_CODE_ZH = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多雲",
    3: "多雲",
    45: "有霧",
    48: "霧凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "較強毛毛雨",
    56: "凍毛毛雨",
    57: "較強凍毛毛雨",
    61: "小雨",
    63: "雨",
    65: "大雨",
    66: "凍雨",
    67: "較強凍雨",
    71: "小雪",
    73: "雪",
    75: "大雪",
    77: "雪粒",
    80: "短暫小雨",
    81: "短暫陣雨",
    82: "強陣雨",
    85: "短暫小雪",
    86: "強陣雪",
    95: "雷雨",
    96: "雷雨伴隨小冰雹",
    99: "雷雨伴隨冰雹",
}

WEEKDAY_MAP = {
    "一": 0,
    "1": 0,
    "二": 1,
    "2": 1,
    "三": 2,
    "3": 2,
    "四": 3,
    "4": 3,
    "五": 4,
    "5": 4,
    "六": 5,
    "6": 5,
    "日": 6,
    "天": 6,
    "7": 6,
}


@dataclass
class MusicIntent:
    intent: bool
    action: str = "none"
    query: str = ""
    reason: str = ""
    normalized_text: str = ""


@dataclass
class WeatherIntent:
    intent: bool
    location: str = ""
    reason: str = ""
    normalized_text: str = ""


def normalize_text(text: str | None) -> str:
    value = str(text or "").strip()
    value = re.sub(r"[，。！？、；：,.!?;:()\[\]{}\"'`《》〈〉「」『』]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def strip_wake_words(text: str) -> str:
    value = normalize_text(text)
    for pattern in WAKE_WORD_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    return normalize_text(value)


def clean_query(query: str) -> str:
    value = normalize_text(query)
    value = re.sub(r"^(一下|一首|首|個|个|點|点)\s*", "", value)
    for filler in TRAILING_FILLERS:
        value = re.sub(rf"\s*{re.escape(filler)}\s*$", "", value, flags=re.IGNORECASE)
    value = re.split(r"\s*(?:然後|然后|順便|顺便|and then)\s*", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = normalize_text(value)
    if value in {"歌", "音樂", "音乐", "歌曲", "music", "song", "songs"}:
        return "music"
    return value


def looks_like_audio_complaint(text: str) -> bool:
    lowered = text.lower()
    if any(word in lowered for word in EXPLICIT_MUSIC_REQUEST_WORDS):
        return False
    return any(word in lowered for word in AUDIO_COMPLAINT_WORDS)


def detect_music_intent(text: str | None) -> MusicIntent:
    normalized = strip_wake_words(text or "")
    lowered = normalized.lower()
    if not normalized:
        return MusicIntent(False, reason="empty", normalized_text="")

    for pattern in STOP_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return MusicIntent(True, action="stop", reason=f"stop:{pattern}", normalized_text=normalized)

    for pattern in PAUSE_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return MusicIntent(True, action="pause", reason=f"pause:{pattern}", normalized_text=normalized)

    for pattern in RESUME_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return MusicIntent(True, action="resume", reason=f"resume:{pattern}", normalized_text=normalized)

    if looks_like_audio_complaint(normalized):
        return MusicIntent(False, reason="audio_complaint_not_music", normalized_text=normalized)

    for pattern in CN_PLAY_PATTERNS + EN_PLAY_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            query = clean_query(match.groupdict().get("query", ""))
            if not query and re.search(r"(來一首|来一首|放點|放点)", normalized):
                query = "music"
            return MusicIntent(
                True,
                action="play",
                query=query,
                reason=f"play:{pattern}",
                normalized_text=normalized,
            )

    music_words = ("音樂", "音乐", "歌曲", "聽歌", "听歌", "music", "song")
    play_words = ("播放", "播", "放", "聽", "听", "play", "listen")
    if any(word in lowered for word in music_words) and any(word in lowered for word in play_words):
        return MusicIntent(True, action="play", query="music", reason="implicit_music_play", normalized_text=normalized)

    return MusicIntent(False, reason="no_music_intent", normalized_text=normalized)


def _clean_weather_location(value: str, default_location: str) -> str:
    cleaned = normalize_text(value)
    cleaned = re.sub(r"\d{1,2}\s*(?:點|点|時|时|am|pm)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(今天|明天|後天|后天|現在|现在|目前|等一下|等等|早上|上午|中午|下午|晚上|今晚|明早|明天早上|"
        r"天氣|天气|氣溫|气温|溫度|温度|幾度|几度|會不會|会不会|會|会|下雨|降雨|冷不冷|熱不熱|热不热|"
        r"today|tomorrow|tonight|morning|afternoon|evening|weather|forecast|temperature|rain|umbrella|in|for|at)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = normalize_text(cleaned)
    if not cleaned:
        cleaned = default_location
    return WEATHER_LOCATION_ALIASES.get(cleaned, cleaned)


def extract_weather_location(text: str, default_location: str = DEFAULT_WEATHER_LOCATION) -> str:
    normalized = strip_wake_words(text)
    lowered = normalized.lower()
    if any(word in lowered for word in LOCAL_LOCATION_WORDS):
        return default_location

    for alias, canonical in WEATHER_LOCATION_ALIASES.items():
        if alias.lower() in lowered:
            return canonical

    english_patterns = (
        r"\b(?:weather|forecast|temperature|rain)\s+(?:in|for|at)\s+(?P<location>[A-Za-z][A-Za-z\s.-]{1,40})",
        r"\b(?:in|for|at)\s+(?P<location>[A-Za-z][A-Za-z\s.-]{1,40})\s+(?:weather|forecast|temperature|rain)\b",
    )
    for pattern in english_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return _clean_weather_location(match.group("location"), default_location)

    chinese_patterns = (
        r"(?:查|看|問|问|告訴我|告诉我)?\s*(?P<location>[\u4e00-\u9fffA-Za-z\s.-]{2,24})\s*(?:的)?(?:天氣|天气|氣溫|气温|溫度|温度)",
        r"(?:天氣|天气|氣溫|气温|溫度|温度).*?(?:在|到|去)\s*(?P<location>[\u4e00-\u9fffA-Za-z\s.-]{2,24})",
    )
    for pattern in chinese_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return _clean_weather_location(match.group("location"), default_location)

    return default_location


def detect_weather_intent(text: str | None, *, default_location: str = DEFAULT_WEATHER_LOCATION) -> WeatherIntent:
    normalized = strip_wake_words(text or "")
    lowered = normalized.lower()
    if not normalized:
        return WeatherIntent(False, reason="empty", normalized_text="")
    if any(keyword in lowered for keyword in WEATHER_KEYWORDS):
        location = extract_weather_location(normalized, default_location=default_location)
        return WeatherIntent(True, location=location, reason="weather_keyword", normalized_text=normalized)
    if re.search(r"(今天|明天|後天|后天|今晚|明早).{0,8}(冷|熱|热|雨|傘|伞|幾度|几度)", normalized):
        location = extract_weather_location(normalized, default_location=default_location)
        return WeatherIntent(True, location=location, reason="implicit_weather_time_condition", normalized_text=normalized)
    return WeatherIntent(False, location="", reason="no_weather_intent", normalized_text=normalized)


def _http_get_json(url: str, *, timeout_sec: float) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError(f"GET {url} did not return a JSON object")
    return parsed


def _weather_cache_get(cache: dict[Any, tuple[float, dict[str, Any]]], key: Any) -> dict[str, Any] | None:
    ttl = max(0.0, DEFAULT_WEATHER_CACHE_TTL_SEC)
    if ttl <= 0.0:
        return None
    now = time.monotonic()
    with _WEATHER_CACHE_LOCK:
        item = cache.get(key)
        if item is None:
            return None
        saved_at, value = item
        if now - saved_at > ttl:
            cache.pop(key, None)
            return None
        return dict(value)


def _weather_cache_set(cache: dict[Any, tuple[float, dict[str, Any]]], key: Any, value: dict[str, Any]) -> None:
    ttl = max(0.0, DEFAULT_WEATHER_CACHE_TTL_SEC)
    if ttl <= 0.0:
        return
    with _WEATHER_CACHE_LOCK:
        cache[key] = (time.monotonic(), dict(value))


def geocode_location(location: str, *, language: str = DEFAULT_WEATHER_LANGUAGE, timeout_sec: float = DEFAULT_WEATHER_TIMEOUT_SEC) -> dict[str, Any]:
    query = WEATHER_LOCATION_ALIASES.get(normalize_text(location), normalize_text(location) or DEFAULT_WEATHER_LOCATION)
    cache_key = (query.lower(), language)
    cached = _weather_cache_get(_WEATHER_GEOCODE_CACHE, cache_key)
    if cached is not None:
        cached["cache_hit"] = True
        return cached
    params = urllib.parse.urlencode({"name": query, "count": 1, "language": language, "format": "json"})
    url = f"{OPEN_METEO_GEOCODING_URL}?{params}"
    data = _http_get_json(url, timeout_sec=timeout_sec)
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"找不到地點：{location}")
    first = results[0]
    if not isinstance(first, dict):
        raise RuntimeError(f"地點資料格式不正確：{location}")
    result = {
        "name": first.get("name") or query,
        "country": first.get("country") or "",
        "admin1": first.get("admin1") or "",
        "latitude": float(first["latitude"]),
        "longitude": float(first["longitude"]),
        "timezone": first.get("timezone") or "auto",
        "query": query,
        "geocoding_url": url,
    }
    _weather_cache_set(_WEATHER_GEOCODE_CACHE, cache_key, result)
    return result


def _safe_zone_now(timezone_name: str) -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except Exception:
            pass
    return datetime.now()


def parse_weather_target(text: str | None, *, now: datetime | None = None) -> dict[str, Any]:
    normalized = strip_wake_words(text or "")
    lowered = normalized.lower()
    current_now = now or datetime.now()
    day_offset = 0
    explicit_day = False
    label = "現在"

    if re.search(r"後天|后天|day after tomorrow", lowered, flags=re.IGNORECASE):
        day_offset = 2
        explicit_day = True
        label = "後天"
    elif re.search(r"明天|tomorrow", lowered, flags=re.IGNORECASE):
        day_offset = 1
        explicit_day = True
        label = "明天"
    elif re.search(r"今天|今日|today", lowered, flags=re.IGNORECASE):
        day_offset = 0
        explicit_day = True
        label = "今天"

    weekday_match = re.search(r"(?P<next>下)?(?:星期|禮拜|礼拜|週|周)(?P<day>[一二三四五六日天1-7])", normalized)
    if weekday_match:
        wanted = WEEKDAY_MAP[weekday_match.group("day")]
        day_offset = (wanted - current_now.weekday()) % 7
        if day_offset == 0 or weekday_match.group("next"):
            day_offset += 7
        explicit_day = True
        label = f"{weekday_match.group(0)}"

    hour: int | None = None
    minute = 0
    exact_time = False
    zh_hour_match = re.search(r"(?P<hour>\d{1,2})\s*(?:點|点|時|时)", normalized)
    en_hour_match = re.search(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\b", lowered, flags=re.IGNORECASE)
    if zh_hour_match:
        hour = int(zh_hour_match.group("hour"))
        exact_time = True
    elif en_hour_match:
        hour = int(en_hour_match.group("hour"))
        minute = int(en_hour_match.group("minute") or 0)
        ampm = (en_hour_match.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        exact_time = True

    if hour is not None and re.search(r"下午|晚上|今晚|傍晚", normalized) and hour < 12:
        hour += 12
    if hour is None:
        if re.search(r"明早|早上|上午|morning", lowered, flags=re.IGNORECASE):
            hour = 8
        elif re.search(r"中午|noon", lowered, flags=re.IGNORECASE):
            hour = 12
        elif re.search(r"下午|afternoon", lowered, flags=re.IGNORECASE):
            hour = 15
        elif re.search(r"傍晚|evening", lowered, flags=re.IGNORECASE):
            hour = 18
        elif re.search(r"今晚|晚上|tonight", lowered, flags=re.IGNORECASE):
            hour = 20

    if hour is not None:
        hour = max(0, min(23, hour))
        target_dt = (current_now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return {
            "kind": "hourly",
            "label": f"{label}{hour:02d}:00" if exact_time else f"{label}約{hour:02d}:00",
            "date": target_dt.date().isoformat(),
            "hour": hour,
            "datetime": target_dt.strftime("%Y-%m-%dT%H:00"),
            "explicit_day": explicit_day,
        }

    target_date = (current_now + timedelta(days=day_offset)).date().isoformat()
    if explicit_day:
        return {"kind": "daily", "label": label, "date": target_date, "explicit_day": True}
    return {"kind": "current", "label": "現在", "date": target_date, "explicit_day": False}


def fetch_open_meteo_forecast(location_info: dict[str, Any], *, timeout_sec: float = DEFAULT_WEATHER_TIMEOUT_SEC) -> dict[str, Any]:
    cache_key = (round(float(location_info["latitude"]), 4), round(float(location_info["longitude"]), 4))
    cached = _weather_cache_get(_WEATHER_FORECAST_CACHE, cache_key)
    if cached is not None:
        cached["cache_hit"] = True
        return cached
    params = urllib.parse.urlencode(
        {
            "latitude": location_info["latitude"],
            "longitude": location_info["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "hourly": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "precipitation_probability",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                    "precipitation_sum",
                ]
            ),
            "timezone": "auto",
            "forecast_days": 7,
        }
    )
    url = f"{OPEN_METEO_FORECAST_URL}?{params}"
    data = _http_get_json(url, timeout_sec=timeout_sec)
    data["_request_url"] = url
    _weather_cache_set(_WEATHER_FORECAST_CACHE, cache_key, data)
    return data


def weather_code_text(code: Any) -> str:
    try:
        return WEATHER_CODE_ZH.get(int(code), f"天氣代碼 {code}")
    except Exception:
        return "天氣狀態未知"


def _first_available(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _fmt_number(value: Any, unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "未知"
    try:
        number = float(value)
        if digits == 0:
            text = str(int(round(number)))
        else:
            text = f"{number:.{digits}f}"
        return text + unit
    except Exception:
        return str(value) + unit


def _location_label(location_info: dict[str, Any]) -> str:
    parts = [str(location_info.get("name") or location_info.get("query") or "").strip()]
    admin1 = str(location_info.get("admin1") or "").strip()
    country = str(location_info.get("country") or "").strip()
    if admin1 and " or " not in admin1.lower() and admin1 not in parts:
        parts.append(admin1)
    if country and country not in parts:
        parts.append(country)
    return "、".join(part for part in parts if part) or "所在地"


def _hourly_row(forecast: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    hourly = forecast.get("hourly") if isinstance(forecast.get("hourly"), dict) else {}
    times = hourly.get("time") if isinstance(hourly.get("time"), list) else []
    if not times:
        raise RuntimeError("Open-Meteo hourly forecast is empty")
    target_dt = str(target.get("datetime") or "")
    if target_dt in times:
        idx = times.index(target_dt)
    else:
        target_date = str(target.get("date") or "")
        candidates = [i for i, value in enumerate(times) if str(value).startswith(target_date)]
        if not candidates:
            idx = 0
        else:
            wanted_hour = int(target.get("hour") or 0)
            idx = min(candidates, key=lambda i: abs(int(str(times[i])[11:13]) - wanted_hour))
    return {key: values[idx] for key, values in hourly.items() if isinstance(values, list) and idx < len(values)}


def _daily_row(forecast: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    daily = forecast.get("daily") if isinstance(forecast.get("daily"), dict) else {}
    times = daily.get("time") if isinstance(daily.get("time"), list) else []
    if not times:
        raise RuntimeError("Open-Meteo daily forecast is empty")
    target_date = str(target.get("date") or "")
    idx = times.index(target_date) if target_date in times else 0
    return {key: values[idx] for key, values in daily.items() if isinstance(values, list) and idx < len(values)}


def build_weather_reply(location_info: dict[str, Any], forecast: dict[str, Any], target: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    location = _location_label(location_info)
    kind = target.get("kind")
    if kind == "current":
        current = forecast.get("current") if isinstance(forecast.get("current"), dict) else {}
        condition = weather_code_text(current.get("weather_code"))
        temp = _fmt_number(current.get("temperature_2m"), "°C")
        feels = _fmt_number(current.get("apparent_temperature"), "°C")
        humidity = _fmt_number(current.get("relative_humidity_2m"), "%")
        wind = _fmt_number(current.get("wind_speed_10m"), " km/h")
        rain = _fmt_number(current.get("precipitation"), " mm", digits=1)
        reply = f"{location}現在約 {temp}，體感 {feels}，{condition}，濕度 {humidity}，風速 {wind}，目前降雨量 {rain}。"
        return reply, {
            "kind": "current",
            "condition": condition,
            "weather_code": current.get("weather_code"),
            "temperature_c": current.get("temperature_2m"),
            "precipitation_probability": 0,
        }

    if kind == "hourly":
        row = _hourly_row(forecast, target)
        condition = weather_code_text(row.get("weather_code"))
        temp = _fmt_number(row.get("temperature_2m"), "°C")
        feels = _fmt_number(row.get("apparent_temperature"), "°C")
        pop = _fmt_number(row.get("precipitation_probability"), "%")
        rain = _fmt_number(row.get("precipitation"), " mm", digits=1)
        wind = _fmt_number(row.get("wind_speed_10m"), " km/h")
        reply = f"{location}{target.get('label')}預報約 {temp}，體感 {feels}，{condition}，降雨機率 {pop}，預估雨量 {rain}，風速 {wind}。"
        return reply, {
            "kind": "hourly",
            "condition": condition,
            "weather_code": row.get("weather_code"),
            "temperature_c": row.get("temperature_2m"),
            "precipitation_probability": row.get("precipitation_probability"),
        }

    row = _daily_row(forecast, target)
    condition = weather_code_text(row.get("weather_code"))
    min_temp = _fmt_number(row.get("temperature_2m_min"), "°C")
    max_temp = _fmt_number(row.get("temperature_2m_max"), "°C")
    pop = _fmt_number(row.get("precipitation_probability_max"), "%")
    rain = _fmt_number(row.get("precipitation_sum"), " mm", digits=1)
    reply = f"{location}{target.get('label')}大約 {min_temp} 到 {max_temp}，{condition}，最高降雨機率 {pop}，累積雨量約 {rain}。"
    return reply, {
        "kind": "daily",
        "condition": condition,
        "weather_code": row.get("weather_code"),
        "temperature_min_c": row.get("temperature_2m_min"),
        "temperature_max_c": row.get("temperature_2m_max"),
        "precipitation_probability_max": row.get("precipitation_probability_max"),
    }


def handle_weather_text(
    text: str,
    *,
    default_location: str = DEFAULT_WEATHER_LOCATION,
    language: str = DEFAULT_WEATHER_LANGUAGE,
    timeout_sec: float = DEFAULT_WEATHER_TIMEOUT_SEC,
) -> dict[str, Any]:
    started = time.monotonic()
    timeout_sec = max(0.8, float(timeout_sec or DEFAULT_WEATHER_TIMEOUT_SEC))

    def remaining_timeout(*, floor: float = 0.6) -> float:
        return max(floor, timeout_sec - (time.monotonic() - started))

    intent = detect_weather_intent(text, default_location=default_location)
    result: dict[str, Any] = {
        "ok": True,
        "handled": False,
        "intent": intent.intent,
        "action": "weather" if intent.intent else "none",
        "location": intent.location,
        "reason": intent.reason,
        "normalized_text": intent.normalized_text,
    }
    if not intent.intent:
        result["message"] = "not a weather request"
        return result

    location_info = geocode_location(intent.location or default_location, language=language, timeout_sec=remaining_timeout())
    forecast = fetch_open_meteo_forecast(location_info, timeout_sec=remaining_timeout())
    now = _safe_zone_now(str(forecast.get("timezone") or location_info.get("timezone") or "UTC"))
    target = parse_weather_target(text, now=now)
    reply, summary = build_weather_reply(location_info, forecast, target)
    result.update(
        {
            "ok": True,
            "handled": True,
            "reply": reply,
            "source": "open-meteo",
            "location": _location_label(location_info),
            "location_query": location_info.get("query"),
            "latitude": location_info.get("latitude"),
            "longitude": location_info.get("longitude"),
            "timezone": forecast.get("timezone") or location_info.get("timezone"),
            "target": target,
            "weather": summary,
            "forecast_url": forecast.get("_request_url"),
            "geocoding_url": location_info.get("geocoding_url"),
            "cache": {
                "geocode": bool(location_info.get("cache_hit")),
                "forecast": bool(forecast.get("cache_hit")),
                "ttl_sec": DEFAULT_WEATHER_CACHE_TTL_SEC,
            },
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    )
    return result


def youtube_music_search_url(query: str) -> str:
    return YOUTUBE_MUSIC_SEARCH_URL + urllib.parse.quote_plus(query)


def youtube_search_url(query: str) -> str:
    return YOUTUBE_SEARCH_URL + urllib.parse.quote_plus(query)


def list_mpv_audio_devices(*, timeout_sec: float = 2.0) -> list[tuple[str, str]]:
    mpv = shutil.which("mpv")
    if not mpv:
        return []
    try:
        completed = subprocess.run(
            [mpv, "--audio-device=help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except Exception:
        return []
    devices: list[tuple[str, str]] = []
    for line in f"{completed.stdout}\n{completed.stderr}".splitlines():
        match = re.match(r"\s*'([^']+)'\s+\((.*)\)\s*$", line)
        if match:
            devices.append((match.group(1).strip(), match.group(2).strip()))
    return devices


def uacdemo_alsa_card_names(*, keyword: str = DEFAULT_MPV_AUDIO_DEVICE_KEYWORD) -> list[str]:
    try:
        cards_text = open("/proc/asound/cards", encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    names: list[str] = []
    pattern = re.compile(r"^\s*\d+\s+\[([^\]]+)\]\s*:\s*(.*)$")
    keyword_lower = keyword.lower()
    for line in cards_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        card_name = match.group(1).strip()
        description = match.group(2).strip()
        if keyword_lower in card_name.lower() or keyword_lower in description.lower():
            names.append(card_name)
    return names


def resolve_mpv_audio_device(requested: str | None, *, keyword: str = DEFAULT_MPV_AUDIO_DEVICE_KEYWORD) -> str:
    value = str(requested or "").strip()
    lowered = value.lower()
    if lowered in {"", "default", "system", "none", "off"}:
        return ""
    if lowered not in {"auto", "auto-uacdemo", "uacdemo"}:
        return value

    devices = list_mpv_audio_devices()
    keyword_lower = keyword.lower()

    def matches(identifier: str, label: str) -> bool:
        return keyword_lower in identifier.lower() or keyword_lower in label.lower()

    for prefix in ("pulse/", "alsa/plughw:", "alsa/sysdefault:", "alsa/dmix:"):
        for identifier, label in devices:
            if identifier.startswith(prefix) and matches(identifier, label):
                return identifier
    for identifier, label in devices:
        if matches(identifier, label):
            return identifier
    for card_name in uacdemo_alsa_card_names(keyword=keyword):
        return f"alsa/plughw:CARD={card_name},DEV=0"
    return ""


class MusicPlayer:
    def __init__(
        self,
        *,
        backend: str = DEFAULT_BACKEND,
        dry_run: bool = False,
        mpv_audio_device: str = DEFAULT_MPV_AUDIO_DEVICE,
        mpv_audio_keyword: str = DEFAULT_MPV_AUDIO_DEVICE_KEYWORD,
        mpv_ytdl_cookies: str = DEFAULT_MPV_YTDL_COOKIES,
        mpv_ytdl_cookies_from_browser: str = DEFAULT_MPV_YTDL_COOKIES_FROM_BROWSER,
        mpv_volume: int = DEFAULT_MPV_VOLUME,
        mpv_ready_timeout: float = DEFAULT_MPV_READY_TIMEOUT_SEC,
    ) -> None:
        self.backend = backend
        self.dry_run = dry_run
        self.requested_mpv_audio_device = str(mpv_audio_device or "").strip()
        self.mpv_audio_keyword = str(mpv_audio_keyword or DEFAULT_MPV_AUDIO_DEVICE_KEYWORD).strip() or DEFAULT_MPV_AUDIO_DEVICE_KEYWORD
        self.mpv_audio_device = resolve_mpv_audio_device(self.requested_mpv_audio_device, keyword=self.mpv_audio_keyword)
        self.mpv_ytdl_cookies = str(mpv_ytdl_cookies or "").strip()
        self.mpv_ytdl_cookies_from_browser = str(mpv_ytdl_cookies_from_browser or "").strip()
        self.mpv_volume = max(0, min(150, int(mpv_volume)))
        self.mpv_ready_timeout = max(0.0, float(mpv_ready_timeout))
        self._lock = threading.RLock()
        self._process: subprocess.Popen[Any] | None = None
        self._ipc_path: str | None = None
        self._paused = False
        self.last_query = ""
        self.last_backend = ""
        self.last_title = ""
        self.last_artist = ""
        self.last_url = ""

    def _remove_ipc_socket(self) -> None:
        if self._ipc_path:
            try:
                os.unlink(self._ipc_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass

    def _clear_finished_locked(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self._process = None
            self._paused = False
            self._remove_ipc_socket()
            self._ipc_path = None

    def _is_active_locked(self) -> bool:
        self._clear_finished_locked()
        return self._process is not None and self._process.poll() is None

    def _mpv_command(self, command: list[Any], *, timeout: float = 0.6) -> dict[str, Any]:
        with self._lock:
            if not self._is_active_locked():
                return {"ok": False, "error": "no active mpv process"}
            ipc_path = self._ipc_path
        if not ipc_path:
            return {"ok": False, "error": "mpv IPC socket unavailable"}

        payload = json.dumps({"command": command}, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(ipc_path)
                sock.sendall(payload)
                chunks: list[bytes] = []
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
            raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
            if not raw:
                return {"ok": True, "mpv_error": None}
            response: dict[str, Any] | None = None
            for line in raw.splitlines():
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and "error" in candidate:
                    response = candidate
                    break
            if response is None:
                response = json.loads(raw.splitlines()[0])
            error = response.get("error")
            return {"ok": error in (None, "success"), "mpv_error": error, "mpv_response": response}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _mpv_property(self, name: str, *, timeout: float = 0.6) -> Any:
        result = self._mpv_command(["get_property", name], timeout=timeout)
        if not result.get("ok"):
            return None
        response = result.get("mpv_response") if isinstance(result.get("mpv_response"), dict) else {}
        return response.get("data")

    def _resolve_audio_device_for_play(self) -> str:
        lowered = self.requested_mpv_audio_device.lower()
        if lowered in {"auto", "auto-uacdemo", "uacdemo"}:
            self.mpv_audio_device = resolve_mpv_audio_device(
                self.requested_mpv_audio_device,
                keyword=self.mpv_audio_keyword,
            )
        return self.mpv_audio_device

    def _mpv_playback_snapshot(self) -> dict[str, Any]:
        snapshot = {
            "title": self._mpv_property("media-title"),
            "url": self._mpv_property("path"),
            "pause": self._mpv_property("pause"),
            "core_idle": self._mpv_property("core-idle"),
            "eof_reached": self._mpv_property("eof-reached"),
            "audio_params": self._mpv_property("audio-params"),
            "audio_out": self._mpv_property("audio-out-params"),
            "volume": self._mpv_property("volume"),
        }
        return snapshot

    def _wait_for_mpv_playback(self, *, wait_sec: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, wait_sec)
        latest: dict[str, Any] = {}
        while True:
            with self._lock:
                active = self._is_active_locked()
                returncode = self._process.poll() if self._process is not None else None
            if not active:
                latest["playback_ready"] = False
                latest["mpv_returncode"] = returncode
                return latest
            latest = self._mpv_playback_snapshot()
            if latest.get("audio_out"):
                latest["playback_ready"] = True
                return latest
            if latest.get("title") and latest.get("eof_reached") is not True:
                latest["playback_ready"] = True
                return latest
            if time.monotonic() >= deadline:
                latest["playback_ready"] = False
                return latest
            time.sleep(0.15)

    def _refresh_now_playing_once(self) -> dict[str, Any]:
        title = self._mpv_property("media-title")
        artist = (
            self._mpv_property("metadata/by-key/artist")
            or self._mpv_property("metadata/by-key/uploader")
            or self._mpv_property("metadata/by-key/channel")
        )
        url = self._mpv_property("path")
        with self._lock:
            if title:
                self.last_title = str(title).strip()
            if artist:
                self.last_artist = str(artist).strip()
            if url:
                self.last_url = str(url).strip()
            return {
                "title": self.last_title,
                "artist": self.last_artist,
                "url": self.last_url,
            }

    def _refresh_now_playing(self, *, wait_sec: float = 0.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, wait_sec)
        latest: dict[str, Any] = {}
        while True:
            latest = self._refresh_now_playing_once()
            if latest.get("title") or time.monotonic() >= deadline:
                return latest
            time.sleep(0.15)

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._is_active_locked()
        playback: dict[str, Any] = {}
        if active:
            self._refresh_now_playing()
            playback = self._mpv_playback_snapshot()
        with self._lock:
            active = self._is_active_locked()
            return {
                "active": active,
                "paused": bool(self._paused and active),
                "last_query": self.last_query,
                "last_backend": self.last_backend,
                "last_title": self.last_title,
                "title": self.last_title if active else "",
                "artist": self.last_artist if active else "",
                "url": self.last_url if active else "",
                "ipc_path": self._ipc_path if active else None,
                "mpv_audio_device": self.mpv_audio_device or "system-default",
                "requested_mpv_audio_device": self.requested_mpv_audio_device or "default",
                "mpv_audio_keyword": self.mpv_audio_keyword,
                "mpv_ytdl_cookies": self.mpv_ytdl_cookies,
                "mpv_ytdl_cookies_configured": bool(self.mpv_ytdl_cookies),
                "mpv_ytdl_cookies_from_browser": self.mpv_ytdl_cookies_from_browser,
                "mpv_volume": self.mpv_volume,
                "playback_ready": bool(playback.get("audio_out") or playback.get("title")) if active else False,
                "audio_out": playback.get("audio_out") if active else None,
                "audio_params": playback.get("audio_params") if active else None,
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._is_active_locked():
                self._process = None
                self._paused = False
                self._remove_ipc_socket()
                self._ipc_path = None
                return {"ok": True, "action": "stop", "stopped": False, "message": "no active mpv process"}
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2.0)
            self._process = None
            self._paused = False
            self._remove_ipc_socket()
            self._ipc_path = None
            return {"ok": True, "action": "stop", "stopped": True}

    def pause(self) -> dict[str, Any]:
        result = self._mpv_command(["set_property", "pause", True])
        if result.get("ok"):
            with self._lock:
                self._paused = True
            result.update({"action": "pause", "paused": True, "stopped": False, "message": "mpv paused"})
            return result
        if result.get("error") == "no active mpv process":
            return {
                "ok": True,
                "action": "pause",
                "paused": False,
                "stopped": False,
                "message": "no active mpv process",
            }
        result.update({"action": "pause", "paused": False, "stopped": False})
        return result

    def resume(self) -> dict[str, Any]:
        result = self._mpv_command(["set_property", "pause", False])
        if result.get("ok"):
            with self._lock:
                self._paused = False
            result.update({"action": "resume", "resumed": True, "paused": False, "message": "mpv resumed"})
            return result
        result.update({"action": "resume", "resumed": False})
        return result

    def play(self, query: str, *, backend: str | None = None, dry_run: bool | None = None) -> dict[str, Any]:
        query = clean_query(query)
        selected_backend = (backend or self.backend or "browser").strip().lower()
        selected_dry_run = self.dry_run if dry_run is None else dry_run
        if not query:
            return {"ok": False, "action": "play", "error": "missing song query"}
        if selected_backend not in {"browser", "mpv"}:
            return {"ok": False, "action": "play", "error": f"unsupported backend: {selected_backend}"}

        if selected_dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "action": "play",
                "query": query,
                "backend": selected_backend,
                "url": youtube_music_search_url(query),
            }

        if selected_backend == "mpv":
            return self._play_with_mpv(query)
        return self._open_browser_search(query)

    def _open_browser_search(self, query: str) -> dict[str, Any]:
        url = youtube_music_search_url(query)
        opener = shutil.which("xdg-open")
        with self._lock:
            self.last_query = query
            self.last_backend = "browser"
            self.last_title = ""
            self.last_artist = ""
            self.last_url = url
        try:
            if opener:
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                webbrowser.open(url, new=2)
            return {
                "ok": True,
                "action": "play",
                "backend": "browser",
                "query": query,
                "url": url,
                "message": "opened YouTube Music search in browser",
            }
        except Exception as exc:
            fallback_url = youtube_search_url(query)
            try:
                webbrowser.open(fallback_url, new=2)
                return {
                    "ok": True,
                    "action": "play",
                    "backend": "browser",
                    "query": query,
                    "url": fallback_url,
                    "warning": f"YouTube Music open failed, opened YouTube search instead: {exc}",
                }
            except Exception as fallback_exc:
                return {"ok": False, "action": "play", "backend": "browser", "query": query, "error": str(fallback_exc)}

    def _play_with_mpv(self, query: str) -> dict[str, Any]:
        mpv = shutil.which("mpv")
        if not mpv:
            return {"ok": False, "action": "play", "backend": "mpv", "query": query, "error": "mpv not found"}
        if not (shutil.which("yt-dlp") or shutil.which("youtube-dl")):
            return {
                "ok": False,
                "action": "play",
                "backend": "mpv",
                "query": query,
                "error": "yt-dlp/youtube-dl not found. Install yt-dlp or use --backend browser.",
            }

        audio_device = self._resolve_audio_device_for_play()
        cookies_path = str(Path(self.mpv_ytdl_cookies).expanduser()) if self.mpv_ytdl_cookies else ""
        cookies_from_browser = self.mpv_ytdl_cookies_from_browser
        if cookies_path and not Path(cookies_path).exists():
            return {
                "ok": False,
                "action": "play",
                "backend": "mpv",
                "query": query,
                "error": f"cookies file not found: {cookies_path}",
            }

        self.stop()
        target = f"ytdl://ytsearch1:{query}"
        ipc_path = f"/tmp/makentu_music_web_player_{os.getpid()}.sock"
        try:
            os.unlink(ipc_path)
        except FileNotFoundError:
            pass
        except Exception:
            pass
        command = [
            mpv,
            "--no-video",
            "--force-window=no",
            "--msg-level=all=warn",
            f"--input-ipc-server={ipc_path}",
            "--term-playing-msg=Now playing: ${media-title}",
        ]
        if audio_device:
            command.append(f"--audio-device={audio_device}")
        command.append(f"--volume={self.mpv_volume}")
        if cookies_path:
            command.extend(
                [
                    "--cookies",
                    f"--cookies-file={cookies_path}",
                    f"--ytdl-raw-options=cookies={cookies_path}",
                ]
            )
        elif cookies_from_browser:
            command.append(f"--ytdl-raw-options=cookies-from-browser={cookies_from_browser}")
        command.append(target)
        try:
            with self._lock:
                self._process = subprocess.Popen(command)
                self._ipc_path = ipc_path
                self._paused = False
                self.last_query = query
                self.last_backend = "mpv"
                self.last_title = ""
                self.last_artist = ""
                self.last_url = target
            deadline = time.monotonic() + max(0.3, self.mpv_ready_timeout)
            while time.monotonic() < deadline and not os.path.exists(ipc_path):
                with self._lock:
                    if self._process is None or self._process.poll() is not None:
                        break
                time.sleep(0.05)
            ipc_ready = os.path.exists(ipc_path)
            playback = self._wait_for_mpv_playback(wait_sec=self.mpv_ready_timeout) if ipc_ready else {}
            now_playing = self._refresh_now_playing(wait_sec=0.2) if ipc_ready else {}
            result = {
                "ok": True,
                "action": "play",
                "backend": "mpv",
                "query": query,
                "target": target,
                "audio_device": audio_device or "system-default",
                "requested_audio_device": self.requested_mpv_audio_device or "default",
                "cookies": cookies_path,
                "cookies_configured": bool(cookies_path),
                "cookies_from_browser": "" if cookies_path else cookies_from_browser,
                "volume": self.mpv_volume,
                "ipc_path": ipc_path,
                "ipc_ready": ipc_ready,
                "playback_ready": playback.get("playback_ready") if ipc_ready else False,
                "audio_out": playback.get("audio_out") if ipc_ready else None,
                "audio_params": playback.get("audio_params") if ipc_ready else None,
                "title": now_playing.get("title") or playback.get("title") or "",
                "artist": now_playing.get("artist") or "",
                "url": now_playing.get("url") or playback.get("url") or target,
            }
            if not ipc_ready:
                result["warning"] = "mpv IPC socket was not ready yet; pause/resume may fail until mpv finishes starting"
            elif not result["playback_ready"]:
                result["warning"] = "mpv started but did not expose playback audio status before timeout; check Terminal 4 for yt-dlp/YouTube errors"
            return result
        except Exception as exc:
            return {"ok": False, "action": "play", "backend": "mpv", "query": query, "error": str(exc)}


def handle_text(player: MusicPlayer, text: str, *, backend: str | None = None, dry_run: bool | None = None) -> dict[str, Any]:
    intent = detect_music_intent(text)
    result: dict[str, Any] = {
        "intent": intent.intent,
        "action": intent.action,
        "query": intent.query,
        "reason": intent.reason,
        "normalized_text": intent.normalized_text,
    }
    if not intent.intent:
        result.update({"ok": True, "handled": False, "message": "not a music request"})
        return result

    if intent.action == "stop":
        result.update(player.stop())
        result["handled"] = True
        return result
    if intent.action == "pause":
        result.update(player.pause())
        result["handled"] = True
        return result
    if intent.action == "resume":
        result.update(player.resume())
        result["handled"] = bool(result.get("ok"))
        return result
    if intent.action == "play":
        play_result = player.play(intent.query, backend=backend, dry_run=dry_run)
        result.update(play_result)
        result["handled"] = bool(play_result.get("ok"))
        return result

    result.update({"ok": False, "handled": False, "error": f"unknown action: {intent.action}"})
    return result


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return False
    return True


def make_handler(
    player: MusicPlayer,
    *,
    default_backend: str,
    default_dry_run: bool,
    weather_default_location: str,
    weather_language: str,
    weather_timeout_sec: float = DEFAULT_WEATHER_TIMEOUT_SEC,
) -> type[BaseHTTPRequestHandler]:
    class MusicHandler(BaseHTTPRequestHandler):
        server_version = "MakeNTUMusicTool/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            if self.path.startswith("/health"):
                return
            print(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:
            if self.path.startswith("/health"):
                status = player.status()
                json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "make_ntu_music_web_player",
                        "backend": default_backend,
                        "dry_run": default_dry_run,
                        "mpv_available": bool(shutil.which("mpv")),
                        "yt_dlp_available": bool(shutil.which("yt-dlp") or shutil.which("youtube-dl")),
                        "last_query": player.last_query,
                        "last_backend": player.last_backend,
                        "last_title": status.get("last_title", ""),
                        "title": status.get("title", ""),
                        "artist": status.get("artist", ""),
                        "url": status.get("url", ""),
                        "active": status["active"],
                        "paused": status["paused"],
                        "ipc_path": status["ipc_path"],
                        "mpv_audio_device": status.get("mpv_audio_device"),
                        "requested_mpv_audio_device": status.get("requested_mpv_audio_device"),
                        "mpv_audio_keyword": status.get("mpv_audio_keyword"),
                        "mpv_ytdl_cookies": status.get("mpv_ytdl_cookies"),
                        "mpv_ytdl_cookies_configured": status.get("mpv_ytdl_cookies_configured"),
                        "mpv_ytdl_cookies_from_browser": status.get("mpv_ytdl_cookies_from_browser"),
                        "mpv_volume": status.get("mpv_volume"),
                        "playback_ready": status.get("playback_ready"),
                        "audio_out": status.get("audio_out"),
                        "audio_params": status.get("audio_params"),
                        "weather_available": True,
                        "weather_source": "open-meteo",
                        "weather_default_location": weather_default_location,
                    },
                )
                return
            json_response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if not (self.path.startswith("/music") or self.path.startswith("/weather")):
                json_response(self, 404, {"ok": False, "error": "not found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                json_response(self, 400, {"ok": False, "error": "invalid Content-Length"})
                return
            if content_length > 64_000:
                json_response(self, 413, {"ok": False, "error": "request too large"})
                return
            try:
                data = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": f"invalid JSON: {exc}"})
                return

            text = str(data.get("text") or data.get("transcript") or "").strip()
            if self.path.startswith("/weather"):
                default_location = str(data.get("default_location") or data.get("location") or weather_default_location).strip() or weather_default_location
                language = str(data.get("language") or weather_language).strip() or weather_language
                timeout_sec = float(data.get("timeout_sec") or weather_timeout_sec or DEFAULT_WEATHER_TIMEOUT_SEC)
                if data.get("location") and not text:
                    text = f"{data.get('location')} 天氣"
                try:
                    result = handle_weather_text(
                        text,
                        default_location=default_location,
                        language=language,
                        timeout_sec=timeout_sec,
                    )
                except Exception as exc:
                    result = {
                        "ok": False,
                        "handled": False,
                        "intent": True,
                        "action": "weather",
                        "location": default_location,
                        "error": str(exc),
                        "source": "open-meteo",
                    }
                json_response(self, 200 if result.get("ok") else 500, result)
                return

            backend = str(data.get("backend") or default_backend).strip().lower()
            dry_run = bool(data.get("dry_run", default_dry_run))
            action = str(data.get("action") or "").strip().lower()
            if action == "stop":
                json_response(self, 200, player.stop())
                return
            if action == "pause":
                json_response(self, 200, player.pause())
                return
            if action == "resume":
                result = player.resume()
                json_response(self, 200, result)
                return
            if action == "play" and data.get("query"):
                result = player.play(str(data.get("query")), backend=backend, dry_run=dry_run)
                json_response(self, 200 if result.get("ok") else 500, result)
                return
            if data.get("query"):
                result = player.play(str(data.get("query")), backend=backend, dry_run=dry_run)
                json_response(self, 200 if result.get("ok") else 500, result)
                return

            result = handle_text(player, text, backend=backend, dry_run=dry_run)
            if result.get("action") in {"pause", "resume", "stop"}:
                json_response(self, 200, result)
            else:
                json_response(self, 200 if result.get("ok") else 500, result)

    return MusicHandler


def run_server(args: argparse.Namespace) -> int:
    player = MusicPlayer(
        backend=args.backend,
        dry_run=args.dry_run,
        mpv_audio_device=args.mpv_audio_device,
        mpv_audio_keyword=args.mpv_audio_keyword,
        mpv_ytdl_cookies=args.mpv_ytdl_cookies,
        mpv_ytdl_cookies_from_browser=args.mpv_ytdl_cookies_from_browser,
        mpv_volume=args.mpv_volume,
        mpv_ready_timeout=args.mpv_ready_timeout,
    )
    try:
        server = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(
                player,
                default_backend=args.backend,
                default_dry_run=args.dry_run,
                weather_default_location=args.weather_default_location,
                weather_language=args.weather_language,
                weather_timeout_sec=args.weather_timeout,
            ),
        )
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"ERROR: music web player port {args.port} is already in use.")
            print("A music_web_player server is probably already running.")
            print(f"Check it with: curl http://{args.host}:{args.port}/health")
            print("Stop old music player servers with: pkill -f 'music_web_player.py'")
            return 1
        raise
    stop_event = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()
        player.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print("MakeNTU music web player ready.")
    print(f"  URL     : http://{args.host}:{args.port}/music")
    print(f"  backend : {args.backend}")
    print(f"  dry_run : {args.dry_run}")
    if args.backend == "mpv":
        print(f"  mpv out : {player.mpv_audio_device or 'system-default'}")
        print(f"  mpv vol : {player.mpv_volume}")
        if player.mpv_ytdl_cookies:
            print(f"  cookies : {Path(player.mpv_ytdl_cookies).expanduser()}")
        elif player.mpv_ytdl_cookies_from_browser:
            print(f"  cookies : browser:{player.mpv_ytdl_cookies_from_browser}")
    print(f"  weather : Open-Meteo, default_location={args.weather_default_location}, timeout={args.weather_timeout:g}s")
    print("  note    : browser backend opens a legal streaming/search page; mpv backend requires yt-dlp.")
    server.serve_forever(poll_interval=0.5)
    stop_event.set()
    print("Music web player stopped.")
    return 0


def run_self_test() -> int:
    cases = [
        ("Hey Jarvis 幫我播放周杰倫 稻香", True, "play", "周杰倫 稻香"),
        ("我想聽告白氣球這首歌", True, "play", "告白氣球"),
        ("我想聽歌", True, "play", "music"),
        ("我想要听《告白气球》。", True, "play", "告白气球"),
        ("幫我波 稻香", True, "play", "稻香"),
        ("換成 七里香", True, "play", "七里香"),
        ("play never gonna give you up", True, "play", "never gonna give you up"),
        ("停止音樂", True, "stop", ""),
        ("暫停播放", True, "pause", ""),
        ("繼續播放音樂", True, "resume", ""),
        ("resume music", True, "resume", ""),
        ("今天幾號", False, "none", ""),
        ("為什麼沒聲音，因為我聽到聲音超小", False, "none", ""),
        ("我聽到聲音超小，幫我調大音量", False, "none", ""),
    ]
    for text, expected_intent, expected_action, expected_query in cases:
        intent = detect_music_intent(text)
        if intent.intent != expected_intent or intent.action != expected_action:
            raise AssertionError(f"bad intent for {text!r}: {intent}")
        if expected_query and intent.query != expected_query:
            raise AssertionError(f"bad query for {text!r}: {intent.query!r}")

    player = MusicPlayer(backend="browser", dry_run=True, mpv_audio_device="default")
    result = handle_text(player, "幫我放稻香", dry_run=True)
    if not result.get("ok") or result.get("query") != "稻香":
        raise AssertionError(f"dry-run play failed: {result}")

    class BrokenPipeWriter:
        def write(self, _body: bytes) -> None:
            raise BrokenPipeError("self-test client disconnected")

    class FakeHandler:
        wfile = BrokenPipeWriter()

        def send_response(self, _status: int) -> None:
            pass

        def send_header(self, _name: str, _value: str) -> None:
            pass

        def end_headers(self) -> None:
            pass

    if json_response(FakeHandler(), 200, {"ok": True}):  # type: ignore[arg-type]
        raise AssertionError("json_response should tolerate client disconnects")

    weather_cases = [
        ("所在地天氣如何", True, "Taipei"),
        ("明天會下雨嗎", True, "Taipei"),
        ("明天下午三點台北天氣", True, "Taipei"),
        ("weather in Tokyo tomorrow", True, "Tokyo"),
        ("講個笑話", False, ""),
    ]
    for text, expected_intent, expected_location in weather_cases:
        intent = detect_weather_intent(text, default_location="Taipei")
        if intent.intent != expected_intent:
            raise AssertionError(f"bad weather intent for {text!r}: {intent}")
        if expected_location and expected_location.lower() not in intent.location.lower():
            raise AssertionError(f"bad weather location for {text!r}: {intent.location!r}")

    print("music_web_player self-test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone network music search/player tool for MakeNTU local AI.")
    parser.add_argument("--server", action="store_true", help="Run HTTP sidecar server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--backend", choices=["browser", "mpv"], default=DEFAULT_BACKEND)
    parser.add_argument("--dry-run", action="store_true", help="Detect and return the action without opening browser or mpv.")
    parser.add_argument("--mpv-audio-device", default=DEFAULT_MPV_AUDIO_DEVICE, help="mpv --audio-device value. Use auto to prefer the UACDemo USB audio output.")
    parser.add_argument("--mpv-audio-keyword", default=DEFAULT_MPV_AUDIO_DEVICE_KEYWORD, help="Keyword used by --mpv-audio-device auto.")
    parser.add_argument("--mpv-ytdl-cookies", default=DEFAULT_MPV_YTDL_COOKIES, help="Optional cookies.txt passed to mpv/yt-dlp for logged-in YouTube playback.")
    parser.add_argument("--mpv-ytdl-cookies-from-browser", default=DEFAULT_MPV_YTDL_COOKIES_FROM_BROWSER, help="Optional yt-dlp browser cookie source, e.g. firefox or chrome:Profile 1.")
    parser.add_argument("--mpv-volume", type=int, default=DEFAULT_MPV_VOLUME, help="mpv playback volume, default 100.")
    parser.add_argument("--mpv-ready-timeout", type=float, default=DEFAULT_MPV_READY_TIMEOUT_SEC, help="Seconds to wait for mpv IPC/playback status.")
    parser.add_argument("--weather-default-location", default=DEFAULT_WEATHER_LOCATION, help="Default location for local/here weather requests.")
    parser.add_argument("--weather-language", default=DEFAULT_WEATHER_LANGUAGE, help="Open-Meteo geocoding language.")
    parser.add_argument("--weather-timeout", type=float, default=DEFAULT_WEATHER_TIMEOUT_SEC, help="Total-ish seconds budget for Open-Meteo geocoding + forecast.")
    parser.add_argument("--text", help="One-shot transcript text to detect and handle.")
    parser.add_argument("--weather", help="One-shot transcript text to answer with Open-Meteo weather.")
    parser.add_argument("--query", help="One-shot exact search query to play.")
    parser.add_argument("--stop", action="store_true", help="Stop the active mpv process in this process.")
    parser.add_argument("--pause", action="store_true", help="Pause the active mpv process in this process.")
    parser.add_argument("--resume", action="store_true", help="Resume the active mpv process in this process.")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.self_test:
        return run_self_test()
    if args.server:
        return run_server(args)
    player = MusicPlayer(
        backend=args.backend,
        dry_run=args.dry_run,
        mpv_audio_device=args.mpv_audio_device,
        mpv_audio_keyword=args.mpv_audio_keyword,
        mpv_ytdl_cookies=args.mpv_ytdl_cookies,
        mpv_ytdl_cookies_from_browser=args.mpv_ytdl_cookies_from_browser,
        mpv_volume=args.mpv_volume,
        mpv_ready_timeout=args.mpv_ready_timeout,
    )
    if args.stop:
        print(json.dumps(player.stop(), ensure_ascii=False, indent=2))
        return 0
    if args.pause:
        print(json.dumps(player.pause(), ensure_ascii=False, indent=2))
        return 0
    if args.resume:
        print(json.dumps(player.resume(), ensure_ascii=False, indent=2))
        return 0
    if args.weather:
        print(
            json.dumps(
                handle_weather_text(
                    args.weather,
                    default_location=args.weather_default_location,
                    language=args.weather_language,
                    timeout_sec=args.weather_timeout,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.query:
        print(json.dumps(player.play(args.query), ensure_ascii=False, indent=2))
        return 0
    if args.text:
        print(json.dumps(handle_text(player, args.text), ensure_ascii=False, indent=2))
        return 0
    print("Nothing to do. Use --server, --text, --query, or --self-test.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
