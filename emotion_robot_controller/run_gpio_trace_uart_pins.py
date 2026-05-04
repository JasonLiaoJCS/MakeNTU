from __future__ import annotations

import argparse
import subprocess
import time


DRIVE_LINES = {
    110: "PR.02 / physical pin 8 / UARTA_TX",
    111: "PR.03 / physical pin 10 / UARTA_RX",
}

EXPECTED_LOOPBACK = {110: 111, 111: 110}


def sh(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def gpio_lines(chip: str) -> list[int]:
    res = sh(["gpioinfo", chip])
    lines: list[int] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("line "):
            continue
        try:
            num = int(line.split(":", 1)[0].split()[1])
        except (IndexError, ValueError):
            continue
        lines.append(num)
    return lines


def gpio_line_names(chip: str) -> dict[int, str]:
    res = sh(["gpioinfo", chip])
    names: dict[int, str] = {}
    for raw_line in res.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("line "):
            continue
        try:
            left, right = line.split(":", 1)
            num = int(left.split()[1])
            name = right.split('"', 2)[1]
        except (IndexError, ValueError):
            continue
        names[num] = name
    return names


def gpioget(chip: str, line: int) -> str | None:
    res = sh(["gpioget", chip, str(line)])
    if res.returncode != 0:
        return None
    value = res.stdout.strip()
    return value if value in {"0", "1"} else None


def read_all(chip: str, skip: int) -> dict[int, str | None]:
    return {line: gpioget(chip, line) for line in gpio_lines(chip) if line != skip}


def drive_and_read(out_line: int, value: int) -> dict[int, str | None]:
    proc = subprocess.Popen(["gpioset", "--mode=signal", "gpiochip0", f"{out_line}={value}"])
    try:
        time.sleep(0.2)
        return read_all("gpiochip0", out_line)
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def followers_for_drive(out_line: int) -> list[int]:
    high = drive_and_read(out_line, 1)
    low = drive_and_read(out_line, 0)
    found = []
    for line in sorted(set(high) | set(low)):
        hv = high.get(line)
        lv = low.get(line)
        if hv == "1" and lv == "0":
            found.append(line)
    return found


def print_watch_status(names: dict[int, str]) -> bool:
    followers_110 = followers_for_drive(110)
    followers_111 = followers_for_drive(111)
    ok_110 = 111 in followers_110
    ok_111 = 110 in followers_111
    ok = ok_110 or ok_111

    if ok:
        print("CONNECTED: physical pin 8 TXD is shorted to physical pin 10 RXD.")
        return True

    extra = sorted((set(followers_110) | set(followers_111)) - {110, 111})
    if extra:
        labels = ", ".join(f"line {line} {names.get(line, 'unknown')}" for line in extra)
        print(f"NOT pin 8/10: current wire is touching {labels}.")
    else:
        print("NOT CONNECTED: pin 8 and pin 10 are not electrically shorted.")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace or watch Jetson physical pin 8 <-> pin 10 UART loopback wiring.")
    parser.add_argument("--watch", action="store_true", help="Continuously check until Ctrl+C, useful while moving the jumper wire.")
    parser.add_argument("--interval", type=float, default=0.7)
    args = parser.parse_args()

    print("Trace where the jumper connected to physical pin 8/10 goes.")
    print("Disconnect FRDM. Leave your current jumper wire connected.")
    print("This drives only PR.02/pin8 and PR.03/pin10, then reads all gpiochip0 lines.")
    print("For a correct pin 8 <-> pin 10 loopback, line 110 and line 111 should follow each other.")
    print()

    names = gpio_line_names("gpiochip0")

    if args.watch:
        print("Watch mode: move the jumper wire until this prints CONNECTED.")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                print_watch_status(names)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()
            print("Stopped.")
        return

    any_found = False
    for out_line, out_name in DRIVE_LINES.items():
        print(f"Driving {out_line} {out_name}")
        found = followers_for_drive(out_line)

        if found:
            any_found = True
            print("  Lines following this output:")
            for line in found:
                name = names.get(line, "unknown")
                marker = "  <-- expected loopback pin" if line == EXPECTED_LOOPBACK[out_line] else ""
                print(f"    gpiochip0 line {line} {name}{marker}")
            if EXPECTED_LOOPBACK[out_line] not in found:
                expected_name = names.get(EXPECTED_LOOPBACK[out_line], "unknown")
                print(f"  NOTE: expected gpiochip0 line {EXPECTED_LOOPBACK[out_line]} {expected_name}, but it did not follow.")
        else:
            print("  No other GPIO line followed this output.")
        print()

    if not any_found:
        print("No electrical connection from physical pin 8 or pin 10 was detected on gpiochip0.")
        print("Likely causes: wrong physical holes, bad jumper wire, or poor contact.")


if __name__ == "__main__":
    main()
