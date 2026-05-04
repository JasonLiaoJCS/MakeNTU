from __future__ import annotations

import argparse
from pathlib import Path
import time

import serial

from pc_controller.config_loader import load_config


DEFAULT_SCAN_PORTS = [
    "/dev/ttyTHS0",
    "/dev/ttyTHS1",
    "/dev/ttyTHS2",
    "/dev/ttyS0",
    "/dev/ttyS1",
    "/dev/ttyS2",
    "/dev/ttyS3",
]


def test_port(port: str, baudrate: int, timeout: float, message: str) -> bool:
    if not Path(port).exists():
        print(f"SKIP {port}: not found")
        return False

    try:
        with serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            line = message + "\n"
            print(f"TEST {port}: TX {message!r}")
            ser.write(line.encode("utf-8"))
            ser.flush()
            time.sleep(0.2)
            rx = ser.read(256)
    except serial.SerialException as exc:
        print(f"FAIL {port}: {exc}")
        return False

    if not rx:
        print(f"FAIL {port}: no data")
        return False

    decoded = rx.decode("utf-8", errors="replace").strip()
    print(f"RX   {port}: {decoded!r}")
    if message in decoded:
        print(f"PASS {port}")
        return True

    print(f"FAIL {port}: received data did not match")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Jetson UART by shorting TX and RX together."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", help="Override port, for example /dev/ttyTHS1")
    parser.add_argument("--baudrate", type=int, help="Override baudrate")
    parser.add_argument("--message", default="UART_LOOPBACK_TEST")
    parser.add_argument("--scan", action="store_true", help="Try common Jetson UART device names")
    args = parser.parse_args()

    config = load_config(args.config)
    serial_cfg = config.get("serial", {})
    port = args.port or serial_cfg.get("port", "/dev/ttyTHS1")
    baudrate = args.baudrate or int(serial_cfg.get("baudrate", 115200))
    timeout = float(serial_cfg.get("timeout_sec", 1.0))

    print("Loopback test")
    print("1. Disconnect FRDM TX/RX first.")
    print("2. Connect Jetson J41 pin 8 TXD directly to J41 pin 10 RXD.")
    print("3. Keep GND unused for this local loopback test.")

    if args.scan:
        print(f"Scanning common UART ports at {baudrate} baud")
        passed = [p for p in DEFAULT_SCAN_PORTS if test_port(p, baudrate, timeout, args.message)]
        if passed:
            print("Use this port in config.yaml:", passed[0])
        else:
            print("No UART port passed loopback.")
        return

    print(f"Opening {port} at {baudrate} baud")
    ok = test_port(port, baudrate, timeout, args.message)
    if not ok:
        print("Try: python run_uart_loopback_test.py --scan")


if __name__ == "__main__":
    main()
