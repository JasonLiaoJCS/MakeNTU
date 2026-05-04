from __future__ import annotations

import subprocess
import time


# Around Jetson Orin Nano UARTA/flow-control pins:
# PR.02 = physical pin 8  UARTA TX
# PR.03 = physical pin 10 UARTA RX
# PR.04/PR.05 = physical pins 11/36 when UARTA RTS/CTS is enabled
LINES = {
    108: "PR.00",
    109: "PR.01",
    110: "PR.02 / physical pin 8 / UARTA_TX",
    111: "PR.03 / physical pin 10 / UARTA_RX",
    112: "PR.04",
    113: "PR.05",
}


def sh(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def gpioget(line: int) -> str | None:
    res = sh(["gpioget", "gpiochip0", str(line)])
    if res.returncode != 0:
        return None
    return res.stdout.strip()


def drive_and_read(out_line: int, value: int) -> dict[int, str | None]:
    proc = subprocess.Popen(["gpioset", "--mode=signal", "gpiochip0", f"{out_line}={value}"])
    try:
        time.sleep(0.15)
        return {line: gpioget(line) for line in LINES if line != out_line}
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def main() -> None:
    print("Jumper finder for nearby Jetson header GPIO lines")
    print("Leave your current jumper wire connected. Disconnect FRDM.")
    print("This scans PR.00..PR.05 and looks for a line that follows another line.")
    print()

    found: list[tuple[int, int]] = []
    for out_line, out_name in LINES.items():
        high = drive_and_read(out_line, 1)
        low = drive_and_read(out_line, 0)
        print(f"Drive {out_line:3d} {out_name}")
        for in_line, in_name in LINES.items():
            if in_line == out_line:
                continue
            hv = high.get(in_line)
            lv = low.get(in_line)
            print(f"  read {in_line:3d} {in_name:36s}: high={hv} low={lv}")
            if hv == "1" and lv == "0":
                found.append((out_line, in_line))
        print()

    if found:
        print("Possible jumper connection(s):")
        for out_line, in_line in found:
            print(f"  {out_line} {LINES[out_line]}  ->  {in_line} {LINES[in_line]}")
    else:
        print("No jumper found among PR.00..PR.05.")
        print("That means the wire is likely not on the pin 8/10 neighborhood, the wire is bad, or it is not making contact.")


if __name__ == "__main__":
    main()

