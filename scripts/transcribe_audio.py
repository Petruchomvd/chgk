"""Transcribe a local audio file with faster-whisper."""

import argparse
import sys
from pathlib import Path

import av

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.youtube_transcriber import transcribe


def _get_duration_seconds(audio_path: Path) -> int:
    container = av.open(str(audio_path))
    try:
        stream = container.streams.audio[0]
        if stream.duration is None:
            return 0
        return int(float(stream.duration * stream.time_base))
    finally:
        container.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_path", help="Path to local audio file")
    parser.add_argument("--model", default="large-v3", help="Whisper model size")
    parser.add_argument(
        "--chunk-minutes",
        type=int,
        default=5,
        help="Chunk size in minutes for long audio",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="Preferred device for faster-whisper",
    )
    parser.add_argument(
        "--output",
        help="Optional path for plain-text transcript. Defaults to <audio>.txt",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio_path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    out_path = Path(args.output).expanduser().resolve() if args.output else audio_path.with_suffix(".txt")
    meta = {
        "video_id": audio_path.stem,
        "title": audio_path.stem,
        "url": "",
        "channel": "local-file",
        "duration": _get_duration_seconds(audio_path),
    }

    result = transcribe(
        str(audio_path),
        meta,
        model_size=args.model,
        chunk_minutes=args.chunk_minutes,
        prefer_device=args.device,
    )
    out_path.write_text(result["full_text"], encoding="utf-8")

    print(f"Done: {out_path}")
    print(f"Segments: {len(result['segments'])}")


if __name__ == "__main__":
    main()
