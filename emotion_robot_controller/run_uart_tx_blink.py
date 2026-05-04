from __future__ import annotations

import argparse
import itertools
import time

import serial

from pc_controller.config_loader import load_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Continuously transmit UART text for checking Jetson TX pin with a logic analyzer or USB-UART adapter."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", default=None)
    parser.add_argument("--baudrate", type=int, default=None)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    config = load_config(args.config)
    serial_cfg = config.get("serial", {})
    port = args.port or serial_cfg.get("port", "/dev/ttyTHS1")
    baudrate = args.baudrate or int(serial_cfg.get("baudrate", 115200))

    print(f"Opening {port} at {baudrate} baud")
    print("Sending TX_TEST_N repeatedly. Press Ctrl+C to stop.")
    print("Check Jetson pin 8 with a logic analyzer or connect pin 8 to a USB-UART RX pin plus common GND.")

    with serial.Serial(port=port, baudrate=baudrate, timeout=1.0, write_timeout=1.0) as ser:
        for index in itertools.count(1):
            text = f"TX_TEST_{index}\r\n"
            ser.write(text.encode("ascii"))
            ser.flush()
            print("TX:", text.strip())
            time.sleep(args.interval)


if __name__ == "__main__":
    main()

