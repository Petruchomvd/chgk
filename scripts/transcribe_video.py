"""Transcribe a YouTube video to text file."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper.youtube_transcriber import download_audio, transcribe

video_id = sys.argv[1] if len(sys.argv) > 1 else input("Video ID: ")
url = f"https://www.youtube.com/watch?v={video_id}"

print(f"Downloading audio for {video_id}...")
meta = download_audio(url)

print("Transcribing...")
result = transcribe(meta["audio_path"], meta)

out_path = f"data/{video_id}_transcript.txt"
with open(out_path, "w", encoding="utf-8") as f:
    for seg in result["segments"]:
        m, s = divmod(int(seg["start"]), 60)
        h, m = divmod(m, 60)
        f.write(f"[{h}:{m:02d}:{s:02d}] {seg['text'].strip()}\n")

print(f"Done: {out_path} ({len(result['segments'])} segments)")
