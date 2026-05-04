#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${MODEL_DIR:-$PROJECT_DIR/models}"
VOICE="${1:-${PIPER_VOICE:-zh_CN-chaowen-medium}}"
BASE_URL="${PIPER_VOICE_BASE_URL:-https://huggingface.co/rhasspy/piper-voices/resolve/main}"

mkdir -p "$MODELS_DIR"

IFS='-' read -r LOCALE SPEAKER QUALITY <<< "$VOICE"
if [[ -z "${LOCALE:-}" || -z "${SPEAKER:-}" || -z "${QUALITY:-}" ]]; then
  echo "ERROR: voice name should look like zh_CN-chaowen-medium"
  exit 1
fi

LANG="${LOCALE%%_*}"
VOICE_PATH="$LANG/$LOCALE/$SPEAKER/$QUALITY"
MODEL_URL="$BASE_URL/$VOICE_PATH/$VOICE.onnx"
CONFIG_URL="$BASE_URL/$VOICE_PATH/$VOICE.onnx.json"
MODEL_OUT="$MODELS_DIR/$VOICE.onnx"
CONFIG_OUT="$MODELS_DIR/$VOICE.onnx.json"

download_one() {
  local url="$1"
  local output="$2"
  echo "==> Downloading $url"
  if ! curl -fL --retry 3 --retry-delay 2 -o "$output.tmp" "$url"; then
    rm -f "$output.tmp"
    echo "ERROR: download failed: $url"
    return 1
  fi
  if [[ ! -s "$output.tmp" ]]; then
    rm -f "$output.tmp"
    echo "ERROR: downloaded file is empty: $url"
    return 1
  fi
  mv "$output.tmp" "$output"
}

if ! download_one "$MODEL_URL" "$MODEL_OUT"; then
  echo
  echo "Manual download:"
  echo "  $MODEL_URL"
  echo "Save as:"
  echo "  $MODEL_OUT"
  exit 1
fi

if ! download_one "$CONFIG_URL" "$CONFIG_OUT"; then
  echo
  echo "Manual download:"
  echo "  $CONFIG_URL"
  echo "Save as:"
  echo "  $CONFIG_OUT"
  exit 1
fi

echo
echo "Downloaded voice:"
ls -lh "$MODEL_OUT" "$CONFIG_OUT"
echo
echo "Default .env values:"
echo "  PIPER_MODEL=$MODEL_OUT"
echo "  PIPER_CONFIG=$CONFIG_OUT"
