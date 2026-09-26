#!/usr/bin/env python3
"""
Build a beat-based edit map for short-form rough cuts.

The script decodes audio through ffmpeg, estimates tempo from energy onsets, and
writes JSON/CSV reports. With --add-resolve-markers it also tries to add markers
to the current DaVinci Resolve timeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class BeatMarker:
    time: float
    marker_type: str
    label: str
    strength: float
    beat_index: int


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
        "sample_rate",
        "hop_size",
        "bpm_min",
        "bpm_max",
        "beats_per_bar",
        "cut_every_beats",
        "marker_duration_frames",
        "output_dir",
        "marker_color",
        "strong_marker_color",
        "cut_marker_color",
        "drop_marker_color",
        "timeline_fps",
        "max_markers",
    }
    unknown_keys = sorted(set(config) - allowed_keys)
    if unknown_keys:
        raise RuntimeError(f"Unknown config keys: {', '.join(unknown_keys)}")

    return config


def parse_args(argv: list[str]) -> argparse.Namespace:
    config_defaults = load_config_defaults(argv)
    parser = argparse.ArgumentParser(
        description="Analyze music/audio and create beat-based cut markers."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional JSON config with defaults for beat/cut settings",
    )
    parser.add_argument("audio", type=Path, help="Path to exported music/audio file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(config_defaults.get("output_dir", "reports/beat_cut")),
        help="Directory for JSON/CSV reports",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=int(config_defaults.get("sample_rate", 22050)),
        help="Analysis sample rate used by ffmpeg",
    )
    parser.add_argument(
        "--hop-size",
        type=int,
        default=int(config_defaults.get("hop_size", 256)),
        help="Analysis hop size in samples",
    )
    parser.add_argument(
        "--bpm-min",
        type=float,
        default=float(config_defaults.get("bpm_min", 70)),
        help="Minimum BPM for tempo search",
    )
    parser.add_argument(
        "--bpm-max",
        type=float,
        default=float(config_defaults.get("bpm_max", 190)),
        help="Maximum BPM for tempo search",
    )
    parser.add_argument(
        "--beats-per-bar",
        type=int,
        default=int(config_defaults.get("beats_per_bar", 4)),
        help="Meter hint for strong beat markers",
    )
    parser.add_argument(
        "--cut-every-beats",
        type=int,
        default=int(config_defaults.get("cut_every_beats", 2)),
        help="Mark every Nth beat as a suggested cut point",
    )
    parser.add_argument(
        "--max-markers",
        type=int,
        default=int(config_defaults.get("max_markers", 500)),
        help="Maximum markers to write/add",
    )
    parser.add_argument(
        "--add-resolve-markers",
        action="store_true",
        help="Try to add markers to the current Resolve timeline",
    )
    parser.add_argument(
        "--marker-duration-frames",
        type=int,
        default=int(config_defaults.get("marker_duration_frames", 1)),
        help="Resolve marker duration in frames",
    )
    parser.add_argument(
        "--marker-color",
        default=config_defaults.get("marker_color", "Blue"),
        help="Resolve marker color for regular beats",
    )
    parser.add_argument(
        "--strong-marker-color",
        default=config_defaults.get("strong_marker_color", "Cyan"),
        help="Resolve marker color for strong beats",
    )
    parser.add_argument(
        "--cut-marker-color",
        default=config_defaults.get("cut_marker_color", "Yellow"),
        help="Resolve marker color for suggested cut points",
    )
    parser.add_argument(
        "--drop-marker-color",
        default=config_defaults.get("drop_marker_color", "Red"),
        help="Resolve marker color for likely drops/energy peaks",
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
    return parser.parse_args(argv)


def decode_audio(audio_path: Path, sample_rate: int) -> array:
    if not shutil.which("ffmpeg"):
        if audio_path.suffix.lower() == ".wav":
            return decode_wav_pcm(audio_path, sample_rate)
        raise RuntimeError("ffmpeg is not installed or not found in PATH")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}\n{error}")

    samples = array("f")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def decode_wav_pcm(audio_path: Path, sample_rate: int) -> array:
    with wave.open(str(audio_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        source_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        raw = wav_file.readframes(frame_count)

    if sample_width not in {1, 2, 3, 4}:
        raise RuntimeError(
            "Unsupported WAV sample width. Install ffmpeg for broader audio support."
        )

    source_samples: list[float] = []
    bytes_per_frame = sample_width * channels
    for frame_start in range(0, len(raw), bytes_per_frame):
        channel_sum = 0.0
        for channel in range(channels):
            start = frame_start + channel * sample_width
            sample_bytes = raw[start : start + sample_width]
            if sample_width == 1:
                value = (sample_bytes[0] - 128) / 128.0
            elif sample_width == 2:
                value = int.from_bytes(sample_bytes, "little", signed=True) / 32768.0
            elif sample_width == 3:
                value_int = int.from_bytes(sample_bytes + b"\x00", "little", signed=True)
                if value_int & 0x800000:
                    value_int -= 0x1000000
                value = value_int / 8388608.0
            else:
                value = int.from_bytes(sample_bytes, "little", signed=True) / 2147483648.0
            channel_sum += value
        source_samples.append(channel_sum / channels)

    if source_rate == sample_rate:
        return array("f", source_samples)

    ratio = source_rate / sample_rate
    output_length = int(len(source_samples) / ratio)
    resampled = array("f")
    for output_index in range(output_length):
        position = output_index * ratio
        left = int(position)
        right = min(left + 1, len(source_samples) - 1)
        fraction = position - left
        value = source_samples[left] * (1.0 - fraction) + source_samples[right] * fraction
        resampled.append(value)
    return resampled


def moving_average(values: list[float], radius: int) -> list[float]:
    if not values:
        return []
    averaged: list[float] = []
    window_sum = 0.0
    left = 0
    right = 0
    for index in range(len(values)):
        while right < len(values) and right <= index + radius:
            window_sum += values[right]
            right += 1
        while left < index - radius:
            window_sum -= values[left]
            left += 1
        averaged.append(window_sum / max(1, right - left))
    return averaged


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, round((len(sorted_values) - 1) * ratio)))
    return sorted_values[index]


def build_energy(samples: array, hop_size: int) -> list[float]:
    energies: list[float] = []
    for start in range(0, len(samples), hop_size):
        chunk = samples[start : start + hop_size]
        if not chunk:
            continue
        square_sum = sum(sample * sample for sample in chunk)
        energies.append(math.sqrt(square_sum / len(chunk)))
    return moving_average(energies, radius=2)


def build_novelty(energies: list[float]) -> list[float]:
    if not energies:
        return []

    novelty = [0.0]
    for index in range(1, len(energies)):
        history = energies[max(0, index - 8) : index]
        previous = sum(history) / max(1, len(history))
        current = energies[index]
        novelty.append(max(0.0, current - previous))

    return moving_average(novelty, radius=2)


def estimate_tempo(
    novelty: list[float],
    sample_rate: int,
    hop_size: int,
    bpm_min: float,
    bpm_max: float,
) -> tuple[float, int]:
    min_lag = max(1, round((60.0 / bpm_max) * sample_rate / hop_size))
    max_lag = max(min_lag + 1, round((60.0 / bpm_min) * sample_rate / hop_size))
    max_lag = min(max_lag, len(novelty) // 2)
    if max_lag <= min_lag:
        raise RuntimeError("Audio is too short for BPM estimation")

    best_lag = min_lag
    best_score = -1.0
    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        count = 0
        for index in range(lag, len(novelty)):
            score += novelty[index] * novelty[index - lag]
            count += 1
        normalized = score / max(1, count)
        if normalized > best_score:
            best_score = normalized
            best_lag = lag

    seconds_per_beat = best_lag * hop_size / sample_rate
    bpm = 60.0 / seconds_per_beat
    return bpm, best_lag


def choose_phase(energies: list[float], novelty: list[float], beat_lag: int) -> int:
    best_phase = 0
    best_score = -1.0
    for phase in range(beat_lag):
        score = 0.0
        for index in range(phase, len(novelty), beat_lag):
            energy = energies[index] if index < len(energies) else 0.0
            score += novelty[index] + energy * 0.25
        if score > best_score:
            best_score = score
            best_phase = phase
    return best_phase


def choose_downbeat_offset(
    beat_indices: list[int], energies: list[float], beats_per_bar: int
) -> int:
    if beats_per_bar <= 1:
        return 0

    scores = [0.0 for _ in range(beats_per_bar)]
    for beat_number, frame_index in enumerate(beat_indices):
        if frame_index < len(energies):
            scores[beat_number % beats_per_bar] += energies[frame_index]
    return max(range(beats_per_bar), key=lambda offset: scores[offset])


def build_markers(
    energies: list[float],
    novelty: list[float],
    beat_lag: int,
    phase: int,
    sample_rate: int,
    hop_size: int,
    beats_per_bar: int,
    cut_every_beats: int,
    max_markers: int,
) -> list[BeatMarker]:
    beat_indices = list(range(phase, len(novelty), beat_lag))
    downbeat_offset = choose_downbeat_offset(beat_indices, energies, beats_per_bar)
    strong_threshold = percentile(
        [energies[index] for index in beat_indices if index < len(energies)], 0.75
    )
    drop_threshold = percentile(novelty, 0.95)

    markers: list[BeatMarker] = []
    for beat_number, frame_index in enumerate(beat_indices):
        if len(markers) >= max_markers:
            break

        time = frame_index * hop_size / sample_rate
        energy = energies[frame_index] if frame_index < len(energies) else 0.0
        onset = novelty[frame_index] if frame_index < len(novelty) else 0.0
        is_downbeat = beats_per_bar > 0 and beat_number % beats_per_bar == downbeat_offset
        is_cut = cut_every_beats > 0 and beat_number % cut_every_beats == 0
        is_drop = onset >= drop_threshold and energy >= strong_threshold

        marker_type = "BEAT"
        label = "BEAT"
        strength = energy + onset
        if is_downbeat or energy >= strong_threshold:
            marker_type = "STRONG_BEAT"
            label = "STRONG BEAT"
        if is_cut:
            marker_type = "CUT_POINT"
            label = "CUT POINT"
        if is_drop:
            marker_type = "DROP"
            label = "DROP?"

        markers.append(
            BeatMarker(
                time=round(time, 3),
                marker_type=marker_type,
                label=label,
                strength=round(strength, 6),
                beat_index=beat_number,
            )
        )

    return markers


def seconds_to_timecode(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def write_reports(
    audio_path: Path,
    output_dir: Path,
    markers: list[BeatMarker],
    bpm: float,
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem
    json_path = output_dir / f"{stem}.beat_cut.json"
    csv_path = output_dir / f"{stem}.beat_cut.csv"

    payload = {
        "audio": str(audio_path),
        "settings": {
            "sample_rate": args.sample_rate,
            "hop_size": args.hop_size,
            "bpm_min": args.bpm_min,
            "bpm_max": args.bpm_max,
            "beats_per_bar": args.beats_per_bar,
            "cut_every_beats": args.cut_every_beats,
            "max_markers": args.max_markers,
        },
        "summary": {
            "estimated_bpm": round(bpm, 2),
            "marker_count": len(markers),
            "cut_point_count": sum(
                1 for marker in markers if marker.marker_type == "CUT_POINT"
            ),
            "drop_count": sum(1 for marker in markers if marker.marker_type == "DROP"),
        },
        "markers": [
            {
                **asdict(marker),
                "timecode": seconds_to_timecode(marker.time),
            }
            for marker in markers
        ],
    }

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "timecode",
                "time",
                "marker_type",
                "label",
                "strength",
                "beat_index",
            ],
        )
        writer.writeheader()
        for marker in payload["markers"]:
            writer.writerow(
                {
                    "timecode": marker["timecode"],
                    "time": marker["time"],
                    "marker_type": marker["marker_type"],
                    "label": marker["label"],
                    "strength": marker["strength"],
                    "beat_index": marker["beat_index"],
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


def marker_color(marker: BeatMarker, args: argparse.Namespace) -> str:
    if marker.marker_type == "DROP":
        return str(args.drop_marker_color)
    if marker.marker_type == "CUT_POINT":
        return str(args.cut_marker_color)
    if marker.marker_type == "STRONG_BEAT":
        return str(args.strong_marker_color)
    return str(args.marker_color)


def add_resolve_markers(markers: list[BeatMarker], args: argparse.Namespace) -> int:
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

    fps = get_timeline_fps(timeline, args.timeline_fps)
    added = 0
    for marker in markers:
        frame = int(round(marker.time * fps))
        note = (
            f"{marker.marker_type} at {seconds_to_timecode(marker.time)}. "
            f"Strength: {marker.strength}."
        )
        if timeline.AddMarker(
            frame,
            marker_color(marker, args),
            marker.label,
            note,
            args.marker_duration_frames,
            "",
        ):
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
        samples = decode_audio(audio_path, args.sample_rate)
        if len(samples) < args.sample_rate:
            raise RuntimeError("Audio is too short for beat analysis")

        energies = build_energy(samples, args.hop_size)
        novelty = build_novelty(energies)
        bpm, beat_lag = estimate_tempo(
            novelty, args.sample_rate, args.hop_size, args.bpm_min, args.bpm_max
        )
        phase = choose_phase(energies, novelty, beat_lag)
        markers = build_markers(
            energies,
            novelty,
            beat_lag,
            phase,
            args.sample_rate,
            args.hop_size,
            args.beats_per_bar,
            args.cut_every_beats,
            args.max_markers,
        )
        json_path, csv_path = write_reports(audio_path, args.output_dir, markers, bpm, args)

        print(f"Estimated BPM: {bpm:.2f}")
        print(f"Markers: {len(markers)}")
        print(f"Cut points: {sum(1 for marker in markers if marker.marker_type == 'CUT_POINT')}")
        print(f"Drops/peaks: {sum(1 for marker in markers if marker.marker_type == 'DROP')}")
        print(f"JSON report: {json_path}")
        print(f"CSV report: {csv_path}")

        if args.add_resolve_markers:
            marker_count = add_resolve_markers(markers, args)
            print(f"Added Resolve markers: {marker_count}")

    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
