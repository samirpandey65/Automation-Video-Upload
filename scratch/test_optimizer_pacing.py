import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.optimizer import (
    get_optimizer_pacing_gap, get_optimizer_batch_limit,
    get_channel_cooldown_hours, get_channel_optimization_cooldown_status,
    record_channel_optimization, get_optimizer_operating_window,
    run_channel_performance_audit, bulk_optimize_old_videos
)


class TestOptimizerPacingAndCooldown(unittest.TestCase):
    def setUp(self):
        # In-memory settings dictionary: completely isolated from real uploader.db
        self.mock_settings = {
            "enable_auto_optimizer": "0",
            "scheduler_paused": "1",
            "optimizer_gap_seconds": "1.5",
            "optimizer_batch_limit": "1",
            "optimizer_channel_cooldown_hours": "2.0"
        }

    def mock_get_setting(self, key):
        return self.mock_settings.get(key)

    def mock_set_setting(self, key, val):
        self.mock_settings[key] = str(val)

    def test_01_defaults_enforce_1_video_and_2hr_cooldown(self):
        with patch("app.optimizer.get_app_setting", side_effect=self.mock_get_setting):
            self.assertEqual(get_optimizer_batch_limit(), 1, "Must default to strictly 1 video per run")
            self.assertAlmostEqual(get_channel_cooldown_hours(), 2.0, places=1, msg="Must default to 2.0 hours cooldown")
            win = get_optimizer_operating_window()
            self.assertEqual(win["batch_limit"], 1)
            self.assertEqual(win["channel_cooldown_hours"], 2.0)
            self.assertFalse(win["auto_optimizer_enabled"])

    def test_02_channel_cooldown_blocks_immediate_repeat(self):
        with patch("app.optimizer.get_app_setting", side_effect=self.mock_get_setting), \
             patch("app.optimizer.set_app_setting", side_effect=self.mock_set_setting):
            channel_id = "UC_TEST_123"

            # Initially not in cooldown
            status = get_channel_optimization_cooldown_status(channel_id)
            self.assertFalse(status["in_cooldown"])

            # Record 1 optimization
            record_channel_optimization(channel_id)

            # Check cooldown immediately active
            status = get_channel_optimization_cooldown_status(channel_id)
            self.assertTrue(status["in_cooldown"], "Channel must be in cooldown immediately after optimization")
            self.assertGreater(status["remaining_seconds"], 7100, "Should have nearly 7200s (2 hrs) remaining")

    def test_03_run_channel_audit_enforces_1_video_then_cooldown(self):
        fake_optimize_res = {
            "status": "optimized",
            "old_title": "Old Title",
            "new_title": "New Hook Title",
            "cycle": 1
        }

        mock_rows = [
            {"id": 1, "filename": "v1.mp4", "title": "Old 1", "youtube_video_id": "yt1", "channel_id": "UC_TEST_123"},
            {"id": 2, "filename": "v2.mp4", "title": "Old 2", "youtube_video_id": "yt2", "channel_id": "UC_TEST_123"},
            {"id": 3, "filename": "v3.mp4", "title": "Old 3", "youtube_video_id": "yt3", "channel_id": "UC_TEST_123"},
        ]

        with patch("app.optimizer.get_app_setting", side_effect=self.mock_get_setting), \
             patch("app.optimizer.set_app_setting", side_effect=self.mock_set_setting), \
             patch("app.optimizer.is_optimizer_time_window_active", return_value=(True, "Daytime active")), \
             patch("app.optimizer.audit_and_optimize_video", return_value=fake_optimize_res), \
             patch("app.optimizer.get_conn") as mock_conn:

            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = mock_rows
            mock_conn.return_value.execute.return_value = mock_cursor

            # First run: updates exactly 1 video
            res1 = run_channel_performance_audit("UC_TEST_123", force=False, allow_night=True)
            self.assertEqual(len(res1), 1, "Must update only 1 video per channel")
            self.assertEqual(res1[0]["status"], "optimized")

            # Second immediate run: blocked by 2-hour cooldown
            res2 = run_channel_performance_audit("UC_TEST_123", force=False, allow_night=True)
            self.assertEqual(len(res2), 1)
            self.assertEqual(res2[0]["status"], "cooldown_active")
            self.assertIn("remaining_minutes", res2[0])

    def test_04_bulk_optimize_skips_same_channel(self):
        fake_audit = {
            "total_scanned": 3,
            "total_flagged": 3,
            "total_clean": 0,
            "flagged_videos": [
                {"video_id": 101, "youtube_video_id": "yt101", "channel_id": "CH_A", "current_title": "Old A1", "proposed_title": "Clean A1"},
                {"video_id": 102, "youtube_video_id": "yt102", "channel_id": "CH_A", "current_title": "Old A2", "proposed_title": "Clean A2"},
                {"video_id": 201, "youtube_video_id": "yt201", "channel_id": "CH_B", "current_title": "Old B1", "proposed_title": "Clean B1"}
            ],
            "operating_window": {}
        }

        with patch("app.optimizer.get_app_setting", side_effect=self.mock_get_setting), \
             patch("app.optimizer.set_app_setting", side_effect=self.mock_set_setting), \
             patch("app.optimizer.audit_old_videos", return_value=fake_audit), \
             patch("app.optimizer.is_optimizer_time_window_active", return_value=(True, "Daytime")), \
             patch("app.optimizer.update_video_metadata", return_value=True), \
             patch("app.optimizer.update_video", return_value=True), \
             patch("time.sleep", return_value=None):

            res = bulk_optimize_old_videos(auto_apply_youtube=True, allow_night=True)

            # CH_A had 2 videos flagged, but ONLY 1 can be updated (then 2-hr cooldown).
            # CH_B had 1 video flagged, and 1 is updated. Total = 2.
            self.assertEqual(res["total_updated"], 2, "Only 1 video per channel can be updated per run")

    def test_05_environment_dev_blocks_automated_audits(self):
        from app.optimizer import run_global_performance_audit
        with patch("app.config.get_app_env", return_value="development"), \
             patch("app.config.is_dev_env", return_value=True):
            res = run_global_performance_audit()
            self.assertEqual(res["status"], "dev_env_disabled")
            self.assertEqual(res["total_optimized"], 0)

    def test_06_single_schedule_test_execution(self):
        from app.scheduler import run_schedule_for_test
        mock_schedule = {
            "id": 10,
            "channel_id": "UC_TEST_CH",
            "weekday": 0,
            "time_local": "18:00"
        }
        with patch("app.scheduler.get_schedule", return_value=mock_schedule), \
             patch("app.scheduler._run_schedule", return_value="nothing_to_upload"), \
             patch("app.scheduler.mark_schedule_run") as mock_mark, \
             patch("app.scheduler.record_scheduler_run") as mock_rec, \
             patch("app.config.is_dev_env", return_value=True), \
             patch("app.config.get_app_env", return_value="development"):
            res = run_schedule_for_test(10)
            self.assertTrue(res["ok"])
            self.assertEqual(res["result"], "nothing_to_upload")
            self.assertTrue(res["one_shot"])
            mock_mark.assert_called_once()
            mock_rec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
