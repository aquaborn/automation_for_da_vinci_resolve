#!/usr/bin/env python3
"""
Find speech pauses and long filler sounds using Whisper word timestamps.

This is meant for rough-cut assistance: it marks places where a person stopped
speaking, hesitated, or stretched filler sounds such as "эээ", "ааа", "ммм".
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ELONGATED_RE = re.compile(r"([аоэеиыуяюёaeiouмm])\1{2,}", re.IGNORECASE)


@dataclass(frozen=True)
class SpeechWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class SpeechMarker:
    time: float
    duration: float
    marker_type: str
    label: str
    note: str
    color: str


def load_config_defaults(argv: list[str]) -> dict[str, Any]:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    pre_args, _ = pre_parser.parse_known_args(argv)
    if not pre_args.config:
        return {}

    config_path = pre_args.config.expanduser().resolve()
    if not config_path.exists():
        raise RuntimeError(f"Config file not found: {config_path}")
    with config_path.open(encoding="utf-8") as config_file:
        return json.load(config_file)


def parse_args(argv: list[str]) -> argparse.Namespace:
    config = load_config_defaults(argv)
    parser = argparse.ArgumentParser(
        description="Find speech pauses and filler words from Whisper timestamps."
    )
    parser.add_argument("audio", type=Path, help="Audio/video file to transcribe")
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--words-json",
        type=Path,
        help="Optional precomputed Whisper JSON with word timestamps",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(config.get("output_dir", "reports/speech_pauses")),
    )
    parser.add_argument(
        "--model",
        default=config.get("model", "small"),
        help="Whisper model name, for example tiny/base/small/medium",
    )
    parser.add_argument(
        "--language",
        default=config.get("language", "ru"),
        help="Whisper language hint. Use empty string for auto",
    )
    parser.add_argument(
        "--min-pause",
        type=float,
        default=float(config.get("min_pause", 0.55)),
        help="Minimum gap between words to mark as speech pause",
    )
    parser.add_argument(
        "--keep-before",
        type=float,
        default=float(config.get("keep_before", 0.08)),
        help="Seconds to keep before next word when suggesting pause cut",
    )
    parser.add_argument(
        "--keep-after",
        type=float,
        default=float(config.get("keep_after", 0.10)),
        help="Seconds to keep after previous word when suggesting pause cut",
    )
    parser.add_argument(
        "--max-pause",
        type=float,
        default=float(config.get("max_pause", 8.0)),
        help="Ignore pauses longer than this; they are often section breaks",
    )
    parser.add_argument(
        "--long-filler",
        type=float,
        default=float(config.get("long_filler", 0.45)),
        help="Minimum duration for filler word/sound markers",
    )
    parser.add_argument(
        "--filler-words",
        nargs="*",
        default=config.get(
            "filler_words",
            ["э", "ээ", "эээ", "эм", "мм", "ммм", "а", "аа", "ааа", "ну"],
        ),
    )
    parser.add_argument(
        "--pause-color",
        default=config.get("pause_color", "Yellow"),
    )
    parser.add_argument(
        "--filler-color",
        default=config.get("filler_color", "Orange"),
    )
    parser.add_argument(
        "--long-filler-color",
        default=config.get("long_filler_color", "Red"),
    )
    return parser.parse_args(argv)


def normalize_word(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", "", text).lower().replace("ё", "е")


def is_filler(word: SpeechWord, filler_words: set[str]) -> bool:
    normalized = normalize_word(word.text)
    if not normalized:
        return False
    if normalized in filler_words:
        return True
    return bool(ELONGATED_RE.search(normalized))


def read_words_json(path: Path) -> list[SpeechWord]:
    with path.expanduser().resolve().open(encoding="utf-8") as file:
        payload = json.load(file)
    return words_from_payload(payload)


def words_from_payload(payload: dict[str, Any]) -> list[SpeechWord]:
    words: list[SpeechWord] = []
    for segment in payload.get("segments", []):
        for word in segment.get("words", []):
            text = str(word.get("word") or word.get("text") or "").strip()
            start = word.get("start")
            end = word.get("end")
            if text and start is not None and end is not None:
                words.append(SpeechWord(text=text, start=float(start), end=float(end)))
    return sorted(words, key=lambda item: item.start)


def transcribe_words(audio_path: Path, model_name: str, language: str | None) -> list[SpeechWord]:
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel(model_name, device="auto", compute_type="int8")
        segments, _ = model.transcribe(
            str(audio_path),
            language=language or None,
            word_timestamps=True,
            vad_filter=True,
        )
        words: list[SpeechWord] = []
        for segment in segments:
            for word in segment.words or []:
                words.append(
                    SpeechWord(
                        text=str(word.word).strip(),
                        start=float(word.start),
                        end=float(word.end),
                    )
                )
        return sorted(words, key=lambda item: item.start)
    except ImportError:
        pass

    try:
        import whisper_timestamped as whisper  # type: ignore

        model = whisper.load_model(model_name)
        result = whisper.transcribe(
            model,
            str(audio_path),
            language=language or None,
            vad=False,
        )
        return words_from_payload(result)
    except ImportError:
        pass

    try:
        import whisper  # type: ignore

        model = whisper.load_model(model_name)
        result = model.transcribe(
            str(audio_path),
            language=language or None,
            word_timestamps=True,
            verbose=False,
        )
        return words_from_payload(result)
    except ImportError as error:
        raise RuntimeError(
            "No Whisper backend found. Install one of: "
            "`pip install faster-whisper`, `pip install whisper-timestamped`, "
            "or `pip install openai-whisper`."
        ) from error


def build_markers(
    words: list[SpeechWord],
    min_pause: float,
    max_pause: float,
    keep_before: float,
    keep_after: float,
    long_filler: float,
    filler_words: set[str],
    pause_color: str,
    filler_color: str,
    long_filler_color: str,
) -> list[SpeechMarker]:
    markers: list[SpeechMarker] = []

    for previous, current in zip(words, words[1:]):
        gap = current.start - previous.end
        cut_start = previous.end + keep_after
        cut_end = current.start - keep_before
        cut_duration = cut_end - cut_start
        if gap >= min_pause and gap <= max_pause and cut_duration > 0:
            markers.append(
                SpeechMarker(
                    time=round(cut_start, 3),
                    duration=round(cut_duration, 3),
                    marker_type="SPEECH_PAUSE",
                    label=f"SPEECH PAUSE {gap:.1f}s",
                    note=(
                        f"Between '{previous.text}' and '{current.text}'. "
                        f"Gap {gap:.2f}s, suggested cut {cut_duration:.2f}s."
                    ),
                    color=pause_color,
                )
            )

    for word in words:
        duration = word.end - word.start
        if is_filler(word, filler_words) and duration >= long_filler:
            marker_type = "LONG_FILLER" if duration >= long_filler * 1.5 else "FILLER"
            color = long_filler_color if marker_type == "LONG_FILLER" else filler_color
            label = "LONG FILLER" if marker_type == "LONG_FILLER" else "FILLER"
            markers.append(
                SpeechMarker(
                    time=round(word.start, 3),
                    duration=round(max(0.1, duration), 3),
                    marker_type=marker_type,
                    label=f"{label} {word.text}",
                    note=f"'{word.text}' duration {duration:.2f}s.",
                    color=color,
                )
            )

    return sorted(markers, key=lambda item: item.time)


def seconds_to_timecode(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def write_reports(
    audio_path: Path, output_dir: Path, words: list[SpeechWord], markers: list[SpeechMarker]
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem
    json_path = output_dir / f"{stem}.speech_pauses.json"
    csv_path = output_dir / f"{stem}.speech_pauses.csv"
    payload = {
        "audio": str(audio_path),
        "summary": {
            "word_count": len(words),
            "marker_count": len(markers),
            "speech_pause_count": sum(1 for item in markers if item.marker_type == "SPEECH_PAUSE"),
            "filler_count": sum(1 for item in markers if item.marker_type in {"FILLER", "LONG_FILLER"}),
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
            fieldnames=["timecode", "time", "duration", "marker_type", "label", "note"],
        )
        writer.writeheader()
        for marker in payload["markers"]:
            writer.writerow(
                {
                    "timecode": marker["timecode"],
                    "time": marker["time"],
                    "duration": marker["duration"],
                    "marker_type": marker["marker_type"],
                    "label": marker["label"],
                    "note": marker["note"],
                }
            )
    return json_path, csv_path


def analyze_audio(args: argparse.Namespace) -> tuple[list[SpeechWord], list[SpeechMarker]]:
    audio_path = args.audio.expanduser().resolve()
    if args.words_json:
        words = read_words_json(args.words_json)
    else:
        words = transcribe_words(audio_path, str(args.model), str(args.language or ""))
    filler_words = {normalize_word(word) for word in args.filler_words}
    markers = build_markers(
        words,
        args.min_pause,
        args.max_pause,
        args.keep_before,
        args.keep_after,
        args.long_filler,
        filler_words,
        str(args.pause_color),
        str(args.filler_color),
        str(args.long_filler_color),
    )
    return words, markers


def main() -> int:
    try:
        args = parse_args(sys.argv[1:])
        audio_path = args.audio.expanduser().resolve()
        if not audio_path.exists():
            print(f"Audio file not found: {audio_path}", file=sys.stderr)
            return 2
        words, markers = analyze_audio(args)
        json_path, csv_path = write_reports(audio_path, args.output_dir, words, markers)
        print(f"Words: {len(words)}")
        print(f"Speech markers: {len(markers)}")
        print(f"Speech pauses: {sum(1 for item in markers if item.marker_type == 'SPEECH_PAUSE')}")
        print(f"Fillers: {sum(1 for item in markers if item.marker_type in {'FILLER', 'LONG_FILLER'})}")
        print(f"JSON report: {json_path}")
        print(f"CSV report: {csv_path}")
        return 0
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
