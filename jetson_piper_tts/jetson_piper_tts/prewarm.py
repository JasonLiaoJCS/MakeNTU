from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import PROJECT_ROOT, get_settings
from .piper_engine import TTSService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prewarm Piper and optionally pre-cache preset phrases.")
    parser.add_argument("--text", default="系统启动完成。", help="Warm-up text.")
    parser.add_argument("--play", action="store_true", help="Play the warm-up phrase.")
    parser.add_argument("--presets", action="store_true", help="Pre-synthesize all preset phrases.")
    parser.add_argument("--preset-file", type=Path, default=PROJECT_ROOT / "presets" / "preset_phrases.json")
    parser.add_argument("--length-scale", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = TTSService(get_settings())

    phrases = {"warmup": args.text}
    if args.presets:
        with args.preset_file.open("r", encoding="utf-8") as file:
            phrases.update(json.load(file))

    for name, text in phrases.items():
        result = service.speak(
            text,
            blocking=True,
            play=args.play and name == "warmup",
            length_scale=args.length_scale,
        )
        print(f"{name:>12}: chunks={len(result['chunks'])} cache_hits={result['cache_hits']} total_ms={result['total_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
