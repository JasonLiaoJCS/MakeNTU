from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from jetson_piper_tts.server import create_app


class FakeService:
    def __init__(self) -> None:
        self.last_speak_kwargs: dict[str, Any] = {}

    def health(self) -> dict[str, Any]:
        return {
            "engine": {"ready": True},
            "audio": {"backend": "fake"},
            "cache": {"wav_files": 0},
            "settings": {},
        }

    def warm_up(self) -> dict[str, Any]:
        return {"ok": True}

    def speak(self, text: str, **kwargs: Any) -> dict[str, Any]:
        self.last_speak_kwargs = kwargs
        return {
            "original_text": text,
            "normalized_text": text,
            "chunks": [text],
            "wav_files": ["/tmp/fake.wav"],
            "cache_hits": 0,
            "synth_ms": 1,
            "total_ms": 1,
            "playback": {"played": 1},
        }

    def list_voices(self) -> list[dict[str, Any]]:
        return [{"name": "fake", "config_exists": True}]


class FakeWorker:
    def __init__(self) -> None:
        self.last_enqueue_kwargs: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        return {"queue_size": 0, "running": False}

    def enqueue(self, text: str, **kwargs: Any) -> SimpleNamespace:
        self.last_enqueue_kwargs = kwargs
        return SimpleNamespace(id="job123", text=text)

    def stop(self) -> dict[str, Any]:
        return {"cleared": 0, "stopped_current": False}

    def shutdown(self) -> None:
        return None


def test_api_smoke() -> None:
    service = FakeService()
    worker = FakeWorker()
    app = create_app(service=service, worker=worker)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ready"] is True

    speak = client.post("/speak", json={"text": "你好。", "volume_gain": 2.5})
    assert speak.status_code == 200
    assert speak.json()["normalized_text"] == "你好。"
    assert service.last_speak_kwargs["volume_gain"] == 2.5

    async_reply = client.post("/speak_async", json={"text": "排队播放。", "volume_gain": 2.0})
    assert async_reply.status_code == 200
    assert async_reply.json()["job_id"] == "job123"
    assert worker.last_enqueue_kwargs["volume_gain"] == 2.0

    too_loud = client.post("/speak", json={"text": "太大声。", "volume_gain": 9.0})
    assert too_loud.status_code == 422

    assert client.post("/stop").json()["cleared"] == 0
