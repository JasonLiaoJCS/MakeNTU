from __future__ import annotations

import logging
import json
import os
import shutil
import site
import subprocess
import sys
import threading
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Any

from .audio_player import AudioPlayer
from .cache import AudioCache
from .config import Settings
from .text_normalizer import NormalizedText, normalize_text

logger = logging.getLogger(__name__)


class PiperError(RuntimeError):
    pass


class PiperEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._voice_lock = threading.Lock()
        self._voice_cache: dict[tuple[str, str, bool], Any] = {}

    def _resolve_voice(self, voice: str | Path | None) -> tuple[Path, Path]:
        if voice is None:
            return self.settings.piper_model, self.settings.piper_config

        raw = Path(str(voice)).expanduser()
        if raw.suffix != ".onnx":
            raw = self.settings.model_dir / f"{raw.name}.onnx"
        elif not raw.is_absolute():
            raw = (Path.cwd() / raw).resolve() if raw.exists() else self.settings.model_dir / raw.name

        return raw, raw.with_suffix(raw.suffix + ".json")

    def check_ready(self, *, voice: str | Path | None = None) -> dict[str, Any]:
        model, config = self._resolve_voice(voice)
        piper_path = self.settings.piper_path
        return {
            "piper_bin": self.settings.piper_bin,
            "piper_path": piper_path,
            "piper_executable": piper_path is not None,
            "model": str(model),
            "model_exists": model.exists(),
            "config": str(config),
            "config_exists": config.exists(),
            "ready": piper_path is not None and model.exists() and config.exists(),
        }

    def _validate_ready(self, *, voice: str | Path | None = None) -> tuple[str, Path, Path]:
        model, config = self._resolve_voice(voice)
        piper_path = self.settings.piper_path
        if not piper_path:
            raise PiperError(
                "Piper executable was not found. Install piper-tts or set PIPER_BIN to a Piper binary."
            )
        if not model.exists():
            raise PiperError(f"Piper model does not exist: {model}")
        if not config.exists():
            raise PiperError(f"Piper model config does not exist: {config}")
        return piper_path, model, config

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        extra_paths: list[str] = []

        if self.settings.extra_pythonpath:
            extra_paths.extend(
                path for path in self.settings.extra_pythonpath.split(os.pathsep) if path
            )

        # Piper's Chinese phonemizer imports g2pw -> torch. On Jetson, torch is
        # often installed in the user's site-packages instead of the project venv.
        user_site = site.getusersitepackages()
        if user_site and Path(user_site).exists():
            extra_paths.append(user_site)

        current_pythonpath = env.get("PYTHONPATH")
        if current_pythonpath:
            extra_paths.append(current_pythonpath)

        if extra_paths:
            seen: set[str] = set()
            ordered = []
            for path in extra_paths:
                if path not in seen:
                    ordered.append(path)
                    seen.add(path)
            env["PYTHONPATH"] = os.pathsep.join(ordered)

        return env

    def _ensure_python_import_path(self) -> None:
        if self.settings.enable_hf_offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        paths: list[str] = []
        if self.settings.extra_pythonpath:
            paths.extend(path for path in self.settings.extra_pythonpath.split(os.pathsep) if path)

        user_site = site.getusersitepackages()
        if user_site and Path(user_site).exists():
            paths.append(user_site)

        for path in reversed(paths):
            if path not in sys.path:
                sys.path.insert(0, path)

    def _failure_hint(self, stderr: str) -> str:
        if "No module named 'g2pw'" in stderr:
            return (
                "\nHint: Chinese Piper voices need g2pw. Run: "
                "python -m pip install g2pw unicode-rbnf sentence-stream"
            )
        if "No module named 'unicode_rbnf'" in stderr or "No module named 'sentence_stream'" in stderr:
            return (
                "\nHint: Chinese Piper voices need extra phonemizer packages. Run: "
                "python -m pip install unicode-rbnf sentence-stream"
            )
        if "No module named 'torch'" in stderr:
            return (
                "\nHint: g2pw needs PyTorch. On Jetson, install NVIDIA/Jetson PyTorch, "
                "or set EXTRA_PYTHONPATH to the site-packages directory that already contains torch."
            )
        if "libcudart.so.13" in stderr or "libcublasLt.so" in stderr:
            return (
                "\nHint: this venv has a torch wheel without its matching CUDA runtime. "
                "For this project, remove the broken venv torch with `python -m pip uninstall torch`, "
                "then use your Jetson/user-site torch through EXTRA_PYTHONPATH."
            )
        return ""

    def _voice_sample_rate(self, config_path: Path) -> int:
        try:
            with config_path.open("r", encoding="utf-8") as file:
                config = json.load(file)
            return int(config.get("audio", {}).get("sample_rate") or 22050)
        except Exception as exc:
            logger.warning("failed to read sample rate from %s: %s", config_path, exc)
            return 22050

    def _piper_command(
        self,
        *,
        piper_path: str,
        model: Path,
        config: Path,
        length_scale: float,
        noise_scale: float,
        noise_w: float,
        output_file: Path | None = None,
        output_raw: bool = False,
    ) -> list[str]:
        command = [
            piper_path,
            "--model",
            str(model),
            "--config",
            str(config),
            "--length_scale",
            str(length_scale),
            "--noise_scale",
            str(noise_scale),
            "--noise_w",
            str(noise_w),
        ]
        if output_raw:
            command.append("--output-raw")
        elif output_file is not None:
            command.extend(["--output_file", str(output_file)])
        return command

    def stream_raw_to_player(
        self,
        text: str,
        player: AudioPlayer,
        *,
        voice: str | Path | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        piper_path, model, config = self._validate_ready(voice=voice)
        length = float(length_scale if length_scale is not None else self.settings.default_length_scale)
        noise = float(noise_scale if noise_scale is not None else self.settings.default_noise_scale)
        noise_weight = float(noise_w if noise_w is not None else self.settings.default_noise_w)
        command = self._piper_command(
            piper_path=piper_path,
            model=model,
            config=config,
            length_scale=length,
            noise_scale=noise,
            noise_w=noise_weight,
            output_raw=True,
        )
        sample_rate = self._voice_sample_rate(config)
        started = time.perf_counter()
        try:
            playback = player.play_raw_from_command(
                command,
                input_text=text.strip() + "\n",
                env=self._subprocess_env(),
                sample_rate=sample_rate,
                channels=1,
                sample_format="S16_LE",
                timeout_seconds=timeout_seconds or self.settings.synth_timeout_seconds,
            )
        except Exception as exc:
            raise PiperError(f"Piper raw streaming playback failed: {exc}") from exc

        return {
            **playback,
            "mode": "raw_stream",
            "stream_ms": int((time.perf_counter() - started) * 1000),
        }

    def _load_voice_inprocess(
        self,
        *,
        voice: str | Path | None = None,
        use_cuda: bool = False,
    ) -> Any:
        _, model, config = self._validate_ready(voice=voice)
        key = (str(model.resolve()), str(config.resolve()), use_cuda)
        with self._voice_lock:
            cached = self._voice_cache.get(key)
            if cached is not None:
                return cached

            self._ensure_python_import_path()
            try:
                from piper import PiperVoice
            except Exception as exc:
                raise PiperError(
                    "Unable to import Piper in-process. Install piper-tts, or disable "
                    "ENABLE_INPROCESS_PIPER to use the subprocess backend."
                ) from exc

            loaded = PiperVoice.load(
                model,
                config_path=config,
                use_cuda=use_cuda,
                download_dir=Path.cwd(),
            )
            self._voice_cache[key] = loaded
            return loaded

    def stream_inprocess_to_player(
        self,
        text: str,
        player: AudioPlayer,
        *,
        voice: str | Path | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
    ) -> dict[str, Any]:
        self._ensure_python_import_path()
        try:
            from piper import SynthesisConfig
        except Exception as exc:
            raise PiperError("Unable to import Piper SynthesisConfig") from exc

        piper_voice = self._load_voice_inprocess(voice=voice)
        syn_config = SynthesisConfig(
            length_scale=float(length_scale if length_scale is not None else self.settings.default_length_scale),
            noise_scale=float(noise_scale if noise_scale is not None else self.settings.default_noise_scale),
            noise_w_scale=float(noise_w if noise_w is not None else self.settings.default_noise_w),
        )

        started = time.perf_counter()

        def audio_chunks():
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                for chunk in piper_voice.synthesize(line, syn_config):
                    yield chunk.audio_int16_bytes

        try:
            playback = player.play_raw_chunks(
                audio_chunks(),
                sample_rate=int(piper_voice.config.sample_rate),
                channels=1,
                sample_format="S16_LE",
            )
        except Exception as exc:
            raise PiperError(f"Piper in-process playback failed: {exc}") from exc

        return {
            **playback,
            "mode": "inprocess_raw_stream",
            "stream_ms": int((time.perf_counter() - started) * 1000),
        }

    def warm_up_inprocess(self, text: str = "系统启动完成。") -> dict[str, Any]:
        self._ensure_python_import_path()
        try:
            from piper import SynthesisConfig
        except Exception as exc:
            raise PiperError("Unable to import Piper SynthesisConfig") from exc

        piper_voice = self._load_voice_inprocess()
        syn_config = SynthesisConfig(
            length_scale=self.settings.default_length_scale,
            noise_scale=self.settings.default_noise_scale,
            noise_w_scale=self.settings.default_noise_w,
        )
        started = time.perf_counter()
        byte_count = 0
        for chunk in piper_voice.synthesize(text, syn_config):
            byte_count += len(chunk.audio_int16_bytes)
        return {
            "mode": "inprocess_warmup",
            "sample_rate": int(piper_voice.config.sample_rate),
            "bytes": byte_count,
            "warmup_ms": int((time.perf_counter() - started) * 1000),
        }

    def synthesize_to_file(
        self,
        text: str,
        output_wav: Path,
        *,
        voice: str | Path | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
        timeout_seconds: int | None = None,
    ) -> Path:
        piper_path, model, config = self._validate_ready(voice=voice)
        output = Path(output_wav)
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")

        command = self._piper_command(
            piper_path=piper_path,
            model=model,
            config=config,
            length_scale=float(length_scale if length_scale is not None else self.settings.default_length_scale),
            noise_scale=float(noise_scale if noise_scale is not None else self.settings.default_noise_scale),
            noise_w=float(noise_w if noise_w is not None else self.settings.default_noise_w),
            output_file=tmp,
        )

        started = time.perf_counter()
        try:
            result = subprocess.run(
                command,
                input=(text.strip() + "\n"),
                text=True,
                capture_output=True,
                env=self._subprocess_env(),
                timeout=timeout_seconds or self.settings.synth_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            tmp.unlink(missing_ok=True)
            raise PiperError(f"Piper timed out after {exc.timeout}s while synthesizing text: {text[:60]}") from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        stderr = result.stderr.strip()
        if stderr:
            logger.debug("piper stderr: %s", stderr)

        if result.returncode != 0:
            tmp.unlink(missing_ok=True)
            stdout = result.stdout.strip()
            raise PiperError(
                f"Piper failed with code {result.returncode}. stderr={stderr!r} stdout={stdout!r}"
                f"{self._failure_hint(stderr)}"
            )

        if not tmp.exists() or tmp.stat().st_size <= 44:
            tmp.unlink(missing_ok=True)
            raise PiperError(f"Piper produced an empty WAV file for text: {text[:60]}")

        os.replace(tmp, output)
        logger.info("synthesized %d chars to %s in %d ms", len(text), output, elapsed_ms)
        return output

    def synthesize_chunks(
        self,
        chunks: list[str],
        *,
        voice: str | Path | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
        output_dir: Path | None = None,
    ) -> list[Path]:
        destination = output_dir or (self.settings.cache_dir / "_chunks")
        destination.mkdir(parents=True, exist_ok=True)
        wavs: list[Path] = []
        for index, chunk in enumerate(chunks):
            wav = destination / f"chunk_{int(time.time())}_{index}_{uuid.uuid4().hex[:8]}.wav"
            wavs.append(
                self.synthesize_to_file(
                    chunk,
                    wav,
                    voice=voice,
                    length_scale=length_scale,
                    noise_scale=noise_scale,
                    noise_w=noise_w,
                )
            )
        return wavs

    def warm_up(self, text: str = "系统启动完成。") -> Path:
        output = self.settings.cache_dir / "_warmup.wav"
        return self.synthesize_to_file(text, output)


def concat_wavs(wavs: list[Path], destination: Path) -> Path:
    if not wavs:
        raise ValueError("No WAV files to concatenate")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if len(wavs) == 1:
        tmp = destination.with_suffix(destination.suffix + f".{uuid.uuid4().hex}.tmp")
        shutil.copy2(wavs[0], tmp)
        os.replace(tmp, destination)
        return destination

    tmp = destination.with_suffix(destination.suffix + f".{uuid.uuid4().hex}.tmp")
    params = None
    frames: list[bytes] = []
    for wav in wavs:
        with wave.open(str(wav), "rb") as reader:
            current_params = reader.getparams()
            if params is None:
                params = current_params
            elif current_params[:3] != params[:3] or current_params.framerate != params.framerate:
                raise PiperError(f"Cannot concatenate WAV files with different audio formats: {wav}")
            frames.append(reader.readframes(reader.getnframes()))

    assert params is not None
    with wave.open(str(tmp), "wb") as writer:
        writer.setparams(params)
        for data in frames:
            writer.writeframes(data)

    os.replace(tmp, destination)
    return destination


class TTSService:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: PiperEngine | None = None,
        cache: AudioCache | None = None,
        player: AudioPlayer | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine or PiperEngine(settings)
        self.cache = cache or AudioCache(settings.cache_dir)
        self.player = player or AudioPlayer(aplay_bin=settings.aplay_bin, device=settings.audio_device)
        self.runtime_dir = settings.cache_dir / "_runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def normalize(self, text: str) -> NormalizedText:
        return normalize_text(
            text,
            enable_traditional_to_simplified=self.settings.enable_traditional_to_simplified,
            max_text_chars=self.settings.max_text_chars,
            max_chunk_chars=self.settings.max_chunk_chars,
        )

    def _params(
        self,
        length_scale: float | None,
        noise_scale: float | None,
        noise_w: float | None,
    ) -> tuple[float, float, float]:
        return (
            float(length_scale if length_scale is not None else self.settings.default_length_scale),
            float(noise_scale if noise_scale is not None else self.settings.default_noise_scale),
            float(noise_w if noise_w is not None else self.settings.default_noise_w),
        )

    def _synthesize_one_chunk(
        self,
        *,
        chunk: str,
        index: int,
        voice: str | Path | None,
        model_path: Path,
        length: float,
        noise: float,
        noise_weight: float,
    ) -> tuple[Path, bool, bool, int]:
        started = time.perf_counter()
        key = self.cache.key_for(
            text=chunk,
            model_path=model_path,
            length_scale=length,
            noise_scale=noise,
            noise_w=noise_weight,
        )
        cached = self.cache.get_cached_wav(key) if self.settings.enable_cache else None
        if cached is not None:
            return cached, True, False, int((time.perf_counter() - started) * 1000)

        tmp = self.runtime_dir / f"synth_{int(time.time())}_{index}_{uuid.uuid4().hex[:8]}.wav"
        self.engine.synthesize_to_file(
            chunk,
            tmp,
            voice=voice,
            length_scale=length,
            noise_scale=noise,
            noise_w=noise_weight,
        )
        if self.settings.enable_cache:
            wav = self.cache.put_cached_wav(key, tmp)
            tmp.unlink(missing_ok=True)
        else:
            wav = tmp
        return wav, False, True, int((time.perf_counter() - started) * 1000)

    def synthesize_text(
        self,
        text: str,
        *,
        voice: str | Path | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
    ) -> dict[str, Any]:
        normalized = self.normalize(text)
        if not normalized.chunks:
            raise PiperError("No speakable text after normalization")

        length, noise, noise_weight = self._params(length_scale, noise_scale, noise_w)
        model_path, _ = self.engine._resolve_voice(voice)
        wavs: list[Path] = []
        cache_hits = 0
        synthesized = 0
        synth_ms = 0

        for index, chunk in enumerate(normalized.chunks):
            wav, hit, did_synthesize, elapsed_ms = self._synthesize_one_chunk(
                chunk=chunk,
                index=index,
                voice=voice,
                model_path=model_path,
                length=length,
                noise=noise,
                noise_weight=noise_weight,
            )
            wavs.append(wav)
            synth_ms += elapsed_ms
            cache_hits += int(hit)
            synthesized += int(did_synthesize)

        return {
            "original_text": normalized.original_text,
            "normalized_text": normalized.normalized_text,
            "chunks": normalized.chunks,
            "warnings": normalized.warnings,
            "wav_files": [str(path) for path in wavs],
            "cache_hits": cache_hits,
            "synthesized_chunks": synthesized,
            "synth_ms": synth_ms,
        }

    def play_wavs(self, wav_files: list[str | Path], *, blocking: bool = True) -> dict[str, Any]:
        started = time.perf_counter()
        paths = [Path(path) for path in wav_files]
        if not blocking:
            import threading

            thread = threading.Thread(target=self.play_wavs, args=(paths,), kwargs={"blocking": True}, daemon=True)
            thread.start()
            return {"blocking": False, "queued_for_playback": len(paths), "play_ms": 0}

        results = [self.player.play_file(path, blocking=True) for path in paths]
        return {
            "blocking": True,
            "played": len(results),
            "play_ms": int((time.perf_counter() - started) * 1000),
        }

    def speak(
        self,
        text: str,
        *,
        blocking: bool = True,
        play: bool = True,
        output: str | Path | None = None,
        voice: str | Path | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
        stream: bool | None = None,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        normalized = self.normalize(text)
        if not normalized.chunks:
            raise PiperError("No speakable text after normalization")

        should_stream = play and output is None and (self.settings.enable_stream_playback if stream is None else stream)
        if should_stream:
            if not blocking:
                thread = threading.Thread(
                    target=self.speak,
                    args=(text,),
                    kwargs={
                        "blocking": True,
                        "play": True,
                        "output": None,
                        "voice": voice,
                        "length_scale": length_scale,
                        "noise_scale": noise_scale,
                        "noise_w": noise_w,
                        "stream": True,
                    },
                    daemon=True,
                )
                thread.start()
                return {
                    "original_text": normalized.original_text,
                    "normalized_text": normalized.normalized_text,
                    "chunks": normalized.chunks,
                    "warnings": normalized.warnings,
                    "wav_files": [],
                    "cache_hits": 0,
                    "synthesized_chunks": len(normalized.chunks),
                    "synth_ms": 0,
                    "output_wav": None,
                    "playback": {
                        "blocking": False,
                        "streaming": True,
                        "queued_for_playback": len(normalized.chunks),
                    },
                    "total_ms": int((time.perf_counter() - total_started) * 1000),
                }

            stream_text = "\n".join(normalized.chunks)
            if self.settings.enable_inprocess_piper:
                try:
                    playback = self.engine.stream_inprocess_to_player(
                        stream_text,
                        self.player,
                        voice=voice,
                        length_scale=length_scale,
                        noise_scale=noise_scale,
                        noise_w=noise_w,
                    )
                except PiperError as exc:
                    logger.warning("in-process Piper failed, falling back to subprocess streaming: %s", exc)
                    playback = self.engine.stream_raw_to_player(
                        stream_text,
                        self.player,
                        voice=voice,
                        length_scale=length_scale,
                        noise_scale=noise_scale,
                        noise_w=noise_w,
                    )
            else:
                playback = self.engine.stream_raw_to_player(
                    stream_text,
                    self.player,
                    voice=voice,
                    length_scale=length_scale,
                    noise_scale=noise_scale,
                    noise_w=noise_w,
                )
            return {
                "original_text": normalized.original_text,
                "normalized_text": normalized.normalized_text,
                "chunks": normalized.chunks,
                "warnings": normalized.warnings,
                "wav_files": [],
                "cache_hits": 0,
                "synthesized_chunks": len(normalized.chunks),
                "synth_ms": playback.get("stream_ms", 0),
                "output_wav": None,
                "playback": playback,
                "total_ms": int((time.perf_counter() - total_started) * 1000),
            }

        length, noise, noise_weight = self._params(length_scale, noise_scale, noise_w)
        model_path, _ = self.engine._resolve_voice(voice)
        wavs: list[Path] = []
        cache_hits = 0
        synthesized = 0
        synth_ms = 0
        play_ms = 0
        played = 0

        for index, chunk in enumerate(normalized.chunks):
            wav, hit, did_synthesize, elapsed_ms = self._synthesize_one_chunk(
                chunk=chunk,
                index=index,
                voice=voice,
                model_path=model_path,
                length=length,
                noise=noise,
                noise_weight=noise_weight,
            )
            wavs.append(wav)
            cache_hits += int(hit)
            synthesized += int(did_synthesize)
            synth_ms += elapsed_ms

            if play and blocking:
                play_started = time.perf_counter()
                self.player.play_file(wav, blocking=True)
                play_ms += int((time.perf_counter() - play_started) * 1000)
                played += 1

        output_path = None
        if output is not None:
            output_path = concat_wavs(wavs, Path(output))

        playback = {"play_ms": play_ms, "played": played, "blocking": blocking}
        if play and not blocking:
            playback = self.play_wavs(wavs, blocking=False)

        return {
            "original_text": normalized.original_text,
            "normalized_text": normalized.normalized_text,
            "chunks": normalized.chunks,
            "warnings": normalized.warnings,
            "wav_files": [str(path) for path in wavs],
            "cache_hits": cache_hits,
            "synthesized_chunks": synthesized,
            "synth_ms": synth_ms,
            "output_wav": str(output_path) if output_path else None,
            "playback": playback,
            "total_ms": int((time.perf_counter() - total_started) * 1000),
        }

    def stop_current(self) -> bool:
        return self.player.stop()

    def warm_up(self, text: str = "系统启动完成。") -> dict[str, Any]:
        if self.settings.enable_stream_playback and self.settings.enable_inprocess_piper:
            normalized = self.normalize(text)
            return {
                "original_text": normalized.original_text,
                "normalized_text": normalized.normalized_text,
                "chunks": normalized.chunks,
                **self.engine.warm_up_inprocess("\n".join(normalized.chunks)),
            }
        return self.speak(text, blocking=True, play=False)

    def list_voices(self) -> list[dict[str, Any]]:
        voices = []
        for model in sorted(self.settings.model_dir.glob("*.onnx")):
            config = model.with_suffix(model.suffix + ".json")
            voices.append(
                {
                    "name": model.stem,
                    "model": str(model),
                    "config": str(config),
                    "config_exists": config.exists(),
                    "is_default": model.resolve() == self.settings.piper_model.resolve(),
                }
            )
        return voices

    def health(self) -> dict[str, Any]:
        return {
            "engine": self.engine.check_ready(),
            "audio": self.player.check_ready(),
            "cache": self.cache.stats(),
            "settings": self.settings.health(),
        }
