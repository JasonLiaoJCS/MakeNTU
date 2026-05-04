#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="$PROJECT_DIR/systemd/jetson-piper-tts.service"
SERVICE_DST="/etc/systemd/system/jetson-piper-tts.service"
RUN_USER="${RUN_USER:-$(id -un)}"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "ERROR: .venv not found. Run ./scripts/setup_jetson.sh first."
  exit 1
fi

TMP_FILE="$(mktemp)"
sed \
  -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
  -e "s#__USER__#$RUN_USER#g" \
  "$SERVICE_SRC" > "$TMP_FILE"

$SUDO cp "$TMP_FILE" "$SERVICE_DST"
rm -f "$TMP_FILE"

$SUDO systemctl daemon-reload
$SUDO systemctl enable jetson-piper-tts.service

echo "Installed $SERVICE_DST"
echo
echo "Commands:"
echo "  sudo systemctl start jetson-piper-tts"
echo "  sudo systemctl status jetson-piper-tts --no-pager"
echo "  journalctl -u jetson-piper-tts -f"
