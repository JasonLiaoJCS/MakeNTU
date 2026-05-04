from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import get_settings
from .piper_engine import TTSService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Speak text with local Piper TTS.")
    parser.add_argument("text", nargs="+", help="Text to synthesize.")
    parser.add_argument("--no-play", action="store_true", help="Only synthesize; do not play audio.")
    parser.add_argument("--output", type=Path, help="Write combined WAV to this path.")
    parser.add_argument("--voice", help="Voice name in models/ or path to .onnx.")
    parser.add_argument("--length-scale", type=float, help="Piper length_scale. Lower is faster.")
    parser.add_argument("--noise-scale", type=float, help="Piper noise_scale.")
    parser.add_argument("--noise-w", type=float, help="Piper noise_w.")
    parser.add_argument("--device", help="ALSA device, e.g. default or plughw:1,0.")
    parser.add_argument("--stream", action="store_true", default=None, help="Stream raw PCM directly to aplay.")
    parser.add_argument("--file-playback", action="store_true", help="Synthesize WAV files before playback.")
    parser.add_argument("--json", action="store_true", help="Print full JSON metrics.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.device is not None:
        settings = settings.with_overrides(audio_device=args.device)

    service = TTSService(settings)
    result = service.speak(
        " ".join(args.text),
        blocking=True,
        play=not args.no_play,
        output=args.output,
        voice=args.voice,
        length_scale=args.length_scale,
        noise_scale=args.noise_scale,
        noise_w=args.noise_w,
        stream=False if args.file_playback else args.stream,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"normalized: {result['normalized_text']}")
        print(f"chunks: {len(result['chunks'])}, cache_hits: {result['cache_hits']}")
        print(f"synth_ms: {result['synth_ms']}, total_ms: {result['total_ms']}")
        if result.get("output_wav"):
            print(f"output: {result['output_wav']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
