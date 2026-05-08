#!/usr/bin/env bash
set -u

TARGET_PERCENT="${1:-100%}"
PULSE_PERCENT="${PULSE_PERCENT:-80%}"

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

set_simple_control() {
  device="$1"
  label="$2"
  control="$3"

  if ! amixer -D "$device" sget "$control" >/dev/null 2>&1; then
    return 0
  fi

  echo "Setting ${label} ${control} to ${TARGET_PERCENT} and unmuting."
  amixer -D "$device" sset "$control" "$TARGET_PERCENT" unmute >/dev/null 2>&1 || true
  amixer -D "$device" sget "$control" 2>/dev/null | sed 's/^/  /'
}

try_amixer_device() {
  device="$1"
  label="$2"

  if ! have_cmd amixer; then
    echo "amixer not found. Install alsa-utils if you want mixer control."
    return 1
  fi

  echo
  echo "== ${label} mixer (${device}) =="
  controls="$(amixer -D "$device" scontrols 2>/dev/null || true)"
  if [ -z "$controls" ]; then
    echo "No simple mixer controls visible for ${device}."
    return 1
  fi

  changed=0
  for control in Master PCM Speaker Headphone Playback Front; do
    if amixer -D "$device" sget "$control" >/dev/null 2>&1; then
      changed=1
    fi
    set_simple_control "$device" "$label" "$control"
  done
  if [ "$changed" = 0 ]; then
    echo "No common output controls found on ${device}; leaving its many device-specific controls untouched."
  fi
}

detect_usb_cards() {
  if ! have_cmd aplay; then
    return 0
  fi
  aplay -l 2>/dev/null |
    awk '/UACDemo|USB Audio|Jieli/ { card=$2; gsub(":", "", card); print card }' |
    sort -u
}

try_pulse_sinks() {
  if ! have_cmd pactl; then
    return 0
  fi

  echo
  echo "== PulseAudio sinks =="
  pactl list short sinks 2>/dev/null || {
    echo "pactl is installed but PulseAudio is not reachable in this shell."
    return 0
  }

  matched_lines="$(pactl list short sinks 2>/dev/null | grep -Ei 'uacdemo|usb|jieli' || true)"
  if [ -n "$matched_lines" ]; then
    printf '%s\n' "$matched_lines" | while IFS= read -r line; do
      sink="$(printf '%s\n' "$line" | awk '{print $1}')"
      name="$(printf '%s\n' "$line" | awk '{print $2}')"
      echo "Setting PulseAudio sink ${sink} (${name}) to ${PULSE_PERCENT} and unmuting."
      pactl set-sink-mute "$sink" 0 2>/dev/null || true
      pactl set-sink-volume "$sink" "$PULSE_PERCENT" 2>/dev/null || true
      pactl set-default-sink "$sink" 2>/dev/null || true
    done
  else
    echo "No obvious UACDemo/USB PulseAudio sink matched."
  fi
}

echo "Boosting output volume."
echo "ALSA target: ${TARGET_PERCENT}; PulseAudio target: ${PULSE_PERCENT}."
echo "This does not install packages, reboot, or change boot/display settings."

if have_cmd aplay; then
  echo
  echo "== ALSA playback cards =="
  aplay -l 2>/dev/null || true
else
  echo "aplay not found; cannot discover playback cards."
fi

cards="$(detect_usb_cards)"
if [ -n "$cards" ]; then
  for card in $cards; do
    try_amixer_device "hw:${card}" "USB card ${card}"
  done
else
  echo
  echo "No UACDemo/USB Audio playback card found in aplay -l."
fi

try_amixer_device default default
try_pulse_sinks

echo
echo "Recommended TTS output for this setup:"
echo "  AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0"
echo "  DEFAULT_VOLUME_GAIN=2.25"
echo
echo "Restart Terminal 2 TTS server after changing .env, then test /speak_async."
