"""
run_cli.py — Run the clipper from the command line (no web UI needed).

Usage:
    python run_cli.py path/to/your/video.mp4
    python run_cli.py video.mp4 --clips 5 --model base --mode balanced
    python run_cli.py video.mp4 --mode aggressive --max-clips 20
"""

import argparse
import json
import os
import subprocess
from clipper.transcriber import transcribe_video
from clipper.scorer import get_top_clips
from clipper.exporter import export_clips


def get_video_duration(video_path: str) -> float:
    """Use ffprobe to get video duration in seconds."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            ],
            capture_output=True, text=True, check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Warning: could not read video duration ({e}). Using 5 minutes as estimate.")
        return 300.0


def main():
    parser = argparse.ArgumentParser(description="Video Clipper CLI")
    parser.add_argument("video", help="Path to input video file")
    parser.add_argument("--clips", type=int, default=None,
                        help="Number of clips to generate (default: auto based on video length)")
    parser.add_argument("--model", default="base",
                        help="Whisper model: tiny, base, small, medium, large")
    parser.add_argument("--mode", default="balanced",
                        choices=["conservative", "balanced", "aggressive"],
                        help="Clip selection mode (default: balanced)")
    parser.add_argument("--max-clips", type=int, default=25,
                        help="Hard cap on clips produced (default: 25)")
    parser.add_argument("--output", default="output", help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"Error: File not found: {args.video}")
        return

    print(f"Processing: {args.video}")

    video_duration = get_video_duration(args.video)
    print(f"Video duration: {video_duration / 60:.1f} minutes")

    result = transcribe_video(args.video, model_size=args.model)

    os.makedirs(args.output, exist_ok=True)
    with open(os.path.join(args.output, "transcript.txt"), "w", encoding="utf-8") as f:
        f.write(result["text"])

    top_clips = get_top_clips(
        whisper_result=result,
        video_duration=video_duration,
        mode=args.mode,
        top_n=args.clips,   # None = auto
        max_clips=args.max_clips,
    )

    if not top_clips:
        print("No clips passed quality filter. Try --mode aggressive.")
        return

    with open(os.path.join(args.output, "top_clips.json"), "w", encoding="utf-8") as f:
        json.dump(top_clips, f, indent=2)

    print(f"\nTop {len(top_clips)} clips selected:")
    for i, clip in enumerate(top_clips):
        print(f"  {i+1}. [{clip['start']}s → {clip['end']}s] score={clip['score']} | {clip['text'][:60]}")

    export_clips(
        video_path=args.video,
        clips=top_clips,
        output_dir=args.output,
    )

    print(f"\nDone. Clips saved to: {args.output}/")


if __name__ == "__main__":
    main()
