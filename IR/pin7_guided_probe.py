#!/usr/bin/env python3
"""Guided BOARD pin 7 input test for Jetson Orin Nano."""

import statistics
import sys
import time

try:
    import Jetson.GPIO as GPIO
except ImportError as exc:
    print("ERROR: Could not import Jetson.GPIO.", file=sys.stderr)
    print("Run this on the Jetson with Jetson.GPIO installed.", file=sys.stderr)
    raise SystemExit(1) from exc


PIN = 7
SAMPLES = 200
SAMPLE_DELAY_SECONDS = 0.005


def level_name(value):
    return "HIGH" if value else "LOW"


def wait_for_user(message):
    print()
    print(message)
    input("Press Enter when ready...")


def sample_pin(label):
    values = []
    start = time.perf_counter()
    for _ in range(SAMPLES):
        values.append(GPIO.input(PIN))
        time.sleep(SAMPLE_DELAY_SECONDS)
    elapsed = time.perf_counter() - start

    high = sum(1 for value in values if value)
    low = len(values) - high
    high_pct = high / len(values) * 100.0
    low_pct = low / len(values) * 100.0
    median = int(statistics.median(values))

    print()
    print(f"{label}:")
    print(f"  Samples: {len(values)} over {elapsed:.2f}s")
    print(f"  HIGH: {high} ({high_pct:.0f}%)")
    print(f"  LOW:  {low} ({low_pct:.0f}%)")
    print(f"  Median level: {median} ({level_name(median)})")
    return median, high_pct, low_pct


def main():
    print("Jetson BOARD pin 7 guided input probe")
    print("This script does not enable an internal pull-up.")
    print("Use resistors. Do not connect BOARD pin 7 to 5V.")
    print()
    print("Expected result:")
    print("  - 10k to 3.3V should read HIGH.")
    print("  - 1k-10k to GND should read LOW.")

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(PIN, GPIO.IN)

    try:
        wait_for_user("Step 1: Disconnect the IR S wire. Leave BOARD pin 7 unconnected.")
        floating = sample_pin("Floating pin 7")

        wait_for_user("Step 2: Connect BOARD pin 7 to 3.3V through a 10k resistor.")
        pulled_high = sample_pin("Pin 7 pulled to 3.3V")

        wait_for_user("Step 3: Connect BOARD pin 7 to GND through a 1k-10k resistor.")
        pulled_low = sample_pin("Pin 7 pulled to GND")

        print()
        print("Verdict:")
        if pulled_high[0] == GPIO.HIGH and pulled_low[0] == GPIO.LOW:
            print("  PASS: BOARD pin 7 can read external HIGH and LOW.")
            print("  Next: reconnect the IR receiver and add 10k from S to 3.3V if idle is LOW.")
        elif pulled_high[0] == GPIO.LOW:
            print("  FAIL: BOARD pin 7 stayed LOW even when pulled to 3.3V.")
            print("  Most likely causes:")
            print("    1. You are not on physical BOARD pin 7.")
            print("    2. The pin 7 overlay was not applied or the Jetson was not rebooted.")
            print("    3. Pin 7 is still muxed to another function, not GPIO input.")
            print("    4. Something external is shorting pin 7 to GND.")
        else:
            print("  MIXED: pin 7 did not behave like a clean GPIO input.")
            print("  Recheck wiring, resistors, overlay, and whether anything else is attached.")

        _ = floating
        return 0
    except KeyboardInterrupt:
        print()
        print("Stopped by Ctrl+C.")
        return 0
    finally:
        GPIO.cleanup()
        print("GPIO cleanup done.")


if __name__ == "__main__":
    raise SystemExit(main())
