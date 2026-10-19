import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import (
    init_db, get_conn, get_channel, list_channels,
    list_meta_connections, get_meta_connection,
    link_meta_connection_to_channels, upsert_meta_connection,
    get_inventory_by_id
)
from app.scheduler import _run_meta_publish
import scripts.syndicate_to_meta as syndicator


class TestMetaFix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_01_meta_connection_id_column_exists(self):
        conn = get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(youtube_channels)").fetchall()}
        conn.close()
        self.assertIn("meta_connection_id", cols, "meta_connection_id column must exist in youtube_channels")

    def test_02_auto_link_meta_page_to_channel_and_schedules(self):
        # Insert or verify Football Video Meta Connection
        conn = get_conn()
        existing = conn.execute("SELECT id FROM meta_connections WHERE page_id='1305659892638232'").fetchone()
        if not existing:
            upsert_meta_connection(
                account_name="Football Video Meta",
                app_id="123456789",
                page_id="1305659892638232",
                page_name="Football Video",
                instagram_user_id="17841461621593298",
                instagram_username="football_video_official",
                token_key="meta_page_1305659892638232"
            )

        linked_count = link_meta_connection_to_channels()
        self.assertGreater(linked_count, 0, "link_meta_connection_to_channels should link matching channels")

        # Verify channel UC63hKHobZMgC_muYECQt4FA has meta_connection_id set
        ch = conn.execute("SELECT * FROM youtube_channels WHERE channel_id='UC63hKHobZMgC_muYECQt4FA'").fetchone()
        self.assertIsNotNone(ch)
        self.assertIsNotNone(ch["meta_connection_id"], "Channel UC63hKHobZMgC_muYECQt4FA must have meta_connection_id linked")

        # Verify schedules for Football Video have meta publishing enabled
        scheds = conn.execute("SELECT * FROM upload_schedules WHERE channel_id='UC63hKHobZMgC_muYECQt4FA'").fetchall()
        for s in scheds:
            self.assertEqual(s["publish_facebook_video"], 1, f"Schedule {s['id']} must have publish_facebook_video=1")
            self.assertEqual(s["publish_facebook_reel"], 1, f"Schedule {s['id']} must have publish_facebook_reel=1")
            self.assertEqual(s["publish_instagram_reel"], 1, f"Schedule {s['id']} must have publish_instagram_reel=1")
            self.assertIsNotNone(s["meta_connection_id"], f"Schedule {s['id']} must have meta_connection_id set")
        conn.close()

    def test_03_run_meta_publish_auto_resolves_and_enables(self):
        # Test calling _run_meta_publish with completely blank schedule flags and None meta_cid
        blank_row = {
            "channel_id": "UC63hKHobZMgC_muYECQt4FA",
            "meta_connection_id": None,
            "publish_facebook_video": 0,
            "publish_facebook_reel": 0,
            "publish_instagram_reel": 0,
        }
        test_item = {
            "id": 999999,
            "channel_id": "UC63hKHobZMgC_muYECQt4FA",
            "filename": "test_video.mp4",
            "sha256": "dummy_sha_for_test",
            "facebook_video_id": "",
            "facebook_reel_id": "",
            "instagram_media_id": "",
        }

        # Mock token and upload functions so no real API call happens
        with patch("app.scheduler.load_page_token", return_value={"access_token": "test_token"}), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("app.scheduler.upload_facebook_page_video", return_value={"id": "fb_vid_123"}), \
             patch("app.scheduler.upload_facebook_page_reel", return_value={"id": "fb_reel_123"}), \
             patch("app.scheduler.upload_instagram_reel", return_value={"id": "ig_reel_123"}), \
             patch("app.scheduler.mark_platform_published") as mock_mark:

            _run_meta_publish(
                blank_row,
                test_item,
                title="Test Video Title",
                description="Test Description",
                hashtags=["football", "skills"],
                file_path="/dummy/test_video.mp4"
            )

            # Check that mark_platform_published was called for facebook_video, facebook_reel, instagram_reel
            published_platforms = [c[0][1] for c in mock_mark.call_args_list]
            self.assertIn("facebook_video", published_platforms)
            self.assertIn("facebook_reel", published_platforms)
            self.assertIn("instagram_reel", published_platforms)

    def test_04_syndicate_cli_dry_run(self):
        videos = syndicator.get_videos_for_syndication(video_id=3261)
        self.assertEqual(len(videos), 1, "Video 3261 should be found in inventory")
        v = videos[0]
        self.assertEqual(v["channel_id"], "UC63hKHobZMgC_muYECQt4FA")

        # Run dry run
        ok = syndicator.syndicate_video(v, dry_run=True)
        self.assertTrue(ok, "Dry run for video 3261 must succeed")


if __name__ == "__main__":
    unittest.main()
