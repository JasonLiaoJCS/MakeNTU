#!/usr/bin/env bash
set -euo pipefail

PINMUX=/sys/kernel/debug/pinctrl/2430000.pinmux/pinmux-select

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo:"
  echo "  sudo ./apply_runtime_uarta_pinmux.sh"
  exit 1
fi

if [[ ! -w "$PINMUX" ]]; then
  echo "Cannot write $PINMUX"
  exit 1
fi

echo "Applying runtime pinmux:"
echo "  uart1_tx_pr2 -> uarta"
echo "  uart1_rx_pr3 -> uarta"
echo "uarta uart1_tx_pr2" > "$PINMUX"
echo "uarta uart1_rx_pr3" > "$PINMUX"

if grep -q "uart1_rts_pr4" /sys/kernel/debug/pinctrl/2430000.pinmux/pingroups; then
  echo "  uart1_rts_pr4 -> uarta"
  echo "uarta uart1_rts_pr4" > "$PINMUX" || true
fi
if grep -q "uart1_cts_pr5" /sys/kernel/debug/pinctrl/2430000.pinmux/pingroups; then
  echo "  uart1_cts_pr5 -> uarta"
  echo "uarta uart1_cts_pr5" > "$PINMUX" || true
fi

echo
echo "Pinmux state now:"
grep -n "UART1_.*PR[2345]" /sys/kernel/debug/pinctrl/2430000.pinmux/pinmux-pins || true

