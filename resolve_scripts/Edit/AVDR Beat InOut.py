#!/usr/bin/env python3
"""Resolve menu launcher: add beat/cut markers to the current In/Out range."""

from __future__ import annotations

import contextlib
import traceback
from datetime import datetime
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = Path.home() / "Desktop" / "avdr_resolve_scripts.log"
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
            print(f"\n[{datetime.now().isoformat(timespec='seconds')}] AVDR Beat InOut")
            print(f"Repo root: {REPO_ROOT}")
            print(f"Scripts dir: {SCRIPTS_DIR}")
            try:
                import resolve_inout_tools

                sys.argv = [
                    str(REPO_ROOT / "scripts" / "resolve_inout_tools.py"),
                    "beat",
                    "--render-audio",
                    "--config",
                    str(REPO_ROOT / "examples" / "beat_cut_shorts.json"),
                    "--timeline-fps",
                    "25",
                ]
                print(f"Args: {sys.argv}")
                result = resolve_inout_tools.main()
                print(f"Exit code: {result}")
                return result
            except Exception:
                traceback.print_exc()
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
