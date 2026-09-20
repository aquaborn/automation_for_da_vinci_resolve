#!/usr/bin/env python3
"""
Find long silences in exported timeline audio and turn them into rough-cut hints.

The script is intentionally conservative: by default it only writes JSON/CSV
reports. With --add-resolve-markers it also tries to add markers to the current
DaVinci Resolve timeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>[0-9.]+)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<duration>[0-9.]+)"
)


@dataclass(frozen=True)
class SilenceCandidate:
    start: float
    end: float
    duration: float
    cut_start: float
    cut_end: float
    cut_duration: float
    reason: str = "long_silence"


def load_config_defaults(argv: list[str]) -> dict[str, object]:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    pre_args, _ = pre_parser.parse_known_args(argv)

    if not pre_args.config:
        return {}

    config_path = pre_args.config.expanduser().resolve()
    if not config_path.exists():
        raise RuntimeError(f"Config file not found: {config_path}")

    with config_path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)

    allowed_keys = {
        "noise_db",
        "min_silence",
        "keep_before",
        "keep_after",
        "max_cut",
        "output_dir",
        "marker_color",
        "timeline_fps",
    }
    unknown_keys = sorted(set(config) - allowed_keys)
    if unknown_keys:
        raise RuntimeError(f"Unknown config keys: {', '.join(unknown_keys)}")

    return config


def parse_args(argv: list[str]) -> argparse.Namespace:
    config_defaults = load_config_defaults(argv)
    parser = argparse.ArgumentParser(
        description="Analyze exported timeline audio and find long silences."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional JSON config with defaults for thresholds and timings",
    )
    parser.add_argument("audio", type=Path, help="Path to exported audio file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(config_defaults.get("output_dir", "reports/rough_cut")),
        help="Directory for JSON/CSV reports",
    )
    parser.add_argument(
        "--noise-db",
        default=config_defaults.get("noise_db", "-38dB"),
        help="ffmpeg silencedetect noise threshold, for example -35dB or -42dB",
    )
    parser.add_argument(
        "--min-silence",
        type=float,
        default=float(config_defaults.get("min_silence", 0.8)),
        help="Minimum silence duration in seconds",
    )
    parser.add_argument(
        "--keep-before",
        type=float,
        default=float(config_defaults.get("keep_before", 0.12)),
        help="Seconds to keep before speech resumes/cut starts",
    )
    parser.add_argument(
        "--keep-after",
        type=float,
        default=float(config_defaults.get("keep_after", 0.18)),
        help="Seconds to keep after speech ends/cut finishes",
    )
    parser.add_argument(
        "--max-cut",
        type=float,
        default=float(config_defaults.get("max_cut", 8.0)),
        help="Ignore cuts longer than this many seconds; useful for scene breaks",
    )
    parser.add_argument(
        "--add-resolve-markers",
        action="store_true",
        help="Try to add markers to the current Resolve timeline",
    )
    parser.add_argument(
        "--marker-color",
        default=config_defaults.get("marker_color", "Yellow"),
        help="Resolve marker color when --add-resolve-markers is used",
    )
    parser.add_argument(
        "--timeline-fps",
        type=float,
        default=(
            float(config_defaults["timeline_fps"])
            if "timeline_fps" in config_defaults
            else None
        ),
        help="Timeline FPS fallback for Resolve markers if API cannot read it",
    )
    return parser.parse_args()


def run_ffmpeg_silencedetect(
    audio_path: Path, noise_db: str, min_silence: float
) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not found in PATH")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={noise_db}:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output = result.stderr + "\n" + result.stdout
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}\n{output}")
    return output


def parse_silences(ffmpeg_output: str) -> list[tuple[float, float, float]]:
    starts: list[float] = []
    silences: list[tuple[float, float, float]] = []

    for line in ffmpeg_output.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            starts.append(float(start_match.group("start")))
            continue

        end_match = SILENCE_END_RE.search(line)
        if end_match:
            end = float(end_match.group("end"))
            duration = float(end_match.group("duration"))
            start = starts.pop(0) if starts else end - duration
            silences.append((start, end, duration))

    return silences


def build_candidates(
    silences: Iterable[tuple[float, float, float]],
    keep_before: float,
    keep_after: float,
    max_cut: float,
) -> list[SilenceCandidate]:
    candidates: list[SilenceCandidate] = []

    for start, end, duration in silences:
        cut_start = start + keep_after
        cut_end = end - keep_before
        cut_duration = max(0.0, cut_end - cut_start)

        if cut_duration <= 0:
            continue
        if cut_duration > max_cut:
            continue

        candidates.append(
            SilenceCandidate(
                start=round(start, 3),
                end=round(end, 3),
                duration=round(duration, 3),
                cut_start=round(cut_start, 3),
                cut_end=round(cut_end, 3),
                cut_duration=round(cut_duration, 3),
            )
        )

    return candidates


def seconds_to_timecode(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def write_reports(
    audio_path: Path,
    output_dir: Path,
    candidates: list[SilenceCandidate],
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem
    json_path = output_dir / f"{stem}.rough_cut_silence.json"
    csv_path = output_dir / f"{stem}.rough_cut_silence.csv"

    payload = {
        "audio": str(audio_path),
        "settings": {
            "noise_db": args.noise_db,
            "min_silence": args.min_silence,
            "keep_before": args.keep_before,
            "keep_after": args.keep_after,
            "max_cut": args.max_cut,
        },
        "summary": {
            "candidate_count": len(candidates),
            "estimated_removed_seconds": round(
                sum(item.cut_duration for item in candidates), 3
            ),
        },
        "candidates": [
            {
                **asdict(item),
                "start_tc": seconds_to_timecode(item.start),
                "end_tc": seconds_to_timecode(item.end),
                "cut_start_tc": seconds_to_timecode(item.cut_start),
                "cut_end_tc": seconds_to_timecode(item.cut_end),
            }
            for item in candidates
        ],
    }

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "reason",
                "start_tc",
                "end_tc",
                "duration",
                "cut_start_tc",
                "cut_end_tc",
                "cut_duration",
            ],
        )
        writer.writeheader()
        for item in payload["candidates"]:
            writer.writerow(
                {
                    "reason": item["reason"],
                    "start_tc": item["start_tc"],
                    "end_tc": item["end_tc"],
                    "duration": item["duration"],
                    "cut_start_tc": item["cut_start_tc"],
                    "cut_end_tc": item["cut_end_tc"],
                    "cut_duration": item["cut_duration"],
                }
            )

    return json_path, csv_path


def import_resolve_script_api():
    try:
        import DaVinciResolveScript  # type: ignore

        return DaVinciResolveScript
    except ImportError:
        pass

    candidates = [
        Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"),
        Path.home()
        / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    ]
    for candidate in candidates:
        if candidate.exists():
            sys.path.append(str(candidate))
            try:
                import DaVinciResolveScript  # type: ignore

                return DaVinciResolveScript
            except ImportError:
                continue

    raise RuntimeError("Could not import DaVinciResolveScript")


def get_timeline_fps(timeline, fallback: float | None) -> float:
    fps_value = None
    try:
        fps_value = timeline.GetSetting("timelineFrameRate")
    except Exception:
        fps_value = None

    if fps_value:
        try:
            return float(str(fps_value).split()[0])
        except ValueError:
            pass

    if fallback:
        return fallback

    raise RuntimeError("Could not read timeline FPS. Pass --timeline-fps 25")


def add_resolve_markers(
    candidates: list[SilenceCandidate], marker_color: str, timeline_fps: float | None
) -> int:
    resolve_api = import_resolve_script_api()
    resolve = resolve_api.scriptapp("Resolve")
    if not resolve:
        raise RuntimeError("Resolve is not running or scripting is disabled")

    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject()
    if not project:
        raise RuntimeError("No current Resolve project")

    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current Resolve timeline")

    fps = get_timeline_fps(timeline, timeline_fps)
    added = 0
    for item in candidates:
        frame = int(round(item.cut_start * fps))
        duration = max(1, int(round(item.cut_duration * fps)))
        name = f"CUT? silence {item.cut_duration:.1f}s"
        note = (
            f"Silence: {seconds_to_timecode(item.start)}-"
            f"{seconds_to_timecode(item.end)}. "
            f"Suggested cut: {seconds_to_timecode(item.cut_start)}-"
            f"{seconds_to_timecode(item.cut_end)}."
        )
        if timeline.AddMarker(frame, marker_color, name, note, duration, ""):
            added += 1

    return added


def main() -> int:
    try:
        args = parse_args(sys.argv[1:])
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    audio_path = args.audio.expanduser().resolve()

    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        return 2

    try:
        ffmpeg_output = run_ffmpeg_silencedetect(
            audio_path, args.noise_db, args.min_silence
        )
        silences = parse_silences(ffmpeg_output)
        candidates = build_candidates(
            silences, args.keep_before, args.keep_after, args.max_cut
        )
        json_path, csv_path = write_reports(audio_path, args.output_dir, candidates, args)

        removed = sum(item.cut_duration for item in candidates)
        print(f"Found {len(candidates)} rough-cut candidates")
        print(f"Estimated removable silence: {seconds_to_timecode(removed)}")
        print(f"JSON report: {json_path}")
        print(f"CSV report: {csv_path}")

        if args.add_resolve_markers:
            marker_count = add_resolve_markers(
                candidates, args.marker_color, args.timeline_fps
            )
            print(f"Added Resolve markers: {marker_count}")

    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
