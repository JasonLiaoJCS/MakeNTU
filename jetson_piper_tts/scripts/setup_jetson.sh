#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
  echo "WARN: expected Jetson ARM64/aarch64, got: $ARCH"
fi

echo "==> Installing system packages"
$SUDO apt-get update
$SUDO apt-get install -y \
  python3-venv \
  python3-pip \
  espeak-ng \
  alsa-utils \
  ffmpeg \
  curl \
  git

echo "==> Creating Python virtual environment"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing Python requirements"
python -m pip install --upgrade pip setuptools wheel
TMP_REQ="$(mktemp)"
grep -v -E '^(piper-tts)([<=> ].*)?$' requirements.txt > "$TMP_REQ"
python -m pip install -r "$TMP_REQ"
rm -f "$TMP_REQ"

echo "==> Installing Piper Python package and Chinese phonemizer dependencies"
if ! python -m pip install piper-tts; then
  echo
  echo "WARN: pip install piper-tts failed on this platform."
  echo "      You can still use this project with a Piper binary:"
  echo "      1. Install or build piper manually."
  echo "      2. Set PIPER_BIN=/path/to/piper in .env."
fi
python -m pip install g2pw unicode-rbnf sentence-stream

USER_SITE="$(python - <<'PY'
import site
print(site.getusersitepackages())
PY
)"

echo "==> Checking PyTorch for Chinese phonemizer"
if python - <<'PY'
import torch
print(torch.__version__)
PY
then
  echo "PyTorch is importable inside .venv."
elif PYTHONPATH="$USER_SITE${PYTHONPATH:+:$PYTHONPATH}" python - <<'PY'
import torch
print(torch.__version__)
PY
then
  echo "PyTorch is available from user site: $USER_SITE"
  echo "The TTS engine will automatically add user site to Piper's PYTHONPATH."
else
  echo
  echo "WARN: PyTorch is not importable. Chinese Piper voices need torch through g2pw."
  echo "      Install Jetson-compatible PyTorch, or set EXTRA_PYTHONPATH in .env to a site-packages path containing torch."
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example"
fi

echo
echo "Install complete."
echo
echo "Next steps:"
echo "  1. ./scripts/download_voice.sh"
echo "  2. source .venv/bin/activate"
echo "  3. python -m jetson_piper_tts.speak \"系统测试声音。\""
echo "  4. python -m jetson_piper_tts.server"
