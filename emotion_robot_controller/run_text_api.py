from __future__ import annotations

import argparse
import json
import logging

from pc_controller.backends import create_backend
from pc_controller.config_loader import PROJECT_ROOT, load_config
from pc_controller.emotion_map import load_emotion_map
from pc_controller.serial.packet_builder import PacketBuilder
from pc_controller.serial.serial_bridge import SerialBridge


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    log_path = PROJECT_ROOT / log_cfg.get("log_file", "logs/controller.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Text input -> OpenAI emotion decision -> Serial ACT.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", help="Override serial port")
    parser.add_argument("--no-serial", action="store_true", help="Analyze and print packet without sending")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.port:
        config["serial"]["port"] = args.port
    setup_logging(config)
    map_data = load_emotion_map(config)
    backend = create_backend(config, map_data, "openai")
    builder = PacketBuilder()
    bridge = None if args.no_serial else SerialBridge(config)
    if bridge:
        bridge.open()

    try:
        print("OpenAI API mode. Type Chinese text, or 'quit' to exit.")
        while True:
            text = input("\n你> ").strip()
            if not text:
                continue
            if text.lower() in {"q", "quit", "exit"}:
                break

            decision = backend.analyze(text)
            print("AI JSON:")
            print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
            packet = builder.act(decision, mode="API")
            print(f"TX: {packet.line}")
            if bridge:
                response = bridge.send_and_wait(packet)
                print(f"RX: {response.line or response.error_code}  ok={response.ok}  msg={response.message}")
            print(f"Robot reply: {decision.reply_text}")
    finally:
        if bridge:
            bridge.close()


if __name__ == "__main__":
    main()

