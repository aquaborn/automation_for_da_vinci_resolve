#!/usr/bin/env python3
"""
Run rough-cut helpers against the current DaVinci Resolve timeline In/Out range.

The reliable workflow is:
1. Mark In/Out in Resolve.
2. Export audio for exactly that range.
3. Run this script with the exported audio.

Markers are added back to the active timeline with the In-point offset.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import beat_cut_markers
import rough_cut_silence


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


def get_resolve_context() -> tuple[Any, Any, Any]:
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

    return resolve, project, timeline


def get_timeline_fps(timeline: Any, fallback: float | None) -> float:
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


def parse_frame(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def first_frame(*values: Any) -> int | None:
    for value in values:
        frame = parse_frame(value)
        if frame is not None:
            return frame
    return None


def extract_mark_range(mark_data: Any) -> tuple[int, int] | None:
    if isinstance(mark_data, dict):
        for key in ("video", "Video", "audio", "Audio"):
            nested = mark_data.get(key)
            nested_range = extract_mark_range(nested)
            if nested_range:
                return nested_range

        in_frame = first_frame(
            mark_data.get("in"),
            mark_data.get("In"),
            mark_data.get("markIn"),
            mark_data.get("MarkIn"),
        )
        out_frame = first_frame(
            mark_data.get("out"),
            mark_data.get("Out"),
            mark_data.get("markOut"),
            mark_data.get("MarkOut"),
        )
        if in_frame is not None and out_frame is not None:
            return min(in_frame, out_frame), max(in_frame, out_frame)

    if isinstance(mark_data, (list, tuple)) and len(mark_data) >= 2:
        in_frame = parse_frame(mark_data[0])
        out_frame = parse_frame(mark_data[1])
        if in_frame is not None and out_frame is not None:
            return min(in_frame, out_frame), max(in_frame, out_frame)

    return None


def get_timeline_inout(timeline: Any) -> tuple[int, int]:
    if hasattr(timeline, "GetMarkInOut"):
        mark_range = extract_mark_range(timeline.GetMarkInOut())
        if mark_range:
            return mark_range

    raise RuntimeError(
        "Could not read timeline In/Out. Set In/Out in Resolve, or check that "
        "your Resolve scripting API exposes Timeline.GetMarkInOut()."
    )


def seconds_to_timecode(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def frame_to_timecode(frame: int, fps: float) -> str:
    return seconds_to_timecode(frame / fps)


def load_config(config_path: Path | None) -> dict[str, Any]:
    if not config_path:
        return {}
    resolved = config_path.expanduser().resolve()
    if not resolved.exists():
        raise RuntimeError(f"Config file not found: {resolved}")
    with resolved.open(encoding="utf-8") as config_file:
        return json.load(config_file)


def get_marker_type_filter(config: dict[str, Any]) -> set[str] | None:
    value = config.get("include_marker_types")
    if not value:
        return None
    if not isinstance(value, list):
        raise RuntimeError("include_marker_types must be a JSON array")
    return {str(item).upper() for item in value}


def get_min_marker_spacing(config: dict[str, Any]) -> float:
    return float(config.get("min_marker_spacing_seconds", 0.0))


def render_inout_audio(
    project: Any,
    in_frame: int,
    out_frame: int,
    output_dir: Path,
    name_prefix: str,
    wait: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    custom_name = f"{name_prefix}_{in_frame}_{out_frame}"

    render_attempts = [
        {
            "name": "wav-empty-codec",
            "format": "wav",
            "codec": "",
            "extension": ".wav",
            "export_video": False,
            "settings": {},
        },
        {
            "name": "wave-empty-codec",
            "format": "Wave",
            "codec": "",
            "extension": ".wav",
            "export_video": False,
            "settings": {},
        },
        {
            "name": "mov-h264-audio",
            "format": "mov",
            "codec": "H264",
            "extension": ".mov",
            "export_video": True,
            "settings": {
                "VideoQuality": "Least",
            },
        },
    ]
    last_settings = None
    attempt_results = []
    output_path = None

    for attempt in render_attempts:
        output_path = output_dir / f"{custom_name}{attempt['extension']}"
        set_format_result = None
        if hasattr(project, "SetCurrentRenderFormatAndCodec"):
            set_format_result = project.SetCurrentRenderFormatAndCodec(
                attempt["format"], attempt["codec"]
            )

        settings = {
            "TargetDir": str(output_dir),
            "CustomName": custom_name,
            "UniqueFilenameStyle": 0,
            "SelectAllFrames": False,
            "MarkIn": in_frame,
            "MarkOut": out_frame,
            "ExportVideo": attempt["export_video"],
            "ExportAudio": True,
            "AudioBitDepth": 24,
            "AudioSampleRate": 48000,
        }
        settings.update(attempt["settings"])
        if attempt["codec"]:
            settings["AudioCodec"] = attempt["codec"]

        last_settings = settings
        set_settings_result = project.SetRenderSettings(settings)
        attempt_results.append(
            {
                "name": attempt["name"],
                "format": attempt["format"],
                "codec": attempt["codec"],
                "set_format": set_format_result,
                "set_settings": set_settings_result,
                "settings": settings,
            }
        )
        if set_settings_result:
            break
    else:
        debug_lines = ["Resolve rejected audio render settings."]
        debug_lines.append(f"Last settings: {last_settings}")
        debug_lines.append(f"Attempts: {attempt_results}")
        if hasattr(project, "GetRenderFormats"):
            try:
                debug_lines.append(f"Render formats: {project.GetRenderFormats()}")
            except Exception as error:
                debug_lines.append(f"GetRenderFormats failed: {error}")
        if hasattr(project, "GetRenderCodecs"):
            for format_name in ("wav", "mov"):
                try:
                    debug_lines.append(
                        f"Render codecs for {format_name}: {project.GetRenderCodecs(format_name)}"
                    )
                except Exception as error:
                    debug_lines.append(f"GetRenderCodecs({format_name}) failed: {error}")
        raise RuntimeError("\n".join(debug_lines))

    job_id = project.AddRenderJob()
    if not job_id:
        raise RuntimeError("Could not add Resolve render job")

    if not project.StartRendering(job_id):
        raise RuntimeError("Could not start Resolve audio render")

    if wait:
        while project.IsRenderingInProgress():
            time.sleep(1.0)

    if output_path is None or not output_path.exists():
        candidates = sorted(output_dir.glob(f"{custom_name}.*"))
        if candidates:
            return candidates[0]
        raise RuntimeError(
            f"Resolve render finished, but expected file was not found: {output_path}"
        )

    return output_path


def maybe_trim_audio_with_ffmpeg(
    source: Path,
    target: Path,
    in_frame: int,
    out_frame: int,
    fps: float,
) -> Path:
    start_seconds = in_frame / fps
    duration_seconds = max(0.0, (out_frame - in_frame) / fps)
    if duration_seconds <= 0:
        raise RuntimeError("In/Out range is empty")

    ffmpeg_path = beat_cut_markers.find_ffmpeg()
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is not installed or not found in PATH")

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg trim failed with exit code {result.returncode}\n{error}")
    return target


def resolve_audio_input(
    args: argparse.Namespace,
    project: Any,
    in_frame: int,
    out_frame: int,
    fps: float,
    prefix: str,
) -> Path:
    if args.audio:
        audio_path = args.audio.expanduser().resolve()
        if not audio_path.exists():
            raise RuntimeError(f"Audio file not found: {audio_path}")
        if args.audio_is_full_timeline:
            temp_dir = Path(tempfile.mkdtemp(prefix=f"{prefix}_inout_"))
            return maybe_trim_audio_with_ffmpeg(
                audio_path, temp_dir / f"{prefix}_inout.wav", in_frame, out_frame, fps
            )
        return audio_path

    if args.render_audio:
        render_dir = args.render_dir or Path(tempfile.mkdtemp(prefix=f"{prefix}_render_"))
        return render_inout_audio(project, in_frame, out_frame, render_dir, prefix, True)

    raise RuntimeError(
        "Pass --audio with audio exported for the current In/Out range, or use "
        "--render-audio to let Resolve try rendering it."
    )


def add_timeline_marker(
    timeline: Any,
    frame: int,
    color: str,
    name: str,
    note: str,
    duration: int,
) -> bool:
    return bool(timeline.AddMarker(frame, color, name, note, max(1, duration), ""))


def command_silence(args: argparse.Namespace) -> int:
    _, project, timeline = get_resolve_context()
    fps = get_timeline_fps(timeline, args.timeline_fps)
    in_frame, out_frame = get_timeline_inout(timeline)
    audio_path = resolve_audio_input(args, project, in_frame, out_frame, fps, "silence")

    config = load_config(args.config)
    noise_db = str(args.noise_db or config.get("noise_db", "-38dB"))
    min_silence = float(args.min_silence or config.get("min_silence", 0.8))
    keep_before = float(args.keep_before or config.get("keep_before", 0.12))
    keep_after = float(args.keep_after or config.get("keep_after", 0.18))
    max_cut = float(args.max_cut or config.get("max_cut", 8.0))
    marker_color = str(args.marker_color or config.get("marker_color", "Yellow"))

    ffmpeg_output = rough_cut_silence.run_ffmpeg_silencedetect(
        audio_path, noise_db, min_silence
    )
    silences = rough_cut_silence.parse_silences(ffmpeg_output)
    candidates = rough_cut_silence.build_candidates(
        silences, keep_before, keep_after, max_cut
    )

    added = 0
    for item in candidates:
        frame = in_frame + int(round(item.cut_start * fps))
        duration = int(round(item.cut_duration * fps))
        note = (
            f"In/Out local cut: {seconds_to_timecode(item.cut_start)}-"
            f"{seconds_to_timecode(item.cut_end)}. "
            f"Timeline: {frame_to_timecode(frame, fps)}. "
            f"Silence duration: {item.duration:.2f}s."
        )
        if add_timeline_marker(
            timeline,
            frame,
            marker_color,
            f"CUT? silence {item.cut_duration:.1f}s",
            note,
            duration,
        ):
            added += 1

    print(f"In/Out frames: {in_frame}-{out_frame}")
    print(f"Audio analyzed: {audio_path}")
    print(f"Silence candidates: {len(candidates)}")
    print(f"Resolve markers added: {added}")
    return 0


def command_beat(args: argparse.Namespace) -> int:
    _, project, timeline = get_resolve_context()
    fps = get_timeline_fps(timeline, args.timeline_fps)
    in_frame, out_frame = get_timeline_inout(timeline)
    audio_path = resolve_audio_input(args, project, in_frame, out_frame, fps, "beat")

    config = load_config(args.config)
    sample_rate = int(args.sample_rate or config.get("sample_rate", 22050))
    hop_size = int(args.hop_size or config.get("hop_size", 256))
    bpm_min = float(args.bpm_min or config.get("bpm_min", 70))
    bpm_max = float(args.bpm_max or config.get("bpm_max", 190))
    beats_per_bar = int(args.beats_per_bar or config.get("beats_per_bar", 4))
    cut_every_beats = int(args.cut_every_beats or config.get("cut_every_beats", 2))
    max_markers = int(args.max_markers or config.get("max_markers", 500))
    marker_duration_frames = int(
        args.marker_duration_frames or config.get("marker_duration_frames", 1)
    )

    samples = beat_cut_markers.decode_audio(audio_path, sample_rate)
    energies = beat_cut_markers.build_energy(samples, hop_size)
    novelty = beat_cut_markers.build_novelty(energies)
    bpm, beat_lag = beat_cut_markers.estimate_tempo(
        novelty, sample_rate, hop_size, bpm_min, bpm_max
    )
    phase = beat_cut_markers.choose_phase(energies, novelty, beat_lag)
    markers = beat_cut_markers.build_markers(
        energies,
        novelty,
        beat_lag,
        phase,
        sample_rate,
        hop_size,
        beats_per_bar,
        cut_every_beats,
        max_markers,
    )
    marker_type_filter = get_marker_type_filter(config)
    unfiltered_marker_count = len(markers)
    markers = beat_cut_markers.filter_markers(
        markers, marker_type_filter, get_min_marker_spacing(config)
    )

    color_by_type = {
        "BEAT": str(args.marker_color or config.get("marker_color", "Blue")),
        "STRONG_BEAT": str(
            args.strong_marker_color or config.get("strong_marker_color", "Cyan")
        ),
        "CUT_POINT": str(args.cut_marker_color or config.get("cut_marker_color", "Yellow")),
        "DROP": str(args.drop_marker_color or config.get("drop_marker_color", "Red")),
    }

    added = 0
    for marker in markers:
        frame = in_frame + int(round(marker.time * fps))
        if frame > out_frame:
            continue
        note = (
            f"In/Out local time: {seconds_to_timecode(marker.time)}. "
            f"Timeline: {frame_to_timecode(frame, fps)}. "
            f"Strength: {marker.strength}."
        )
        if add_timeline_marker(
            timeline,
            frame,
            color_by_type.get(marker.marker_type, "Blue"),
            marker.label,
            note,
            marker_duration_frames,
        ):
            added += 1

    print(f"In/Out frames: {in_frame}-{out_frame}")
    print(f"Audio analyzed: {audio_path}")
    print(f"Estimated BPM: {bpm:.2f}")
    print(f"Beat markers found: {unfiltered_marker_count}")
    if marker_type_filter:
        print(f"Marker type filter: {sorted(marker_type_filter)}")
        print(f"Beat markers after filter: {len(markers)}")
    print(f"Resolve markers added: {added}")
    return 0


def add_common_audio_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--audio",
        type=Path,
        help="Audio file exported for the current In/Out range",
    )
    parser.add_argument(
        "--audio-is-full-timeline",
        action="store_true",
        help="Treat --audio as full timeline audio and trim it to In/Out with ffmpeg",
    )
    parser.add_argument(
        "--render-audio",
        action="store_true",
        help="Experimental: ask Resolve to render current In/Out audio to WAV first",
    )
    parser.add_argument(
        "--render-dir",
        type=Path,
        help="Directory for the experimental --render-audio WAV",
    )
    parser.add_argument(
        "--timeline-fps",
        type=float,
        default=None,
        help="Fallback FPS if Resolve cannot report timelineFrameRate",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Config JSON for the selected analyzer",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add rough-cut markers to the active Resolve timeline In/Out range."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    silence = subparsers.add_parser("silence", help="Add silence cut markers")
    add_common_audio_args(silence)
    silence.add_argument("--noise-db", default=None)
    silence.add_argument("--min-silence", type=float, default=None)
    silence.add_argument("--keep-before", type=float, default=None)
    silence.add_argument("--keep-after", type=float, default=None)
    silence.add_argument("--max-cut", type=float, default=None)
    silence.add_argument("--marker-color", default=None)
    silence.set_defaults(func=command_silence)

    beat = subparsers.add_parser("beat", help="Add beat/cut-point markers")
    add_common_audio_args(beat)
    beat.add_argument("--sample-rate", type=int, default=None)
    beat.add_argument("--hop-size", type=int, default=None)
    beat.add_argument("--bpm-min", type=float, default=None)
    beat.add_argument("--bpm-max", type=float, default=None)
    beat.add_argument("--beats-per-bar", type=int, default=None)
    beat.add_argument("--cut-every-beats", type=int, default=None)
    beat.add_argument("--max-markers", type=int, default=None)
    beat.add_argument("--marker-duration-frames", type=int, default=None)
    beat.add_argument("--marker-color", default=None)
    beat.add_argument("--strong-marker-color", default=None)
    beat.add_argument("--cut-marker-color", default=None)
    beat.add_argument("--drop-marker-color", default=None)
    beat.set_defaults(func=command_beat)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
