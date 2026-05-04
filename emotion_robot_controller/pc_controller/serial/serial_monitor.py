from __future__ import annotations

import logging
import threading
from typing import Any

import serial


LOGGER = logging.getLogger(__name__)


def run_monitor(config: dict[str, Any]) -> None:
    serial_cfg = config.get("serial", {})
    port = serial_cfg.get("port", "COM5")
    baudrate = int(serial_cfg.get("baudrate", 115200))
    timeout = float(serial_cfg.get("timeout_sec", 1.0))

    with serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout) as ser:
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                raw = ser.readline()
                if raw:
                    print(f"RX: {raw.decode('utf-8', errors='replace').rstrip()}")

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        print(f"Monitoring {port} at {baudrate} baud.")
        print("Type a raw packet and press Enter to send. Type 'quit' to exit.")
        try:
            while True:
                line = input("TX> ").strip()
                if line.lower() in {"q", "quit", "exit"}:
                    break
                if not line:
                    continue
                ser.write(line.encode("utf-8") + b"\n")
                ser.flush()
        finally:
            stop.set()
            thread.join(timeout=1.0)

