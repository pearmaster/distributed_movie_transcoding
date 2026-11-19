import subprocess
import time
import shutil
import logging
from pathlib import Path
from typing import Tuple

from .config import HANDBRAKE_PRESET, HANDBRAKE_ENCODER, HANDBRAKE_QUALITY

logger = logging.getLogger(__name__)

def probe_duration(filepath: Path) -> float:
    """Probes media duration via ffprobe if available."""
    if not shutil.which("ffprobe"):
        return 0.0
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
        logger.warning(f"ffprobe duration failed on worker for {filepath}: {e}")
        return 0.0

def transcode_to_h264_mkv(
    input_file: Path,
    output_file: Path,
    fallback_duration: float = 0.0
) -> Tuple[float, float]:
    """
    Transcodes an MPEG-2 TS segment into H.264 MKV using HandBrakeCLI.
    Measures:
    - video_duration: video duration in seconds
    - transcode_time: elapsed wall-clock seconds
    Returns (video_duration, transcode_time).
    """
    if not shutil.which("HandBrakeCLI"):
        raise RuntimeError("HandBrakeCLI binary not found in PATH")

    # Get segment video duration
    video_dur = probe_duration(input_file)
    if video_dur <= 0.0:
        video_dur = fallback_duration

    # Construct HandBrakeCLI command
    cmd = [
        "HandBrakeCLI",
        "-i", str(input_file),
        "-o", str(output_file),
    ]

    if HANDBRAKE_PRESET:
        cmd.extend(["-Z", HANDBRAKE_PRESET])
    else:
        cmd.extend([
            "-e", HANDBRAKE_ENCODER,
            "-q", str(HANDBRAKE_QUALITY),
            "-E", "av_aac",
            "-B", "160"
        ])

    logger.info(f"Starting HandBrake conversion: {' '.join(cmd)}")
    start_time = time.perf_counter()

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    elapsed_time = time.perf_counter() - start_time

    if proc.returncode != 0:
        logger.error(f"HandBrakeCLI failed with code {proc.returncode}:\n{proc.stderr}")
        raise RuntimeError(f"HandBrakeCLI failed: {proc.stderr}")

    if not output_file.exists() or output_file.stat().st_size == 0:
        raise RuntimeError(f"HandBrakeCLI exited 0 but output file {output_file} is missing or empty")

    logger.info(
        f"Transcode complete: {output_file.name} ({output_file.stat().st_size} bytes) in {elapsed_time:.2f}s "
        f"(video duration: {video_dur:.2f}s, speed: {video_dur / max(elapsed_time, 0.001):.2f}x)"
    )

    return video_dur, elapsed_time
