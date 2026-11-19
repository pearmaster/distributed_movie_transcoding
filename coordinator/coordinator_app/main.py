import os
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import (
    INPUT_DIR,
    OUTPUT_DIR,
    DATA_DIR,
    DB_PATH,
    SEGMENTS_DIR,
    SEGMENT_DURATION_SECONDS,
    WATCHER_POLL_INTERVAL,
    TASK_TIMEOUT_SECONDS,
    HOST,
    PORT,
)
from .database import Database
from .splitter import probe_duration
from .merger import join_mkv_segments
from .scheduler import SegmentScheduler
from .watcher import DirectoryWatcher

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("coordinator")

# Database & Scheduler initialization
db = Database(DB_PATH)
scheduler = SegmentScheduler(db)

# Async signal event to notify waiting workers
signal_event = asyncio.Event()

def fire_work_signal():
    """Thread-safe signal trigger to wake workers waiting for tasks."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(signal_event.set)
    except Exception:
        pass

watcher = DirectoryWatcher(
    input_dir=INPUT_DIR,
    segments_dir=SEGMENTS_DIR,
    db=db,
    segment_duration=SEGMENT_DURATION_SECONDS,
    poll_interval=WATCHER_POLL_INTERVAL,
    task_timeout=TASK_TIMEOUT_SECONDS,
    on_work_available=fire_work_signal
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: start watcher thread
    logger.info("Starting coordinator service...")
    watcher.start()
    yield
    # Shutdown
    logger.info("Shutting down coordinator service...")
    watcher.stop()

app = FastAPI(
    title="Distributed Movie Transcoding Coordinator",
    version="1.0.0",
    lifespan=lifespan
)

# Pydantic models
class RegisterWorkerRequest(BaseModel):
    worker_id: str

class HeartbeatRequest(BaseModel):
    worker_id: str

def merge_completed_file(file_id: int):
    """Background task to merge all segments of a completed file."""
    try:
        file_info = db.get_file(file_id)
        if not file_info:
            return

        filename = file_info["filename"]
        base_name = Path(filename).stem
        final_output_path = OUTPUT_DIR / f"{base_name}.mkv"

        db.update_file(file_id, status="joining")
        logger.info(f"Joining segments for file {filename} (id={file_id}) into {final_output_path}")

        segments = db.get_segments_for_file(file_id)
        segments.sort(key=lambda s: s["segment_index"])

        mkv_paths = []
        for s in segments:
            if not s["output_path"] or not Path(s["output_path"]).exists():
                raise RuntimeError(f"Missing transcoded segment {s['segment_index']} at {s['output_path']}")
            mkv_paths.append(Path(s["output_path"]))

        join_mkv_segments(mkv_paths, final_output_path)

        db.update_file(
            file_id,
            status="completed",
            output_path=str(final_output_path.resolve())
        )
        logger.info(f"File {filename} (id={file_id}) successfully transcoded and merged into {final_output_path}")

    except Exception as e:
        logger.error(f"Failed to merge segments for file {file_id}: {e}", exc_info=True)
        db.update_file(file_id, status="failed", error_message=str(e))

@app.get("/api/health")
def get_health():
    return {
        "status": "healthy",
        "input_dir": str(INPUT_DIR),
        "output_dir": str(OUTPUT_DIR),
        "data_dir": str(DATA_DIR)
    }

@app.post("/api/workers/register")
def register_worker(req: RegisterWorkerRequest):
    worker = db.register_worker(req.worker_id)
    logger.info(f"Worker registered: {req.worker_id} (speed={worker.get('avg_speed_ratio'):.2f}x)")
    return {"status": "registered", "worker": worker}

@app.post("/api/workers/heartbeat")
def heartbeat_worker(req: HeartbeatRequest):
    db.heartbeat_worker(req.worker_id)
    return {"status": "ok"}

@app.get("/api/workers")
def list_workers():
    workers = db.get_all_workers()
    return {"workers": workers}

@app.post("/api/signal")
def trigger_signal():
    """Manual signal endpoint to wake workers or signal work availability."""
    fire_work_signal()
    return {"status": "signaled"}

@app.get("/api/tasks/next")
async def get_next_task(
    worker_id: str = Query(..., description="Worker Identifier"),
    wait: bool = Query(True, description="Wait/long-poll if no task is immediately ready"),
    timeout: float = Query(30.0, description="Max seconds to wait for a signal")
):
    """
    Assigns the next segment to the worker based on worker stats ranking:
    - Faster workers receive earlier segments.
    - Slower workers receive later segments.
    If no segment is ready and wait=True, waits for the work signal.
    """
    db.heartbeat_worker(worker_id)

    # First attempt
    segment = scheduler.select_segment_for_worker(worker_id)
    if segment:
        return _format_task_response(segment)

    if not wait or timeout <= 0:
        return {"status": "no_work"}

    # Wait on signal event
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        remaining = timeout - (asyncio.get_event_loop().time() - start_time)
        if remaining <= 0:
            break
        try:
            signal_event.clear()
            await asyncio.wait_for(signal_event.wait(), timeout=min(remaining, 5.0))
        except asyncio.TimeoutError:
            pass

        segment = scheduler.select_segment_for_worker(worker_id)
        if segment:
            return _format_task_response(segment)

    return {"status": "no_work"}

def _format_task_response(segment: Dict[str, Any]) -> Dict[str, Any]:
    file_id = segment["file_id"]
    seg_id = segment["id"]
    return {
        "status": "task_assigned",
        "task_id": seg_id,
        "segment_id": seg_id,
        "file_id": file_id,
        "segment_index": segment["segment_index"],
        "duration_seconds": segment["duration_seconds"],
        "segment_filename": segment["segment_filename"],
        "download_url": f"/api/segments/{file_id}/{seg_id}/download"
    }

@app.get("/api/segments/{file_id}/{segment_id}/download")
def download_segment(file_id: int, segment_id: int):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM segments WHERE id = ? AND file_id = ?", (segment_id, file_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Segment not found")
        path = Path(row["segment_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="Segment file missing on disk")
        return FileResponse(
            path=str(path),
            filename=row["segment_filename"],
            media_type="video/MP2T"
        )

@app.post("/api/segments/{file_id}/{segment_id}/upload")
async def upload_transcoded_segment(
    file_id: int,
    segment_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    worker_id: str = Form(...),
    duration_seconds: float = Form(...),
    transcode_time_seconds: float = Form(...)
):
    """
    Receives converted H.264 MKV segment and transcoding report.
    Updates worker stats and triggers final merge if all segments are done.
    """
    with db.get_connection() as conn:
        seg = conn.execute("SELECT * FROM segments WHERE id = ? AND file_id = ?", (segment_id, file_id)).fetchone()
        if not seg:
            raise HTTPException(status_code=404, detail="Segment not found")

    # Destination for converted MKV segment
    seg_dir = Path(seg["segment_path"]).parent
    output_filename = f"segment_{seg['segment_index']:05d}.mkv"
    output_path = seg_dir / output_filename

    # Save uploaded file
    with open(output_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

    logger.info(
        f"Received transcoded segment {segment_id} (index {seg['segment_index']}) from worker {worker_id}: "
        f"duration={duration_seconds:.2f}s, transcode_time={transcode_time_seconds:.2f}s, "
        f"speed={duration_seconds / max(transcode_time_seconds, 0.001):.2f}x"
    )

    is_file_done, file_data = db.complete_segment(
        segment_id=segment_id,
        worker_id=worker_id,
        duration_seconds=duration_seconds,
        transcode_time_seconds=transcode_time_seconds,
        output_filename=output_filename,
        output_path=str(output_path.resolve())
    )

    if is_file_done:
        logger.info(f"All segments for file {file_id} completed! Queuing join task.")
        background_tasks.add_task(merge_completed_file, file_id)

    # Wake any waiting workers to pick up other pending segments
    fire_work_signal()

    return {
        "status": "success",
        "segment_id": segment_id,
        "is_file_done": is_file_done
    }

@app.get("/api/files")
def list_files():
    files = db.get_all_files()
    return {"files": files}

@app.get("/api/files/{file_id}")
def get_file_detail(file_id: int):
    f = db.get_file(file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    segments = db.get_segments_for_file(file_id)
    return {"file": f, "segments": segments}

@app.get("/api/reports")
def list_reports(limit: int = Query(100, ge=1, le=1000)):
    reports = db.get_reports(limit)
    return {"reports": reports}

@app.get("/api/status")
def get_system_status():
    files = db.get_all_files()
    workers = db.get_all_workers()
    pending = db.get_pending_segments()
    reports = db.get_reports(10)
    return {
        "files_count": len(files),
        "pending_segments_count": len(pending),
        "workers_count": len(workers),
        "workers": workers,
        "active_files": [f for f in files if f["status"] not in ("completed", "failed")],
        "recent_reports": reports
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("coordinator_app.main:app", host=HOST, port=PORT, reload=False)
