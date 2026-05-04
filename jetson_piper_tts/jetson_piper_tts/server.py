from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .piper_engine import PiperError, TTSService
from .queue_worker import TTSQueueWorker


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)
    blocking: bool = True
    interrupt: bool = False
    priority: int = 0
    voice: str | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    stream: bool | None = None


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)


def create_app(
    *,
    settings: Settings | None = None,
    service: TTSService | None = None,
    worker: TTSQueueWorker | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings.log_level)
    service = service or TTSService(settings)
    worker = worker or TTSQueueWorker(service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.server_warmup:
            ready = service.health()["engine"]["ready"]
            if ready:
                try:
                    service.warm_up()
                except Exception as exc:  # pragma: no cover - deployment path
                    logging.getLogger(__name__).warning("warm-up failed: %s", exc)
        yield
        worker.shutdown()

    app = FastAPI(title="Jetson Piper TTS", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.tts_service = service
    app.state.tts_worker = worker

    @app.get("/health")
    def health() -> dict[str, Any]:
        service_health = service.health()
        return {
            "service": "jetson_piper_tts",
            "ready": service_health["engine"]["ready"],
            **service_health,
            "queue": worker.status(),
        }

    @app.get("/voices")
    def voices() -> dict[str, Any]:
        return {"voices": service.list_voices()}

    @app.get("/queue")
    def queue_status() -> dict[str, Any]:
        return worker.status()

    @app.post("/speak")
    def speak(request: SpeakRequest) -> dict[str, Any]:
        if request.interrupt:
            worker.stop()
        try:
            return service.speak(
                request.text,
                blocking=request.blocking,
                play=True,
                voice=request.voice,
                length_scale=request.length_scale,
                noise_scale=request.noise_scale,
                noise_w=request.noise_w,
                stream=request.stream,
            )
        except PiperError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/speak_async")
    def speak_async(request: SpeakRequest) -> dict[str, Any]:
        job = worker.enqueue(
            request.text,
            priority=request.priority,
            interrupt=request.interrupt,
            voice=request.voice,
            length_scale=request.length_scale,
            noise_scale=request.noise_scale,
            noise_w=request.noise_w,
            stream=request.stream,
        )
        return {"queued": True, "job_id": job.id, "queue": worker.status()}

    @app.post("/stop")
    def stop() -> dict[str, Any]:
        return worker.stop()

    return app


app = create_app() if __name__ != "__main__" else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Jetson Piper TTS HTTP server.")
    parser.add_argument("--host", help="Bind host. Default comes from HOST or .env.")
    parser.add_argument("--port", type=int, help="Bind port. Default comes from PORT or .env.")
    parser.add_argument("--log-level", help="Log level, e.g. INFO or DEBUG.")
    parser.add_argument("--no-warmup", action="store_true", help="Disable server startup warm-up synthesis.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings().with_overrides(
        host=args.host,
        port=args.port,
        log_level=args.log_level.upper() if args.log_level else None,
        server_warmup=False if args.no_warmup else None,
    )
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
