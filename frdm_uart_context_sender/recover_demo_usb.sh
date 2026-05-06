#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] Stop stuck wake bridge processes"
pkill -9 -f wake_voice_chat_frdm_bridge.py 2>/dev/null || true

echo "[2/4] Reset Jetson USB host controller"
sudo sh -c 'echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/unbind; sleep 2; echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/bind'

echo "[3/4] Wait for USB devices"
sleep 5

echo "[4/4] Current demo devices"
echo "--- lsusb demo devices ---"
lsusb | grep -E 'UACDemo|Global Shutter|MCU-LINK|Realtek.*Hub' || true

echo "--- ALSA capture ---"
arecord -l | grep -E 'UACDemo|CAPTURE' || true

echo "--- ALSA playback ---"
aplay -l | grep -E 'UACDemo|PLAYBACK' || true

echo "--- video / UART ---"
ls -l /dev/video* /dev/ttyACM* 2>/dev/null || true

echo
echo "If UACDemo does not appear, move the USB audio/camera/FRDM devices to a powered hub or separate Jetson USB ports."
