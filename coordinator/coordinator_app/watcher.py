import time
import threading
import logging
from pathlib import Path
from typing import Callable, Optional
from .database import Database
from .splitter import split_ts_file

logger = logging.getLogger(__name__)

class DirectoryWatcher:
    """
    Monitors INPUT_DIR for *.ts files, adds them to the database,
    and splits them into 5-minute segments.
    """

    def __init__(
        self,
        input_dir: Path,
        segments_dir: Path,
        db: Database,
        segment_duration: int = 300,
        poll_interval: float = 5.0,
        task_timeout: int = 900,
        on_work_available: Optional[Callable[[], None]] = None
    ):
        self.input_dir = Path(input_dir)
        self.segments_dir = Path(segments_dir)
        self.db = db
        self.segment_duration = segment_duration
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout
        self.on_work_available = on_work_available

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DirectoryWatcher")
        self._thread.start()
        logger.info(f"DirectoryWatcher started monitoring {self.input_dir}")

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            logger.info("DirectoryWatcher stopped.")

    def _is_file_ready(self, filepath: Path) -> bool:
        """Checks if file is done writing by checking size stability."""
        try:
            size1 = filepath.stat().st_size
            if size1 == 0:
                return False
            time.sleep(0.5)
            size2 = filepath.stat().st_size
            return size1 == size2
        except Exception:
            return False

    def _process_file(self, filepath: Path):
        filename = filepath.name
        existing = self.db.get_file_by_name(filename)
        if existing:
            # File already discovered/processed
            return

        if not self._is_file_ready(filepath):
            logger.debug(f"File {filename} is still being written or is empty. Skipping for now.")
            return

        logger.info(f"Discovered new file: {filename}")
        file_id = self.db.add_file(filename=filename, original_path=str(filepath.resolve()))

        try:
            self.db.update_file(file_id, status="splitting")
            file_seg_dir = self.segments_dir / f"file_{file_id}_{filepath.stem}"
            segments = split_ts_file(
                input_path=filepath,
                output_dir=file_seg_dir,
                segment_duration=self.segment_duration
            )

            self.db.add_segments(file_id, segments)
            logger.info(f"File {filename} (id={file_id}) split into {len(segments)} segments and queued for transcoding.")

            # Signal waiting workers that work is available!
            if self.on_work_available:
                self.on_work_available()

        except Exception as e:
            logger.error(f"Error processing {filename}: {e}", exc_info=True)
            self.db.update_file(file_id, status="failed", error_message=str(e))

    def _run_loop(self):
        last_reap_time = time.time()
        while not self._stop_event.is_set():
            try:
                # Find all *.ts files
                ts_files = sorted(list(self.input_dir.glob("*.ts")) + list(self.input_dir.glob("*.TS")))
                for ts_file in ts_files:
                    if self._stop_event.is_set():
                        break
                    self._process_file(ts_file)

                # Periodic cleanup of stale assigned tasks
                now = time.time()
                if now - last_reap_time > 30.0:
                    reaped = self.db.reap_stale_tasks(self.task_timeout)
                    if reaped > 0:
                        logger.warning(f"Reaped {reaped} stale segment tasks back to pending state.")
                        if self.on_work_available:
                            self.on_work_available()
                    last_reap_time = now

            except Exception as e:
                logger.error(f"Error in watcher loop: {e}", exc_info=True)

            self._stop_event.wait(self.poll_interval)
