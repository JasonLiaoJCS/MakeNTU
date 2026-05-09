#!/usr/bin/env bash
set -u

ROOT_DIR="${ROOT_DIR:-/home/asrlab-yian/MakeNTU}"
TIMEOUT_SEC="${DEMO_DEVICE_READY_TIMEOUT:-${DEVICE_READY_TIMEOUT:-30}}"
REQUIRE_ALL="${DEMO_REQUIRE_ALL_DEVICES:-0}"
REPORT_INTERVAL_SEC=2

find_uacdemo_playback() {
  aplay -l 2>/dev/null | awk '
    /^card [0-9]+:/ && $0 ~ /UACDemo|Jieli/ {
      card=$3
      gsub(/[\[\]:]/, "", card)
      if (card != "") {
        print "plughw:CARD=" card ",DEV=0"
        exit
      }
    }
  '
}

find_uacdemo_capture() {
  arecord -l 2>/dev/null | awk '
    /^card [0-9]+:/ && $0 ~ /UACDemo|Jieli/ {
      card=$3
      gsub(/[\[\]:]/, "", card)
      if (card != "") {
        print "hw:CARD=" card ",DEV=0"
        exit
      }
    }
  '
}

find_frdm_uart() {
  for path in /dev/serial/by-id/*; do
    [ -e "$path" ] || continue
    case "$path" in
      *FRDM*|*MCU-LINK*|*CMSIS-DAP*|*NXP*) echo "$path"; return 0 ;;
    esac
  done
  for path in /dev/serial/by-id/* /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$path" ] || continue
    echo "$path"
    return 0
  done
}

find_camera_node() {
  for path in /dev/video*; do
    [ -e "$path" ] || continue
    echo "$path"
    return 0
  done
}

missing_labels() {
  local missing=()
  [ -n "${speaker:-}" ] || missing+=("speaker")
  [ -n "${mic:-}" ] || missing+=("mic")
  [ -n "${frdm:-}" ] || missing+=("FRDM")
  [ -n "${camera:-}" ] || missing+=("camera")
  if [ "${#missing[@]}" -gt 0 ]; then
    printf '%s\n' "${missing[@]}"
  fi
}

deadline=$((SECONDS + ${TIMEOUT_SEC%.*}))
last_report=$((SECONDS - REPORT_INTERVAL_SEC))

while true; do
  speaker="$(find_uacdemo_playback)"
  mic="$(find_uacdemo_capture)"
  frdm="$(find_frdm_uart)"
  camera="$(find_camera_node)"

  mapfile -t missing < <(missing_labels)
  if [ "${#missing[@]}" -eq 0 ]; then
    break
  fi

  if [ "${TIMEOUT_SEC%.*}" -le 0 ] || [ "$SECONDS" -ge "$deadline" ]; then
    break
  fi

  if [ $((SECONDS - last_report)) -ge "$REPORT_INTERVAL_SEC" ]; then
    printf 'Device auto-detect: waiting for %s...\n' "${missing[*]}"
    last_report="$SECONDS"
  fi
  sleep 0.5
done

if [ -r "$ROOT_DIR/frdm_uart_context_sender/set_uacdemo_volume.sh" ]; then
  bash "$ROOT_DIR/frdm_uart_context_sender/set_uacdemo_volume.sh" >/dev/null 2>&1 || true
fi

echo "Device auto-detect result:"
echo "  speaker : ${speaker:-missing} (TTS AUDIO_DEVICE=${AUDIO_DEVICE:-auto:UACDemo})"
echo "  mic     : ${mic:-missing} (Wake Bridge uses --mic-keyword UACDemo)"
echo "  FRDM    : ${frdm:-missing} (Wake Bridge uses --uart-port auto)"
echo "  camera  : ${camera:-missing} (Wake Bridge uses --camera-id auto)"

mapfile -t missing < <(missing_labels)
if [ "${#missing[@]}" -gt 0 ]; then
  echo "WARNING: missing demo device(s): ${missing[*]}"
  echo "WARNING: plug/replug USB devices or run frdm_uart_context_sender/recover_demo_usb.sh."
  if [ "$REQUIRE_ALL" = "1" ]; then
    exit 1
  fi
fi
