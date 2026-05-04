#!/usr/bin/env bash
set -euo pipefail

echo "== Jetson UARTA pin 8/10 fixer =="
echo "This script enables UARTA on the 40-pin header and disables nvgetty."
echo

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo ./fix_jetson_uarta.sh"
  exit 1
fi

echo "-- Board model --"
tr -d '\0' </proc/device-tree/model 2>/dev/null || true
echo

echo "-- Supported header functions --"
/opt/nvidia/jetson-io/config-by-function.py -l all || true
echo

echo "-- Currently enabled header functions --"
/opt/nvidia/jetson-io/config-by-function.py -l enabled || true
echo

echo "-- Disabling nvgetty if present --"
systemctl stop nvgetty 2>/dev/null || true
systemctl disable nvgetty 2>/dev/null || true
echo "nvgetty active: $(systemctl is-active nvgetty 2>/dev/null || true)"
echo

echo "-- Applying UARTA to header 1 pins 8/10 --"
/opt/nvidia/jetson-io/config-by-function.py -o dt 1="uarta"
echo

echo "-- Boot config after applying --"
sed -n '1,220p' /boot/extlinux/extlinux.conf || true
echo

echo "-- Recent /boot overlay-like files --"
find /boot -maxdepth 3 -type f \( -iname '*user*' -o -iname '*hdr40*' -o -iname '*jetson*' -o -iname '*.dtbo' \) \
  -printf '%TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort | tail -80 || true
echo

echo "-- Serial aliases --"
for f in /proc/device-tree/aliases/serial*; do
  [[ -e "$f" ]] || continue
  printf '%s -> ' "$f"
  tr -d '\0' <"$f" 2>/dev/null || true
  echo
done
echo

echo "-- ttyTHS devices --"
for d in /sys/class/tty/ttyTHS* /sys/class/tty/ttyS*; do
  [[ -e "$d" ]] || continue
  printf '%s -> ' "$d"
  readlink -f "$d/device" || true
done
echo

echo "DONE."
echo "Now reboot:"
echo "  sudo reboot"
echo
echo "After reboot, short Jetson physical pin 8 to pin 10, then run:"
echo "  cd /home/asrlab-yian/MakeNTU/emotion_robot_controller"
echo "  source .venv/bin/activate"
echo "  python run_uart_loopback_test.py --port /dev/ttyTHS1"

