from __future__ import annotations

import os
import shutil
import site
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_path(name: str, default: Path | str) -> Path:
    raw = os.getenv(name)
    value = Path(raw) if raw else Path(default)
    if not value.is_absolute():
        value = PROJECT_ROOT / value
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(slots=True)
class Settings:
    piper_bin: str
    piper_model: Path
    piper_config: Path
    audio_device: str | None
    aplay_bin: str
    extra_pythonpath: str | None
    cache_dir: Path
    model_dir: Path
    host: str
    port: int
    default_length_scale: float
    default_noise_scale: float
    default_noise_w: float
    default_volume_gain: float
    max_text_chars: int
    max_chunk_chars: int
    enable_traditional_to_simplified: bool
    enable_cache: bool
    enable_stream_playback: bool
    enable_inprocess_piper: bool
    enable_hf_offline: bool
    synth_timeout_seconds: int
    server_warmup: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        load_dotenv()

        model_dir = _env_path("MODEL_DIR", PROJECT_ROOT / "models")
        default_model = model_dir / "zh_CN-chaowen-medium.onnx"
        piper_model = _env_path("PIPER_MODEL", default_model)
        default_config = piper_model.with_suffix(piper_model.suffix + ".json")

        audio_device = os.getenv("AUDIO_DEVICE", "default").strip()
        if audio_device == "":
            audio_device = None

        return cls(
            piper_bin=os.getenv("PIPER_BIN", "piper"),
            piper_model=piper_model,
            piper_config=_env_path("PIPER_CONFIG", default_config),
            audio_device=audio_device,
            aplay_bin=os.getenv("APLAY_BIN", "aplay"),
            extra_pythonpath=os.getenv("EXTRA_PYTHONPATH", "").strip() or None,
            cache_dir=_env_path("CACHE_DIR", PROJECT_ROOT / "cache"),
            model_dir=model_dir,
            host=os.getenv("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8777),
            default_length_scale=_env_float("DEFAULT_LENGTH_SCALE", 0.90),
            default_noise_scale=_env_float("DEFAULT_NOISE_SCALE", 0.667),
            default_noise_w=_env_float("DEFAULT_NOISE_W", 0.8),
            default_volume_gain=_env_float("DEFAULT_VOLUME_GAIN", 1.0),
            max_text_chars=_env_int("MAX_TEXT_CHARS", 600),
            max_chunk_chars=_env_int("MAX_CHUNK_CHARS", 70),
            enable_traditional_to_simplified=_env_bool("ENABLE_TRADITIONAL_TO_SIMPLIFIED", True),
            enable_cache=_env_bool("ENABLE_CACHE", True),
            enable_stream_playback=_env_bool("ENABLE_STREAM_PLAYBACK", True),
            enable_inprocess_piper=_env_bool("ENABLE_INPROCESS_PIPER", True),
            enable_hf_offline=_env_bool("ENABLE_HF_OFFLINE", False),
            synth_timeout_seconds=_env_int("SYNTH_TIMEOUT_SECONDS", 45),
            server_warmup=_env_bool("SERVER_WARMUP", True),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    def with_overrides(self, **kwargs: Any) -> "Settings":
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})

    @property
    def piper_path(self) -> str | None:
        if os.path.sep in self.piper_bin or (os.path.altsep and os.path.altsep in self.piper_bin):
            path = Path(self.piper_bin).expanduser()
            return str(path) if path.exists() and os.access(path, os.X_OK) else None
        return shutil.which(self.piper_bin)

    @property
    def aplay_path(self) -> str | None:
        if os.path.sep in self.aplay_bin or (os.path.altsep and os.path.altsep in self.aplay_bin):
            path = Path(self.aplay_bin).expanduser()
            return str(path) if path.exists() and os.access(path, os.X_OK) else None
        return shutil.which(self.aplay_bin)

    def health(self) -> dict[str, Any]:
        return {
            "project_root": str(PROJECT_ROOT),
            "piper_bin": self.piper_bin,
            "piper_path": self.piper_path,
            "piper_executable": self.piper_path is not None,
            "piper_model": str(self.piper_model),
            "piper_model_exists": self.piper_model.exists(),
            "piper_config": str(self.piper_config),
            "piper_config_exists": self.piper_config.exists(),
            "model_dir": str(self.model_dir),
            "cache_dir": str(self.cache_dir),
            "audio_backend": "aplay",
            "audio_device": self.audio_device or "default",
            "aplay_bin": self.aplay_bin,
            "aplay_path": self.aplay_path,
            "aplay_executable": self.aplay_path is not None,
            "extra_pythonpath": self.extra_pythonpath,
            "user_site_packages": site.getusersitepackages(),
            "default_length_scale": self.default_length_scale,
            "default_noise_scale": self.default_noise_scale,
            "default_noise_w": self.default_noise_w,
            "default_volume_gain": self.default_volume_gain,
            "enable_cache": self.enable_cache,
            "enable_stream_playback": self.enable_stream_playback,
            "enable_inprocess_piper": self.enable_inprocess_piper,
            "enable_hf_offline": self.enable_hf_offline,
            "enable_traditional_to_simplified": self.enable_traditional_to_simplified,
        }


def get_settings() -> Settings:
    return Settings.from_env()
