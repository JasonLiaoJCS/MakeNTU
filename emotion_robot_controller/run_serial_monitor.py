from __future__ import annotations

import argparse
import logging

from pc_controller.config_loader import PROJECT_ROOT, load_config
from pc_controller.serial.serial_monitor import run_monitor


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
    parser = argparse.ArgumentParser(description="Raw Serial RX/TX monitor.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", help="Override serial port")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.port:
        config["serial"]["port"] = args.port
    setup_logging(config)
    run_monitor(config)


if __name__ == "__main__":
    main()

