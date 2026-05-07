#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys


def _must_have(binary: str) -> None:
    if shutil.which(binary) is None:
        raise RuntimeError(f"Missing dependency: {binary}")


def _yt_dlp_cmd() -> list[str]:
    try:
        import yt_dlp  # noqa: F401
    except Exception as exc:
        raise RuntimeError("Missing dependency: yt-dlp Python module") from exc
    return [sys.executable, "-m", "yt_dlp"]


def _resolve_first_result_url(query: str) -> str:
    cmd = _yt_dlp_cmd() + ["--default-search", "ytsearch1", "--get-url", f"ytsearch1:{query}"]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    url = result.stdout.strip().splitlines()
    if not url:
        raise RuntimeError("No playable URL found from search result")
    return url[-1].strip()


def _launch_player(url: str) -> None:
    if shutil.which("mpv"):
        subprocess.Popen(["mpv", "--no-video", "--force-window=no", url], start_new_session=True)
        return
    if shutil.which("cvlc"):
        subprocess.Popen(["cvlc", "--intf", "dummy", url], start_new_session=True)
        return
    raise RuntimeError("No supported player found (mpv or cvlc)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search YouTube music and play first result with local player.")
    parser.add_argument("query", nargs="+", help="Song query")
    args = parser.parse_args()

    query = " ".join(args.query).strip()
    if not query:
        print("Query is empty", file=sys.stderr)
        return 2

    try:
        _must_have("yt-dlp")
        url = _resolve_first_result_url(query)
        _launch_player(url)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
