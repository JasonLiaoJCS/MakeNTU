#!/usr/bin/env bash
set -u

# Normalize the USB speaker to absolute levels. Do not pass +5%/-5% here:
# those are relative changes and would drift after repeated boots/terminals.
DEFAULT_UACDEMO_PCM_VOLUME="70%"
DEFAULT_UACDEMO_PULSE_VOLUME="70%"
UACDEMO_PCM_VOLUME="${MAKE_NTU_UACDEMO_PCM_VOLUME:-${UACDEMO_PCM_VOLUME:-$DEFAULT_UACDEMO_PCM_VOLUME}}"
UACDEMO_PULSE_VOLUME="${MAKE_NTU_UACDEMO_PULSE_VOLUME:-${UACDEMO_PULSE_VOLUME:-$DEFAULT_UACDEMO_PULSE_VOLUME}}"
UACDEMO_PULSE_KEYWORD="${UACDEMO_PULSE_KEYWORD:-UACDemo}"
WAIT_SECONDS=0
VERBOSE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pcm-volume)
      UACDEMO_PCM_VOLUME="${2:-}"
      shift 2
      ;;
    --pulse-volume)
      UACDEMO_PULSE_VOLUME="${2:-}"
      shift 2
      ;;
    --keyword)
      UACDEMO_PULSE_KEYWORD="${2:-}"
      shift 2
      ;;
    --wait)
      WAIT_SECONDS="${2:-0}"
      shift 2
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    *)
      echo "WARNING: unknown option ignored: $1" >&2
      shift
      ;;
  esac
done

absolute_volume_or_default() {
  local value="$1"
  local fallback="$2"
  case "$value" in
    +*|-*)
      echo "WARNING: relative volume '$value' rejected; using absolute '$fallback'." >&2
      printf '%s\n' "$fallback"
      ;;
    "")
      printf '%s\n' "$fallback"
      ;;
    *)
      printf '%s\n' "$value"
      ;;
  esac
}

UACDEMO_PCM_VOLUME="$(absolute_volume_or_default "$UACDEMO_PCM_VOLUME" "$DEFAULT_UACDEMO_PCM_VOLUME")"
UACDEMO_PULSE_VOLUME="$(absolute_volume_or_default "$UACDEMO_PULSE_VOLUME" "$DEFAULT_UACDEMO_PULSE_VOLUME")"

find_uacdemo_cards() {
  if [ -r /proc/asound/cards ]; then
    awk -F'[][]' '/UACDemo|Jieli/ {
      card=$2
      gsub(/[[:space:]]/, "", card)
      if (card != "") print card
    }' /proc/asound/cards
  fi
}

find_uacdemo_sink() {
  pactl list short sinks 2>/dev/null | awk -v keyword="$UACDEMO_PULSE_KEYWORD" '
    BEGIN { low_keyword = tolower(keyword) }
    index(tolower($0), low_keyword) { print $2; exit }
  '
}

apply_once() {
  local alsa_applied=0
  local pulse_applied=0
  local pulse_running=0
  local cards=""
  local sink=""

  if command -v amixer >/dev/null 2>&1; then
    cards="$(find_uacdemo_cards)"
    for card in $cards; do
      if amixer -c "$card" sset PCM "$UACDEMO_PCM_VOLUME" unmute >/dev/null 2>&1; then
        alsa_applied=1
        [ "$VERBOSE" = "1" ] && echo "UACDemo ALSA PCM set absolute: card=$card volume=$UACDEMO_PCM_VOLUME"
      fi
    done
  fi

  if command -v pactl >/dev/null 2>&1 && pactl info >/dev/null 2>&1; then
    pulse_running=1
    sink="$(find_uacdemo_sink)"
    if [ -n "$sink" ]; then
      pactl set-sink-mute "$sink" 0 >/dev/null 2>&1 || true
      if pactl set-sink-volume "$sink" "$UACDEMO_PULSE_VOLUME" >/dev/null 2>&1; then
        pulse_applied=1
        [ "$VERBOSE" = "1" ] && echo "UACDemo Pulse sink set absolute: sink=$sink volume=$UACDEMO_PULSE_VOLUME"
      fi
      pactl set-default-sink "$sink" >/dev/null 2>&1 || true
    fi
  fi

  if [ "$pulse_running" = "1" ]; then
    [ "$pulse_applied" = "1" ] && return 0
    return 1
  fi
  if [ "$alsa_applied" = "1" ]; then
    return 0
  fi
  return 1
}

deadline=$((SECONDS + ${WAIT_SECONDS%.*}))
while true; do
  if apply_once; then
    exit 0
  fi
  if [ "${WAIT_SECONDS%.*}" -le 0 ] || [ "$SECONDS" -ge "$deadline" ]; then
    [ "$VERBOSE" = "1" ] && echo "UACDemo audio device not found; absolute volume will be applied by the next terminal/demo start." >&2
    exit 0
  fi
  sleep 1
done
