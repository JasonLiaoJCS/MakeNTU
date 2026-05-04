from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(value: str) -> str:
    value = _SAFE_RE.sub("_", value).strip("._")
    return value[:80] or "voice"


@dataclass(frozen=True, slots=True)
class CacheKey:
    text: str
    model_path: str
    length_scale: float
    noise_scale: float
    noise_w: float

    def digest(self) -> str:
        payload = json.dumps(
            {
                "text": self.text,
                "model_path": self.model_path,
                "length_scale": round(float(self.length_scale), 4),
                "noise_scale": round(float(self.noise_scale), 4),
                "noise_w": round(float(self.noise_w), 4),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AudioCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def key_for(
        self,
        *,
        text: str,
        model_path: Path,
        length_scale: float,
        noise_scale: float,
        noise_w: float,
    ) -> CacheKey:
        return CacheKey(
            text=text,
            model_path=str(Path(model_path).expanduser().resolve()),
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w=noise_w,
        )

    def path_for(self, key: CacheKey) -> Path:
        digest = key.digest()
        voice = _safe(Path(key.model_path).stem)
        return self.cache_dir / f"{voice}_l{key.length_scale:.2f}_n{key.noise_scale:.2f}_w{key.noise_w:.2f}_{digest[:20]}.wav"

    def get_cached_wav(self, key: CacheKey) -> Path | None:
        path = self.path_for(key)
        if path.exists() and path.stat().st_size > 44:
            return path
        return None

    def put_cached_wav(self, key: CacheKey, source_wav: Path) -> Path:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
        shutil.copy2(source_wav, tmp)
        os.replace(tmp, destination)
        return destination

    def clear_cache(self) -> int:
        count = 0
        for wav in self.cache_dir.glob("*.wav"):
            wav.unlink(missing_ok=True)
            count += 1
        return count

    def stats(self) -> dict[str, Any]:
        files = list(self.cache_dir.glob("*.wav"))
        return {
            "cache_dir": str(self.cache_dir),
            "wav_files": len(files),
            "bytes": sum(path.stat().st_size for path in files if path.exists()),
        }
