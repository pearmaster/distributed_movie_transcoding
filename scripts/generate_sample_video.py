#!/usr/bin/env python3
"""
Generates synthetic MPEG-2 Transport Stream (.ts) files with audio for testing.
"""

import argparse
import subprocess
import sys
from pathlib import Path

def generate_ts(output_path: Path, duration_seconds: int = 15):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating {duration_seconds}s MPEG-2 TS file at {output_path}...")

    # Generates a test pattern with moving timestamp and test tone
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration_seconds}:size=640x360:rate=30",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration_seconds}",
        "-vf", "drawtext=text='%{pts\\:hms}':fontcolor=white:fontsize=24:x=10:y=10:box=1:boxcolor=black@0.5",
        "-c:v", "mpeg2video",
        "-b:v", "2000k",
        "-c:a", "mp2",
        "-b:a", "128k",
        "-f", "mpegts",
        str(output_path)
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        # If drawtext fails due to missing font, fallback without filter
        cmd_no_vf = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration_seconds}:size=640x360:rate=30",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={duration_seconds}",
            "-c:v", "mpeg2video",
            "-b:v", "2000k",
            "-c:a", "mp2",
            "-b:a", "128k",
            "-f", "mpegts",
            str(output_path)
        ]
        res = subprocess.run(cmd_no_vf, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f"Failed to generate TS file: {res.stderr}", file=sys.stderr)
            sys.exit(1)

    print(f"Generated {output_path} successfully ({output_path.stat().st_size} bytes).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic .ts file")
    parser.add_argument("--output", "-o", default="input/sample_movie.ts", help="Output path")
    parser.add_argument("--duration", "-d", type=int, default=15, help="Duration in seconds")
    args = parser.parse_args()

    out_p = Path(args.output).resolve()
    generate_ts(out_p, args.duration)
