import subprocess
import re
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def probe_duration(filepath: Path) -> float:
    """Probes the media file duration using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(filepath)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = res.stdout.strip()
        return float(val) if val else 0.0
    except Exception as e:
        logger.warning(f"ffprobe duration failed for {filepath}: {e}")
        return 0.0

def split_ts_file(
    input_path: Path,
    output_dir: Path,
    segment_duration: int = 300
) -> List[Dict[str, Any]]:
    """
    Splits an MPEG-2 TS file into segments of approximately `segment_duration` seconds (default 300s = 5m)
    losslessly using ffmpeg copy. Returns metadata for each generated segment.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "segment_%05d.ts")

    logger.info(f"Splitting {input_path} into {segment_duration}s segments at {output_dir}")
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-c", "copy",
        "-map", "0",
        "-f", "segment",
        "-segment_time", str(segment_duration),
        "-reset_timestamps", "1",
        "-segment_format", "mpegts",
        pattern
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        logger.error(f"FFmpeg split failed: {proc.stderr}")
        raise RuntimeError(f"FFmpeg segmenting failed: {proc.stderr}")

    # Gather generated segments in numerical order
    segment_files = sorted(
        output_dir.glob("segment_*.ts"),
        key=lambda p: int(re.findall(r"\d+", p.stem)[-1]) if re.findall(r"\d+", p.stem) else 0
    )

    if not segment_files:
        raise RuntimeError(f"No segments generated from {input_path}")

    segments_meta = []
    for idx, seg_file in enumerate(segment_files):
        dur = probe_duration(seg_file)
        # Fallback if ffprobe returned 0 for some reason
        if dur <= 0:
            dur = float(segment_duration)
        segments_meta.append({
            "index": idx,
            "filename": seg_file.name,
            "path": str(seg_file.resolve()),
            "duration": dur
        })

    logger.info(f"Split {input_path.name} into {len(segments_meta)} segments.")
    return segments_meta
