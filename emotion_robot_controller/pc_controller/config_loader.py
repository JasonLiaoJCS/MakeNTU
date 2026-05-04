from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigError(RuntimeError):
    """Raised when config.yaml is missing or malformed."""


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config.yaml and .env.

    The .env file is optional. Environment variables already exported by the
    shell stay available and are not overwritten.
    """

    root = PROJECT_ROOT
    path = Path(config_path) if config_path else root / "config.yaml"
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    load_dotenv(root / ".env", override=False)

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    _require_section(data, "serial")
    _require_section(data, "ai")
    _require_section(data, "motion")
    _require_section(data, "emotion")
    return data


def _require_section(data: dict[str, Any], name: str) -> None:
    if name not in data or not isinstance(data[name], dict):
        raise ConfigError(f"Missing required config section: {name}")


def project_path(relative_or_absolute: str | Path) -> Path:
    path = Path(relative_or_absolute)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

