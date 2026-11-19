import subprocess
import shutil
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

def join_mkv_segments(segment_paths: List[Path], output_path: Path) -> Path:
    """
    Concatenates a list of MKV segment files in order into a single MKV file.
    Prefers mkvmerge if installed, with a fallback to ffmpeg concat demuxer.
    """
    if not segment_paths:
        raise ValueError("Cannot join empty segment list")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(".tmp.mkv")
    if temp_output.exists():
        temp_output.unlink()

    # Try mkvmerge first
    has_mkvmerge = shutil.which("mkvmerge") is not None
    if has_mkvmerge:
        try:
            logger.info(f"Joining {len(segment_paths)} segments using mkvmerge to {output_path}")
            # mkvmerge syntax: mkvmerge -o output part1.mkv + part2.mkv + part3.mkv ...
            cmd = ["mkvmerge", "-o", str(temp_output), str(segment_paths[0])]
            for seg in segment_paths[1:]:
                cmd.extend(["+", str(seg)])

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # mkvmerge exit code 0 = success, 1 = warnings (still created valid file)
            if res.returncode in (0, 1) and temp_output.exists() and temp_output.stat().st_size > 0:
                temp_output.rename(output_path)
                logger.info(f"Successfully joined segments with mkvmerge: {output_path} ({output_path.stat().st_size} bytes)")
                return output_path
            else:
                logger.warning(f"mkvmerge exited with code {res.returncode}: {res.stderr}. Trying ffmpeg concat.")
        except Exception as e:
            logger.warning(f"mkvmerge join failed: {e}. Falling back to ffmpeg concat.")

    # Fallback to ffmpeg concat demuxer
    logger.info(f"Joining {len(segment_paths)} segments using ffmpeg concat demuxer to {output_path}")
    concat_list_file = output_path.parent / f"concat_{output_path.stem}.txt"
    try:
        with open(concat_list_file, "w") as f:
            for p in segment_paths:
                f.write(f"file '{p.resolve()}'\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c", "copy",
            str(temp_output)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg concat failed: {res.stderr}")

        temp_output.rename(output_path)
        logger.info(f"Successfully joined segments with ffmpeg: {output_path} ({output_path.stat().st_size} bytes)")
        return output_path
    finally:
        if concat_list_file.exists():
            concat_list_file.unlink(missing_ok=True)
