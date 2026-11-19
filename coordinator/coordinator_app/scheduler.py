import logging
from typing import Optional, Dict, Any, List
from .database import Database

logger = logging.getLogger(__name__)

class SegmentScheduler:
    """
    Implements intelligent segment assignment:
    - Faster workers receive earlier segments (lower segment_index).
    - Slower workers receive later segments (higher segment_index).
    """

    def __init__(self, db: Database):
        self.db = db

    def select_segment_for_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        # Get all pending segments
        pending_segments = self.db.get_pending_segments()
        if not pending_segments:
            return None

        # Prioritize the earliest active file (FIFO file processing)
        current_file_id = pending_segments[0]["file_id"]
        file_pending = [s for s in pending_segments if s["file_id"] == current_file_id]
        if not file_pending:
            file_pending = pending_segments

        # If only one segment is available, worker gets it immediately
        if len(file_pending) == 1:
            seg = file_pending[0]
            assigned = self.db.assign_segment(seg["id"], worker_id)
            if assigned:
                logger.info(f"Assigned only pending segment {seg['id']} (index {seg['segment_index']}) to worker {worker_id}")
            return assigned

        # Multiple pending segments: sort by segment_index ASC
        file_pending.sort(key=lambda s: s["segment_index"])
        num_segments = len(file_pending)

        # Get all registered workers and sort by avg_speed_ratio DESC (fastest first)
        workers = self.db.get_all_workers()
        if not workers:
            target_idx = 0
        else:
            # Sort workers by avg_speed_ratio descending
            workers.sort(key=lambda w: w.get("avg_speed_ratio", 1.0), reverse=True)
            worker_ids = [w["worker_id"] for w in workers]

            if worker_id in worker_ids and len(workers) > 1:
                worker_rank = worker_ids.index(worker_id) # 0 = fastest, len-1 = slowest
                total_workers = len(workers)
                # Percentile p from 0.0 (fastest) to 1.0 (slowest)
                p = worker_rank / float(total_workers - 1)
                # Map p to segment index: 0 is earliest, num_segments - 1 is latest
                target_idx = int(round(p * (num_segments - 1)))
                target_idx = max(0, min(num_segments - 1, target_idx))
                logger.info(
                    f"Scheduler: worker {worker_id} ranked {worker_rank}/{total_workers - 1} "
                    f"(speed ratio {workers[worker_rank].get('avg_speed_ratio', 1.0):.2f}x). "
                    f"Assigned segment position {target_idx}/{num_segments - 1} "
                    f"(segment_index {file_pending[target_idx]['segment_index']})"
                )
            else:
                # If worker is new or only one worker, assign earliest pending segment
                target_idx = 0

        # Try assigning the chosen segment; if race condition occurred, try next available
        chosen_segment = file_pending[target_idx]
        assigned = self.db.assign_segment(chosen_segment["id"], worker_id)
        if assigned:
            return assigned

        # Fallback to any remaining pending segment in file
        for seg in file_pending:
            assigned = self.db.assign_segment(seg["id"], worker_id)
            if assigned:
                return assigned

        return None
