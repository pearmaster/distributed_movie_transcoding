import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

INPUT_DIR = Path(os.getenv("INPUT_DIR", "/input")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/output")).resolve()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data")).resolve()

DB_PATH = DATA_DIR / "transcode.db"
SEGMENTS_DIR = DATA_DIR / "segments"

SEGMENT_DURATION_SECONDS = int(os.getenv("SEGMENT_DURATION_SECONDS", "300"))
WATCHER_POLL_INTERVAL = float(os.getenv("WATCHER_POLL_INTERVAL", "5.0"))
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "900"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Ensure directories exist
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
