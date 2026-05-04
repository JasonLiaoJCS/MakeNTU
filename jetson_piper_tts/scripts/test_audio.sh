#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE="${AUDIO_DEVICE:-default}"
cd "$PROJECT_DIR"

echo "==> ALSA playback hardware"
aplay -l || true
echo
echo "==> ALSA PCM devices"
aplay -L | sed -n '1,80p' || true

echo
echo "==> speaker-test on device: $DEVICE"
echo "You should hear a short sine tone."
timeout 3 speaker-test -D "$DEVICE" -t sine -f 440 -c 2 || true

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

echo
echo "==> Piper synthesis + aplay test"
python -m jetson_piper_tts.speak --device "$DEVICE" "系统测试声音。"
