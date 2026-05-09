#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/asrlab-yian/MakeNTU"
VENV_DIR="$ROOT_DIR/emotion_robot_controller/.venv"

SERVER_URL="${SERVER_URL:-http://100.108.141.26:8766/voice-chat}"
FOCUS_SERVER_URL="${FOCUS_SERVER_URL:-http://100.108.141.26:8766/focus-check}"
DEVICE_READY_TIMEOUT="${DEVICE_READY_TIMEOUT:-30}"
TTS_VOLUME_GAIN="${TTS_VOLUME_GAIN:-2.25}"
MUSIC_MPV_AUDIO_DEVICE="${MUSIC_MPV_AUDIO_DEVICE:-auto}"
MUSIC_MPV_VOLUME="${MUSIC_MPV_VOLUME:-100}"
MUSIC_MPV_READY_TIMEOUT="${MUSIC_MPV_READY_TIMEOUT:-1.5}"

cd "$ROOT_DIR"
source "$VENV_DIR/bin/activate"

cmd=(
  python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
  --server-url "$SERVER_URL"
  --mic-keyword UACDemo
  --beep-keyword UACDemo
  --beep-player auto
  --noisy-room
  --tts-volume-gain "$TTS_VOLUME_GAIN"
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
  --fan-speed-max 3
  --todo-list-path "$ROOT_DIR/frdm_uart_context_sender/logs/todo_list.json"
  --focus-notify-mode discord
  --music-backend mpv
  --music-mpv-audio-device "$MUSIC_MPV_AUDIO_DEVICE"
  --music-mpv-volume "$MUSIC_MPV_VOLUME"
  --music-mpv-ready-timeout "$MUSIC_MPV_READY_TIMEOUT"
  --music-timeout 5
  --music-wake-pause-timeout 0.25
  --music-wake-beep-settle 0.05
  --post-music-standby-cooldown 0.8
  --music-debug
  --weather-default-location Taipei
  --weather-timeout 6
  --weather-api-timeout 4.5
  --weather-debug
  --esp32-temperature-mode push
  --esp32-temperature-host 0.0.0.0
  --esp32-temperature-port 8790
  --esp32-temperature-path /temperature
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
  --tts-debug
  --uart-debug
)

if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
  cmd+=(--focus-discord-webhook-url "$DISCORD_WEBHOOK_URL")
fi

if [[ -n "${MUSIC_MPV_YTDL_COOKIES:-}" ]]; then
  cmd+=(--music-mpv-ytdl-cookies "$MUSIC_MPV_YTDL_COOKIES")
fi

if [[ -n "${MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER:-}" ]]; then
  cmd+=(--music-mpv-ytdl-cookies-from-browser "$MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER")
fi

exec "${cmd[@]}" "$@"
