from __future__ import annotations

from typing import Any

import requests


class JetsonPiperTTSClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8777", *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def speak(
        self,
        text: str,
        *,
        blocking: bool = False,
        interrupt: bool = False,
        length_scale: float | None = None,
    ) -> dict[str, Any]:
        endpoint = "/speak" if blocking else "/speak_async"
        payload = {
            "text": text,
            "blocking": blocking,
            "interrupt": interrupt,
            "length_scale": length_scale,
        }
        response = requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def stop(self) -> dict[str, Any]:
        response = requests.post(f"{self.base_url}/stop", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}/health", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def voices(self) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}/voices", timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def speak(text: str, blocking: bool = False, interrupt: bool = False) -> dict[str, Any]:
    return JetsonPiperTTSClient().speak(text, blocking=blocking, interrupt=interrupt)


def stop() -> dict[str, Any]:
    return JetsonPiperTTSClient().stop()


def health() -> dict[str, Any]:
    return JetsonPiperTTSClient().health()
