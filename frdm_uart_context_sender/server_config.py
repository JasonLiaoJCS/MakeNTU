"""
Central server configuration for desktop response-model host.

Put the local desktop IP here or override with env vars:
  DESKTOP_SERVER_HOST, DESKTOP_SERVER_PORT

This file is imported by bridge and focus modules so you can change host in one place.
"""
from __future__ import annotations

import os

DESKTOP_SERVER_HOST = os.getenv("DESKTOP_SERVER_HOST", "192.168.1.127")
DESKTOP_SERVER_PORT = int(os.getenv("DESKTOP_SERVER_PORT", "8766"))

VOICE_CHAT_PATH = "/voice-chat"
FOCUS_CHECK_PATH = "/focus-check"

DEFAULT_SERVER_URL = f"http://{DESKTOP_SERVER_HOST}:{DESKTOP_SERVER_PORT}{VOICE_CHAT_PATH}"
DEFAULT_FOCUS_URL = f"http://{DESKTOP_SERVER_HOST}:{DESKTOP_SERVER_PORT}{FOCUS_CHECK_PATH}"

def server_url_for(path: str = VOICE_CHAT_PATH) -> str:
    return f"http://{DESKTOP_SERVER_HOST}:{DESKTOP_SERVER_PORT}{path}"
