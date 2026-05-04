#!/usr/bin/env bash
set -euo pipefail

echo "== Root UART/pinmux diagnostics =="
echo

echo "-- dmesg uart lines --"
dmesg | grep -i "3100000\|3140000\|ttyTHS\|uart" | tail -160 || true
echo

echo "-- serial aliases --"
for f in /proc/device-tree/aliases/serial*; do
  [[ -e "$f" ]] || continue
  printf '%s -> ' "$f"
  tr -d '\0' <"$f" 2>/dev/null || true
  echo
done
echo

echo "-- serial node status --"
for n in 3100000 3110000 3140000; do
  node="/proc/device-tree/bus@0/serial@$n"
  [[ -d "$node" ]] || continue
  echo "--- serial@$n"
  for f in status compatible; do
    printf '%s=' "$f"
    tr -d '\0' <"$node/$f" 2>/dev/null || true
    echo
  done
done
echo

echo "-- tty devices --"
for d in /sys/class/tty/ttyTHS* /sys/class/tty/ttyS*; do
  [[ -e "$d" ]] || continue
  printf '%s -> ' "$d"
  readlink -f "$d/device" || true
done
echo

echo "-- pinctrl files --"
find /sys/kernel/debug/pinctrl -maxdepth 2 -type f -printf '%p\n' 2>/dev/null | sort | sed -n '1,160p' || true
echo

echo "-- pinmux grep --"
grep -Rni "uarta\|uart\|pr.02\|pr.03\|3100000\|3140000\|uart1\|tx\|rx" /sys/kernel/debug/pinctrl 2>/dev/null | head -260 || true
echo

echo "-- gpio debug grep --"
cat /sys/kernel/debug/gpio 2>/dev/null | grep -i "PR\\|uart\\|8\\|10" | head -220 || true

