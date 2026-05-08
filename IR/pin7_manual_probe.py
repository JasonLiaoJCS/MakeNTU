#!/usr/bin/env python3
"""Manual BOARD pin 7 input probe for Jetson Orin Nano."""

import argparse
import sys
import time

try:
    import Jetson.GPIO as GPIO
except ImportError as exc:
    print("ERROR: Could not import Jetson.GPIO.", file=sys.stderr)
    print("Run this on the Jetson with Jetson.GPIO installed.", file=sys.stderr)
    raise SystemExit(1) from exc


PIN = 7


def level_name(value):
    return "HIGH" if value else "LOW"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Probe Jetson BOARD pin 7 as a plain GPIO input."
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.01,
        help="polling delay in seconds, default: 0.01",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.sleep <= 0:
        print("ERROR: --sleep must be greater than 0.", file=sys.stderr)
        return 2

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(PIN, GPIO.IN)

    last_state = GPIO.input(PIN)
    last_report = time.perf_counter()
    high_count = 0
    low_count = 0

    print("Jetson BOARD pin 7 manual input probe")
    print("This script does not enable an internal pull-up.")
    print("Test steps:")
    print("  1. Disconnect the IR receiver S wire from BOARD pin 7.")
    print("  2. Connect BOARD pin 7 to 3.3V through a 10k resistor.")
    print("     Expected: HIGH.")
    print("  3. Connect BOARD pin 7 to GND through a 1k-10k resistor.")
    print("     Expected: LOW.")
    print("  4. Use Ctrl+C to stop.")
    print(f"Initial: {last_state} ({level_name(last_state)})")

    try:
        while True:
            state = GPIO.input(PIN)
            now = time.perf_counter()

            if state:
                high_count += 1
            else:
                low_count += 1

            if state != last_state:
                print(f"Changed: {last_state} -> {state} ({level_name(state)})")
                last_state = state

            if now - last_report >= 1.0:
                total = high_count + low_count
                high_pct = (high_count / total) * 100.0 if total else 0.0
                low_pct = (low_count / total) * 100.0 if total else 0.0
                print(
                    "Last 1s samples: "
                    f"HIGH {high_count} ({high_pct:.0f}%), "
                    f"LOW {low_count} ({low_pct:.0f}%)"
                )
                high_count = 0
                low_count = 0
                last_report = now

            time.sleep(args.sleep)

    except KeyboardInterrupt:
        print()
        print("Stopped by Ctrl+C.")
        return 0
    finally:
        GPIO.cleanup()
        print("GPIO cleanup done.")


if __name__ == "__main__":
    raise SystemExit(main())
