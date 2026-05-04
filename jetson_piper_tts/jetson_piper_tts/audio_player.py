from __future__ import annotations

import logging
import itertools
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class AudioPlaybackError(RuntimeError):
    pass


class AudioPlayer:
    def __init__(self, *, aplay_bin: str = "aplay", device: str | None = "default") -> None:
        self.aplay_bin = aplay_bin
        self.device = device if device not in {"", None} else None
        self._lock = threading.Lock()
        self._current_process: subprocess.Popen[bytes] | None = None
        self._current_processes: list[subprocess.Popen[bytes]] = []
        self._current_file: Path | None = None

    @property
    def aplay_path(self) -> str | None:
        if os.path.sep in self.aplay_bin or (os.path.altsep and os.path.altsep in self.aplay_bin):
            path = Path(self.aplay_bin).expanduser()
            return str(path) if path.exists() and os.access(path, os.X_OK) else None
        return shutil.which(self.aplay_bin)

    def check_ready(self) -> dict[str, Any]:
        return {
            "backend": "aplay",
            "aplay_bin": self.aplay_bin,
            "aplay_path": self.aplay_path,
            "available": self.aplay_path is not None,
            "device": self.device or "default",
            "current_file": str(self._current_file) if self._current_file else None,
            "playing": self.is_playing(),
        }

    def play_file(self, wav_path: Path, *, blocking: bool = True) -> dict[str, Any]:
        path = Path(wav_path)
        if not path.exists():
            raise AudioPlaybackError(f"WAV file does not exist: {path}")

        aplay_path = self.aplay_path
        if not aplay_path:
            raise AudioPlaybackError(
                "aplay was not found. Install alsa-utils or set APLAY_BIN to a valid player."
            )

        command = [aplay_path, "-q"]
        if self.device:
            command.extend(["-D", self.device])
        command.append(str(path))

        started = time.perf_counter()
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        with self._lock:
            self._current_process = process
            self._current_processes = [process]
            self._current_file = path

        if not blocking:
            return {
                "wav": str(path),
                "blocking": False,
                "pid": process.pid,
                "started_ms": int((time.perf_counter() - started) * 1000),
            }

        stderr = b""
        try:
            _, stderr = process.communicate()
        finally:
            with self._lock:
                if self._current_process is process:
                    self._current_process = None
                    self._current_processes = []
                    self._current_file = None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise AudioPlaybackError(f"aplay failed with code {process.returncode}: {error}")

        logger.info("played %s in %d ms", path, elapsed_ms)
        return {"wav": str(path), "blocking": True, "elapsed_ms": elapsed_ms}

    def play_raw_from_command(
        self,
        producer_command: list[str],
        *,
        input_text: str,
        env: dict[str, str] | None = None,
        sample_rate: int,
        channels: int = 1,
        sample_format: str = "S16_LE",
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        aplay_path = self.aplay_path
        if not aplay_path:
            raise AudioPlaybackError(
                "aplay was not found. Install alsa-utils or set APLAY_BIN to a valid player."
            )

        aplay_command = [
            aplay_path,
            "-q",
            "-t",
            "raw",
            "-f",
            sample_format,
            "-r",
            str(sample_rate),
            "-c",
            str(channels),
        ]
        if self.device:
            aplay_command.extend(["-D", self.device])

        started = time.perf_counter()
        aplay_process = subprocess.Popen(
            aplay_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        producer_process = subprocess.Popen(
            producer_command,
            stdin=subprocess.PIPE,
            stdout=aplay_process.stdin,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

        if aplay_process.stdin is not None:
            aplay_process.stdin.close()
            aplay_process.stdin = None

        with self._lock:
            self._current_process = aplay_process
            self._current_processes = [producer_process, aplay_process]
            self._current_file = None

        producer_stderr = b""
        aplay_stderr = b""
        try:
            _, producer_stderr = producer_process.communicate(
                input=input_text.encode("utf-8"),
                timeout=timeout_seconds,
            )
            _, aplay_stderr = aplay_process.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            self.stop()
            raise AudioPlaybackError(f"raw playback pipeline timed out after {exc.timeout}s") from exc
        finally:
            with self._lock:
                if self._current_process is aplay_process:
                    self._current_process = None
                    self._current_processes = []
                    self._current_file = None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        producer_error = producer_stderr.decode("utf-8", errors="replace").strip()
        aplay_error = aplay_stderr.decode("utf-8", errors="replace").strip()

        if producer_process.returncode != 0:
            raise AudioPlaybackError(
                f"audio producer failed with code {producer_process.returncode}: {producer_error}"
            )
        if aplay_process.returncode != 0:
            raise AudioPlaybackError(f"aplay failed with code {aplay_process.returncode}: {aplay_error}")

        return {
            "blocking": True,
            "streaming": True,
            "producer": "subprocess",
            "elapsed_ms": elapsed_ms,
            "sample_rate": sample_rate,
            "channels": channels,
            "format": sample_format,
        }

    def play_raw_chunks(
        self,
        audio_chunks: Iterable[bytes],
        *,
        sample_rate: int,
        channels: int = 1,
        sample_format: str = "S16_LE",
    ) -> dict[str, Any]:
        aplay_path = self.aplay_path
        if not aplay_path:
            raise AudioPlaybackError(
                "aplay was not found. Install alsa-utils or set APLAY_BIN to a valid player."
            )

        command = [
            aplay_path,
            "-q",
            "-t",
            "raw",
            "-f",
            sample_format,
            "-r",
            str(sample_rate),
            "-c",
            str(channels),
        ]
        if self.device:
            command.extend(["-D", self.device])

        started = time.perf_counter()
        bytes_written = 0
        stderr = b""
        iterator = iter(audio_chunks)
        first_audio = b""
        for audio in iterator:
            if audio:
                first_audio = audio
                break

        if not first_audio:
            return {
                "blocking": True,
                "streaming": True,
                "producer": "inprocess",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "sample_rate": sample_rate,
                "channels": channels,
                "format": sample_format,
                "bytes_written": 0,
            }

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        with self._lock:
            self._current_process = process
            self._current_processes = [process]
            self._current_file = None

        try:
            assert process.stdin is not None
            for audio in itertools.chain((first_audio,), iterator):
                if not audio:
                    continue
                process.stdin.write(audio)
                process.stdin.flush()
                bytes_written += len(audio)
            process.stdin.close()
            process.stdin = None
            _, stderr = process.communicate(timeout=5)
        except BrokenPipeError as exc:
            self.stop()
            raise AudioPlaybackError("aplay stopped while raw audio was being written") from exc
        finally:
            with self._lock:
                if self._current_process is process:
                    self._current_process = None
                    self._current_processes = []
                    self._current_file = None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise AudioPlaybackError(f"aplay failed with code {process.returncode}: {error}")

        return {
            "blocking": True,
            "streaming": True,
            "producer": "inprocess",
            "elapsed_ms": elapsed_ms,
            "sample_rate": sample_rate,
            "channels": channels,
            "format": sample_format,
            "bytes_written": bytes_written,
        }

    def stop(self) -> bool:
        with self._lock:
            processes = list(self._current_processes)
            process = self._current_process
            self._current_process = None
            self._current_processes = []
            self._current_file = None

        if not processes and process is not None:
            processes = [process]

        if not processes:
            return False

        stopped = False
        for proc in processes:
            if proc is None or proc.poll() is not None:
                continue
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                stopped = True
            except ProcessLookupError:
                pass

        deadline = time.monotonic() + 1.5
        for proc in processes:
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=1)
                    stopped = True
                except ProcessLookupError:
                    pass
        return stopped

    def is_playing(self) -> bool:
        with self._lock:
            processes = list(self._current_processes)
            process = self._current_process
        if processes:
            return any(proc.poll() is None for proc in processes)
        return process is not None and process.poll() is None
