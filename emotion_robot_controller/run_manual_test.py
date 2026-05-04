from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pc_controller.config_loader import PROJECT_ROOT, load_config
from pc_controller.emotion_map import load_emotion_map
from pc_controller.serial.packet_builder import PacketBuilder
from pc_controller.serial.serial_bridge import SerialBridge


EMOTION_OPTIONS = [
    "neutral",
    "happy",
    "excited",
    "sad",
    "tired",
    "angry",
    "surprised",
    "curious",
    "confused",
    "thinking",
    "concerned",
    "sleepy",
]

TEST_MOTIONS = ["CENTER", "ROLL_LEFT", "ROLL_RIGHT", "PITCH_UP", "PITCH_DOWN"]


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    log_path = PROJECT_ROOT / log_cfg.get("log_file", "logs/controller.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )


def print_menu() -> None:
    print("\nManual test commands")
    print("  emotions:", ", ".join(EMOTION_OPTIONS))
    print("  tests:   ", ", ".join(TEST_MOTIONS))
    print("  control:  reset, status, ping, quit")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual Serial/UART test without AI.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", help="Override serial port, for example COM5 or /dev/ttyACM0")
    parser.add_argument("--no-serial", action="store_true", help="Print packets without opening serial")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.port:
        config["serial"]["port"] = args.port
    setup_logging(config)
    load_emotion_map(config)

    builder = PacketBuilder()
    bridge = None if args.no_serial else SerialBridge(config)
    if bridge:
        bridge.open()

    try:
        print_menu()
        while True:
            command = input("\ncommand> ").strip()
            if not command:
                continue
            lowered = command.lower()
            if lowered in {"q", "quit", "exit"}:
                break
            if lowered in EMOTION_OPTIONS:
                packet = builder.emo(lowered)
            elif command.upper() in TEST_MOTIONS:
                packet = builder.test(command.upper())
            elif lowered == "reset":
                packet = builder.reset()
            elif lowered == "status":
                packet = builder.status()
            elif lowered == "ping":
                packet = builder.ping()
            elif lowered in {"help", "menu"}:
                print_menu()
                continue
            else:
                print("Unknown command. Type 'help' to show options.")
                continue

            print(f"TX: {packet.line}")
            if args.no_serial:
                continue
            assert bridge is not None
            response = bridge.send_and_wait(packet)
            print(f"RX: {response.line or response.error_code}  ok={response.ok}  msg={response.message}")
    finally:
        if bridge:
            bridge.close()


if __name__ == "__main__":
    main()

