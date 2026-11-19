import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        with self._lock, self.get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                original_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'discovered', -- discovered, splitting, transcoding, joining, completed, failed
                total_segments INTEGER DEFAULT 0,
                completed_segments INTEGER DEFAULT 0,
                output_path TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'idle', -- idle, busy, offline
                total_jobs_completed INTEGER DEFAULT 0,
                total_video_seconds REAL DEFAULT 0.0,
                total_transcode_seconds REAL DEFAULT 0.0,
                avg_speed_ratio REAL DEFAULT 1.0 -- video_duration / transcode_time (higher = faster)
            );

            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                segment_index INTEGER NOT NULL,
                segment_filename TEXT NOT NULL,
                segment_path TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', -- pending, assigned, completed, failed
                assigned_worker_id TEXT REFERENCES workers(worker_id),
                assigned_at TIMESTAMP,
                completed_at TIMESTAMP,
                transcode_time_seconds REAL,
                output_filename TEXT,
                output_path TEXT,
                UNIQUE(file_id, segment_index)
            );

            CREATE TABLE IF NOT EXISTS transcode_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT NOT NULL REFERENCES workers(worker_id),
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
                segment_index INTEGER NOT NULL,
                video_duration_seconds REAL NOT NULL,
                transcode_time_seconds REAL NOT NULL,
                speed_ratio REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_segments_status ON segments(status);
            CREATE INDEX IF NOT EXISTS idx_segments_file_status ON segments(file_id, status);
            CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
            """)

    def register_worker(self, worker_id: str) -> Dict[str, Any]:
        with self._lock, self.get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
            INSERT INTO workers (worker_id, first_seen, last_seen, status, avg_speed_ratio)
            VALUES (?, ?, ?, 'idle', 1.0)
            ON CONFLICT(worker_id) DO UPDATE SET
                last_seen = ?,
                status = CASE WHEN status = 'offline' THEN 'idle' ELSE status END
            """, (worker_id, now, now, now))
            row = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
            return dict(row)

    def heartbeat_worker(self, worker_id: str):
        with self._lock, self.get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
            UPDATE workers SET last_seen = ? WHERE worker_id = ?
            """, (now, worker_id))

    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
            return dict(row) if row else None

    def get_all_workers(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY avg_speed_ratio DESC").fetchall()
            return [dict(r) for r in rows]

    def add_file(self, filename: str, original_path: str) -> int:
        with self._lock, self.get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute("""
            INSERT INTO files (filename, original_path, status, created_at, updated_at)
            VALUES (?, ?, 'discovered', ?, ?)
            ON CONFLICT(filename) DO UPDATE SET
                updated_at = excluded.updated_at
            RETURNING id
            """, (filename, original_path, now, now))
            file_id = cursor.fetchone()[0]
            return file_id

    def update_file(self, file_id: int, **kwargs):
        allowed = {"status", "total_segments", "completed_segments", "output_path", "error_message"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [file_id]
        with self._lock, self.get_connection() as conn:
            conn.execute(f"UPDATE files SET {set_clause} WHERE id = ?", values)

    def get_file(self, file_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            return dict(row) if row else None

    def get_file_by_name(self, filename: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM files WHERE filename = ?", (filename,)).fetchone()
            return dict(row) if row else None

    def get_all_files(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM files ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def add_segments(self, file_id: int, segments: List[Dict[str, Any]]):
        with self._lock, self.get_connection() as conn:
            for seg in segments:
                conn.execute("""
                INSERT INTO segments (file_id, segment_index, segment_filename, segment_path, duration_seconds, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(file_id, segment_index) DO UPDATE SET
                    segment_filename = excluded.segment_filename,
                    segment_path = excluded.segment_path,
                    duration_seconds = excluded.duration_seconds,
                    status = 'pending'
                """, (file_id, seg["index"], seg["filename"], seg["path"], seg["duration"]))
            conn.execute("""
            UPDATE files SET
                total_segments = (SELECT COUNT(*) FROM segments WHERE file_id = ?),
                status = 'transcoding',
                updated_at = ?
            WHERE id = ?
            """, (file_id, datetime.now(timezone.utc).isoformat(), file_id))

    def get_pending_segments(self, file_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if file_id is not None:
                rows = conn.execute("""
                SELECT s.*, f.filename as source_filename
                FROM segments s
                JOIN files f ON s.file_id = f.id
                WHERE s.file_id = ? AND s.status = 'pending'
                ORDER BY s.segment_index ASC
                """, (file_id,)).fetchall()
            else:
                rows = conn.execute("""
                SELECT s.*, f.filename as source_filename
                FROM segments s
                JOIN files f ON s.file_id = f.id
                WHERE s.status = 'pending'
                ORDER BY s.file_id ASC, s.segment_index ASC
                """).fetchall()
            return [dict(r) for r in rows]

    def assign_segment(self, segment_id: int, worker_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self.get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute("""
            UPDATE segments
            SET status = 'assigned', assigned_worker_id = ?, assigned_at = ?
            WHERE id = ? AND status = 'pending'
            """, (worker_id, now, segment_id))
            if cursor.rowcount == 0:
                return None
            conn.execute("""
            UPDATE workers SET status = 'busy', last_seen = ? WHERE worker_id = ?
            """, (now, worker_id))
            row = conn.execute("""
            SELECT s.*, f.filename as source_filename
            FROM segments s
            JOIN files f ON s.file_id = f.id
            WHERE s.id = ?
            """, (segment_id,)).fetchone()
            return dict(row) if row else None

    def complete_segment(
        self,
        segment_id: int,
        worker_id: str,
        duration_seconds: float,
        transcode_time_seconds: float,
        output_filename: str,
        output_path: str
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Marks segment completed, records transcoding report, updates worker speed stats,
        and returns (is_file_fully_completed, file_record).
        """
        with self._lock, self.get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            seg = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
            if not seg:
                raise ValueError(f"Segment {segment_id} not found")

            file_id = seg["file_id"]
            segment_index = seg["segment_index"]

            # Safe guard against zero division
            safe_transcode_time = max(transcode_time_seconds, 0.001)
            speed_ratio = duration_seconds / safe_transcode_time

            # Update segment
            conn.execute("""
            UPDATE segments
            SET status = 'completed',
                completed_at = ?,
                transcode_time_seconds = ?,
                output_filename = ?,
                output_path = ?
            WHERE id = ?
            """, (now, transcode_time_seconds, output_filename, output_path, segment_id))

            # Record transcode report
            conn.execute("""
            INSERT INTO transcode_reports
                (worker_id, file_id, segment_id, segment_index, video_duration_seconds, transcode_time_seconds, speed_ratio, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (worker_id, file_id, segment_id, segment_index, duration_seconds, transcode_time_seconds, speed_ratio, now))

            # Update worker cumulative stats and average speed ratio
            conn.execute("""
            UPDATE workers
            SET total_jobs_completed = total_jobs_completed + 1,
                total_video_seconds = total_video_seconds + ?,
                total_transcode_seconds = total_transcode_seconds + ?,
                avg_speed_ratio = (total_video_seconds + ?) / MAX(total_transcode_seconds + ?, 0.001),
                status = 'idle',
                last_seen = ?
            WHERE worker_id = ?
            """, (duration_seconds, transcode_time_seconds, duration_seconds, transcode_time_seconds, now, worker_id))

            # Check file progress
            completed_count = conn.execute(
                "SELECT COUNT(*) FROM segments WHERE file_id = ? AND status = 'completed'",
                (file_id,)
            ).fetchone()[0]

            total_count = conn.execute(
                "SELECT total_segments FROM files WHERE id = ?",
                (file_id,)
            ).fetchone()[0]

            conn.execute("""
            UPDATE files
            SET completed_segments = ?, updated_at = ?
            WHERE id = ?
            """, (completed_count, now, file_id))

            file_row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            file_data = dict(file_row) if file_row else None
            is_done = (completed_count >= total_count and total_count > 0)
            return is_done, file_data

    def fail_segment(self, segment_id: int, error_message: str):
        with self._lock, self.get_connection() as conn:
            conn.execute("""
            UPDATE segments
            SET status = 'pending', assigned_worker_id = NULL, assigned_at = NULL
            WHERE id = ?
            """, (segment_id,))

    def get_segments_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("""
            SELECT * FROM segments
            WHERE file_id = ?
            ORDER BY segment_index ASC
            """, (file_id,)).fetchall()
            return [dict(r) for r in rows]

    def reap_stale_tasks(self, timeout_seconds: int = 900) -> int:
        with self._lock, self.get_connection() as conn:
            now_dt = datetime.now(timezone.utc)
            rows = conn.execute("""
            SELECT id, assigned_at FROM segments
            WHERE status = 'assigned' AND assigned_at IS NOT NULL
            """).fetchall()
            reaped = 0
            for r in rows:
                try:
                    assigned_dt = datetime.fromisoformat(r["assigned_at"])
                    if assigned_dt.tzinfo is None:
                        assigned_dt = assigned_dt.replace(tzinfo=timezone.utc)
                    elapsed = (now_dt - assigned_dt).total_seconds()
                    if elapsed > timeout_seconds:
                        conn.execute("""
                        UPDATE segments
                        SET status = 'pending', assigned_worker_id = NULL, assigned_at = NULL
                        WHERE id = ?
                        """, (r["id"],))
                        reaped += 1
                except Exception:
                    pass
            return reaped

    def get_reports(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("""
            SELECT r.*, f.filename
            FROM transcode_reports r
            JOIN files f ON r.file_id = f.id
            ORDER BY r.id DESC
            LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
