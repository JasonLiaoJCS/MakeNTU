from __future__ import annotations

import argparse
import subprocess
import sys
import time


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Probe Jetson 40-pin physical pins through libgpiod. "
            "This is for wiring/debug only, not for UART operation."
        )
    )
    parser.add_argument("--out-line", help="gpiochip/line for physical pin 8 after checking gpioinfo")
    parser.add_argument("--in-line", help="gpiochip/line for physical pin 10 after checking gpioinfo")
    parser.add_argument("--list", action="store_true", help="List gpio chips and lines that mention header/pin names")
    args = parser.parse_args()

    if args.list:
        print("gpiochip list:")
        print(run(["gpioinfo"], check=False).stdout)
        return

    if not args.out_line or not args.in_line:
        print("First install and inspect gpio names:")
        print("  sudo apt install gpiod")
        print("  python run_header_gpio_probe.py --list")
        print()
        print("Then rerun with lines, for example:")
        print("  python run_header_gpio_probe.py --out-line gpiochip0:123 --in-line gpiochip0:124")
        sys.exit(2)

    out_chip, out_num = args.out_line.split(":")
    in_chip, in_num = args.in_line.split(":")
    print("This test assumes physical pin 8 is jumpered to physical pin 10.")
    print(f"Driving {out_chip} line {out_num}; reading {in_chip} line {in_num}")

    # Set output high for a short moment, read input, then set low and read again.
    proc = subprocess.Popen(["gpioset", "--mode=signal", out_chip, f"{out_num}=1"])
    try:
        time.sleep(0.2)
        high = run(["gpioget", in_chip, in_num], check=False).stdout.strip()
    finally:
        proc.terminate()
        proc.wait(timeout=2)

    proc = subprocess.Popen(["gpioset", "--mode=signal", out_chip, f"{out_num}=0"])
    try:
        time.sleep(0.2)
        low = run(["gpioget", in_chip, in_num], check=False).stdout.strip()
    finally:
        proc.terminate()
        proc.wait(timeout=2)

    print(f"Input while output high: {high}")
    print(f"Input while output low:  {low}")
    if high == "1" and low == "0":
        print("PASS: the physical jumper and GPIO path work.")
    else:
        print("FAIL: the input did not follow the output.")


if __name__ == "__main__":
    main()

