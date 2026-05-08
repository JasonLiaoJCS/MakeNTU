#!/usr/bin/env python3
"""Careful BOARD pin 7 output/input self-test for Jetson Orin Nano."""

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


def read_many(count=20, delay=0.01):
    values = []
    for _ in range(count):
        values.append(GPIO.input(PIN))
        time.sleep(delay)
    high = sum(1 for value in values if value)
    low = len(values) - high
    return high, low


def main():
    print("Jetson BOARD pin 7 output/input self-test")
    print()
    print("IMPORTANT:")
    print("  - Disconnect the IR receiver S wire from BOARD pin 7.")
    print("  - Disconnect the 10k pull-up/pull-down test resistor too.")
    print("  - BOARD pin 7 must not be directly connected to 3.3V, 5V, or GND.")
    print("  - Do not run this test if anything else is driving pin 7.")
    print()
    answer = input("Type YES to continue: ").strip()
    if answer != "YES":
        print("Canceled.")
        return 1

    GPIO.setmode(GPIO.BOARD)
    try:
        print()
        print("Setting BOARD pin 7 as OUTPUT HIGH...")
        GPIO.setup(PIN, GPIO.OUT, initial=GPIO.HIGH)
        time.sleep(0.2)
        high_read = GPIO.input(PIN)
        print(f"Readback while output HIGH: {high_read} ({level_name(high_read)})")

        print("Setting BOARD pin 7 as OUTPUT LOW...")
        GPIO.output(PIN, GPIO.LOW)
        time.sleep(0.2)
        low_read = GPIO.input(PIN)
        print(f"Readback while output LOW:  {low_read} ({level_name(low_read)})")

        print("Setting BOARD pin 7 back to INPUT...")
        GPIO.setup(PIN, GPIO.IN)
        high_count, low_count = read_many()
        print(f"Input samples after release: HIGH {high_count}, LOW {low_count}")

        print()
        print("Verdict:")
        if high_read == GPIO.HIGH and low_read == GPIO.LOW:
            print("  Jetson.GPIO can drive BOARD pin 7 HIGH and LOW.")
            print("  If external 10k-to-3.3V still reads LOW, recheck the physical pin.")
        else:
            print("  BOARD pin 7 did not follow output HIGH/LOW commands.")
            print("  Reapply the pin 7 overlay, reboot, and verify the header pin location.")
        return 0
    finally:
        GPIO.cleanup()
        print("GPIO cleanup done.")


if __name__ == "__main__":
    raise SystemExit(main())
