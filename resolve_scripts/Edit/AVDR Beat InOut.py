#!/usr/bin/env python3
"""Resolve menu launcher: add beat/cut markers to the current In/Out range."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import resolve_inout_tools


def main() -> int:
    sys.argv = [
        str(REPO_ROOT / "scripts" / "resolve_inout_tools.py"),
        "beat",
        "--render-audio",
        "--config",
        str(REPO_ROOT / "examples" / "beat_cut_shorts.json"),
        "--timeline-fps",
        "25",
    ]
    return resolve_inout_tools.main()


if __name__ == "__main__":
    raise SystemExit(main())
