#!/usr/bin/env python3
"""
End-to-End Integration Test for Distributed Video Transcoding System.
"""

import time
import subprocess
import sys
import shutil
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
COORDINATOR_URL = "http://localhost:8000"

def log(msg):
    print(f"[TEST] {msg}", flush=True)

def wait_for_coordinator(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COORDINATOR_URL}/api/health", timeout=2)
            if r.status_code == 200:
                log("Coordinator is healthy and responding.")
                return True
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError("Coordinator did not become healthy in time.")

def wait_for_workers(min_workers=1, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COORDINATOR_URL}/api/workers", timeout=2)
            if r.status_code == 200:
                workers = r.json().get("workers", [])
                if len(workers) >= min_workers:
                    log(f"Found {len(workers)} registered workers: {[w['worker_id'] for w in workers]}")
                    return workers
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Did not find {min_workers} workers in time.")

def run_e2e_test():
    log("=== Starting End-to-End Distributed Transcoding Test ===")

    # 1. Check Coordinator Health
    wait_for_coordinator()

    # 2. Check Workers Registration
    wait_for_workers(min_workers=2)

    # 3. Generate a sample TS file directly in input directory
    test_video_duration = 15  # 15 seconds
    test_filename = f"e2e_movie_{int(time.time())}.ts"
    input_file = INPUT_DIR / test_filename
    output_expected_file = OUTPUT_DIR / f"{input_file.stem}.mkv"

    log(f"Generating synthetic MPEG-2 TS test video: {input_file} (duration: {test_video_duration}s)...")
    gen_cmd = [
        "python3",
        str(BASE_DIR / "scripts" / "generate_sample_video.py"),
        "-o", str(input_file),
        "-d", str(test_video_duration)
    ]
    subprocess.run(gen_cmd, check=True)
    log(f"Sample TS created: {input_file} ({input_file.stat().st_size} bytes)")

    # 4. Wait for coordinator to detect and complete the transcoding
    log("Waiting for coordinator and workers to transcode and merge the file...")
    start_time = time.time()
    timeout = 180  # 3 minutes

    file_record = None
    while time.time() - start_time < timeout:
        try:
            r = requests.get(f"{COORDINATOR_URL}/api/files", timeout=5)
            if r.status_code == 200:
                files = r.json().get("files", [])
                for f in files:
                    if f["filename"] == test_filename:
                        file_record = f
                        log(f"File status: {f['status']} | Segments completed: {f['completed_segments']}/{f['total_segments']}")
                        break
            if file_record and file_record["status"] == "completed":
                break
        except Exception as e:
            log(f"Polling error: {e}")
        time.sleep(3)

    if not file_record or file_record["status"] != "completed":
        # Print status summary
        r = requests.get(f"{COORDINATOR_URL}/api/status")
        log(f"Coordinator status dump: {r.text}")
        raise RuntimeError(f"Transcoding did not complete within {timeout}s. Current state: {file_record}")

    log(f"File marked completed by coordinator! Total segments: {file_record['total_segments']}")

    # 5. Verify the merged MKV file in output directory
    log(f"Verifying final merged MKV file at {output_expected_file}...")
    if not output_expected_file.exists():
        raise FileNotFoundError(f"Expected output file not found: {output_expected_file}")

    file_size = output_expected_file.stat().st_size
    log(f"Output MKV file exists! Size: {file_size} bytes")
    if file_size < 1000:
        raise ValueError(f"Output file size is suspiciously small: {file_size} bytes")

    # 6. Verify with ffprobe (container, codec, duration)
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=format_name,duration:stream=codec_name",
        "-of", "default=noprint_wrappers=1",
        str(output_expected_file)
    ]
    probe_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    probe_output = probe_res.stdout
    log(f"ffprobe details:\n{probe_output}")

    assert "matroska" in probe_output, "Container should be matroska"
    assert "h264" in probe_output, "Video stream should be H.264"

    # 7. Check reports and worker stats
    r_reports = requests.get(f"{COORDINATOR_URL}/api/reports")
    reports = r_reports.json().get("reports", [])
    log(f"Recorded transcode reports: {len(reports)}")
    for rep in reports:
        log(
            f"  Report: worker={rep['worker_id']}, seg={rep['segment_index']}, "
            f"video_duration={rep['video_duration_seconds']:.2f}s, "
            f"transcode_time={rep['transcode_time_seconds']:.2f}s, "
            f"speed={rep['speed_ratio']:.2f}x"
        )

    r_workers = requests.get(f"{COORDINATOR_URL}/api/workers")
    workers = r_workers.json().get("workers", [])
    log("Worker cumulative statistics:")
    for w in workers:
        log(
            f"  Worker {w['worker_id']}: jobs={w['total_jobs_completed']}, "
            f"video_sec={w['total_video_seconds']:.2f}s, "
            f"transcode_sec={w['total_transcode_seconds']:.2f}s, "
            f"avg_speed={w['avg_speed_ratio']:.2f}x"
        )

    log("=== E2E TEST PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_e2e_test()
