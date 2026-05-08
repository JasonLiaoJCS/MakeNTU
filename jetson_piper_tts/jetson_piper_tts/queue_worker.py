from __future__ import annotations

import itertools
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .piper_engine import TTSService


@dataclass(slots=True)
class TTSJob:
    text: str
    priority: int = 0
    blocking: bool = True
    interrupt: bool = False
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    stream: bool | None = None
    volume_gain: float | None = None
    voice: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)


class TTSQueueWorker:
    def __init__(self, service: TTSService) -> None:
        self.service = service
        self._queue: queue.PriorityQueue[tuple[int, int, TTSJob]] = queue.PriorityQueue()
        self._counter = itertools.count()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._current: TTSJob | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._thread = threading.Thread(target=self._run, name="tts-queue-worker", daemon=True)
        self._thread.start()

    def enqueue(
        self,
        text: str,
        *,
        priority: int = 0,
        interrupt: bool = False,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
        stream: bool | None = None,
        volume_gain: float | None = None,
        voice: str | None = None,
    ) -> TTSJob:
        if interrupt:
            self.clear()
            self.stop_current()

        job = TTSJob(
            text=text,
            priority=priority,
            interrupt=interrupt,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w=noise_w,
            stream=stream,
            volume_gain=volume_gain,
            voice=voice,
        )
        self._queue.put((-priority, next(self._counter), job))
        return job

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                _, _, job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            with self._lock:
                self._current = job
                self._last_error = None
            try:
                result = self.service.speak(
                    job.text,
                    blocking=True,
                    play=True,
                    voice=job.voice,
                    length_scale=job.length_scale,
                    noise_scale=job.noise_scale,
                    noise_w=job.noise_w,
                    stream=job.stream,
                    volume_gain=job.volume_gain,
                )
                with self._lock:
                    self._last_result = {"job_id": job.id, **result}
            except Exception as exc:  # pragma: no cover - integration path
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    self._current = None
                self._queue.task_done()

    def clear(self) -> int:
        removed = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return removed
            self._queue.task_done()
            removed += 1

    def stop_current(self) -> bool:
        return self.service.stop_current()

    def stop(self) -> dict[str, Any]:
        removed = self.clear()
        stopped = self.stop_current()
        return {"cleared": removed, "stopped_current": stopped}

    def shutdown(self) -> None:
        self._stop_event.set()
        self.stop_current()
        self._thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        with self._lock:
            current = self._current
            last_result = self._last_result
            last_error = self._last_error
        return {
            "queue_size": self._queue.qsize(),
            "running": current is not None,
            "current": {
                "id": current.id,
                "text_preview": current.text[:80],
                "created_at": current.created_at,
            }
            if current
            else None,
            "last_error": last_error,
            "last_result": last_result,
        }
