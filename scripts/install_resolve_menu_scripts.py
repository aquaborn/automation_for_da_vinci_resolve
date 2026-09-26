#!/usr/bin/env python3
"""Install Resolve menu launchers for this repository."""

from __future__ import annotations

import argparse
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "resolve_scripts" / "Edit"
TARGET_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Blackmagic Design"
    / "DaVinci Resolve"
    / "Fusion"
    / "Scripts"
    / "Edit"
)


def rewrite_repo_root(source: str) -> str:
    return source.replace(
        "REPO_ROOT = Path(__file__).resolve().parents[2]",
        f"REPO_ROOT = Path({str(REPO_ROOT)!r})",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install this repo's launchers into Resolve Workspace > Scripts."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be installed without writing files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.dry_run:
        print(f"Source: {SOURCE_DIR}")
        print(f"Target: {TARGET_DIR}")
        for source_path in sorted(SOURCE_DIR.glob("*.py")):
            print(f"Would install: {TARGET_DIR / source_path.name}")
        return 0

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    installed = []

    for source_path in sorted(SOURCE_DIR.glob("*.py")):
        target_path = TARGET_DIR / source_path.name
        content = rewrite_repo_root(source_path.read_text(encoding="utf-8"))
        target_path.write_text(content, encoding="utf-8")
        target_path.chmod(0o755)
        installed.append(target_path)

    print("Installed Resolve menu scripts:")
    for path in installed:
        print(f"- {path}")
    print("Restart DaVinci Resolve, then open Workspace > Scripts > Edit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
