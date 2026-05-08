from __future__ import annotations

import logging
import audioop
import itertools
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class AudioPlaybackError(RuntimeError):
    pass


def _normalize_volume_gain(volume_gain: float | None) -> float:
    if volume_gain is None:
        return 1.0
    try:
        gain = float(volume_gain)
    except (TypeError, ValueError):
        return 1.0
    if gain <= 0:
        return 1.0
    return max(0.05, min(gain, 8.0))


def _apply_volume_gain(audio: bytes, *, sample_format: str, volume_gain: float | None) -> bytes:
    gain = _normalize_volume_gain(volume_gain)
    if gain == 1.0 or not audio:
        return audio
    if sample_format.upper() != "S16_LE":
        logger.warning("volume_gain is only supported for S16_LE raw audio; ignoring gain=%s", gain)
        return audio
    try:
        return audioop.mul(audio, 2, gain)
    except Exception as exc:
        logger.warning("failed to apply volume_gain=%s: %s", gain, exc)
        return audio


def _write_gain_adjusted_wav(source: Path, *, volume_gain: float | None) -> Path:
    gain = _normalize_volume_gain(volume_gain)
    if gain == 1.0:
        return source

    with wave.open(str(source), "rb") as reader:
        params = reader.getparams()
        if params.sampwidth != 2:
            logger.warning("volume_gain is only supported for 16-bit WAV files; ignoring gain=%s", gain)
            return source
        frames = reader.readframes(params.nframes)

    adjusted = _apply_volume_gain(frames, sample_format="S16_LE", volume_gain=gain)
    temp = tempfile.NamedTemporaryFile(prefix="tts_gain_", suffix=".wav", delete=False)
    temp_path = Path(temp.name)
    temp.close()

    with wave.open(str(temp_path), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(adjusted)
    return temp_path


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

    def _aplay_devices(self) -> list[tuple[str, str]]:
        aplay_path = self.aplay_path
        if not aplay_path:
            return []
        try:
            result = subprocess.run(
                [aplay_path, "-L"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []

        devices: list[tuple[str, str]] = []
        name: str | None = None
        detail: list[str] = []
        for line in result.stdout.splitlines():
            if line.strip() and not line.startswith((" ", "\t")):
                if name is not None:
                    devices.append((name, " ".join(detail)))
                name = line.strip()
                detail = []
            elif name is not None:
                detail.append(line.strip())
        if name is not None:
            devices.append((name, " ".join(detail)))
        return devices

    def _device_available(self, device: str) -> bool:
        return any(name == device for name, _detail in self._aplay_devices())

    def _find_device_by_keyword(self, keyword: str) -> str | None:
        lowered = keyword.strip().lower()
        if not lowered:
            return None
        matches = [
            name
            for name, detail in self._aplay_devices()
            if lowered in f"{name} {detail}".lower()
        ]
        if not matches:
            return None
        for prefix in ("plughw:", "sysdefault:", "front:"):
            for name in matches:
                if name.startswith(prefix):
                    return name
        return matches[0]

    def _auto_keyword(self, device: str) -> str | None:
        lowered = device.lower()
        for prefix in ("auto:", "keyword:"):
            if lowered.startswith(prefix):
                return device.split(":", 1)[1].strip()
        if "uacdemo" in lowered:
            return "UACDemo"
        match = re.search(r"CARD=([^,]+)", device)
        if match:
            return match.group(1).split("_", 1)[0]
        return None

    def resolve_device(self) -> str | None:
        device = self.device
        if not device:
            return None
        keyword = self._auto_keyword(device)
        if device.lower().startswith(("auto:", "keyword:")):
            found = self._find_device_by_keyword(keyword or "")
            if found:
                return found
            logger.warning("no ALSA playback device matched %r; using default", keyword)
            return None
        if self._device_available(device):
            return device
        if keyword:
            found = self._find_device_by_keyword(keyword)
            if found:
                logger.warning("ALSA playback device %r is unavailable; using %r", device, found)
                return found
        return device

    def check_ready(self) -> dict[str, Any]:
        resolved_device = self.resolve_device()
        return {
            "backend": "aplay",
            "aplay_bin": self.aplay_bin,
            "aplay_path": self.aplay_path,
            "available": self.aplay_path is not None,
            "device": resolved_device or "default",
            "configured_device": self.device or "default",
            "current_file": str(self._current_file) if self._current_file else None,
            "playing": self.is_playing(),
        }

    def play_file(self, wav_path: Path, *, blocking: bool = True, volume_gain: float | None = None) -> dict[str, Any]:
        path = Path(wav_path)
        if not path.exists():
            raise AudioPlaybackError(f"WAV file does not exist: {path}")

        aplay_path = self.aplay_path
        if not aplay_path:
            raise AudioPlaybackError(
                "aplay was not found. Install alsa-utils or set APLAY_BIN to a valid player."
            )

        gain = _normalize_volume_gain(volume_gain)
        playback_path = path
        temp_playback_path: Path | None = None
        if gain != 1.0:
            if blocking:
                playback_path = _write_gain_adjusted_wav(path, volume_gain=gain)
                if playback_path != path:
                    temp_playback_path = playback_path
            else:
                logger.warning("volume_gain is ignored for non-blocking direct WAV playback")

        command = [aplay_path, "-q"]
        device = self.resolve_device()
        if device:
            command.extend(["-D", device])
        command.append(str(playback_path))

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
            self._current_file = playback_path

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
            if temp_playback_path is not None:
                temp_playback_path.unlink(missing_ok=True)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise AudioPlaybackError(f"aplay failed with code {process.returncode}: {error}")

        logger.info("played %s in %d ms", path, elapsed_ms)
        return {"wav": str(path), "blocking": True, "elapsed_ms": elapsed_ms, "volume_gain": gain}

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
        volume_gain: float | None = None,
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
        device = self.resolve_device()
        if device:
            aplay_command.extend(["-D", device])

        started = time.perf_counter()
        gain = _normalize_volume_gain(volume_gain)
        use_gain = gain != 1.0

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
            stdout=subprocess.PIPE if use_gain else aplay_process.stdin,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

        if not use_gain and aplay_process.stdin is not None:
            aplay_process.stdin.close()
            aplay_process.stdin = None

        with self._lock:
            self._current_process = aplay_process
            self._current_processes = [producer_process, aplay_process]
            self._current_file = None

        producer_stderr = b""
        aplay_stderr = b""
        try:
            producer_stdout, producer_stderr = producer_process.communicate(
                input=input_text.encode("utf-8"),
                timeout=timeout_seconds,
            )
            if use_gain:
                processed = _apply_volume_gain(
                    producer_stdout or b"",
                    sample_format=sample_format,
                    volume_gain=gain,
                )
                _, aplay_stderr = aplay_process.communicate(input=processed, timeout=5)
            else:
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
            "volume_gain": gain,
        }

    def play_raw_chunks(
        self,
        audio_chunks: Iterable[bytes],
        *,
        sample_rate: int,
        channels: int = 1,
        sample_format: str = "S16_LE",
        volume_gain: float | None = None,
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
        device = self.resolve_device()
        if device:
            command.extend(["-D", device])

        started = time.perf_counter()
        bytes_written = 0
        gain = _normalize_volume_gain(volume_gain)
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
                audio = _apply_volume_gain(audio, sample_format=sample_format, volume_gain=gain)
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
            "volume_gain": gain,
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
