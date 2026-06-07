#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${MAKE_NTU_ROOT:-/home/asrlab-yian/MakeNTU}"
DEMO_VENV_DIR="${DEMO_VENV_DIR:-$ROOT_DIR/emotion_robot_controller/.venv}"
TTS_DIR="$ROOT_DIR/jetson_piper_tts"
TTS_VENV_DIR="${TTS_VENV_DIR:-$TTS_DIR/.venv}"
LOG_ROOT="${JETSON_LOCAL_OLLAMA_LOG_ROOT:-$ROOT_DIR/frdm_uart_context_sender/logs/jetson_local_ollama}"
PID_DIR="$LOG_ROOT/pids"
LOCAL_AI_LOG="$LOG_ROOT/local_ai.log"
TTS_LOG="$LOG_ROOT/tts.log"
OLLAMA_LOG="$LOG_ROOT/ollama.log"
WAKE_STATUS_PATH="${WAKE_STATUS_PATH:-$ROOT_DIR/frdm_uart_context_sender/logs/wake_status.json}"

LOCAL_AI_HOST="${JETSON_LOCAL_AI_HOST:-127.0.0.1}"
LOCAL_AI_PORT="${JETSON_LOCAL_AI_PORT:-8766}"
LOCAL_AI_MIN_DEBUG_VERSION="${JETSON_LOCAL_AI_MIN_DEBUG_VERSION:-19}"
LOCAL_AI_URL="http://$LOCAL_AI_HOST:$LOCAL_AI_PORT"
SERVER_URL="$LOCAL_AI_URL/voice-chat"
FOCUS_SERVER_URL="$LOCAL_AI_URL/focus-check"

OLLAMA_URL="${JETSON_LOCAL_OLLAMA_URL:-http://127.0.0.1:11434/api/chat}"
OLLAMA_TAGS_URL="${OLLAMA_URL%/api/chat}/api/tags"
OLLAMA_MODEL="${JETSON_LOCAL_OLLAMA_MODEL:-qwen3:1.7b-q4_K_M}"
OLLAMA_FALLBACK_MODEL="${JETSON_LOCAL_OLLAMA_FALLBACK_MODEL:-qwen3:1.7b-q4_K_M}"
VISION_MODEL="${JETSON_LOCAL_VISION_MODEL:-gemma3:4b}"
LOCAL_VISION_ENABLED="${JETSON_LOCAL_VISION:-1}"
LOCAL_FORCE_VISION="${JETSON_LOCAL_FORCE_VISION:-0}"
LOCAL_MAX_IMAGE_BYTES="${JETSON_LOCAL_MAX_IMAGE_BYTES:-2000000}"
LOCAL_VISION_TIMEOUT="${JETSON_LOCAL_VISION_TIMEOUT:-120}"
OLLAMA_NUM_CTX="${JETSON_LOCAL_OLLAMA_NUM_CTX:-2048}"
OLLAMA_NUM_PREDICT="${JETSON_LOCAL_OLLAMA_NUM_PREDICT:-120}"
OLLAMA_TEMPERATURE="${JETSON_LOCAL_OLLAMA_TEMPERATURE:-0.5}"
OLLAMA_KEEP_ALIVE="${JETSON_LOCAL_OLLAMA_KEEP_ALIVE:-10m}"
LOCAL_MEMORY_TURNS="${JETSON_LOCAL_MEMORY_TURNS:-0}"

TTS_HOST="${TTS_HOST:-0.0.0.0}"
TTS_PORT="${TTS_PORT:-8777}"
TTS_HEALTH_URL="http://127.0.0.1:$TTS_PORT/health"

WHISPER_CPP_BIN="${WHISPER_CPP_BIN:-}"
WHISPER_CPP_MODEL="${WHISPER_CPP_MODEL:-}"
WHISPER_CPP_LANGUAGE="${WHISPER_CPP_LANGUAGE:-zh}"
WHISPER_CPP_TIMEOUT="${WHISPER_CPP_TIMEOUT:-30}"
WHISPER_CPP_EXTRA_ARGS="${WHISPER_CPP_EXTRA_ARGS:-}"
MIC_TEST_SECONDS="${MIC_TEST_SECONDS:-3}"
LOCAL_WAKE_PIN_UACDEMO_DEVICE="${LOCAL_WAKE_PIN_UACDEMO_DEVICE:-1}"

PIPELINE_TTS_VOLUME_GAIN="${PIPELINE_TTS_VOLUME_GAIN:-3.6}"
BEEP_PLAYER="${BEEP_PLAYER:-aplay}"
BEEP_VOLUME="${BEEP_VOLUME:-0.55}"
PET_IDLE_REFLECTION="${PET_IDLE_REFLECTION:-0}"

# Local Ollama v1 validates the voice path only. Keep optional ESP32/temp
# features quiet unless explicitly enabled by the caller.
ESP32_BLE="${ESP32_BLE:-0}"
ESP32_BLE_SIDECAR="${ESP32_BLE_SIDECAR:-0}"
ESP32_TEMPERATURE_MODE="${ESP32_TEMPERATURE_MODE:-disabled}"
TEMP_ROOM_UART_INTERVAL_SEC="${TEMP_ROOM_UART_INTERVAL_SEC:-0}"

ACTION="${1:-start}"
case "$ACTION" in
  start|check|plan|stop|watch-wake|mic-test|mic-scan|help|--help|-h)
    shift || true
    ;;
  *)
    ACTION="start"
    ;;
esac

BRIDGE_EXTRA_ARGS=("$@")

usage() {
  cat <<'EOF'
Usage:
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start [bridge args...]
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh check
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh plan
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh watch-wake
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh mic-test
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh mic-scan
  ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh stop

Runs a Jetson-only v1 voice flow:
  wake bridge -> local AI sidecar :8766 -> local Ollama -> Jetson Piper TTS

Required local assets:
  ollama text model : qwen3:1.7b-q4_K_M
  ollama vision    : gemma3:4b
  whisper.cpp binary: whisper-cli
  whisper.cpp model : ggml-base.bin or WHISPER_CPP_MODEL

Useful overrides:
  JETSON_LOCAL_OLLAMA_MODEL=qwen3:1.7b-q4_K_M
  JETSON_LOCAL_OLLAMA_FALLBACK_MODEL=qwen3:1.7b-q4_K_M
  JETSON_LOCAL_VISION_MODEL=gemma3:4b
  JETSON_LOCAL_VISION=1
  JETSON_LOCAL_FORCE_VISION=0
  JETSON_LOCAL_MEMORY_TURNS=8
  JETSON_LOCAL_OLLAMA_NUM_CTX=2048
  WHISPER_CPP_BIN=/path/to/whisper-cli
  WHISPER_CPP_MODEL=/path/to/ggml-base.bin
  WHISPER_CPP_LANGUAGE=zh
  ESP32_BLE=1  # opt in when the ESP32 BLE peripheral is powered and nearby
  MIC_TEST_SECONDS=3
EOF
}

mkdir -p "$PID_DIR" "$LOG_ROOT"

pid_file() {
  printf '%s/%s.pid' "$PID_DIR" "$1"
}

is_pid_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

stop_pid_file() {
  local name="$1"
  local file
  file="$(pid_file "$name")"
  if [[ ! -r "$file" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  if is_pid_alive "$pid"; then
    echo "Stopping $name pid=$pid"
    kill "$pid" >/dev/null 2>&1 || true
    sleep 0.5
    if is_pid_alive "$pid"; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$file"
}

http_ok() {
  local url="$1"
  curl -fsS --connect-timeout 1 --max-time 3 "$url" >/dev/null 2>&1
}

json_field_true() {
  local url="$1"
  local field="$2"
  python3 - "$url" "$field" <<'PY'
import json
import sys
import urllib.request

url, field = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if data.get(field) is True else 1)
PY
}

json_string_field() {
  local url="$1"
  local field="$2"
  python3 - "$url" "$field" <<'PY'
import json
import sys
import urllib.request

url, field = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
value = data.get(field, "")
print(value if isinstance(value, str) else "")
PY
}

json_int_field() {
  local url="$1"
  local field="$2"
  python3 - "$url" "$field" <<'PY'
import json
import sys
import urllib.request

url, field = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
try:
    print(int(data.get(field, 0)))
except Exception:
    print(0)
PY
}

find_pulse_uacdemo_source() {
  if ! command -v pactl >/dev/null 2>&1; then
    return 1
  fi
  pactl list short sources 2>/dev/null | awk '
    $2 ~ /^alsa_input/ && tolower($0) ~ /uacdemo|jieli/ { print $2; exit }
  '
}

configure_pulse_uacdemo_source() {
  local source
  source="$(find_pulse_uacdemo_source || true)"
  if [[ -z "$source" ]]; then
    return 0
  fi
  pactl set-source-mute "$source" 0 >/dev/null 2>&1 || true
  pactl set-source-volume "$source" 95% >/dev/null 2>&1 || true
  pactl set-default-source "$source" >/dev/null 2>&1 || true
  export PULSE_SOURCE="${PULSE_SOURCE:-$source}"
  echo "Pulse mic source: $source"
}

find_uacdemo_capture_card() {
  arecord -l 2>/dev/null | awk '
    /^card [0-9]+:/ && tolower($0) ~ /uacdemo|jieli/ {
      card=$3
      gsub(/[\[\]:]/, "", card)
      print card
      exit
    }
  '
}

find_sounddevice_uacdemo_input_index() {
  "$DEMO_VENV_DIR/bin/python" - <<'PY'
try:
    import sounddevice as sd
except Exception:
    raise SystemExit(1)

for index, device in enumerate(sd.query_devices()):
    name = str(device.get("name", ""))
    if ("uacdemo" in name.lower() or "jieli" in name.lower()) and int(device.get("max_input_channels", 0) or 0) > 0:
        print(index)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

bridge_args_include_device() {
  local arg
  for arg in "${BRIDGE_EXTRA_ARGS[@]}"; do
    case "$arg" in
      --device|--device=*)
        return 0
        ;;
    esac
  done
  return 1
}

resolve_whisper_bin() {
  if [[ -n "$WHISPER_CPP_BIN" ]]; then
    printf '%s\n' "$WHISPER_CPP_BIN"
    return 0
  fi
  if command -v whisper-cli >/dev/null 2>&1; then
    command -v whisper-cli
    return 0
  fi
  if command -v main >/dev/null 2>&1; then
    command -v main
    return 0
  fi
  printf '%s\n' "whisper-cli"
}

resolve_whisper_model() {
  if [[ -n "$WHISPER_CPP_MODEL" ]]; then
    printf '%s\n' "$WHISPER_CPP_MODEL"
    return 0
  fi
  local candidates=(
    "$HOME/whisper.cpp/models/ggml-base.bin"
    "$HOME/.cache/whisper.cpp/ggml-base.bin"
    "$ROOT_DIR/models/whisper/ggml-base.bin"
    "$HOME/whisper.cpp/models/ggml-tiny.bin"
    "$HOME/.cache/whisper.cpp/ggml-tiny.bin"
    "$ROOT_DIR/models/whisper/ggml-tiny.bin"
  )
  local path
  for path in "${candidates[@]}"; do
    if [[ -f "$path" ]]; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  printf '%s\n' "${candidates[0]}"
}

ensure_ollama_running() {
  if http_ok "$OLLAMA_TAGS_URL"; then
    return 0
  fi
  if ! command -v ollama >/dev/null 2>&1; then
    echo "ERROR: ollama was not found. Install Ollama on the Jetson first." >&2
    return 1
  fi
  echo "Starting local Ollama server..."
  if command -v setsid >/dev/null 2>&1; then
    setsid ollama serve >"$OLLAMA_LOG" 2>&1 </dev/null &
  else
    nohup ollama serve >"$OLLAMA_LOG" 2>&1 </dev/null &
  fi
  echo "$!" >"$(pid_file ollama)"
  local i
  for ((i = 0; i < 20; i++)); do
    if http_ok "$OLLAMA_TAGS_URL"; then
      return 0
    fi
    sleep 0.5
  done
  echo "ERROR: Ollama did not become ready. See $OLLAMA_LOG" >&2
  return 1
}

ollama_has_model() {
  local model="$1"
  python3 - "$OLLAMA_TAGS_URL" "$model" <<'PY'
import json
import sys
import urllib.request

url, wanted = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
models = data.get("models", [])
names = set()
for item in models:
    if isinstance(item, dict):
        for key in ("name", "model"):
            value = item.get(key)
            if isinstance(value, str):
                names.add(value)
raise SystemExit(0 if wanted in names else 1)
PY
}

check_required_assets() {
  ensure_ollama_running

  if ! ollama_has_model "$OLLAMA_MODEL"; then
    echo "ERROR: local Ollama model is missing: $OLLAMA_MODEL" >&2
    echo "Run this on Jetson first:" >&2
    echo "  ollama pull $OLLAMA_MODEL" >&2
    echo "Optional fallback:" >&2
    echo "  ollama pull $OLLAMA_FALLBACK_MODEL" >&2
    return 1
  fi
  if [[ "$LOCAL_VISION_ENABLED" =~ ^(1|true|yes|on)$ ]] && ! ollama_has_model "$VISION_MODEL"; then
    echo "ERROR: local Ollama vision model is missing: $VISION_MODEL" >&2
    echo "Run this on Jetson first:" >&2
    echo "  ollama pull $VISION_MODEL" >&2
    return 1
  fi

  local whisper_bin
  local whisper_model
  whisper_bin="$(resolve_whisper_bin)"
  whisper_model="$(resolve_whisper_model)"
  if ! command -v "$whisper_bin" >/dev/null 2>&1 && [[ ! -x "$whisper_bin" ]]; then
    echo "ERROR: whisper.cpp binary not found: $whisper_bin" >&2
    echo "Build whisper.cpp and set WHISPER_CPP_BIN=/path/to/whisper-cli." >&2
    return 1
  fi
  if [[ ! -f "$whisper_model" ]]; then
    echo "ERROR: whisper.cpp model not found: $whisper_model" >&2
    echo "Download ggml-base.bin or ggml-tiny.bin and set WHISPER_CPP_MODEL=/path/to/model." >&2
    return 1
  fi
}

start_tts_if_needed() {
  if json_field_true "$TTS_HEALTH_URL" ready; then
    echo "TTS ready: $TTS_HEALTH_URL"
    return 0
  fi
  echo "Starting Jetson Piper TTS..."
  (
    cd "$TTS_DIR"
    local cmd=(
      env
      "PYTHONPATH=$TTS_DIR"
      "AUDIO_DEVICE=${AUDIO_DEVICE:-auto:UACDemo}"
      "MIC_DEVICE_KEYWORD=${MIC_DEVICE_KEYWORD:-UACDemo}"
      "SPEAKER_DEVICE_KEYWORD=${SPEAKER_DEVICE_KEYWORD:-UACDemo}"
      "$TTS_VENV_DIR/bin/python" -m jetson_piper_tts.server
      --host "$TTS_HOST"
      --port "$TTS_PORT"
      --no-warmup
    )
    if command -v setsid >/dev/null 2>&1; then
      setsid "${cmd[@]}" >"$TTS_LOG" 2>&1 </dev/null &
    else
      nohup "${cmd[@]}" >"$TTS_LOG" 2>&1 </dev/null &
    fi
    echo "$!" >"$(pid_file tts)"
  )
  local i
  for ((i = 0; i < 60; i++)); do
    if json_field_true "$TTS_HEALTH_URL" ready; then
      echo "TTS ready: $TTS_HEALTH_URL"
      return 0
    fi
    sleep 0.5
  done
  echo "ERROR: TTS did not become ready. See $TTS_LOG" >&2
  return 1
}

start_local_ai_if_needed() {
  local health_url="$LOCAL_AI_URL/health"
  local need_start=0
  if http_ok "$health_url"; then
    local service
    service="$(json_string_field "$health_url" service || true)"
    if [[ "$service" == "jetson_local_ai_server" ]]; then
      local debug_version
      debug_version="$(json_int_field "$health_url" debug_version || printf '0\n')"
      if (( debug_version < LOCAL_AI_MIN_DEBUG_VERSION )); then
        echo "Local AI debug_version=$debug_version is older than $LOCAL_AI_MIN_DEBUG_VERSION; restarting..."
        stop_pid_file local_ai
        if http_ok "$health_url"; then
          echo "ERROR: old local AI is still occupying port $LOCAL_AI_PORT. Stop it, then rerun this launcher." >&2
          return 1
        fi
        need_start=1
      else
        local running_memory_turns
        local running_num_ctx
        local running_num_predict
        local running_model
        local running_vision_model
        local running_vision_enabled
        running_memory_turns="$(json_int_field "$health_url" memory_turns || printf '0\n')"
        running_num_ctx="$(json_int_field "$health_url" num_ctx || printf '0\n')"
        running_num_predict="$(json_int_field "$health_url" num_predict || printf '0\n')"
        running_model="$(json_string_field "$health_url" configured_ollama_model || true)"
        running_vision_model="$(json_string_field "$health_url" vision_model || true)"
        running_vision_enabled=0
        if json_field_true "$health_url" vision_enabled; then
          running_vision_enabled=1
        fi
        local desired_vision_enabled=0
        if [[ "$LOCAL_VISION_ENABLED" =~ ^(1|true|yes|on)$ ]]; then
          desired_vision_enabled=1
        fi
        if (( running_memory_turns != LOCAL_MEMORY_TURNS || running_num_ctx != OLLAMA_NUM_CTX || running_num_predict != OLLAMA_NUM_PREDICT )) ||
          [[ "$running_model" != "$OLLAMA_MODEL" || "$running_vision_model" != "$VISION_MODEL" || "$running_vision_enabled" != "$desired_vision_enabled" ]]; then
          echo "Local AI config changed; restarting..."
          echo "  running: model=$running_model vision_enabled=$running_vision_enabled vision_model=$running_vision_model memory_turns=$running_memory_turns num_ctx=$running_num_ctx num_predict=$running_num_predict"
          echo "  desired : model=$OLLAMA_MODEL vision_enabled=$desired_vision_enabled vision_model=$VISION_MODEL memory_turns=$LOCAL_MEMORY_TURNS num_ctx=$OLLAMA_NUM_CTX num_predict=$OLLAMA_NUM_PREDICT"
          stop_pid_file local_ai
          if http_ok "$health_url"; then
            echo "ERROR: old local AI is still occupying port $LOCAL_AI_PORT. Stop it, then rerun this launcher." >&2
            return 1
          fi
          need_start=1
        else
          echo "Local AI already running: $health_url"
        fi
      fi
    else
      echo "ERROR: port $LOCAL_AI_PORT is occupied by service '$service', not jetson_local_ai_server." >&2
      return 1
    fi
  else
    need_start=1
  fi

  if (( need_start )); then
    echo "Starting Jetson local AI sidecar..."
    local whisper_bin
    local whisper_model
    whisper_bin="$(resolve_whisper_bin)"
    whisper_model="$(resolve_whisper_model)"
    local cmd=(
      env
      PYTHONUNBUFFERED=1
      "$DEMO_VENV_DIR/bin/python" -u "$ROOT_DIR/emotion_robot_controller/voice_stt_remote/jetson_local_ai_server.py"
      --host "$LOCAL_AI_HOST"
      --port "$LOCAL_AI_PORT"
      --ollama-url "$OLLAMA_URL"
      --ollama-model "$OLLAMA_MODEL"
      --fallback-ollama-model "$OLLAMA_FALLBACK_MODEL"
      --num-ctx "$OLLAMA_NUM_CTX"
      --num-predict "$OLLAMA_NUM_PREDICT"
      --temperature "$OLLAMA_TEMPERATURE"
      --keep-alive "$OLLAMA_KEEP_ALIVE"
      --memory-turns "$LOCAL_MEMORY_TURNS"
      --vision-model "$VISION_MODEL"
      --vision-timeout "$LOCAL_VISION_TIMEOUT"
      --max-image-bytes "$LOCAL_MAX_IMAGE_BYTES"
      --stt-bin "$whisper_bin"
      --stt-model "$whisper_model"
      --stt-language "$WHISPER_CPP_LANGUAGE"
      --stt-timeout "$WHISPER_CPP_TIMEOUT"
      --stt-extra-args "$WHISPER_CPP_EXTRA_ARGS"
      --debug-log "$LOG_ROOT/jetson_local_ai_debug.jsonl"
    )
    if [[ ! "$LOCAL_VISION_ENABLED" =~ ^(1|true|yes|on)$ ]]; then
      cmd+=(--no-vision)
    fi
    if [[ "$LOCAL_FORCE_VISION" =~ ^(1|true|yes|on)$ ]]; then
      cmd+=(--force-vision)
    fi
    if command -v setsid >/dev/null 2>&1; then
      setsid "${cmd[@]}" >"$LOCAL_AI_LOG" 2>&1 </dev/null &
    else
      nohup "${cmd[@]}" >"$LOCAL_AI_LOG" 2>&1 </dev/null &
    fi
    echo "$!" >"$(pid_file local_ai)"
  fi

  local i
  for ((i = 0; i < 60; i++)); do
    if json_field_true "$health_url" chat_ready && json_field_true "$health_url" asr_loaded; then
      echo "Local AI ready: $health_url"
      return 0
    fi
    sleep 0.5
  done
  echo "ERROR: local AI did not become ready. See $LOCAL_AI_LOG" >&2
  curl -fsS "$health_url" || true
  return 1
}

print_plan() {
  cat <<EOF
Jetson local Ollama plan:
  Ollama tags : $OLLAMA_TAGS_URL
  LLM model   : $OLLAMA_MODEL
  Vision      : enabled=$LOCAL_VISION_ENABLED force=$LOCAL_FORCE_VISION model=$VISION_MODEL max_image_bytes=$LOCAL_MAX_IMAGE_BYTES
  Context     : memory_turns=$LOCAL_MEMORY_TURNS num_ctx=$OLLAMA_NUM_CTX num_predict=$OLLAMA_NUM_PREDICT
  STT binary  : $(resolve_whisper_bin)
  STT model   : $(resolve_whisper_model)
  TTS health  : $TTS_HEALTH_URL
  Local AI    : $LOCAL_AI_URL/voice-chat
  Bridge      : SERVER_URL=$SERVER_URL FOCUS_SERVER_URL=$FOCUS_SERVER_URL ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh -- --no-focus-mode --no-temp-room-uart
  Wake status : $WAKE_STATUS_PATH
  ESP32 BLE   : $ESP32_BLE
EOF
}

run_checks() {
  check_required_assets
  start_tts_if_needed
  start_local_ai_if_needed
  echo "Jetson local Ollama checks passed."
}

watch_wake_status() {
  python3 - "$WAKE_STATUS_PATH" <<'PY'
import json
import os
import sys
import time

path = sys.argv[1]
last_key = None
last_stale_report = 0.0
print(f"Watching wake status: {path}", flush=True)
print("Ctrl+C to stop this watcher.", flush=True)
while True:
    now = time.time()
    try:
        stat = os.stat(path)
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        data = json.loads(raw)
        key = (stat.st_mtime_ns, raw)
        age = now - stat.st_mtime
        if key != last_key or (age > 3.0 and now - last_stale_report >= 3.0):
            stale = " STALE" if age > 3.0 else ""
            print(
                f"{time.strftime('%H:%M:%S')} age={age:4.1f}s{stale} "
                f"phase={data.get('phase')} listening={data.get('listening')} "
                f"vol={data.get('volume')} peak={data.get('recent_peak')} "
                f"wake={data.get('wake_score')}/{data.get('wake_threshold')} "
                f"wake_vol>={data.get('wake_volume_threshold')} "
                f"noise={data.get('noise_floor')}",
                flush=True,
            )
            last_key = key
            if age > 3.0:
                last_stale_report = now
    except FileNotFoundError:
        if now - last_stale_report >= 3.0:
            print(f"{time.strftime('%H:%M:%S')} status file does not exist yet", flush=True)
            last_stale_report = now
    except Exception as exc:
        if now - last_stale_report >= 3.0:
            print(f"{time.strftime('%H:%M:%S')} failed to read status: {exc}", flush=True)
            last_stale_report = now
    time.sleep(0.5)
PY
}

mic_test() {
  local card
  card="$(find_uacdemo_capture_card || true)"
  if [[ -z "$card" ]]; then
    echo "ERROR: no UACDemo/Jieli capture device found in arecord -l." >&2
    echo "Replug the USB mic side, then run: arecord -l" >&2
    return 1
  fi

  configure_pulse_uacdemo_source

  local wav_path="/tmp/jetson_local_uacdemo_mic_test.wav"
  local device="plughw:CARD=$card,DEV=0"
  local sd_index
  sd_index="$(find_sounddevice_uacdemo_input_index || true)"
  if [[ -n "$sd_index" ]]; then
    echo "sounddevice UACDemo input index: $sd_index"
  else
    echo "WARNING: sounddevice does not currently list a UACDemo input device."
  fi
  echo "Recording $MIC_TEST_SECONDS seconds from $device ..."
  echo "Speak clearly into the mic now."
  if ! arecord -q -D "$device" -f S16_LE -r 16000 -c 1 -d "$MIC_TEST_SECONDS" "$wav_path"; then
    echo "ERROR: arecord failed. If the wake bridge is running, press Ctrl+C there and rerun mic-test." >&2
    return 1
  fi
  python3 - "$wav_path" <<'PY'
import audioop
import wave
import sys

path = sys.argv[1]
with wave.open(path, "rb") as wav:
    frames = wav.readframes(wav.getnframes())
    sample_width = wav.getsampwidth()
    rate = wav.getframerate()
    channels = wav.getnchannels()
rms = audioop.rms(frames, sample_width) if frames else 0
peak = audioop.max(frames, sample_width) if frames else 0
print(f"mic_test: path={path} rate={rate} channels={channels} rms={rms} peak={peak}")
if peak == 0:
    print("mic_test_result: ZERO_SIGNAL")
elif peak < 500:
    print("mic_test_result: VERY_LOW_SIGNAL")
else:
    print("mic_test_result: SIGNAL_OK")
PY
}

mic_scan() {
  configure_pulse_uacdemo_source
  "$DEMO_VENV_DIR/bin/python" - <<'PY'
import audioop
import sys
import time

try:
    import sounddevice as sd
except Exception as exc:
    print(f"ERROR: sounddevice unavailable: {exc}", file=sys.stderr)
    raise SystemExit(1)

duration = 1.2
samplerate = 16000
candidates = []
for index, device in enumerate(sd.query_devices()):
    inputs = int(device.get("max_input_channels", 0) or 0)
    if inputs <= 0:
        continue
    name = str(device.get("name", ""))
    if "NVIDIA Jetson Orin Nano APE" in name:
        continue
    candidates.append((index, name, inputs))

print("Speak continuously while this scans input devices.")
print("A useful mic should show peak clearly above 0, usually hundreds or thousands.")
for index, name, inputs in candidates:
    try:
        data = sd.rec(
            int(duration * samplerate),
            samplerate=samplerate,
            channels=1,
            dtype="int16",
            device=index,
            blocking=True,
        )
        raw = data.reshape(-1).tobytes()
        rms = audioop.rms(raw, 2) if raw else 0
        peak = audioop.max(raw, 2) if raw else 0
        print(f"[{index:2d}] peak={peak:5d} rms={rms:5d} inputs={inputs:2d} name={name}")
    except Exception as exc:
        print(f"[{index:2d}] ERROR {exc} name={name}")
    time.sleep(0.2)
PY
}

start_bridge() {
  export PET_IDLE_REFLECTION
  export PIPELINE_TTS_VOLUME_GAIN
  export BEEP_PLAYER
  export BEEP_VOLUME
  export SERVER_URL
  export FOCUS_SERVER_URL
  export ESP32_BLE
  export ESP32_BLE_SIDECAR
  export ESP32_TEMPERATURE_MODE
  export TEMP_ROOM_UART_INTERVAL_SEC
  export WAKE_STATUS_PATH
  configure_pulse_uacdemo_source

  echo "Starting local wake bridge."
  echo "AI path: Jetson wake/record -> $SERVER_URL -> local Ollama -> Jetson TTS."
  cd "$ROOT_DIR"
  source "$DEMO_VENV_DIR/bin/activate"

  local bridge_args=(
    --no-focus-mode
    --no-temp-room-uart
  )
  if [[ ! "$LOCAL_VISION_ENABLED" =~ ^(1|true|yes|on)$ ]]; then
    bridge_args+=(--no-vision)
  fi
  if [[ "$LOCAL_FORCE_VISION" =~ ^(1|true|yes|on)$ ]]; then
    bridge_args+=(--force-vision)
  fi
  if [[ "$LOCAL_WAKE_PIN_UACDEMO_DEVICE" =~ ^(1|true|yes|on)$ ]] && ! bridge_args_include_device; then
    local mic_index
    mic_index="$(find_sounddevice_uacdemo_input_index || true)"
    if [[ -n "$mic_index" ]]; then
      echo "Pinned wake mic to current UACDemo sounddevice input index: $mic_index"
      bridge_args+=(--device "$mic_index")
    else
      echo "WARNING: no UACDemo sounddevice input index found; falling back to --mic-keyword UACDemo."
    fi
  fi
  bridge_args+=("${BRIDGE_EXTRA_ARGS[@]}")

  exec ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh \
    -- \
    "${bridge_args[@]}"
}

case "$ACTION" in
  help|--help|-h)
    usage
    ;;
  plan)
    print_plan
    ;;
  check)
    run_checks
    ;;
  watch-wake)
    watch_wake_status
    ;;
  mic-test)
    mic_test
    ;;
  mic-scan)
    mic_scan
    ;;
  stop)
    stop_pid_file local_ai
    stop_pid_file tts
    echo "Stopped local AI/TTS processes started by this launcher."
    ;;
  start)
    run_checks
    start_bridge
    ;;
esac
