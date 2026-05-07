#!/usr/bin/env python3
"""Standalone network music search/player tool for the MakeNTU local AI stack.

This file intentionally does not import or modify the existing wake/TTS/UART
bridge. Run it as a sidecar service, then let the local AI server call /music
when the transcript clearly asks to play music.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


DEFAULT_HOST = os.getenv("MUSIC_TOOL_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("MUSIC_TOOL_PORT", "8788"))
DEFAULT_BACKEND = os.getenv("MUSIC_PLAYER_BACKEND", "browser")
YOUTUBE_MUSIC_SEARCH_URL = "https://music.youtube.com/search?q="
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="

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

CN_PLAY_PATTERNS = (
    r"(?:請|请|麻煩你|麻烦你|可以)?(?:幫我|帮我)?(?:播放|播一下|播|放一下|放|放首|放一首)\s*(?P<query>.+)",
    r"(?:我想聽|我想听|想聽|想听|我要聽|我要听|聽一下|听一下|聽|听)\s*(?P<query>.+)",
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


@dataclass
class MusicIntent:
    intent: bool
    action: str = "none"
    query: str = ""
    reason: str = ""
    normalized_text: str = ""


def normalize_text(text: str | None) -> str:
    value = str(text or "").strip()
    value = re.sub(r"[，。！？、；：,.!?;:()\[\]{}\"'`]+", " ", value)
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


def youtube_music_search_url(query: str) -> str:
    return YOUTUBE_MUSIC_SEARCH_URL + urllib.parse.quote_plus(query)


def youtube_search_url(query: str) -> str:
    return YOUTUBE_SEARCH_URL + urllib.parse.quote_plus(query)


class MusicPlayer:
    def __init__(self, *, backend: str = DEFAULT_BACKEND, dry_run: bool = False) -> None:
        self.backend = backend
        self.dry_run = dry_run
        self._lock = threading.RLock()
        self._process: subprocess.Popen[Any] | None = None
        self.last_query = ""
        self.last_backend = ""

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = None
                return {"ok": True, "action": "stop", "stopped": False, "message": "no active mpv process"}
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2.0)
            self._process = None
            return {"ok": True, "action": "stop", "stopped": True}

    def pause(self) -> dict[str, Any]:
        # Browser playback cannot be controlled safely from this tool. For mpv,
        # "pause" is implemented as stop because this sidecar keeps no IPC socket.
        result = self.stop()
        result["action"] = "pause"
        result["message"] = "pause is implemented as stop in this standalone tool"
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

        self.stop()
        target = f"ytdl://ytsearch1:{query}"
        command = [
            mpv,
            "--no-video",
            "--force-window=no",
            "--really-quiet",
            "--term-playing-msg=Now playing: ${media-title}",
            target,
        ]
        try:
            with self._lock:
                self._process = subprocess.Popen(command)
                self.last_query = query
                self.last_backend = "mpv"
            return {"ok": True, "action": "play", "backend": "mpv", "query": query, "target": target}
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
    if intent.action == "play":
        play_result = player.play(intent.query, backend=backend, dry_run=dry_run)
        result.update(play_result)
        result["handled"] = bool(play_result.get("ok"))
        return result

    result.update({"ok": False, "handled": False, "error": f"unknown action: {intent.action}"})
    return result


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(player: MusicPlayer, *, default_backend: str, default_dry_run: bool) -> type[BaseHTTPRequestHandler]:
    class MusicHandler(BaseHTTPRequestHandler):
        server_version = "MakeNTUMusicTool/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:
            if self.path.startswith("/health"):
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
                    },
                )
                return
            json_response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if not self.path.startswith("/music"):
                json_response(self, 404, {"ok": False, "error": "not found"})
                return
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > 64_000:
                json_response(self, 413, {"ok": False, "error": "request too large"})
                return
            try:
                data = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": f"invalid JSON: {exc}"})
                return

            text = str(data.get("text") or data.get("transcript") or "").strip()
            backend = str(data.get("backend") or default_backend).strip().lower()
            dry_run = bool(data.get("dry_run", default_dry_run))
            if data.get("action") == "stop":
                json_response(self, 200, player.stop())
                return
            if data.get("query"):
                result = player.play(str(data.get("query")), backend=backend, dry_run=dry_run)
                json_response(self, 200 if result.get("ok") else 500, result)
                return

            result = handle_text(player, text, backend=backend, dry_run=dry_run)
            json_response(self, 200 if result.get("ok") else 500, result)

    return MusicHandler


def run_server(args: argparse.Namespace) -> int:
    player = MusicPlayer(backend=args.backend, dry_run=args.dry_run)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(player, default_backend=args.backend, default_dry_run=args.dry_run))
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
    print("  note    : browser backend opens a legal streaming/search page; mpv backend requires yt-dlp.")
    server.serve_forever(poll_interval=0.5)
    stop_event.set()
    print("Music web player stopped.")
    return 0


def run_self_test() -> int:
    cases = [
        ("Hey Jarvis 幫我播放周杰倫 稻香", True, "play", "周杰倫 稻香"),
        ("我想聽告白氣球這首歌", True, "play", "告白氣球"),
        ("play never gonna give you up", True, "play", "never gonna give you up"),
        ("停止音樂", True, "stop", ""),
        ("暫停播放", True, "pause", ""),
        ("今天幾號", False, "none", ""),
    ]
    for text, expected_intent, expected_action, expected_query in cases:
        intent = detect_music_intent(text)
        if intent.intent != expected_intent or intent.action != expected_action:
            raise AssertionError(f"bad intent for {text!r}: {intent}")
        if expected_query and intent.query != expected_query:
            raise AssertionError(f"bad query for {text!r}: {intent.query!r}")

    player = MusicPlayer(backend="browser", dry_run=True)
    result = handle_text(player, "幫我放稻香", dry_run=True)
    if not result.get("ok") or result.get("query") != "稻香":
        raise AssertionError(f"dry-run play failed: {result}")
    print("music_web_player self-test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone network music search/player tool for MakeNTU local AI.")
    parser.add_argument("--server", action="store_true", help="Run HTTP sidecar server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--backend", choices=["browser", "mpv"], default=DEFAULT_BACKEND)
    parser.add_argument("--dry-run", action="store_true", help="Detect and return the action without opening browser or mpv.")
    parser.add_argument("--text", help="One-shot transcript text to detect and handle.")
    parser.add_argument("--query", help="One-shot exact search query to play.")
    parser.add_argument("--stop", action="store_true", help="Stop the active mpv process in this process.")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.self_test:
        return run_self_test()
    player = MusicPlayer(backend=args.backend, dry_run=args.dry_run)
    if args.server:
        return run_server(args)
    if args.stop:
        print(json.dumps(player.stop(), ensure_ascii=False, indent=2))
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
