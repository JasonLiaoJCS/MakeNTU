#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${MAKE_NTU_ROOT:-/home/asrlab-yian/MakeNTU}"
TTS_DIR="$ROOT_DIR/jetson_piper_tts"
TTS_VENV_DIR="${TTS_VENV_DIR:-$TTS_DIR/.venv}"
DEMO_VENV_DIR="${DEMO_VENV_DIR:-$ROOT_DIR/emotion_robot_controller/.venv}"
LOG_ROOT="${JETSON_PIPELINE_LOG_ROOT:-$ROOT_DIR/frdm_uart_context_sender/logs/jetson_pipeline}"
PID_DIR="$LOG_ROOT/pids"
RUNS_DIR="$LOG_ROOT/runs"
LATEST_LINK="$LOG_ROOT/latest"

TTS_HOST="${TTS_HOST:-0.0.0.0}"
TTS_PORT="${TTS_PORT:-8777}"
MUSIC_HOST="${MUSIC_HOST:-127.0.0.1}"
MUSIC_PORT="${MUSIC_PORT:-8788}"
MUSIC_BACKEND="${MUSIC_BACKEND:-mpv}"
MUSIC_MPV_AUDIO_DEVICE="${MUSIC_MPV_AUDIO_DEVICE:-auto}"
MUSIC_MPV_VOLUME="${MUSIC_MPV_VOLUME:-150}"
MUSIC_MPV_VOLUME_MAX="${MUSIC_MPV_VOLUME_MAX:-200}"
MUSIC_MPV_READY_TIMEOUT="${MUSIC_MPV_READY_TIMEOUT:-1.5}"
MUSIC_WEATHER_DEFAULT_LOCATION="${MUSIC_WEATHER_DEFAULT_LOCATION:-Taipei}"
MUSIC_WEATHER_TIMEOUT="${MUSIC_WEATHER_TIMEOUT:-4.5}"
DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8789}"
TTS_VOLUME_GAIN="${PIPELINE_TTS_VOLUME_GAIN:-3.6}"
BEEP_PLAYER="${BEEP_PLAYER:-aplay}"
BEEP_VOLUME="${BEEP_VOLUME:-0.55}"
ESP32_BLE="${ESP32_BLE:-1}"
ESP32_BLE_NAME="${ESP32_BLE_NAME:-ESP32S3_FAN_LED_TEMP}"
ESP32_BLE_ADDRESS="${ESP32_BLE_ADDRESS:-}"
ESP32_BLE_ADAPTER="${ESP32_BLE_ADAPTER:-${BLE_ADAPTER:-}}"
ESP32_BLE_MIN_FAN_PWM="${ESP32_BLE_MIN_FAN_PWM:-${FAN_MIN_PWM:-96}}"
ESP32_BLE_SCAN_DUPLICATES="${ESP32_BLE_SCAN_DUPLICATES:-1}"
ESP32_BLE_COMMAND_QUEUE_MAX="${ESP32_BLE_COMMAND_QUEUE_MAX:-64}"
ESP32_BLE_API_HOST="${ESP32_BLE_API_HOST:-127.0.0.1}"
ESP32_BLE_API_PORT="${ESP32_BLE_API_PORT:-8791}"
ESP32_BLE_API_TIMEOUT="${ESP32_BLE_API_TIMEOUT:-0.2}"
case "${ESP32_BLE,,}" in
  1|true|yes|on)
    ESP32_BLE_SIDECAR_DEFAULT=1
    ESP32_TEMPERATURE_MODE_DEFAULT=pull
    ;;
  *)
    ESP32_BLE_SIDECAR_DEFAULT=0
    ESP32_TEMPERATURE_MODE_DEFAULT=disabled
    ;;
esac

export AUDIO_DEVICE="${MAKE_NTU_AUDIO_DEVICE:-auto:UACDemo}"
export MIC_DEVICE_KEYWORD="${MAKE_NTU_MIC_DEVICE_KEYWORD:-UACDemo}"
export SPEAKER_DEVICE_KEYWORD="${MAKE_NTU_SPEAKER_DEVICE_KEYWORD:-UACDemo}"
export WAKE_CAMERA_ID="${MAKE_NTU_WAKE_CAMERA_ID:-auto}"
export FOCUS_CAMERA_ID="${MAKE_NTU_FOCUS_CAMERA_ID:-auto}"
export FOCUS_UART_PORT="${MAKE_NTU_FOCUS_UART_PORT:-auto}"
export MAKE_NTU_TTS_VOLUME_GAIN="$TTS_VOLUME_GAIN"
export TTS_VOLUME_GAIN="$TTS_VOLUME_GAIN"
export DEFAULT_VOLUME_GAIN="$TTS_VOLUME_GAIN"
export BEEP_PLAYER
export BEEP_VOLUME
export ESP32_BLE_API_URL="http://127.0.0.1:$ESP32_BLE_API_PORT/api/esp32"
export ESP32_BLE_API_TIMEOUT
export ESP32_BLE_SIDECAR="${ESP32_BLE_SIDECAR:-$ESP32_BLE_SIDECAR_DEFAULT}"
export ESP32_TEMPERATURE_MODE="${ESP32_TEMPERATURE_MODE:-$ESP32_TEMPERATURE_MODE_DEFAULT}"
export ESP32_TEMPERATURE_URL="${ESP32_TEMPERATURE_URL:-http://127.0.0.1:$ESP32_BLE_API_PORT/api/esp32/status}"
export ESP32_TEMPERATURE_TIMEOUT="${ESP32_TEMPERATURE_TIMEOUT:-0.2}"
export PET_IDLE_REFLECTION="${PET_IDLE_REFLECTION:-0}"
export ENABLE_STREAM_PLAYBACK="${ENABLE_STREAM_PLAYBACK:-true}"
export UACDEMO_PCM_VOLUME="${MAKE_NTU_UACDEMO_PCM_VOLUME:-70%}"
export UACDEMO_PULSE_VOLUME="${MAKE_NTU_UACDEMO_PULSE_VOLUME:-70%}"

ACTION="${1:-start}"
case "$ACTION" in
  start|restart|stop|status|logs|tail|monitor|plan|help|--help|-h)
    shift || true
    ;;
  *)
    ACTION="start"
    ;;
esac

CLEAN_OLD=1
CLEAN_PORTS=1
STOP_USER_SERVICES=1
WAIT_HEALTH=1
TAIL_AFTER_START=0
OPEN_TERMINALS=0
DRY_RUN=0
BRIDGE_EXTRA_ARGS=()
LOG_SERVICE=""

usage() {
  cat <<'EOF'
Usage:
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh start [options] [-- bridge-args...]
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh restart [options] [-- bridge-args...]
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh stop
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh status
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh monitor
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh plan
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh logs [tts|esp32|music|dashboard|bridge]
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh tail [tts|esp32|music|dashboard|bridge]

Starts all Jetson-side Quick Start terminals in order:
  Terminal 2: jetson_piper_tts.server        -> http://127.0.0.1:8777
  Terminal 6: ESP32 BLE sidecar API          -> http://127.0.0.1:8791
  Terminal 4: music_web_player.py            -> http://127.0.0.1:8788
  Terminal 5: smart_home_dashboard/server.py -> http://JETSON_IP:8789/dashboard
  Terminal 3: run_wake_bridge_full_demo.sh   -> full Wake Bridge parser/settings

Useful options:
  plan        Print the exact ordered terminals/commands without starting anything.
  --tail       Start services, then follow all logs.
  --terminals  After start/restart, open one log terminal per service.
  --no-wait    Do not wait for HTTP health checks.
  --no-clean   Do not stop old matching Jetson demo processes before start.
  --no-clean-ports
              Do not kill processes listening on demo ports.
  --no-stop-user-services
              Do not stop user systemd services such as makentu-wake-bridge.
  --dry-run    Print the commands without starting them.

Pass extra Wake Bridge flags after --, for example:
  ./frdm_uart_context_sender/run_jetson_full_demo_pipeline.sh start -- --no-startup-time --no-dashboard-uart

Common environment overrides:
  SERVER_URL=http://WINDOWS_IP:8766/voice-chat
  FOCUS_SERVER_URL=http://WINDOWS_IP:8766/focus-check
  ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A ESP32_BLE_ADAPTER=hci0
  ESP32_BLE_API_TIMEOUT=0.2
  PIPELINE_TTS_VOLUME_GAIN=3.6
  PET_IDLE_REFLECTION=0
  BEEP_PLAYER=aplay BEEP_VOLUME=0.55
  MUSIC_MPV_VOLUME=150 MUSIC_MPV_VOLUME_MAX=200
  MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER=firefox
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tail)
      TAIL_AFTER_START=1
      shift
      ;;
    --terminals|--open-terminals)
      OPEN_TERMINALS=1
      shift
      ;;
    --no-wait)
      WAIT_HEALTH=0
      shift
      ;;
    --no-clean)
      CLEAN_OLD=0
      shift
      ;;
    --no-clean-ports)
      CLEAN_PORTS=0
      shift
      ;;
    --no-stop-user-services)
      STOP_USER_SERVICES=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      ACTION="help"
      shift
      ;;
    --)
      shift
      BRIDGE_EXTRA_ARGS=("$@")
      break
      ;;
    *)
      if [[ "$ACTION" == "logs" || "$ACTION" == "tail" ]]; then
        LOG_SERVICE="$1"
        shift
      else
        BRIDGE_EXTRA_ARGS+=("$1")
        shift
      fi
      ;;
  esac
done

mkdir -p "$PID_DIR" "$RUNS_DIR"

service_pid_file() {
  printf '%s/%s.pid' "$PID_DIR" "$1"
}

service_log_file() {
  local service="$1"
  if [[ -L "$LATEST_LINK" || -d "$LATEST_LINK" ]]; then
    printf '%s/%s.log' "$LATEST_LINK" "$service"
  else
    printf '%s' ""
  fi
}

is_pid_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

current_pgid() {
  ps -o pgid= -p "$$" 2>/dev/null | tr -d '[:space:]' || true
}

pid_pgid() {
  local pid="$1"
  ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true
}

wait_pid_dead() {
  local pid="$1"
  local label="$2"
  local timeout_tenths="${3:-20}"
  local i
  for ((i = 0; i < timeout_tenths; i++)); do
    if ! is_pid_alive "$pid"; then
      return 0
    fi
    sleep 0.1
  done
  printf 'WARN: %s pid=%s still alive after TERM.\n' "$label" "$pid" >&2
  return 1
}

kill_pid_or_group() {
  local pid="$1"
  local label="$2"
  local pgid my_pgid target
  if ! is_pid_alive "$pid"; then
    return 0
  fi
  pgid="$(pid_pgid "$pid")"
  my_pgid="$(current_pgid)"
  if [[ "$pgid" =~ ^[0-9]+$ && "$pgid" != "$my_pgid" ]]; then
    target="-$pgid"
    printf 'Stopping %-18s pid=%s pgid=%s\n' "$label" "$pid" "$pgid"
  else
    target="$pid"
    printf 'Stopping %-18s pid=%s\n' "$label" "$pid"
  fi
  kill -TERM "$target" >/dev/null 2>&1 || true
  if ! wait_pid_dead "$pid" "$label" 25; then
    printf 'Killing  %-18s pid=%s\n' "$label" "$pid"
    kill -KILL "$target" >/dev/null 2>&1 || true
    wait_pid_dead "$pid" "$label" 10 >/dev/null 2>&1 || true
  fi
}

read_pid() {
  local file
  file="$(service_pid_file "$1")"
  [[ -r "$file" ]] && tr -d '[:space:]' <"$file" || true
}

print_cmd() {
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

TTS_CMD=()
ESP32_CMD=()
MUSIC_CMD=()
DASHBOARD_CMD=()
BRIDGE_CMD=()

build_tts_cmd() {
  TTS_CMD=(
    "$TTS_VENV_DIR/bin/python" -m jetson_piper_tts.server
    --host "$TTS_HOST"
    --port "$TTS_PORT"
    --no-warmup
  )
}

build_esp32_cmd() {
  ESP32_CMD=()
  case "${ESP32_BLE,,}" in
    1|true|yes|on)
      ESP32_CMD=(
        "$DEMO_VENV_DIR/bin/python3" "$ROOT_DIR/frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py"
        --api-server
        --api-host "$ESP32_BLE_API_HOST"
        --api-port "$ESP32_BLE_API_PORT"
        --name "$ESP32_BLE_NAME"
        --min-fan-pwm "$ESP32_BLE_MIN_FAN_PWM"
        --command-queue-max "$ESP32_BLE_COMMAND_QUEUE_MAX"
        --api-queue-timeout "$ESP32_BLE_API_TIMEOUT"
        --tts-url "http://127.0.0.1:$TTS_PORT"
      )
      if [[ -n "$ESP32_BLE_ADDRESS" ]]; then
        ESP32_CMD+=(--address "$ESP32_BLE_ADDRESS")
      fi
      if [[ -n "$ESP32_BLE_ADAPTER" ]]; then
        ESP32_CMD+=(--adapter "$ESP32_BLE_ADAPTER")
      fi
      case "${ESP32_BLE_SCAN_DUPLICATES,,}" in
        1|true|yes|on)
          ESP32_CMD+=(--scan-duplicates)
          ;;
      esac
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

build_music_cmd() {
  MUSIC_CMD=(
    "$DEMO_VENV_DIR/bin/python3" "$ROOT_DIR/music_web_player/music_web_player.py"
    --server
    --host "$MUSIC_HOST"
    --port "$MUSIC_PORT"
    --backend "$MUSIC_BACKEND"
    --mpv-audio-device "$MUSIC_MPV_AUDIO_DEVICE"
    --mpv-volume "$MUSIC_MPV_VOLUME"
    --mpv-volume-max "$MUSIC_MPV_VOLUME_MAX"
    --mpv-ready-timeout "$MUSIC_MPV_READY_TIMEOUT"
    --weather-default-location "$MUSIC_WEATHER_DEFAULT_LOCATION"
    --weather-timeout "$MUSIC_WEATHER_TIMEOUT"
  )
  if [[ -n "${MUSIC_MPV_YTDL_COOKIES:-}" ]]; then
    MUSIC_CMD+=(--mpv-ytdl-cookies "$MUSIC_MPV_YTDL_COOKIES")
  fi
  if [[ -n "${MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER:-}" ]]; then
    MUSIC_CMD+=(--mpv-ytdl-cookies-from-browser "$MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER")
  fi
}

build_dashboard_cmd() {
  DASHBOARD_CMD=(
    "$DEMO_VENV_DIR/bin/python3" "$ROOT_DIR/smart_home_dashboard/server.py"
    --host "$DASHBOARD_HOST"
    --port "$DASHBOARD_PORT"
    --no-frdm-uart
  )
}

build_bridge_cmd() {
  BRIDGE_CMD=(
    bash "$ROOT_DIR/frdm_uart_context_sender/run_wake_bridge_full_demo.sh"
    "${BRIDGE_EXTRA_ARGS[@]}"
  )
}

print_plan_service() {
  local index="$1"
  local terminal="$2"
  local service="$3"
  local cwd="$4"
  shift 4
  printf '%s. %-18s service=%s\n' "$index" "$terminal" "$service"
  printf '   cwd: %s\n' "$cwd"
  printf '   command: '
  print_cmd "$@"
}

require_executable() {
  if [[ ! -x "$1" ]]; then
    echo "ERROR: executable not found: $1" >&2
    exit 1
  fi
}

http_ok() {
  local url="$1"
  curl -fsS --max-time 1.5 "$url" >/dev/null 2>&1
}

wait_http() {
  local label="$1"
  local url="$2"
  local timeout_sec="$3"
  local waited=0
  printf 'Waiting for %s: %s\n' "$label" "$url"
  while (( waited < timeout_sec )); do
    if http_ok "$url"; then
      printf 'OK: %s is ready.\n' "$label"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  printf 'WARN: %s did not answer within %ss. Check logs.\n' "$label" "$timeout_sec" >&2
  return 1
}

wait_process_alive() {
  local service="$1"
  local timeout_sec="$2"
  local waited=0
  local pid
  pid="$(read_pid "$service")"
  while (( waited < timeout_sec )); do
    if is_pid_alive "$pid"; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  printf 'ERROR: %s process is not alive. Check %s\n' "$service" "$(service_log_file "$service")" >&2
  return 1
}

stop_pid_file() {
  local service="$1"
  local pid
  pid="$(read_pid "$service")"
  kill_pid_or_group "$pid" "$service"
  rm -f "$(service_pid_file "$service")"
}

stop_matching_processes() {
  local entry label pattern pids pid
  local -a patterns=(
    'tts:jetson_piper_tts.server'
    'music:music_web_player.py'
    'dashboard:smart_home_dashboard/server.py'
    'wake-bridge:wake_voice_chat_frdm_bridge.py'
    'wake-launcher:run_wake_bridge_full_demo.sh'
    'focus:focus_work_mode.py'
    'esp32-standalone:esp32s3_ble_fan_led_controller.py'
    'temperature-test:test_esp32_temperature_receiver.py'
  )
  for entry in "${patterns[@]}"; do
    label="${entry%%:*}"
    pattern="${entry#*:}"
    pids="$(pgrep -u "$(id -u)" -f "$pattern" 2>/dev/null || true)"
    [[ -z "$pids" ]] && continue
    while IFS= read -r pid; do
      [[ -z "$pid" || "$pid" == "$$" ]] && continue
      kill_pid_or_group "$pid" "$label"
    done <<<"$pids"
  done
}

port_listener_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$port" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' | sort -u || true
    return
  fi
}

stop_port_holders() {
  local port label pids pid
  local -a ports=(
    "$TTS_PORT:TTS"
    "$MUSIC_PORT:Music/Weather"
    "$DASHBOARD_PORT:Dashboard"
    "8790:ESP32 temperature"
    "8791:ESP32 dashboard API"
  )
  for entry in "${ports[@]}"; do
    port="${entry%%:*}"
    label="${entry#*:}"
    pids="$(port_listener_pids "$port")"
    [[ -z "$pids" ]] && continue
    printf 'Port %-5s is occupied by pid(s): %s (%s)\n' "$port" "$(echo "$pids" | paste -sd, -)" "$label"
    while IFS= read -r pid; do
      [[ -z "$pid" || "$pid" == "$$" ]] && continue
      kill_pid_or_group "$pid" "port-$port"
    done <<<"$pids"
  done
}

stop_user_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return
  fi
  local service
  for service in makentu-wake-bridge.service; do
    if systemctl --user status "$service" >/dev/null 2>&1; then
      printf 'Stopping user service %-28s\n' "$service"
      systemctl --user stop "$service" >/dev/null 2>&1 || true
    fi
  done
}

stop_all() {
  if (( STOP_USER_SERVICES )); then
    stop_user_services
  fi
  stop_pid_file bridge
  stop_pid_file dashboard
  stop_pid_file music
  stop_pid_file esp32
  stop_pid_file tts
  stop_matching_processes
  if (( CLEAN_PORTS )); then
    stop_port_holders
  fi
  echo "Jetson demo pipeline stopped."
}

start_service() {
  local service="$1"
  local cwd="$2"
  local log_file="$3"
  shift 3

  {
    printf '[%s] service=%s\n' "$(date '+%F %T')" "$service"
    printf 'cwd: %s\n' "$cwd"
    printf 'command: '
    print_cmd "$@"
    printf '\n'
  } >"$log_file"

  printf 'Starting %-9s -> %s\n' "$service" "$log_file"
  if (( DRY_RUN )); then
    printf 'DRY RUN %-9s cwd=%s\n' "$service" "$cwd"
    printf 'DRY RUN command: '
    print_cmd "$@"
    return 0
  fi

  setsid bash -c 'cd "$1" || exit 1; shift; exec "$@"' _ "$cwd" "$@" >>"$log_file" 2>&1 </dev/null &
  echo "$!" >"$(service_pid_file "$service")"
}

status_one() {
  local service="$1"
  local pid
  pid="$(read_pid "$service")"
  if is_pid_alive "$pid"; then
    printf '%-9s running pid=%s log=%s\n' "$service" "$pid" "$(service_log_file "$service")"
  else
    printf '%-9s stopped log=%s\n' "$service" "$(service_log_file "$service")"
  fi
}

status_all() {
  status_one tts
  status_one esp32
  status_one music
  status_one dashboard
  status_one bridge
  echo
  http_ok "http://127.0.0.1:$TTS_PORT/health" && echo "TTS health       OK http://127.0.0.1:$TTS_PORT/health" || echo "TTS health       not ready"
  http_ok "http://127.0.0.1:$ESP32_BLE_API_PORT/health" && echo "ESP32 API health OK http://127.0.0.1:$ESP32_BLE_API_PORT/health" || echo "ESP32 API health not ready"
  http_ok "http://127.0.0.1:$MUSIC_PORT/health" && echo "Music health     OK http://127.0.0.1:$MUSIC_PORT/health" || echo "Music health     not ready"
  http_ok "http://127.0.0.1:$DASHBOARD_PORT/api/status" && echo "Dashboard status OK http://127.0.0.1:$DASHBOARD_PORT/api/status" || echo "Dashboard status not ready"
  http_ok "http://127.0.0.1:$ESP32_BLE_API_PORT/api/esp32/status" && echo "ESP32 sidecar API OK http://127.0.0.1:$ESP32_BLE_API_PORT/api/esp32/status" || echo "ESP32 sidecar API not ready yet"
}

tail_logs() {
  local service="$1"
  local log_file
  if [[ -n "$service" ]]; then
    log_file="$(service_log_file "$service")"
    if [[ ! -f "$log_file" ]]; then
      echo "No log for service '$service'. Use: tts esp32 music dashboard bridge" >&2
      exit 1
    fi
    tail -n 120 -f "$log_file"
    return
  fi
  if [[ ! -d "$LATEST_LINK" ]]; then
    echo "No latest pipeline log directory yet." >&2
    exit 1
  fi
  tail -n 80 -f "$LATEST_LINK"/tts.log "$LATEST_LINK"/esp32.log "$LATEST_LINK"/music.log "$LATEST_LINK"/dashboard.log "$LATEST_LINK"/bridge.log
}

open_one_log_terminal() {
  local service="$1"
  local log_file="$2"
  local title="MakeNTU $service"
  local shell_cmd
  shell_cmd="printf '%s\n' '$title'; printf 'log: %s\n\n' '$log_file'; tail -n 120 -F '$log_file'; exec bash"
  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title "$title" -- bash -lc "$shell_cmd" >/dev/null 2>&1 &
    return 0
  fi
  if command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal --title "$title" --command "bash -lc $(printf '%q' "$shell_cmd")" >/dev/null 2>&1 &
    return 0
  fi
  if command -v xterm >/dev/null 2>&1; then
    xterm -T "$title" -e bash -lc "$shell_cmd" >/dev/null 2>&1 &
    return 0
  fi
  return 1
}

open_log_terminals() {
  if [[ ! -d "$LATEST_LINK" ]]; then
    echo "No latest pipeline log directory yet. Start the pipeline first." >&2
    exit 1
  fi
  local service
  local opened=0
  local -a services=(tts esp32 music dashboard bridge)
  for service in "${services[@]}"; do
    if [[ -f "$LATEST_LINK/$service.log" ]]; then
      if open_one_log_terminal "$service" "$LATEST_LINK/$service.log"; then
        opened=1
      fi
    fi
  done
  if (( opened )); then
    echo "Opened log terminals for: ${services[*]}"
    echo "Logs: $LATEST_LINK"
    return 0
  fi
  echo "Could not open GUI terminals. Falling back to combined log tail in this terminal." >&2
  tail_logs ""
}

plan_all() {
  require_executable "$TTS_VENV_DIR/bin/python"
  require_executable "$DEMO_VENV_DIR/bin/python3"
  require_executable "$ROOT_DIR/frdm_uart_context_sender/run_wake_bridge_full_demo.sh"

  echo "Jetson demo pipeline execution plan:"
  echo "0. pre-clean: stop old pipeline PIDs/process groups, same-user demo processes, user makentu-wake-bridge.service, and demo ports 8777/8788/8789/8790/8791."
  if [[ -x "$ROOT_DIR/frdm_uart_context_sender/auto_demo_devices.sh" ]]; then
    echo "0. preflight: bash $ROOT_DIR/frdm_uart_context_sender/auto_demo_devices.sh"
  fi

  build_tts_cmd
  print_plan_service 1 "Terminal 2" tts "$TTS_DIR" "${TTS_CMD[@]}"

  if build_esp32_cmd; then
    print_plan_service 2 "Terminal 6" esp32 "$ROOT_DIR" "${ESP32_CMD[@]}"
  else
    printf '2. Terminal 6         service=esp32 skipped because ESP32_BLE=%s\n' "$ESP32_BLE"
  fi

  build_music_cmd
  print_plan_service 3 "Terminal 4" music "$ROOT_DIR/music_web_player" "${MUSIC_CMD[@]}"

  build_dashboard_cmd
  print_plan_service 4 "Terminal 5" dashboard "$ROOT_DIR" "${DASHBOARD_CMD[@]}"

  build_bridge_cmd
  print_plan_service 5 "Terminal 3" bridge "$ROOT_DIR" "${BRIDGE_CMD[@]}"
  echo "   expanded parser:"
  bash "$ROOT_DIR/frdm_uart_context_sender/run_wake_bridge_full_demo.sh" --print-command "${BRIDGE_EXTRA_ARGS[@]}" | sed 's/^/     /'

  cat <<EOF

Wake Bridge parser detail lives in:
  $ROOT_DIR/frdm_uart_context_sender/run_wake_bridge_full_demo.sh

Sidecar note:
  ESP32 BLE is intentionally Terminal 6 in the pipeline. Terminal 3 receives
  --esp32-ble-sidecar and calls $ESP32_BLE_API_URL with timeout ${ESP32_BLE_API_TIMEOUT}s.
  This replaces the older Terminal-3-owned BLE loop so ESP32 reconnect/status
  polling does not slow normal conversation.
EOF
}

start_all() {
  require_executable "$TTS_VENV_DIR/bin/python"
  require_executable "$DEMO_VENV_DIR/bin/python3"
  require_executable "$ROOT_DIR/frdm_uart_context_sender/run_wake_bridge_full_demo.sh"

  if (( CLEAN_OLD )); then
    if (( DRY_RUN )); then
      echo "DRY RUN: would stop old Jetson demo processes first."
    else
      echo "Stopping old Jetson demo processes first..."
      stop_all
    fi
  fi

  local run_id run_dir
  run_id="$(date '+%Y%m%d_%H%M%S')"
  run_dir="$RUNS_DIR/$run_id"
  mkdir -p "$run_dir"
  ln -sfn "$run_dir" "$LATEST_LINK"

  if [[ -x "$ROOT_DIR/frdm_uart_context_sender/auto_demo_devices.sh" ]]; then
    if (( DRY_RUN )); then
      echo "DRY RUN: would run USB/audio preflight: auto_demo_devices.sh"
    else
      echo "Running USB/audio preflight: auto_demo_devices.sh"
      bash "$ROOT_DIR/frdm_uart_context_sender/auto_demo_devices.sh" >>"$run_dir/preflight.log" 2>&1 || true
    fi
  fi

  build_tts_cmd
  start_service tts "$TTS_DIR" "$run_dir/tts.log" "${TTS_CMD[@]}"

  if (( WAIT_HEALTH && ! DRY_RUN )); then
    wait_process_alive tts 3
    wait_http "TTS" "http://127.0.0.1:$TTS_PORT/health" 90 || true
  fi

  if build_esp32_cmd; then
    start_service esp32 "$ROOT_DIR" "$run_dir/esp32.log" "${ESP32_CMD[@]}"
    if (( WAIT_HEALTH && ! DRY_RUN )); then
      wait_process_alive esp32 3
      wait_http "ESP32 BLE API" "http://127.0.0.1:$ESP32_BLE_API_PORT/health" 12 || true
    fi
  else
    echo "ESP32 BLE sidecar skipped because ESP32_BLE=$ESP32_BLE."
  fi

  build_music_cmd
  start_service music "$ROOT_DIR/music_web_player" "$run_dir/music.log" "${MUSIC_CMD[@]}"

  if (( WAIT_HEALTH && ! DRY_RUN )); then
    wait_process_alive music 3
    wait_http "Music/Weather" "http://127.0.0.1:$MUSIC_PORT/health" 20 || true
  fi

  build_dashboard_cmd
  start_service dashboard "$ROOT_DIR" "$run_dir/dashboard.log" "${DASHBOARD_CMD[@]}"

  if (( WAIT_HEALTH && ! DRY_RUN )); then
    wait_process_alive dashboard 3
    wait_http "Dashboard" "http://127.0.0.1:$DASHBOARD_PORT/api/status" 20 || true
  fi

  build_bridge_cmd
  start_service bridge "$ROOT_DIR" "$run_dir/bridge.log" "${BRIDGE_CMD[@]}"

  if (( WAIT_HEALTH && ! DRY_RUN )); then
    wait_process_alive bridge 5
    sleep 2
  fi

  echo
  if (( DRY_RUN )); then
    echo "DRY RUN complete. No services were started."
  else
    echo "Jetson full demo pipeline started."
  fi
  echo "Logs: $run_dir"
  echo "Dashboard: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$DASHBOARD_PORT/dashboard"
  echo
  status_all

  if (( TAIL_AFTER_START && ! DRY_RUN )); then
    echo
    echo "Following logs. Press Ctrl-C to stop following; services keep running."
    tail_logs ""
  fi
  if (( OPEN_TERMINALS && ! DRY_RUN )); then
    echo
    echo "Opening one log terminal per service..."
    open_log_terminals
  fi
}

case "$ACTION" in
  help|--help|-h)
    usage
    ;;
  start)
    start_all
    ;;
  restart)
    if (( DRY_RUN )); then
      echo "DRY RUN: would stop all Jetson demo processes first."
    else
      stop_all
    fi
    CLEAN_OLD=0
    start_all
    ;;
  stop)
    stop_all
    ;;
  status)
    status_all
    ;;
  monitor)
    open_log_terminals
    ;;
  plan)
    plan_all
    ;;
  logs|tail)
    tail_logs "$LOG_SERVICE"
    ;;
esac
