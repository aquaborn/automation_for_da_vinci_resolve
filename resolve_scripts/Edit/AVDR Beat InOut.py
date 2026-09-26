#!/usr/bin/env python3
"""Resolve menu launcher: add beat/cut markers to the current In/Out range."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "resolve_inout_tools.py"),
        "beat",
        "--render-audio",
        "--config",
        str(REPO_ROOT / "examples" / "beat_cut_shorts.json"),
        "--timeline-fps",
        "25",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
