import unittest
import tempfile
import shutil
from pathlib import Path

# Add coordinator to sys.path
import sys
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "coordinator"))

from coordinator_app.database import Database
from coordinator_app.scheduler import SegmentScheduler

class TestDatabaseAndScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = Database(self.db_path)
        self.scheduler = SegmentScheduler(self.db)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_worker_registration_and_stats(self):
        # Register workers
        w_fast = self.db.register_worker("worker-fast")
        w_slow = self.db.register_worker("worker-slow")
        self.assertEqual(w_fast["worker_id"], "worker-fast")
        self.assertEqual(w_slow["worker_id"], "worker-slow")

        # Add file and segment
        file_id = self.db.add_file("test.ts", "/input/test.ts")
        self.db.add_segments(file_id, [
            {"index": 0, "filename": "segment_00000.ts", "path": "/data/segment_00000.ts", "duration": 300.0},
            {"index": 1, "filename": "segment_00001.ts", "path": "/data/segment_00001.ts", "duration": 300.0}
        ])

        # Complete segment 0 with fast worker: 300s video encoded in 100s -> speed 3.0x
        self.db.assign_segment(1, "worker-fast")
        is_done, _ = self.db.complete_segment(
            segment_id=1,
            worker_id="worker-fast",
            duration_seconds=300.0,
            transcode_time_seconds=100.0,
            output_filename="segment_00000.mkv",
            output_path="/data/segment_00000.mkv"
        )
        self.assertFalse(is_done)

        # Complete segment 1 with slow worker: 300s video encoded in 600s -> speed 0.5x
        self.db.assign_segment(2, "worker-slow")
        is_done, _ = self.db.complete_segment(
            segment_id=2,
            worker_id="worker-slow",
            duration_seconds=300.0,
            transcode_time_seconds=600.0,
            output_filename="segment_00001.mkv",
            output_path="/data/segment_00001.mkv"
        )
        self.assertTrue(is_done)

        # Check worker stats
        w_fast_updated = self.db.get_worker("worker-fast")
        w_slow_updated = self.db.get_worker("worker-slow")
        self.assertAlmostEqual(w_fast_updated["avg_speed_ratio"], 3.0, places=2)
        self.assertAlmostEqual(w_slow_updated["avg_speed_ratio"], 0.5, places=2)

    def test_scheduler_speed_ranking_assignment(self):
        """
        Verify:
        "Workers stats are kept in the database so that the coordinator can send
        the earlier segments to the faster workers and the later segments to the slower workers."
        """
        # Register 3 workers with known different speeds
        self.db.register_worker("w-fast")
        self.db.register_worker("w-medium")
        self.db.register_worker("w-slow")

        # Manually set speeds: fast (3.0), medium (1.5), slow (0.5)
        with self.db.get_connection() as conn:
            conn.execute("UPDATE workers SET avg_speed_ratio = 3.0, total_jobs_completed = 5 WHERE worker_id = 'w-fast'")
            conn.execute("UPDATE workers SET avg_speed_ratio = 1.5, total_jobs_completed = 5 WHERE worker_id = 'w-medium'")
            conn.execute("UPDATE workers SET avg_speed_ratio = 0.5, total_jobs_completed = 5 WHERE worker_id = 'w-slow'")

        # Create a file with 6 pending segments (indices 0 through 5)
        file_id = self.db.add_file("epic_movie.ts", "/input/epic_movie.ts")
        segments = [
            {"index": i, "filename": f"segment_{i:05d}.ts", "path": f"/data/segment_{i:05d}.ts", "duration": 300.0}
            for i in range(6)
        ]
        self.db.add_segments(file_id, segments)

        # When w-fast asks for a segment, it must get the EARLIEST available segment (index 0)
        fast_seg = self.scheduler.select_segment_for_worker("w-fast")
        self.assertIsNotNone(fast_seg)
        self.assertEqual(fast_seg["segment_index"], 0, f"Expected fast worker to get index 0, got {fast_seg['segment_index']}")

        # When w-slow asks for a segment, it must get the LATEST available segment (index 5)
        slow_seg = self.scheduler.select_segment_for_worker("w-slow")
        self.assertIsNotNone(slow_seg)
        self.assertEqual(slow_seg["segment_index"], 5, f"Expected slow worker to get index 5, got {slow_seg['segment_index']}")

        # When w-medium asks for a segment, it should get a middle segment (e.g. index 2 or 3)
        med_seg = self.scheduler.select_segment_for_worker("w-medium")
        self.assertIsNotNone(med_seg)
        self.assertIn(med_seg["segment_index"], [2, 3], f"Expected medium worker to get middle segment, got {med_seg['segment_index']}")

if __name__ == "__main__":
    unittest.main()
