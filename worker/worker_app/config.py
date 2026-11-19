import os
import socket
import uuid
from pathlib import Path

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000").rstrip("/")

# Worker identifier
default_id = f"worker-{socket.gethostname()[:12]}-{uuid.uuid4().hex[:4]}"
WORKER_ID = os.getenv("WORKER_ID", default_id)

WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/transcode_worker")).resolve()
WORK_DIR.mkdir(parents=True, exist_ok=True)

HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "10.0"))
POLL_TIMEOUT = float(os.getenv("POLL_TIMEOUT", "30.0"))

# HandBrake settings
HANDBRAKE_PRESET = os.getenv("HANDBRAKE_PRESET", "")
HANDBRAKE_ENCODER = os.getenv("HANDBRAKE_ENCODER", "x264")
HANDBRAKE_QUALITY = os.getenv("HANDBRAKE_QUALITY", "22")
