import os
import sys
import time
import signal
import threading
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import requests

from .config import (
    COORDINATOR_URL,
    WORKER_ID,
    WORK_DIR,
    HEARTBEAT_INTERVAL,
    POLL_TIMEOUT
)
from .transcoder import transcode_to_h264_mkv

logging.basicConfig(
    level=logging.INFO,
    format=f"[%(asctime)s] [{WORKER_ID}] [%(levelname)s]: %(message)s"
)
logger = logging.getLogger("worker")

class TranscodeWorker:
    def __init__(self):
        self.coordinator_url = COORDINATOR_URL
        self.worker_id = WORKER_ID
        self.work_dir = WORK_DIR
        self.running = False
        self._heartbeat_thread: Optional[threading.Thread] = None

    def start(self):
        self.running = True
        logger.info(f"Worker {self.worker_id} starting. Connecting to coordinator at {self.coordinator_url}")

        self._wait_for_coordinator()
        self._register()
        self._start_heartbeat()
        self._run_loop()

    def stop(self):
        logger.info("Stopping worker...")
        self.running = False

    def _wait_for_coordinator(self):
        """Waits for coordinator to become reachable."""
        health_url = f"{self.coordinator_url}/api/health"
        while self.running:
            try:
                res = requests.get(health_url, timeout=5.0)
                if res.status_code == 200:
                    logger.info("Successfully connected to coordinator.")
                    return
            except Exception as e:
                logger.warning(f"Waiting for coordinator at {health_url}... ({e})")
            time.sleep(3)

    def _register(self):
        """Registers this worker with the coordinator."""
        reg_url = f"{self.coordinator_url}/api/workers/register"
        while self.running:
            try:
                res = requests.post(reg_url, json={"worker_id": self.worker_id}, timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    worker_info = data.get("worker", {})
                    logger.info(
                        f"Registered with coordinator. Current stats: "
                        f"speed={worker_info.get('avg_speed_ratio', 1.0):.2f}x, "
                        f"completed_jobs={worker_info.get('total_jobs_completed', 0)}"
                    )
                    return
            except Exception as e:
                logger.warning(f"Registration attempt failed: {e}. Retrying...")
            time.sleep(3)

    def _start_heartbeat(self):
        def _hb_loop():
            hb_url = f"{self.coordinator_url}/api/workers/heartbeat"
            while self.running:
                try:
                    requests.post(hb_url, json={"worker_id": self.worker_id}, timeout=5.0)
                except Exception:
                    pass
                time.sleep(HEARTBEAT_INTERVAL)

        self._heartbeat_thread = threading.Thread(target=_hb_loop, daemon=True, name="HeartbeatThread")
        self._heartbeat_thread.start()

    def _download_segment(self, download_url: str, dest_path: Path):
        url = f"{self.coordinator_url}{download_url}"
        logger.info(f"Downloading segment from {url} to {dest_path}")
        with requests.get(url, stream=True, timeout=60.0) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        logger.info(f"Downloaded segment ({dest_path.stat().st_size} bytes)")

    def _upload_completed_segment(
        self,
        file_id: int,
        segment_id: int,
        mkv_path: Path,
        duration_seconds: float,
        transcode_time_seconds: float
    ):
        upload_url = f"{self.coordinator_url}/api/segments/{file_id}/{segment_id}/upload"
        logger.info(
            f"Uploading transcoded MKV segment to {upload_url} "
            f"(duration={duration_seconds:.2f}s, transcode_time={transcode_time_seconds:.2f}s)"
        )

        with open(mkv_path, "rb") as f:
            files = {"file": (mkv_path.name, f, "video/x-matroska")}
            data = {
                "worker_id": self.worker_id,
                "duration_seconds": str(duration_seconds),
                "transcode_time_seconds": str(transcode_time_seconds)
            }
            res = requests.post(upload_url, files=files, data=data, timeout=120.0)
            res.raise_for_status()

        logger.info(f"Successfully uploaded segment {segment_id} and transcoding report.")

    def _process_task(self, task: Dict[str, Any]):
        segment_id = task["segment_id"]
        file_id = task["file_id"]
        segment_index = task["segment_index"]
        download_url = task["download_url"]
        expected_duration = task.get("duration_seconds", 0.0)

        logger.info(f"=== Starting Work on Segment {segment_index} (ID: {segment_id}, File: {file_id}) ===")

        ts_path = self.work_dir / f"task_{file_id}_{segment_id}.ts"
        mkv_path = self.work_dir / f"task_{file_id}_{segment_id}.mkv"

        try:
            # 1. Download segment
            self._download_segment(download_url, ts_path)

            # 2. Transcode with HandBrakeCLI
            video_dur, transcode_time = transcode_to_h264_mkv(
                input_file=ts_path,
                output_file=mkv_path,
                fallback_duration=expected_duration
            )

            # 3. Upload result and transcoding report
            self._upload_completed_segment(
                file_id=file_id,
                segment_id=segment_id,
                mkv_path=mkv_path,
                duration_seconds=video_dur,
                transcode_time_seconds=transcode_time
            )

            logger.info(f"=== Completed Segment {segment_index} (ID: {segment_id}) ===")

        finally:
            # Clean up local temporary files
            if ts_path.exists():
                ts_path.unlink(missing_ok=True)
            if mkv_path.exists():
                mkv_path.unlink(missing_ok=True)

    def _run_loop(self):
        next_task_url = f"{self.coordinator_url}/api/tasks/next"

        while self.running:
            try:
                # Long poll / wait for signal for next task
                params = {
                    "worker_id": self.worker_id,
                    "wait": "true",
                    "timeout": str(POLL_TIMEOUT)
                }
                res = requests.get(next_task_url, params=params, timeout=POLL_TIMEOUT + 10.0)
                if res.status_code != 200:
                    logger.warning(f"Next task request returned status {res.status_code}")
                    time.sleep(2)
                    continue

                data = res.json()
                if data.get("status") == "task_assigned":
                    self._process_task(data)
                else:
                    # No work available, loop back and wait for signal
                    time.sleep(1)

            except requests.exceptions.RequestException as e:
                logger.warning(f"Connection issue with coordinator: {e}. Retrying in 5s...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Error during task processing: {e}", exc_info=True)
                time.sleep(3)

def main():
    worker = TranscodeWorker()

    def _signal_handler(sig, frame):
        logger.info("Received termination signal.")
        worker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    worker.start()

if __name__ == "__main__":
    main()
