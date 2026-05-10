#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/asrlab-yian/MakeNTU"
VENV_DIR="$ROOT_DIR/emotion_robot_controller/.venv"

SERVER_URL="${SERVER_URL:-http://100.108.141.26:8766/voice-chat}"
FOCUS_SERVER_URL="${FOCUS_SERVER_URL:-http://100.108.141.26:8766/focus-check}"
DEVICE_READY_TIMEOUT="${DEVICE_READY_TIMEOUT:-30}"
TTS_VOLUME_GAIN="${WAKE_TTS_VOLUME_GAIN:-${PIPELINE_TTS_VOLUME_GAIN:-3.6}}"
MUSIC_MPV_AUDIO_DEVICE="${MUSIC_MPV_AUDIO_DEVICE:-auto}"
MUSIC_MPV_VOLUME="${MUSIC_MPV_VOLUME:-150}"
MUSIC_MPV_VOLUME_MAX="${MUSIC_MPV_VOLUME_MAX:-200}"
MUSIC_MPV_READY_TIMEOUT="${MUSIC_MPV_READY_TIMEOUT:-1.5}"
BEEP_PLAYER="${BEEP_PLAYER:-aplay}"
BEEP_VOLUME="${BEEP_VOLUME:-0.55}"
STARTUP_WEATHER_TEXT="${STARTUP_WEATHER_TEXT:-今天天氣如何}"
STARTUP_WEATHER_CURRENT_TEXT="${STARTUP_WEATHER_CURRENT_TEXT:-現在天氣如何}"
ESP32_BLE="${ESP32_BLE:-1}"
ESP32_BLE_NAME="${ESP32_BLE_NAME:-ESP32S3_FAN_LED_TEMP}"
ESP32_BLE_ADDRESS="${ESP32_BLE_ADDRESS:-}"
ESP32_BLE_ADAPTER="${ESP32_BLE_ADAPTER:-${BLE_ADAPTER:-}}"
ESP32_BLE_MIN_FAN_PWM="${ESP32_BLE_MIN_FAN_PWM:-${FAN_MIN_PWM:-96}}"
ESP32_BLE_SCAN_DUPLICATES="${ESP32_BLE_SCAN_DUPLICATES:-1}"
ESP32_DASHBOARD_HOST="${ESP32_DASHBOARD_HOST:-127.0.0.1}"
ESP32_DASHBOARD_PORT="${ESP32_DASHBOARD_PORT:-8791}"
ESP32_BLE_SIDECAR="${ESP32_BLE_SIDECAR:-0}"
ESP32_BLE_API_URL="${ESP32_BLE_API_URL:-http://127.0.0.1:$ESP32_DASHBOARD_PORT/api/esp32}"
ESP32_BLE_API_TIMEOUT="${ESP32_BLE_API_TIMEOUT:-0.2}"
if [[ -z "${ESP32_TEMPERATURE_MODE:-}" ]]; then
  case "${ESP32_BLE_SIDECAR,,}" in
    1|true|yes|on)
      ESP32_TEMPERATURE_MODE="pull"
      ;;
    *)
      ESP32_TEMPERATURE_MODE="push"
      ;;
  esac
fi
ESP32_TEMPERATURE_URL="${ESP32_TEMPERATURE_URL:-${ESP32_BLE_API_URL%/}/status}"
ESP32_TEMPERATURE_TIMEOUT="${ESP32_TEMPERATURE_TIMEOUT:-0.2}"
TEMP_ROOM_UART_INTERVAL_SEC="${TEMP_ROOM_UART_INTERVAL_SEC:-10}"
TEMP_ROOM_UART_MAX_AGE_SEC="${TEMP_ROOM_UART_MAX_AGE_SEC:-30}"
WAKE_STATUS_PATH="${WAKE_STATUS_PATH:-$ROOT_DIR/frdm_uart_context_sender/logs/wake_status.json}"
PET_IDLE_REFLECTION="${PET_IDLE_REFLECTION:-0}"
export UACDEMO_PCM_VOLUME="${MAKE_NTU_UACDEMO_PCM_VOLUME:-70%}"
export UACDEMO_PULSE_VOLUME="${MAKE_NTU_UACDEMO_PULSE_VOLUME:-70%}"
export DEFAULT_VOLUME_GAIN="$TTS_VOLUME_GAIN"
export AUDIO_DEVICE="${MAKE_NTU_AUDIO_DEVICE:-auto:UACDemo}"
export MIC_DEVICE_KEYWORD="${MAKE_NTU_MIC_DEVICE_KEYWORD:-UACDemo}"
export SPEAKER_DEVICE_KEYWORD="${MAKE_NTU_SPEAKER_DEVICE_KEYWORD:-UACDemo}"
export WAKE_CAMERA_ID="${MAKE_NTU_WAKE_CAMERA_ID:-auto}"
export FOCUS_CAMERA_ID="${MAKE_NTU_FOCUS_CAMERA_ID:-auto}"
export FOCUS_UART_PORT="${MAKE_NTU_FOCUS_UART_PORT:-auto}"

print_redacted_cmd() {
  local arg redact_next=0
  for arg in "$@"; do
    if (( redact_next )); then
      printf '%q ' "***redacted***"
      redact_next=0
      continue
    fi
    case "$arg" in
      --focus-discord-webhook-url|--discord-webhook-url)
        printf '%q ' "$arg"
        redact_next=1
        ;;
      https://discord.com/api/webhooks/*|https://discordapp.com/api/webhooks/*)
        printf '%q ' "***redacted***"
        ;;
      *)
        printf '%q ' "$arg"
        ;;
    esac
  done
  printf '\n'
}

PRINT_COMMAND=0
FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --print-command|--plan|--dry-run)
      PRINT_COMMAND=1
      shift
      ;;
    --)
      shift
      FORWARD_ARGS+=("$@")
      break
      ;;
    *)
      FORWARD_ARGS+=("$1")
      shift
      ;;
  esac
done
set -- "${FORWARD_ARGS[@]}"

cd "$ROOT_DIR"
if [[ "$PRINT_COMMAND" != "1" ]]; then
  source "$VENV_DIR/bin/activate"

  if [ -r "$ROOT_DIR/frdm_uart_context_sender/auto_demo_devices.sh" ]; then
    bash "$ROOT_DIR/frdm_uart_context_sender/auto_demo_devices.sh" || true
  elif [ -r "$ROOT_DIR/frdm_uart_context_sender/set_uacdemo_volume.sh" ]; then
    bash "$ROOT_DIR/frdm_uart_context_sender/set_uacdemo_volume.sh" >/dev/null 2>&1 || true
  elif command -v amixer >/dev/null 2>&1; then
    amixer -c UACDemoV10 sset PCM "$UACDEMO_PCM_VOLUME" unmute >/dev/null 2>&1 || true
  fi
fi

cmd=(
  python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
  --server-url "$SERVER_URL"
  --mic-keyword UACDemo
  --beep-keyword UACDemo
  --beep-player "$BEEP_PLAYER"
  --noisy-room
  --tts-volume-gain "$TTS_VOLUME_GAIN"
  --beep-volume "$BEEP_VOLUME"
  --uart-port auto
  --uart-baudrate 115200
  --frdm-uart-tx-timeout 0.45
  --frdm-uart-failure-threshold 2
  --frdm-uart-circuit-breaker-sec 4.0
  --enable-head-motor
  --boot-normal-delay 2.0
  --device-ready-timeout "$DEVICE_READY_TIMEOUT"
  --wake-threshold 0.75
  --wake-volume-min 500
  --volume-min 1100
  --speech-start-margin 750
  --silence-duration 1.2
  --silence-margin 900
  --max-speech-seconds 5
  --max-recording-seconds 7
  --audio-read-timeout 0.75
  --recording-progress-interval 1.0
  --conversation-mode
  --turn-listen-timeout 8
  --session-idle-timeout 30
  --max-session-turns 20
  --camera-id auto
  --camera-width 320
  --camera-height 240
  --camera-jpeg-quality 70
  --camera-latest-timeout 1.0
  --camera-frame-max-age 2.0
  --focus-script "$ROOT_DIR/frdm_uart_context_sender/focus_work_mode.py"
  --focus-server-url "$FOCUS_SERVER_URL"
  --focus-interval-sec 60
  --focus-first-sample-delay-sec -1
  --focus-duration-min 0
  --focus-log-root /tmp/focus_voice_test
  --focus-alert-threshold 1
  --focus-alert-cooldown-sec 90
  --fan-device-id desk_fan
  --fan-speed-max 100
  --fan-duplicate-suppress-sec 2.0
  --todo-list-path "$ROOT_DIR/frdm_uart_context_sender/logs/todo_list.json"
  --wake-status-path "$WAKE_STATUS_PATH"
  --focus-notify-mode discord
  --music-backend mpv
  --music-mpv-audio-device "$MUSIC_MPV_AUDIO_DEVICE"
  --music-mpv-volume "$MUSIC_MPV_VOLUME"
  --music-mpv-volume-max "$MUSIC_MPV_VOLUME_MAX"
  --music-mpv-ready-timeout "$MUSIC_MPV_READY_TIMEOUT"
  --music-timeout 5
  --music-wake-pause-timeout 0.25
  --music-wake-beep-settle 0.05
  --post-music-standby-cooldown 0.8
  --music-debug
  --weather-default-location Taipei
  --startup-weather-text "$STARTUP_WEATHER_TEXT"
  --startup-weather-current-text "$STARTUP_WEATHER_CURRENT_TEXT"
  --weather-timeout 6
  --weather-api-timeout 4.5
  --weather-debug
  --esp32-temperature-mode "$ESP32_TEMPERATURE_MODE"
  --esp32-temperature-host 0.0.0.0
  --esp32-temperature-port 8790
  --esp32-temperature-path /temperature
  --esp32-temperature-url "$ESP32_TEMPERATURE_URL"
  --esp32-temperature-timeout "$ESP32_TEMPERATURE_TIMEOUT"
  --temp-room-uart-interval-sec "$TEMP_ROOM_UART_INTERVAL_SEC"
  --temp-room-uart-max-age-sec "$TEMP_ROOM_UART_MAX_AGE_SEC"
  --motor-step-delay 0.55
  --motor-smooth-step-deg 120
  --motor-speaking-step-delay 0.72
  --motor-speaking-smooth-step-deg 120
  --motor-reset-repeats 1
  --motor-reset-delay 0.35
  --motor-stop-timeout 6
  --motor-join-timeout 6
  --device-preflight-verbose
  --tts-poll-interval 0.75
  --tts-start-poll-interval 0.12
  --tts-speaking-start-timeout 1.2
  --tts-speaking-require-audio
  --tts-debug
  --uart-debug
)

case "${PET_IDLE_REFLECTION,,}" in
  0|false|no|off)
    cmd+=(--no-pet-idle-reflection)
    ;;
esac

case "${ESP32_BLE,,}" in
  1|true|yes|on)
    cmd+=(--esp32-ble --esp32-ble-name "$ESP32_BLE_NAME")
    cmd+=(--esp32-dashboard-host "$ESP32_DASHBOARD_HOST" --esp32-dashboard-port "$ESP32_DASHBOARD_PORT")
    case "${ESP32_BLE_SIDECAR,,}" in
      1|true|yes|on)
        cmd+=(--esp32-ble-sidecar --esp32-ble-api-url "$ESP32_BLE_API_URL" --esp32-ble-api-timeout "$ESP32_BLE_API_TIMEOUT" --no-esp32-dashboard-control)
        ;;
    esac
    if [[ -n "$ESP32_BLE_ADDRESS" ]]; then
      cmd+=(--esp32-ble-address "$ESP32_BLE_ADDRESS")
    fi
    if [[ -n "$ESP32_BLE_ADAPTER" ]]; then
      cmd+=(--esp32-ble-adapter "$ESP32_BLE_ADAPTER")
    fi
    if [[ -n "$ESP32_BLE_MIN_FAN_PWM" ]]; then
      cmd+=(--esp32-ble-min-fan-pwm "$ESP32_BLE_MIN_FAN_PWM")
    fi
    case "${ESP32_BLE_SCAN_DUPLICATES,,}" in
      1|true|yes|on)
        cmd+=(--esp32-ble-scan-duplicates)
        ;;
    esac
    ;;
esac

if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
  cmd+=(--focus-discord-webhook-url "$DISCORD_WEBHOOK_URL")
fi

if [[ -n "${MUSIC_MPV_YTDL_COOKIES:-}" ]]; then
  cmd+=(--music-mpv-ytdl-cookies "$MUSIC_MPV_YTDL_COOKIES")
fi

if [[ -n "${MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER:-}" ]]; then
  cmd+=(--music-mpv-ytdl-cookies-from-browser "$MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER")
fi

if [[ "$PRINT_COMMAND" == "1" ]]; then
  printf 'cwd: %s\n' "$ROOT_DIR"
  printf 'command: '
  print_redacted_cmd "${cmd[@]}" "$@"
  exit 0
fi

exec "${cmd[@]}" "$@"
