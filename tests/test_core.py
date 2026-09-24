import os
import tempfile
import unittest
from pathlib import Path

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        from app import database
        self.database=database
        database.DB_PATH=Path(self.tmp.name)/"test.db"
        database.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_health(self):
        health=self.database.database_health()
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["schema_version"], "4")

    def test_lowercase_metadata(self):
        from app.ai_validation import validate_metadata
        result=validate_metadata(
            "Funny Animated Baby",
            "A funny animated baby short for Hindi viewers.",
            ["#Shorts","#AnimatedBaby","#FunnyShorts","#HindiShorts","#ChalaakKahaniya"],
            ["Shorts","AnimatedBaby","FunnyShorts","HindiShorts","ChalaakKahaniya"]
        )
        self.assertTrue(result["passed"], result)

    def test_login_endpoint_injects_request(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("async def api_login(payload: dict, request: Request):", source)
        self.assertIn("request.client.host", source)

    def test_job_lifecycle(self):
        d=self.database
        now=d.now_iso()
        # Create minimal channel and video.
        d.upsert_channel("UC_TEST","Test Channel","@test","test@example.com","youtube_token_test")
        v=d.upsert_video("UC_TEST", str(Path(self.tmp.name)/"video.mp4"), "video.mp4", "a"*64, 10, 1.0, "pending")
        job=d.create_upload_job(v["id"],"UC_TEST")
        self.assertEqual(d.get_upload_job(job)["status"],"pending")
        d.update_upload_job(job,status="retry_pending",last_error="test")
        self.assertEqual(d.get_upload_job(job)["status"],"retry_pending")
        d.add_upload_attempt(job,1,"failed",error_message="test",started_at=now,finished_at=now)
        conn=d.get_conn()
        try:
            attempts=conn.execute("SELECT COUNT(*) AS c FROM upload_attempts WHERE job_id=?",(job,)).fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(attempts,1)

if __name__=="__main__":
    unittest.main()
