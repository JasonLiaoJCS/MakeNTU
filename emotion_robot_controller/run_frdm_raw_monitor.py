from __future__ import annotations

import argparse
import time

import serial

from pc_controller.config_loader import load_config


MENU = """
FRDM raw monitor commands
  sleep                 -> send: Sleep
  normal                -> send: Normal
  pitch 90              -> send: MotorPitch 90
  yaw 90                -> send: MotorYaw 90
  raw Sleep             -> send exactly: Sleep
  raw Normal            -> send exactly: Normal
  raw MotorPitch 90     -> send exactly: MotorPitch 90
  quit
"""


def build_command(user_text: str) -> str | None:
    text = user_text.strip()
    if not text:
        return None

    parts = text.split()
    cmd = parts[0].lower()

    if cmd in {"q", "quit", "exit"}:
        return "__QUIT__"
    if cmd in {"help", "menu"}:
        print(MENU)
        return None
    if cmd == "sleep":
        return "Sleep"
    if cmd == "normal":
        return "Normal"
    if cmd == "pitch" and len(parts) >= 2:
        return f"MotorPitch {parts[1]}"
    if cmd == "yaw" and len(parts) >= 2:
        return f"MotorYaw {parts[1]}"
    if cmd == "raw" and len(parts) >= 2:
        return text[len(parts[0]):].strip()

    print("Unknown command. Type 'help'.")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Send existing FRDM SMONITORCOMMAND commands from Jetson/PC.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", help="Override serial port, for example /dev/ttyACM0")
    parser.add_argument("--baudrate", type=int, help="Override baudrate")
    parser.add_argument("--crlf", action="store_true", help="Use CRLF line ending instead of LF")
    parser.add_argument("--read-ms", type=int, default=300, help="Read replies for this many ms after each TX")
    args = parser.parse_args()

    config = load_config(args.config)
    serial_cfg = config.get("serial", {})
    port = args.port or serial_cfg.get("port", "/dev/ttyACM0")
    baudrate = args.baudrate or int(serial_cfg.get("baudrate", 115200))
    timeout = float(serial_cfg.get("timeout_sec", 1.0))
    line_ending = b"\r\n" if args.crlf else b"\n"

    print(f"Opening {port} at {baudrate} baud")
    print(MENU)

    with serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout) as ser:
        while True:
            user_text = input("frdm> ")
            command = build_command(user_text)
            if command is None:
                continue
            if command == "__QUIT__":
                break

            wire = command.encode("utf-8") + line_ending
            print(f"TX: {command!r}")
            ser.write(wire)
            ser.flush()

            deadline = time.monotonic() + args.read_ms / 1000.0
            while time.monotonic() < deadline:
                line = ser.readline()
                if line:
                    print("RX:", line.decode("utf-8", errors="replace").rstrip())


if __name__ == "__main__":
    main()

