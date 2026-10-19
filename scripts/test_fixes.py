import unittest
import json
import sqlite3
from pathlib import Path

from app.thumbnail import _clean_title_hook, generate_ab_test_titles, _extract_video_specific_hooks
from app.database import is_video_published_on_platform, mark_platform_published, sync_duplicate_inventory_records, get_conn, now_iso


class TestFixes(unittest.TestCase):

    def test_clean_title_hook_strips_boilerplate(self):
        cases = [
            ("What ACTUALLY Happened? Mbappe at the 2018 World Cup Was Built Different!",
             "Mbappe at the 2018 World Cup Was Built Different!"),
            ("The Shocking Truth About What ACTUALLY Happened? 1 Lakh Ka Phone Sunke Bachhe Ne Diya Shocking Jawab!",
             "1 Lakh Ka Phone Sunke Bachhe Ne Diya Shocking Jawab!"),
            ("What ACTUALLY Happened? Banana vs Pumpkin: Beach Fight! 🍌🎃",
             "Banana vs Pumpkin: Beach Fight! 🍌🎃"),
            ("Aalu Don Ka Badla Aur Baingan Ki Dushmani! 🥔🍆 (Full Story)",
             "Aalu Don Ka Badla Aur Baingan Ki Dushmani! 🥔🍆"),
            ("आखिर में क्या हुआ?! Chote Bacche Ka Dimag! 😂 (पूरी कहानी)",
             "Chote Bacche Ka Dimag! 😂"),
            ("The Shocking Truth About Prime Kylian Mbappe Will Be Back! 💥",
             "Prime Kylian Mbappe Will Be Back! 💥"),
        ]
        for inp, expected in cases:
            cleaned = _clean_title_hook(inp)
            self.assertNotIn("What ACTUALLY Happened", cleaned)
            self.assertNotIn("The Shocking Truth About", cleaned)
            self.assertNotIn("(Full Story)", cleaned)
            self.assertNotIn("(पूरी कहानी)", cleaned)
            self.assertNotIn("आखिर में क्या हुआ", cleaned)
            self.assertEqual(cleaned, expected)

    def test_generate_ab_test_titles_creative_and_no_boilerplate(self):
        sample_analysis = json.dumps({
            "metadata": {"title": "Mbappe at the 2018 World Cup Was Built Different!"},
            "visual_summary": "Kylian Mbappe scoring lightning fast goals against Argentina in 2018 FIFA World Cup",
            "subjects": ["Mbappe", "World Cup", "Football"],
            "style": "High energy sports montage"
        })
        titles = generate_ab_test_titles("Mbappe at the 2018 World Cup Was Built Different!", sample_analysis)
        self.assertEqual(len(titles), 3)
        for t in titles:
            self.assertLessEqual(len(t), 98)
            self.assertNotIn("What ACTUALLY Happened?", t)
            self.assertNotIn("The Shocking Truth About", t)
            self.assertNotIn("(Full Story)", t)
            self.assertIn("Mbappe", t)

        # Check all 3 titles are distinct
        self.assertEqual(len(set(titles)), 3)

    def test_generate_ab_test_titles_cleans_dirty_input(self):
        dirty_input = "The Shocking Truth About What ACTUALLY Happened? Banana vs Pumpkin: Beach Fight! 🍌🎃 (Full Story)"
        titles = generate_ab_test_titles(dirty_input)
        self.assertEqual(len(titles), 3)
        for t in titles:
            self.assertNotIn("What ACTUALLY Happened", t)
            self.assertNotIn("The Shocking Truth", t)
            self.assertNotIn("(Full Story)", t)
            self.assertIn("Banana vs Pumpkin", t)

    def test_thumbnail_hooks_no_nonsense_clutter(self):
        hooks = _extract_video_specific_hooks("Banana vs Broccoli: Epic Beach Showdown", "Funny animated food fight on beach")
        self.assertEqual(len(hooks), 3)
        for h in hooks:
            # Badges and bottom bars must be empty so no clownish red/black boxes are drawn on thumbnails
            self.assertEqual(h.get("badge", ""), "")
            self.assertEqual(h.get("bottom", ""), "")

    def test_platform_deduplication(self):
        # Insert a temporary test video record in DB using an existing valid channel_id
        conn = get_conn()
        ch_rows = conn.execute("SELECT channel_id FROM youtube_channels LIMIT 2").fetchall()
        ch_id1 = ch_rows[0][0] if len(ch_rows) > 0 else "UCYvhCpK5E2WQJMeq4Zii9cw"
        ch_id2 = ch_rows[1][0] if len(ch_rows) > 1 else "UC5d0vZT7x6ncYA7QveV5nCw"
        test_sha = "test_sha_dedup_" + now_iso().replace(":", "").replace("-", "")
        conn.execute("""
            INSERT INTO video_inventory (channel_id, file_path, filename, sha256, size_bytes, modified_at, status, created_at, updated_at)
            VALUES (?, '/test/path/vid1.mp4', 'vid1.mp4', ?, 1234, 1000.0, 'pending', ?, ?)
        """, (ch_id1, test_sha, now_iso(), now_iso()))
        row1_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert a duplicate record with same sha on channel 2
        conn.execute("""
            INSERT INTO video_inventory (channel_id, file_path, filename, sha256, size_bytes, modified_at, status, created_at, updated_at)
            VALUES (?, '/test/path/vid2.mp4', 'vid2.mp4', ?, 1234, 1000.0, 'pending', ?, ?)
        """, (ch_id2, test_sha, now_iso(), now_iso()))
        row2_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        try:
            # Before publishing
            self.assertFalse(is_video_published_on_platform(sha256=test_sha, platform="instagram_reel"))
            self.assertFalse(is_video_published_on_platform(sha256=test_sha, platform="facebook_reel"))
            self.assertFalse(is_video_published_on_platform(sha256=test_sha, platform="youtube"))

            # Publish row1 as instagram_reel
            mark_platform_published(row1_id, "instagram_reel", "ig_remote_12345")

            # Check both row1 and row2 (and sha256) are now seen as published
            self.assertTrue(is_video_published_on_platform(sha256=test_sha, platform="instagram_reel"))
            self.assertTrue(is_video_published_on_platform(video_id=row1_id, platform="instagram_reel"))
            self.assertTrue(is_video_published_on_platform(video_id=row2_id, platform="instagram_reel"))

            # Verify row2 was synced
            conn = get_conn()
            r2 = conn.execute("SELECT instagram_media_id FROM video_inventory WHERE id=?", (row2_id,)).fetchone()
            self.assertEqual(r2["instagram_media_id"], "ig_remote_12345")
            conn.close()

        finally:
            conn = get_conn()
            conn.execute("DELETE FROM video_inventory WHERE sha256=?", (test_sha,))
            conn.execute("DELETE FROM platform_uploads WHERE video_id IN (?, ?)", (row1_id, row2_id))
            conn.commit()
            conn.close()


if __name__ == "__main__":
    unittest.main()
