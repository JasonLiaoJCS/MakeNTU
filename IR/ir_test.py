#!/usr/bin/env python3
"""Poll Jetson Orin Nano BOARD pin 7 for an IR receiver signal."""

import argparse
import sys
import time
import warnings

try:
    import Jetson.GPIO as GPIO
except ImportError as exc:
    print("ERROR: Could not import Jetson.GPIO.", file=sys.stderr)
    print("Install it on the Jetson, then run this script again.", file=sys.stderr)
    raise SystemExit(1) from exc


IR_PIN = 7  # BOARD pin 7, not BCM/GPIO number 7.
POLL_INTERVAL_SECONDS = 0.0005


def level_name(value):
    return "HIGH" if value else "LOW"


def print_no_change_checklist(seconds):
    print()
    print(f"No state changes detected for {seconds:g} seconds.")
    print("If you were pressing the remote, check:")
    print("  1. The pin 7 overlay was applied and the Jetson was rebooted.")
    print("  2. The IR receiver S/Signal wire is really on BOARD pin 7.")
    print("  3. VCC is connected to Jetson 3.3V, not 5V.")
    print("  4. GND is connected to Jetson GND.")
    print("  5. The IR receiver module pin order is correct.")
    print("  6. S is normally about 3.3V when idle.")
    print("  7. S changes voltage while pressing the remote.")
    print("  8. The remote battery is good.")
    print("  9. The receiver supports common 38 kHz IR remotes.")
    print(" 10. If S stays LOW, try an external 10k pull-up from S to 3.3V.")
    print()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Test an IR receiver on Jetson Orin Nano BOARD pin 7."
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="print how long each HIGH/LOW level lasted, in microseconds",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=POLL_INTERVAL_SECONDS,
        help="polling delay in seconds, default: 0.0005",
    )
    parser.add_argument(
        "--remind-after",
        type=float,
        default=5.0,
        help="print troubleshooting checklist after this many seconds without changes; use 0 to disable",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.sleep <= 0:
        print("ERROR: --sleep must be greater than 0.", file=sys.stderr)
        return 2

    GPIO.setmode(GPIO.BOARD)
    with warnings.catch_warnings(record=True) as setup_warnings:
        warnings.simplefilter("always")
        GPIO.setup(IR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    last_state = GPIO.input(IR_PIN)
    last_change_time = time.perf_counter()
    checklist_printed = False
    pull_up_ignored = any("pull_up_down" in str(item.message) for item in setup_warnings)

    print("Jetson.GPIO IR receiver test")
    print(f"Mode: BOARD")
    print(f"IR_PIN: {IR_PIN} (physical BOARD pin 7)")
    if pull_up_ignored:
        print("Pull-up: requested, but Jetson.GPIO ignored pull_up_down")
        print("         Use pinmux/overlay pull-up or an external 10k pull-up to 3.3V.")
    else:
        print("Pull-up: requested")
    print(f"Poll delay: {args.sleep} seconds")
    print(f"Initial: {last_state} ({level_name(last_state)})")
    if last_state == GPIO.LOW:
        print("NOTE: Idle LOW is suspicious for most 38 kHz IR receiver modules.")
        print("      When idle, S is usually HIGH, then pulses LOW while receiving IR.")
    print("Press buttons on the IR remote. Use Ctrl+C to stop.")

    try:
        while True:
            state = GPIO.input(IR_PIN)
            now = time.perf_counter()

            if state != last_state:
                if args.timing:
                    duration_us = (now - last_change_time) * 1_000_000.0
                    print(
                        "Changed: "
                        f"{last_state} -> {state} "
                        f"| previous {level_name(last_state)} duration: "
                        f"{duration_us:.0f} us"
                    )
                else:
                    print(f"Changed: {last_state} -> {state}")

                last_state = state
                last_change_time = now
                checklist_printed = False

            elif (
                args.remind_after > 0
                and not checklist_printed
                and now - last_change_time >= args.remind_after
            ):
                print_no_change_checklist(args.remind_after)
                checklist_printed = True

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
