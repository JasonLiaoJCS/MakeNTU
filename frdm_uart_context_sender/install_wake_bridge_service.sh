#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="$SCRIPT_DIR/systemd/makentu-wake-bridge.service"
SERVICE_DST="$HOME/.config/systemd/user/makentu-wake-bridge.service"
ENV_DIR="$HOME/.config/makentu"
ENV_DST="$ENV_DIR/wake-bridge.env"

BLE_ADDRESS="${ESP32_BLE_ADDRESS:-}"
BLE_ADAPTER="${ESP32_BLE_ADAPTER:-hci0}"
FAN_PWM="${FAN_MIN_PWM:-96}"

usage() {
  cat <<'EOF'
Usage:
  install_wake_bridge_service.sh [--address MAC] [--adapter hci0] [--fan-min-pwm 96]

Examples:
  ./frdm_uart_context_sender/install_wake_bridge_service.sh --address 78:E3:6D:18:94:6A --adapter hci0
  ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A ./frdm_uart_context_sender/install_wake_bridge_service.sh
EOF
}

upsert_env() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --address)
      BLE_ADDRESS="${2:-}"
      shift 2
      ;;
    --adapter)
      BLE_ADAPTER="${2:-}"
      shift 2
      ;;
    --fan-min-pwm)
      FAN_PWM="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$(dirname "$SERVICE_DST")" "$ENV_DIR"
install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"

if [[ ! -f "$ENV_DST" ]]; then
  cat >"$ENV_DST" <<EOF
ESP32_BLE=1
ESP32_BLE_ADDRESS=$BLE_ADDRESS
ESP32_BLE_ADAPTER=$BLE_ADAPTER
ESP32_BLE_SCAN_DUPLICATES=1
FAN_MIN_PWM=$FAN_PWM

SERVER_URL=http://192.168.1.127:8766/voice-chat
FOCUS_SERVER_URL=http://192.168.1.127:8766/focus-check

DEVICE_READY_TIMEOUT=45
MAKE_NTU_TTS_VOLUME_GAIN=3.6
BEEP_PLAYER=aplay
BEEP_VOLUME=0.55
MUSIC_MPV_VOLUME=150
MUSIC_MPV_VOLUME_MAX=200
TEMP_ROOM_UART_INTERVAL_SEC=10
TEMP_ROOM_UART_MAX_AGE_SEC=30
EOF
  chmod 0644 "$ENV_DST"
else
  echo "Keeping existing env file: $ENV_DST"
  if [[ -n "$BLE_ADDRESS" ]]; then
    upsert_env ESP32_BLE_ADDRESS "$BLE_ADDRESS" "$ENV_DST"
  fi
  if [[ -n "$BLE_ADAPTER" ]]; then
    upsert_env ESP32_BLE_ADAPTER "$BLE_ADAPTER" "$ENV_DST"
  fi
  if [[ -n "$FAN_PWM" ]]; then
    upsert_env FAN_MIN_PWM "$FAN_PWM" "$ENV_DST"
  fi
  echo "Updated ESP32_BLE_ADDRESS / ESP32_BLE_ADAPTER / FAN_MIN_PWM from arguments or environment."
fi

systemctl --user daemon-reload
systemctl --user enable makentu-wake-bridge.service

if command -v loginctl >/dev/null 2>&1; then
  if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    echo "Enabling user linger so this starts after boot before you open a terminal..."
    if ! loginctl enable-linger "$USER" 2>/dev/null; then
      sudo loginctl enable-linger "$USER"
    fi
  fi
fi

echo
echo "Installed user service:"
echo "  $SERVICE_DST"
echo
echo "Runtime env:"
echo "  $ENV_DST"
echo
echo "Start now:"
echo "  systemctl --user start makentu-wake-bridge.service"
echo
echo "Watch logs:"
echo "  journalctl --user -u makentu-wake-bridge.service -f"
