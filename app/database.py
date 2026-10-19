import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from .config import DB_PATH

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS youtube_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL UNIQUE,
        account_email TEXT DEFAULT '',
        client_secret_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'connected',
        created_at TEXT NOT NULL,
        last_verified_at TEXT DEFAULT '',
        auth_last_success_at TEXT DEFAULT '',
        reauth_due_at TEXT DEFAULT '',
        reauth_status TEXT NOT NULL DEFAULT 'monitoring',
        reauth_reminder_days INTEGER NOT NULL DEFAULT 7
    );

    CREATE TABLE IF NOT EXISTS youtube_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER DEFAULT NULL,
        channel_id TEXT NOT NULL UNIQUE,
        channel_name TEXT NOT NULL,
        channel_handle TEXT DEFAULT '',
        google_email TEXT DEFAULT '',
        token_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'connected',
        created_at TEXT NOT NULL,
        last_verified_at TEXT,
        video_folder TEXT DEFAULT '',
        last_sync_at TEXT DEFAULT '',
        last_sync_status TEXT DEFAULT 'never',
        last_sync_error TEXT DEFAULT '',
        remote_video_count INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS video_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        file_path TEXT NOT NULL,
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        modified_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        youtube_video_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        hashtags TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        ai_status TEXT DEFAULT 'not_analyzed',
        ai_summary TEXT DEFAULT '',
        transcript TEXT DEFAULT '',
        analysis_json TEXT DEFAULT '',
        privacy_status TEXT DEFAULT 'private',
        error_message TEXT DEFAULT '',
        facebook_video_id TEXT DEFAULT '',
        facebook_reel_id TEXT DEFAULT '',
        instagram_media_id TEXT DEFAULT '',
        platforms_published TEXT DEFAULT '[]',

        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(channel_id, sha256),
        FOREIGN KEY(channel_id) REFERENCES youtube_channels(channel_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS youtube_remote_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        youtube_video_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        published_at TEXT DEFAULT '',
        privacy_status TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        hash_marker TEXT DEFAULT '',
        statistics_json TEXT DEFAULT '{}',
        content_json TEXT DEFAULT '{}',
        synced_at TEXT NOT NULL,
        UNIQUE(channel_id, youtube_video_id),
        FOREIGN KEY(channel_id) REFERENCES youtube_channels(channel_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS youtube_video_metrics_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        youtube_video_id TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        view_count INTEGER NOT NULL DEFAULT 0,
        like_count INTEGER NOT NULL DEFAULT 0,
        comment_count INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(channel_id) REFERENCES youtube_channels(channel_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TEXT NOT NULL,
        last_login_at TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS ai_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        account_name TEXT DEFAULT '',
        account_email TEXT NOT NULL,
        token_key TEXT NOT NULL UNIQUE,
        client_secret_path TEXT DEFAULT '',
        model TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'connected',
        created_at TEXT NOT NULL,
        last_verified_at TEXT,
        UNIQUE(provider, account_email)
    );

    CREATE TABLE IF NOT EXISTS upload_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        weekday INTEGER NOT NULL,
        time_local TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        privacy_status TEXT NOT NULL DEFAULT 'private',
        category_id TEXT NOT NULL DEFAULT '22',
        title_template TEXT DEFAULT '{filename}',
        description_template TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        made_for_kids INTEGER NOT NULL DEFAULT 0,
        ai_provider TEXT NOT NULL DEFAULT 'gemini',
        ai_connection_id INTEGER DEFAULT NULL,
        last_run_date TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(channel_id, weekday, time_local),
        FOREIGN KEY(channel_id) REFERENCES youtube_channels(channel_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS community_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER,
        channel_id TEXT DEFAULT '',
        platform TEXT NOT NULL,
        post_type TEXT DEFAULT 'teaser',
        content_text TEXT NOT NULL,
        poll_json TEXT DEFAULT '[]',
        media_url TEXT DEFAULT '',
        external_post_id TEXT DEFAULT '',
        external_post_url TEXT DEFAULT '',
        status TEXT DEFAULT 'published',
        error_message TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        published_at TEXT NOT NULL,
        FOREIGN KEY(video_id) REFERENCES video_inventory(id) ON DELETE SET NULL
    );
    """)
    # Phase 20: multi-YouTube-account support. Existing channels are migrated
    # into one account so their current credentials/data remain usable.
    ycols = {row["name"] for row in conn.execute("PRAGMA table_info(youtube_channels)").fetchall()}
    if "account_id" not in ycols:
        conn.execute("ALTER TABLE youtube_channels ADD COLUMN account_id INTEGER DEFAULT NULL")
    existing_account = conn.execute("SELECT id FROM youtube_accounts ORDER BY id LIMIT 1").fetchone()
    if not existing_account:
        from .config import CLIENT_SECRET_PATH
        ch = conn.execute("SELECT channel_name FROM youtube_channels ORDER BY id LIMIT 1").fetchone()
        account_name = (ch["channel_name"] if ch else "YouTube Account 1") or "YouTube Account 1"
        conn.execute("INSERT INTO youtube_accounts(account_name,client_secret_path,status,created_at,last_verified_at) VALUES(?,?,?,?,?)", (account_name, str(Path("credentials") / "client_secret.json"), "connected", now_iso(), now_iso()))
        existing_account = conn.execute("SELECT id FROM youtube_accounts WHERE account_name=?", (account_name,)).fetchone()
    conn.execute("UPDATE youtube_channels SET account_id=? WHERE account_id IS NULL", (existing_account["id"],))

    # Safe migration for databases created by the earlier Phase 1B build.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(youtube_channels)").fetchall()}
    if "video_folder" not in cols:
        conn.execute("ALTER TABLE youtube_channels ADD COLUMN video_folder TEXT DEFAULT ''")
    if "last_sync_at" not in cols: conn.execute("ALTER TABLE youtube_channels ADD COLUMN last_sync_at TEXT DEFAULT ''")
    if "last_sync_status" not in cols: conn.execute("ALTER TABLE youtube_channels ADD COLUMN last_sync_status TEXT DEFAULT 'never'")
    if "last_sync_error" not in cols: conn.execute("ALTER TABLE youtube_channels ADD COLUMN last_sync_error TEXT DEFAULT ''")
    if "remote_video_count" not in cols: conn.execute("ALTER TABLE youtube_channels ADD COLUMN remote_video_count INTEGER NOT NULL DEFAULT 0")
    if "meta_connection_id" not in cols: conn.execute("ALTER TABLE youtube_channels ADD COLUMN meta_connection_id INTEGER DEFAULT NULL")
    vcols = {row["name"] for row in conn.execute("PRAGMA table_info(video_inventory)").fetchall()}
    for col, definition in [
        ("hashtags", "TEXT DEFAULT ''"),
        ("tags", "TEXT DEFAULT ''"),
        ("ai_status", "TEXT DEFAULT 'not_analyzed'"),
        ("ai_summary", "TEXT DEFAULT ''"),
        ("transcript", "TEXT DEFAULT ''"),
        ("analysis_json", "TEXT DEFAULT ''"),
    ]:
        if col not in vcols:
            conn.execute(f"ALTER TABLE video_inventory ADD COLUMN {col} {definition}")
    if "thumbnail_path" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN thumbnail_path TEXT DEFAULT ''")
    if "youtube_playlist_id" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN youtube_playlist_id TEXT DEFAULT ''")
    if "thumbnail_variants_json" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN thumbnail_variants_json TEXT DEFAULT '[]'")
    if "ab_test_titles_json" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN ab_test_titles_json TEXT DEFAULT '[]'")
    if "last_performance_check_at" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN last_performance_check_at TEXT DEFAULT ''")
    if "auto_optimized_count" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN auto_optimized_count INTEGER DEFAULT 0")
    if "optimization_history_json" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN optimization_history_json TEXT DEFAULT '[]'")
    if "facebook_video_id" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN facebook_video_id TEXT DEFAULT ''")
    if "facebook_reel_id" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN facebook_reel_id TEXT DEFAULT ''")
    if "instagram_media_id" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN instagram_media_id TEXT DEFAULT ''")
    if "platforms_published" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN platforms_published TEXT DEFAULT '[]'")
    if "review_status" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN review_status TEXT DEFAULT 'pending'")
    if "review_verdict" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN review_verdict TEXT DEFAULT ''")
    if "review_score" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN review_score REAL DEFAULT 0.0")
    if "review_pdf_path" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN review_pdf_path TEXT DEFAULT ''")
    if "review_json" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN review_json TEXT DEFAULT ''")
    if "cross_syndicated_at" not in vcols:
        conn.execute("ALTER TABLE video_inventory ADD COLUMN cross_syndicated_at TEXT DEFAULT ''")
    # Backfill YouTube platform marker for existing uploaded videos
    conn.execute("UPDATE video_inventory SET platforms_published='[\"youtube\"]' WHERE (COALESCE(platforms_published,'')='' OR platforms_published='[]') AND COALESCE(youtube_video_id,'')!=''")
    # Phase 7 migration: allow multiple upload slots per weekday.
    # Older builds used UNIQUE(channel_id, weekday), which prevented multiple
    # uploads on the same day. Migrate once to the (channel, day, time) key.
    table_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='upload_schedules'").fetchone()
    if table_sql and "UNIQUE(channel_id, weekday, time_local)" not in (table_sql[0] or ""):
        conn.execute("DROP TABLE IF EXISTS upload_schedules_v2")
        conn.execute("""CREATE TABLE upload_schedules_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL, weekday INTEGER NOT NULL, time_local TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, privacy_status TEXT NOT NULL DEFAULT 'private',
            category_id TEXT NOT NULL DEFAULT '22', title_template TEXT DEFAULT '{filename}',
            description_template TEXT DEFAULT '', tags TEXT DEFAULT '', made_for_kids INTEGER NOT NULL DEFAULT 0,
            ai_provider TEXT NOT NULL DEFAULT 'gemini', ai_connection_id INTEGER DEFAULT NULL,
            last_run_date TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(channel_id, weekday, time_local),
            FOREIGN KEY(channel_id) REFERENCES youtube_channels(channel_id) ON DELETE CASCADE
        )""")
        conn.execute("""INSERT INTO upload_schedules_v2
            (channel_id,weekday,time_local,enabled,privacy_status,category_id,title_template,description_template,tags,made_for_kids,ai_provider,ai_connection_id,last_run_date,created_at,updated_at)
            SELECT channel_id,weekday,time_local,enabled,privacy_status,category_id,title_template,description_template,tags,made_for_kids,ai_provider,ai_connection_id,last_run_date,created_at,updated_at
            FROM upload_schedules""")
        conn.execute("DROP TABLE upload_schedules")
        conn.execute("ALTER TABLE upload_schedules_v2 RENAME TO upload_schedules")

    scols = {row["name"] for row in conn.execute("PRAGMA table_info(upload_schedules)").fetchall()}
    if "ai_provider" not in scols: conn.execute("ALTER TABLE upload_schedules ADD COLUMN ai_provider TEXT NOT NULL DEFAULT 'gemini'")
    if "ai_connection_id" not in scols: conn.execute("ALTER TABLE upload_schedules ADD COLUMN ai_connection_id INTEGER DEFAULT NULL")
    if "ai_secondary_connection_id" not in scols: conn.execute("ALTER TABLE upload_schedules ADD COLUMN ai_secondary_connection_id INTEGER DEFAULT NULL")
    for col, definition in [
        ("contains_synthetic_media", "INTEGER NOT NULL DEFAULT 1"),
        ("video_language", "TEXT NOT NULL DEFAULT 'hi'"),
        ("title_description_language", "TEXT NOT NULL DEFAULT 'hi'"),
        ("caption_certification", "TEXT NOT NULL DEFAULT 'none'"),
        ("recording_date", "TEXT DEFAULT ''"),
        ("video_location", "TEXT DEFAULT 'India'"),
        ("thumbnail_engine", "TEXT NOT NULL DEFAULT 'flow'"),
    ]:
        if col not in scols: conn.execute(f"ALTER TABLE upload_schedules ADD COLUMN {col} {definition}")
    # Phase 22: independent Gemini accounts with friendly names and per-account OAuth clients.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_connections)").fetchall()}
    if "account_name" not in cols:
        conn.execute("ALTER TABLE ai_connections ADD COLUMN account_name TEXT DEFAULT ''")
    if "client_secret_path" not in cols:
        conn.execute("ALTER TABLE ai_connections ADD COLUMN client_secret_path TEXT DEFAULT ''")
    conn.execute("UPDATE ai_connections SET account_name=account_email WHERE COALESCE(account_name,'')='' AND provider='gemini'")

    # Schema metadata enables future migrations without relying on table guessing.
    conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    current = conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
    current_version = int(current['value']) if current and str(current['value']).isdigit() else 0
    # The idempotent table/column migrations above bring all supported legacy
    # databases to the current shape. Record the release-level migration
    # explicitly so future releases have a real migration history to build on.
    if current_version < 4:
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(4, ?)", (now_iso(),))
    conn.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('schema_version','4')")
    conn.execute("UPDATE app_meta SET value='4' WHERE key='schema_version'")
    # Meta (Facebook Page + linked Instagram Professional account) connection storage.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS meta_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL,
        app_id TEXT NOT NULL,
        page_id TEXT NOT NULL UNIQUE,
        page_name TEXT NOT NULL DEFAULT '',
        instagram_user_id TEXT DEFAULT '',
        instagram_username TEXT DEFAULT '',
        token_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'connected',
        token_expires_at TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        last_verified_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS platform_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        destination_id TEXT NOT NULL DEFAULT '',
        remote_id TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(video_id, platform, destination_id),
        FOREIGN KEY(video_id) REFERENCES video_inventory(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_platform_uploads_video ON platform_uploads(video_id, platform);
    """)
    # Reliability/audit tables. Safe to create on every startup.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS upload_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER, channel_id TEXT NOT NULL, schedule_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 100,
        scheduled_at TEXT DEFAULT '', started_at TEXT DEFAULT '', completed_at TEXT DEFAULT '',
        attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT DEFAULT '', youtube_video_id TEXT DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_upload_jobs_status ON upload_jobs(status, priority, scheduled_at);
    CREATE TABLE IF NOT EXISTS upload_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, attempt_no INTEGER NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT DEFAULT '', status TEXT NOT NULL, error_message TEXT DEFAULT '',
        youtube_video_id TEXT DEFAULT '', FOREIGN KEY(job_id) REFERENCES upload_jobs(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, action TEXT NOT NULL,
        resource TEXT DEFAULT '', resource_id TEXT DEFAULT '', details_json TEXT DEFAULT '{}', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ai_analysis_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, video_id INTEGER, provider TEXT NOT NULL, model TEXT DEFAULT '',
        status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT DEFAULT '', error_message TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS scheduler_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, run_date TEXT NOT NULL, status TEXT NOT NULL,
        started_at TEXT NOT NULL, completed_at TEXT DEFAULT '', message TEXT DEFAULT '',
        UNIQUE(schedule_id, run_date)
    );
    CREATE TABLE IF NOT EXISTS api_quota_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT NOT NULL DEFAULT 'youtube',
        operation TEXT NOT NULL, units_used INTEGER NOT NULL DEFAULT 1,
        channel_id TEXT DEFAULT '', account_name TEXT DEFAULT '',
        date_pt TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_quota_date ON api_quota_logs(service, date_pt);
    """)
    # Credential lifecycle / re-authentication tracking. The reminder is a local
    # tracking policy, not a claim that Google will expire a refresh token on that date.
    yacols = {r["name"] for r in conn.execute("PRAGMA table_info(youtube_accounts)").fetchall()}
    for col, definition in [
        ("auth_last_success_at", "TEXT DEFAULT ''"),
        ("reauth_due_at", "TEXT DEFAULT ''"),
        ("reauth_status", "TEXT NOT NULL DEFAULT 'monitoring'"),
        ("reauth_reminder_days", "INTEGER NOT NULL DEFAULT 7"),
    ]:
        if col not in yacols:
            conn.execute(f"ALTER TABLE youtube_accounts ADD COLUMN {col} {definition}")
    # Backfill the tracking baseline from the existing verification timestamp.
    conn.execute("UPDATE youtube_accounts SET auth_last_success_at=COALESCE(NULLIF(auth_last_success_at,''), last_verified_at, created_at) WHERE COALESCE(auth_last_success_at,'')=''")
    conn.execute("UPDATE youtube_accounts SET reauth_due_at=datetime(auth_last_success_at, '+' || COALESCE(reauth_reminder_days,7) || ' days') WHERE COALESCE(reauth_due_at,'')='' AND COALESCE(auth_last_success_at,'')!=''")

    # Migration for existing installations created before Google account
    # email was persisted at the YouTube-account level.
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(youtube_accounts)").fetchall()}
    if "account_email" not in columns:
        conn.execute("ALTER TABLE youtube_accounts ADD COLUMN account_email TEXT DEFAULT ''")

    conn.execute("UPDATE upload_schedules SET ai_provider='gemini' WHERE COALESCE(ai_provider,'') != 'gemini'")

    # Link Meta connections to a YouTube account so the UI can group them.
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(meta_connections)").fetchall()}
    if "account_id" not in mcols:
        conn.execute("ALTER TABLE meta_connections ADD COLUMN account_id INTEGER DEFAULT NULL")

    # Meta publishing columns on upload_schedules
    scols2 = {r["name"] for r in conn.execute("PRAGMA table_info(upload_schedules)").fetchall()}
    for col, definition in [
        ("publish_facebook_video",  "INTEGER NOT NULL DEFAULT 0"),
        ("publish_facebook_reel",  "INTEGER NOT NULL DEFAULT 0"),
        ("publish_instagram_reel", "INTEGER NOT NULL DEFAULT 0"),
        ("meta_connection_id",     "INTEGER DEFAULT NULL"),
    ]:
        if col not in scols2:
            conn.execute(f"ALTER TABLE upload_schedules ADD COLUMN {col} {definition}")

    # App-wide key/value settings (Telegram config, etc.)
    conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")

    conn.commit()
    conn.close()

    try:
        link_meta_connection_to_channels()
    except Exception:
        pass

def link_meta_connection_to_channels(target_meta_id: int | None = None):
    """Auto-link Meta connections to matching YouTube channels and auto-enable Facebook/Instagram publishing on their schedules."""
    conn = get_conn()
    if target_meta_id:
        meta_rows = conn.execute("SELECT * FROM meta_connections WHERE id=?", (int(target_meta_id),)).fetchall()
    else:
        meta_rows = conn.execute("SELECT * FROM meta_connections WHERE status='connected'").fetchall()

    channels = conn.execute("SELECT * FROM youtube_channels").fetchall()
    linked_count = 0

    for m_row in meta_rows:
        m = dict(m_row)
        m_id = m["id"]
        m_page_name = (m.get("page_name") or "").strip().lower()
        m_acct_name = (m.get("account_name") or "").strip().lower()
        m_has_ig = bool(m.get("instagram_user_id") or m.get("instagram_username"))

        for ch_row in channels:
            ch = dict(ch_row)
            ch_id = ch["channel_id"]
            ch_name = (ch.get("channel_name") or "").strip().lower()
            ch_handle = (ch.get("channel_handle") or "").strip().lower().lstrip("@")

            matched = False
            if m_page_name and (ch_name == m_page_name or ch_name in m_page_name or m_page_name in ch_name):
                matched = True
            elif ch_handle and m_page_name and (ch_handle == m_page_name or ch_handle in m_page_name):
                matched = True
            elif ch.get("account_id") and m.get("account_id") and ch["account_id"] == m["account_id"]:
                matched = True

            if matched:
                conn.execute("UPDATE youtube_channels SET meta_connection_id=? WHERE channel_id=?", (m_id, ch_id))
                conn.execute("""
                    UPDATE upload_schedules
                    SET meta_connection_id = COALESCE(meta_connection_id, ?),
                        publish_facebook_video = 1,
                        publish_facebook_reel = 1,
                        publish_instagram_reel = CASE WHEN ? THEN 1 ELSE publish_instagram_reel END
                    WHERE channel_id = ?
                """, (m_id, 1 if m_has_ig else 0, ch_id))
                linked_count += 1

    conn.commit()
    conn.close()
    return linked_count

def get_app_setting(key: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None

def set_app_setting(key: str, value: str):
    conn = get_conn()
    conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()

def list_youtube_accounts():
    conn=get_conn()
    rows=conn.execute("""SELECT a.*, COUNT(c.id) AS channel_count
                         FROM youtube_accounts a LEFT JOIN youtube_channels c ON c.account_id=a.id
                         GROUP BY a.id ORDER BY a.account_name COLLATE NOCASE""").fetchall()
    conn.close(); return [dict(r) for r in rows]

def get_youtube_account(account_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM youtube_accounts WHERE id=?", (account_id,)).fetchone(); conn.close(); return dict(row) if row else None

def upsert_youtube_account(account_name, client_secret_path, account_email="", account_id=None):
    conn=get_conn(); now=now_iso()
    from datetime import timedelta
    due=(datetime.fromisoformat(now.replace('Z','+00:00')) + timedelta(days=7)).isoformat()
    account_email = str(account_email or "").strip()
    if account_id:
        conn.execute(
            "UPDATE youtube_accounts SET account_name=?,account_email=?,client_secret_path=?,status='connected',last_verified_at=?,auth_last_success_at=?,reauth_due_at=?,reauth_status='monitoring' WHERE id=?",
            (account_name,account_email,client_secret_path,now,now,due,int(account_id))
        )
        aid=int(account_id)
    else:
        existing=conn.execute("SELECT id FROM youtube_accounts WHERE account_name=?", (account_name,)).fetchone()
        if existing:
            aid=int(existing["id"])
            conn.execute(
                "UPDATE youtube_accounts SET account_email=?,client_secret_path=?,status='connected',last_verified_at=?,auth_last_success_at=?,reauth_due_at=?,reauth_status='monitoring' WHERE id=?",
                (account_email,client_secret_path,now,now,due,aid)
            )
        else:
            cur=conn.execute(
                "INSERT INTO youtube_accounts(account_name,account_email,client_secret_path,status,created_at,last_verified_at,auth_last_success_at,reauth_due_at,reauth_status) VALUES(?,?,?,?,?,?,?,?,?)",
                (account_name,account_email,client_secret_path,'connected',now,now,now,due,'monitoring')
            )
            aid=cur.lastrowid
    conn.commit(); row=conn.execute("SELECT * FROM youtube_accounts WHERE id=?",(aid,)).fetchone(); conn.close(); return dict(row)

def update_youtube_account_status(account_id, status, last_verified_at=None):
    conn=get_conn()
    if last_verified_at is None:
        conn.execute("UPDATE youtube_accounts SET status=? WHERE id=?", (status, int(account_id)))
    else:
        conn.execute("UPDATE youtube_accounts SET status=?, last_verified_at=? WHERE id=?", (status, last_verified_at, int(account_id)))
    conn.commit(); conn.close()

def mark_youtube_account_reauth_required(account_id, reason='Credential re-authentication required.'):
    conn=get_conn(); now=now_iso()
    conn.execute("UPDATE youtube_accounts SET status='reauth_required', reauth_status='required', reauth_due_at=?, last_verified_at=last_verified_at WHERE id=?", (now, int(account_id)))
    conn.commit(); conn.close()

def mark_youtube_account_auth_success(account_id, reminder_days=None):
    conn=get_conn(); now=now_iso()
    if reminder_days is None:
        row=conn.execute("SELECT reauth_reminder_days FROM youtube_accounts WHERE id=?", (int(account_id),)).fetchone()
        reminder_days=int(row['reauth_reminder_days'] if row and row['reauth_reminder_days'] else 7)
    from datetime import timedelta
    due=(datetime.fromisoformat(now.replace('Z','+00:00')) + timedelta(days=int(reminder_days))).isoformat()
    conn.execute("UPDATE youtube_accounts SET status='connected', last_verified_at=?, auth_last_success_at=?, reauth_due_at=?, reauth_status='monitoring' WHERE id=?", (now,now,due,int(account_id)))
    conn.commit(); conn.close()

def set_youtube_reauth_reminder_days(account_id, days):
    days=max(1,min(3650,int(days)))
    conn=get_conn(); conn.execute("UPDATE youtube_accounts SET reauth_reminder_days=? WHERE id=?",(days,int(account_id))); conn.commit(); conn.close()
    mark_youtube_account_auth_success(account_id, days)

def get_youtube_reauth_alerts(threshold_days=2):
    conn=get_conn(); rows=conn.execute("SELECT a.*, COUNT(c.id) AS channel_count FROM youtube_accounts a LEFT JOIN youtube_channels c ON c.account_id=a.id GROUP BY a.id ORDER BY a.account_name COLLATE NOCASE").fetchall(); conn.close()
    out=[]; now=datetime.now(timezone.utc)
    for r in rows:
        d=dict(r); due=d.get('reauth_due_at') or ''
        days_remaining=None
        if due:
            try:
                dt=datetime.fromisoformat(due.replace('Z','+00:00'))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                days_remaining=max(0,(dt-now).total_seconds()/86400)
            except Exception: pass
        st=str(d.get('status','')).lower()
        required=(st == 'reauth_required' or str(d.get('reauth_status','')).lower()=='required')
        due_soon=(st not in {'logged_out'} and int(d.get('channel_count') or 0) > 0 and days_remaining is not None and days_remaining <= int(threshold_days))
        if required or due_soon:
            d['reauth_required']=required; d['days_remaining']=days_remaining; d['reauth_due_at']=due
            out.append(d)
    return out

def set_channel_account(channel_id, account_id):
    conn=get_conn(); conn.execute("UPDATE youtube_channels SET account_id=? WHERE channel_id=?",(account_id,channel_id)); conn.commit(); conn.close()

def list_channels_for_account(account_id):
    conn=get_conn(); rows=conn.execute("SELECT * FROM youtube_channels WHERE account_id=? ORDER BY channel_name COLLATE NOCASE",(account_id,)).fetchall(); conn.close(); return [dict(r) for r in rows]

def list_channels():
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM youtube_channels
        ORDER BY channel_name COLLATE NOCASE
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_channel(channel_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_channel(channel_id, channel_name, channel_handle, google_email, token_key):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM youtube_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    if existing:
        conn.execute("""
            UPDATE youtube_channels
            SET channel_name=?, channel_handle=?, google_email=?, token_key=?,
                status='connected', last_verified_at=?
            WHERE channel_id=?
        """, (channel_name, channel_handle or "", google_email or "", token_key,
              now_iso(), channel_id))
    else:
        conn.execute("""
            INSERT INTO youtube_channels
            (channel_id, channel_name, channel_handle, google_email, token_key,
             status, created_at, last_verified_at)
            VALUES (?, ?, ?, ?, ?, 'connected', ?, ?)
        """, (channel_id, channel_name, channel_handle or "", google_email or "",
              token_key, now_iso(), now_iso()))
    conn.commit()
    conn.close()

def update_channel_sync(channel_id, status, error="", remote_count=None):
    conn=get_conn()
    if remote_count is None:
        conn.execute("UPDATE youtube_channels SET last_sync_at=?, last_sync_status=?, last_sync_error=? WHERE channel_id=?", (now_iso(), status, error or "", channel_id))
    else:
        conn.execute("UPDATE youtube_channels SET last_sync_at=?, last_sync_status=?, last_sync_error=?, remote_video_count=? WHERE channel_id=?", (now_iso(), status, error or "", int(remote_count), channel_id))
    conn.commit(); conn.close()

def get_remote_video(channel_id, youtube_video_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM youtube_remote_videos WHERE channel_id=? AND youtube_video_id=?",(channel_id,youtube_video_id)).fetchone(); conn.close(); return dict(row) if row else None

def delete_channel(channel_id):
    conn = get_conn()
    conn.execute("DELETE FROM youtube_channels WHERE channel_id=?", (channel_id,))
    conn.commit()
    conn.close()

def delete_youtube_account(account_id):
    """Remove a YouTube account and ALL its channels, inventory, schedules and remote videos.

    Credentials are removed by auth.logout_youtube_account() before this function is called.
    All child rows (channels, video_inventory, upload_schedules, youtube_remote_videos) are
    removed via ON DELETE CASCADE so nothing from this account lingers in the database.
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM youtube_accounts WHERE id=?", (int(account_id),)).fetchone()
    if not row:
        conn.close()
        return None
    # Delete channels first — CASCADE removes inventory, schedules, remote videos automatically.
    conn.execute("DELETE FROM youtube_channels WHERE account_id=?", (int(account_id),))
    conn.execute("DELETE FROM youtube_accounts WHERE id=?", (int(account_id),))
    conn.commit()
    conn.close()
    return dict(row)

def set_channel_folder(channel_id, folder):
    conn = get_conn()
    conn.execute(
        "UPDATE youtube_channels SET video_folder=? WHERE channel_id=?",
        (folder or "", channel_id)
    )
    conn.commit()
    conn.close()

def get_channel_folder(channel_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT video_folder FROM youtube_channels WHERE channel_id=?",
        (channel_id,)
    ).fetchone()
    conn.close()
    return (row["video_folder"] if row else "") or ""

def get_inventory(channel_id, limit=500):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM video_inventory
        WHERE channel_id=?
        ORDER BY updated_at DESC
        LIMIT ?
    """, (channel_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_inventory_by_id(video_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM video_inventory WHERE id=?",(int(video_id),)).fetchone(); conn.close(); return dict(row) if row else None

def get_inventory_by_path(channel_id, file_path):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM video_inventory
        WHERE channel_id=? AND file_path=?
    """, (channel_id, file_path)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_inventory_by_hash(channel_id, sha256):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM video_inventory
        WHERE channel_id=? AND sha256=?
    """, (channel_id, sha256)).fetchone()
    conn.close()
    return dict(row) if row else None

def list_pending_inventory(channel_id=None, limit=50):
    conn = get_conn()
    if channel_id:
        rows = conn.execute("""
            SELECT * FROM video_inventory
            WHERE channel_id=? AND status='pending'
            ORDER BY id ASC
            LIMIT ?
        """, (channel_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM video_inventory
            WHERE status='pending'
            ORDER BY id ASC
            LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_last_upload_time(channel_id=None):
    """Return the ISO timestamp of the most recent uploaded video (globally or per channel)."""
    conn = get_conn()
    if channel_id:
        row = conn.execute("""
            SELECT MAX(updated_at) as last_upload
            FROM video_inventory
            WHERE channel_id=? AND status='uploaded' AND youtube_video_id != '' AND youtube_video_id IS NOT NULL
        """, (channel_id,)).fetchone()
    else:
        row = conn.execute("""
            SELECT MAX(updated_at) as last_upload
            FROM video_inventory
            WHERE status='uploaded' AND youtube_video_id != '' AND youtube_video_id IS NOT NULL
        """).fetchone()
    conn.close()
    return (row["last_upload"] if row else None) or None

def count_channel_uploads_on_date(channel_id, target_date_str=None):
    """Return count of uploaded videos for a specific channel on a given calendar date (YYYY-MM-DD)."""
    if not target_date_str:
        from datetime import datetime
        target_date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM video_inventory
        WHERE channel_id=? AND status='uploaded' AND youtube_video_id != '' AND youtube_video_id IS NOT NULL
          AND updated_at LIKE ?
    """, (channel_id, f"{target_date_str}%")).fetchone()
    conn.close()
    return int(row["cnt"]) if row else 0

def upsert_video(channel_id, file_path, filename, sha256, size_bytes,
                 modified_at, status="pending"):
    now = now_iso()
    conn = get_conn()
    conn.execute("""
        INSERT INTO video_inventory
        (channel_id, file_path, filename, sha256, size_bytes, modified_at,
         status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, sha256) DO UPDATE SET
            file_path=excluded.file_path,
            filename=excluded.filename,
            size_bytes=excluded.size_bytes,
            modified_at=excluded.modified_at,
            updated_at=excluded.updated_at
    """, (channel_id, file_path, filename, sha256, size_bytes,
          modified_at, status, now, now))
    conn.commit()
    row = conn.execute("""
        SELECT * FROM video_inventory
        WHERE channel_id=? AND sha256=?
    """, (channel_id, sha256)).fetchone()
    conn.close()
    return dict(row)

def update_video(video_id, **fields):
    allowed = {
        "status", "youtube_video_id", "title", "description",
        "hashtags", "tags", "ai_status", "ai_summary", "transcript",
        "analysis_json", "privacy_status", "error_message", "updated_at",
        "thumbnail_path", "youtube_playlist_id", "thumbnail_variants_json",
        "ab_test_titles_json", "last_performance_check_at",
        "auto_optimized_count", "optimization_history_json",
        "facebook_video_id", "facebook_reel_id", "instagram_media_id",
        "platforms_published", "review_status", "review_verdict",
        "review_score", "review_pdf_path", "review_json", "cross_syndicated_at"
    }
    updates = {k:v for k,v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [video_id]
    conn = get_conn()
    conn.execute(f"UPDATE video_inventory SET {set_clause} WHERE id=?", values)
    conn.commit()
    conn.close()


def mark_platform_published(video_id: int, platform: str, remote_id: str = ""):
    """Synchronize upload status across all platforms (YouTube, Facebook Video, Facebook Reel, Instagram Reel) to prevent duplicate uploads."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM video_inventory WHERE id=?", (int(video_id),)).fetchone()
    if not row:
        conn.close()
        return
    import json as _json
    curr_raw = row["platforms_published"] if "platforms_published" in row.keys() else "[]"
    try:
        platforms = _json.loads(curr_raw or "[]")
        if not isinstance(platforms, list):
            platforms = []
    except Exception:
        platforms = []

    norm_plat = platform.lower().strip()
    if norm_plat not in platforms:
        platforms.append(norm_plat)

    updates = {
        "platforms_published": _json.dumps(platforms),
        "updated_at": now_iso()
    }
    if norm_plat == "youtube" and remote_id:
        updates["youtube_video_id"] = remote_id
        updates["status"] = "uploaded"
    elif norm_plat == "facebook_video" and remote_id:
        updates["facebook_video_id"] = remote_id
    elif norm_plat == "facebook_reel" and remote_id:
        updates["facebook_reel_id"] = remote_id
    elif norm_plat == "instagram_reel" and remote_id:
        updates["instagram_media_id"] = remote_id

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [int(video_id)]
    conn.execute(f"UPDATE video_inventory SET {set_clause} WHERE id=?", values)

    # Sync all other records with the same sha256 to prevent duplicate uploads
    sha = row["sha256"] if "sha256" in row.keys() else ""
    if sha:
        sha_values = list(updates.values()) + [sha, int(video_id)]
        conn.execute(
            f"UPDATE video_inventory SET {set_clause} WHERE sha256=? AND id!=?",
            sha_values
        )

    # Record in platform_uploads audit table
    try:
        now_ts = now_iso()
        conn.execute("""
            INSERT INTO platform_uploads (video_id, platform, remote_id, status, created_at, updated_at)
            VALUES (?, ?, ?, 'published', ?, ?)
        """, (int(video_id), norm_plat, str(remote_id), now_ts, now_ts))
    except Exception:
        pass

    conn.commit()
    conn.close()


def is_video_published_on_platform(sha256: str = "", video_id: int | None = None, platform: str = "youtube", channel_id: str = "") -> bool:
    """Check across all video inventory rows, remote videos, and platform uploads
    whether this video (by SHA-256 or ID) has already been published to the specified platform."""
    conn = get_conn()
    norm_plat = (platform or "").lower().strip()

    # 1. Check by video_id if provided
    if video_id:
        row = conn.execute("SELECT * FROM video_inventory WHERE id=?", (int(video_id),)).fetchone()
        if row:
            if not sha256 and row["sha256"]:
                sha256 = row["sha256"]
            if norm_plat == "youtube" and row["youtube_video_id"]:
                conn.close()
                return True
            elif norm_plat == "facebook_video" and row["facebook_video_id"]:
                conn.close()
                return True
            elif norm_plat == "facebook_reel" and row["facebook_reel_id"]:
                conn.close()
                return True
            elif norm_plat == "instagram_reel" and row["instagram_media_id"]:
                conn.close()
                return True

    # 2. Check by sha256 across all inventory rows
    if sha256:
        col_map = {
            "youtube": "youtube_video_id",
            "facebook_video": "facebook_video_id",
            "facebook_reel": "facebook_reel_id",
            "instagram_reel": "instagram_media_id",
        }
        col = col_map.get(norm_plat)
        if col:
            res = conn.execute(
                f"SELECT id FROM video_inventory WHERE sha256=? AND COALESCE({col}, '') != '' LIMIT 1",
                (sha256,)
            ).fetchone()
            if res:
                conn.close()
                return True

        # Check platforms_published JSON
        rows = conn.execute(
            "SELECT platforms_published FROM video_inventory WHERE sha256=? AND COALESCE(platforms_published, '') != ''",
            (sha256,)
        ).fetchall()
        import json as _json
        for r in rows:
            try:
                plist = _json.loads(r["platforms_published"] or "[]")
                if norm_plat in [p.lower().strip() for p in plist]:
                    conn.close()
                    return True
            except Exception:
                pass

        # If platform is youtube, check youtube_remote_videos
        if norm_plat == "youtube":
            if channel_id:
                rv = conn.execute(
                    "SELECT id FROM youtube_remote_videos WHERE channel_id=? AND hash_marker=? LIMIT 1",
                    (channel_id, sha256)
                ).fetchone()
            else:
                rv = conn.execute(
                    "SELECT id FROM youtube_remote_videos WHERE hash_marker=? LIMIT 1",
                    (sha256,)
                ).fetchone()
            if rv:
                conn.close()
                return True

        # Check platform_uploads table
        pu = conn.execute(
            "SELECT pu.id FROM platform_uploads pu JOIN video_inventory vi ON pu.video_id=vi.id WHERE vi.sha256=? AND pu.platform=? AND pu.status IN ('published', 'success') LIMIT 1",
            (sha256, norm_plat)
        ).fetchone()
        if pu:
            conn.close()
            return True

    conn.close()
    return False


def sync_duplicate_inventory_records():
    """Sync upload status and remote IDs across all duplicate video records with the same SHA256."""
    conn = get_conn()
    # Find all uploaded records with remote IDs
    rows = conn.execute("""
        SELECT sha256, youtube_video_id, facebook_video_id, facebook_reel_id, instagram_media_id, status, platforms_published
        FROM video_inventory
        WHERE (COALESCE(youtube_video_id, '') != '' OR COALESCE(facebook_reel_id, '') != '' OR COALESCE(instagram_media_id, '') != '' OR status='uploaded')
          AND COALESCE(sha256, '') != ''
    """).fetchall()

    updated = 0
    import json as _json
    for r in rows:
        sha = r["sha256"]
        yt = r["youtube_video_id"] or ""
        fb_vid = r["facebook_video_id"] or ""
        fb_reel = r["facebook_reel_id"] or ""
        ig = r["instagram_media_id"] or ""
        stat = r["status"] or ""

        # Update other rows with same sha256 that are missing any of these
        cur = conn.execute("""
            UPDATE video_inventory
            SET youtube_video_id = CASE WHEN COALESCE(youtube_video_id, '') = '' THEN ? ELSE youtube_video_id END,
                facebook_video_id = CASE WHEN COALESCE(facebook_video_id, '') = '' THEN ? ELSE facebook_video_id END,
                facebook_reel_id = CASE WHEN COALESCE(facebook_reel_id, '') = '' THEN ? ELSE facebook_reel_id END,
                instagram_media_id = CASE WHEN COALESCE(instagram_media_id, '') = '' THEN ? ELSE instagram_media_id END,
                status = CASE WHEN status = 'pending' AND ? = 'uploaded' THEN 'uploaded' ELSE status END
            WHERE sha256 = ?
              AND (
                  (COALESCE(youtube_video_id, '') = '' AND ? != '')
                  OR (COALESCE(facebook_video_id, '') = '' AND ? != '')
                  OR (COALESCE(facebook_reel_id, '') = '' AND ? != '')
                  OR (COALESCE(instagram_media_id, '') = '' AND ? != '')
                  OR (status = 'pending' AND ? = 'uploaded')
              )
        """, (yt, fb_vid, fb_reel, ig, stat, sha, yt, fb_vid, fb_reel, ig, stat))
        updated += cur.rowcount

    conn.commit()
    conn.close()
    return updated



def add_remote_metrics_snapshot(channel_id, youtube_video_id, statistics):
    stats = statistics or {}
    conn=get_conn()
    conn.execute(
        "INSERT INTO youtube_video_metrics_history (channel_id,youtube_video_id,captured_at,view_count,like_count,comment_count) VALUES (?,?,?,?,?,?)",
        (channel_id, youtube_video_id, now_iso(), int(stats.get("viewCount") or 0), int(stats.get("likeCount") or 0), int(stats.get("commentCount") or 0))
    )
    conn.commit(); conn.close()

def get_remote_metrics_history(channel_id, youtube_video_id, limit=60):
    conn=get_conn()
    rows=conn.execute("SELECT captured_at,view_count,like_count,comment_count FROM youtube_video_metrics_history WHERE channel_id=? AND youtube_video_id=? ORDER BY captured_at DESC LIMIT ?", (channel_id,youtube_video_id,int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

def upsert_remote_video(channel_id, youtube_video_id, title='', description='', published_at='', privacy_status='', tags=None, hash_marker='', statistics_json='{}', content_json='{}'):
    conn=get_conn(); now=now_iso()
    conn.execute("""
        INSERT INTO youtube_remote_videos
        (channel_id,youtube_video_id,title,description,published_at,privacy_status,tags,hash_marker,statistics_json,content_json,synced_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id,youtube_video_id) DO UPDATE SET
          title=excluded.title, description=excluded.description, published_at=excluded.published_at,
          privacy_status=excluded.privacy_status, tags=excluded.tags, hash_marker=excluded.hash_marker,
          statistics_json=excluded.statistics_json, content_json=excluded.content_json, synced_at=excluded.synced_at
    """, (channel_id,youtube_video_id,title or '',description or '',published_at or '',privacy_status or '',
          ", ".join(tags or []) if isinstance(tags,list) else (tags or ''), hash_marker or '', statistics_json or '{}', content_json or '{}', now))
    conn.commit(); row=conn.execute("SELECT * FROM youtube_remote_videos WHERE channel_id=? AND youtube_video_id=?",(channel_id,youtube_video_id)).fetchone(); conn.close(); return dict(row)

def list_remote_videos(channel_id=None, limit=5000):
    conn=get_conn()
    if channel_id:
        rows=conn.execute("SELECT * FROM youtube_remote_videos WHERE channel_id=? ORDER BY published_at DESC LIMIT ?",(channel_id,int(limit))).fetchall()
    else:
        rows=conn.execute("SELECT * FROM youtube_remote_videos ORDER BY published_at DESC LIMIT ?",(int(limit),)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def get_remote_video_by_hash(channel_id, hash_marker):
    conn=get_conn(); row=conn.execute("SELECT * FROM youtube_remote_videos WHERE channel_id=? AND hash_marker=?",(channel_id,hash_marker)).fetchone(); conn.close(); return dict(row) if row else None

def get_inventory_by_youtube_id(channel_id, youtube_video_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM video_inventory WHERE channel_id=? AND youtube_video_id=?",(channel_id,youtube_video_id)).fetchone(); conn.close(); return dict(row) if row else None

def count_users():
    conn=get_conn(); n=conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]; conn.close(); return int(n)

def get_user(username):
    conn=get_conn(); row=conn.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone(); conn.close(); return dict(row) if row else None

def create_user(username,password_hash,role="admin"):
    conn=get_conn(); now=now_iso(); conn.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",(username,password_hash,role,now)); conn.commit(); row=conn.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone(); conn.close(); return dict(row)

def update_user_password(username,password_hash):
    conn=get_conn(); conn.execute("UPDATE users SET password_hash=? WHERE username=?",(password_hash,username)); conn.commit(); conn.close()

def touch_user_login(username):
    conn=get_conn(); conn.execute("UPDATE users SET last_login_at=? WHERE username=?",(now_iso(),username)); conn.commit(); conn.close()

def list_users():
    conn=get_conn(); rows=conn.execute("SELECT id,username,role,created_at,last_login_at FROM users ORDER BY username").fetchall(); conn.close(); return [dict(r) for r in rows]

def list_ai_connections(provider=None):
    conn=get_conn()
    rows=conn.execute("SELECT * FROM ai_connections WHERE provider=? ORDER BY account_email", (provider,)).fetchall() if provider else conn.execute("SELECT * FROM ai_connections ORDER BY provider, account_email").fetchall()
    conn.close(); return [dict(r) for r in rows]

def get_ai_connection(connection_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM ai_connections WHERE id=?", (connection_id,)).fetchone(); conn.close(); return dict(row) if row else None

def upsert_ai_connection(provider, account_email, token_key, model='', project_id='', account_name='', client_secret_path=''):
    conn=get_conn(); now=now_iso()
    conn.execute("""
        INSERT INTO ai_connections(provider,account_name,account_email,token_key,client_secret_path,model,project_id,status,created_at,last_verified_at)
        VALUES(?,?,?,?,?,? ,?,'connected',?,?)
        ON CONFLICT(provider,account_email) DO UPDATE SET
            account_name=excluded.account_name, token_key=excluded.token_key, client_secret_path=excluded.client_secret_path,
            model=excluded.model, project_id=excluded.project_id, status='connected', last_verified_at=excluded.last_verified_at
    """, (provider,account_name or account_email,account_email,token_key,client_secret_path or '',model or '',project_id or '',now,now))
    conn.commit(); row=conn.execute("SELECT * FROM ai_connections WHERE provider=? AND account_email=?",(provider,account_email)).fetchone(); conn.close(); return dict(row)

def delete_ai_connection(connection_id):
    conn=get_conn(); conn.execute("DELETE FROM ai_connections WHERE id=?",(connection_id,)); conn.commit(); conn.close()

def list_schedules(channel_id=None):
    conn = get_conn()
    if channel_id:
        rows = conn.execute("SELECT * FROM upload_schedules WHERE channel_id=? ORDER BY weekday, time_local", (channel_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM upload_schedules ORDER BY channel_id, weekday, time_local").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_schedule(schedule_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM upload_schedules WHERE id=?", (int(schedule_id),)).fetchone()
    conn.close()
    return dict(row) if row else None

def list_meta_connections():
    conn=get_conn()
    rows=conn.execute("SELECT * FROM meta_connections ORDER BY account_name COLLATE NOCASE, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def list_meta_connections_for_account(account_id):
    conn=get_conn()
    rows=conn.execute("SELECT * FROM meta_connections WHERE account_id=? ORDER BY page_name COLLATE NOCASE", (int(account_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def list_unlinked_meta_connections():
    """Meta connections not yet assigned to any YouTube account."""
    conn=get_conn()
    rows=conn.execute("SELECT * FROM meta_connections WHERE account_id IS NULL ORDER BY page_name COLLATE NOCASE").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def set_meta_connection_account(connection_id, account_id):
    conn=get_conn()
    conn.execute("UPDATE meta_connections SET account_id=? WHERE id=?", (int(account_id), int(connection_id)))
    conn.commit(); conn.close()

def list_accounts_overview():
    """Return every YouTube account with its channels and Meta connections grouped."""
    conn=get_conn()
    accounts=[dict(r) for r in conn.execute("SELECT * FROM youtube_accounts ORDER BY account_name COLLATE NOCASE").fetchall()]
    for a in accounts:
        a["channels"]=[dict(r) for r in conn.execute("SELECT * FROM youtube_channels WHERE account_id=? ORDER BY channel_name COLLATE NOCASE",(a["id"],)).fetchall()]
        a["meta_connections"]=[dict(r) for r in conn.execute("SELECT * FROM meta_connections WHERE account_id=? ORDER BY page_name COLLATE NOCASE",(a["id"],)).fetchall()]
    conn.close()
    return accounts

def get_meta_connection(connection_id):
    conn=get_conn()
    row=conn.execute("SELECT * FROM meta_connections WHERE id=?", (int(connection_id),)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_meta_connection(account_name, app_id, page_id, page_name='', instagram_user_id='', instagram_username='', token_key='', last_verified_at='', account_id=None):
    conn=get_conn(); now=last_verified_at or now_iso()
    existing=conn.execute("SELECT id FROM meta_connections WHERE page_id=?", (page_id,)).fetchone()
    if existing:
        conn.execute("""UPDATE meta_connections SET account_name=?,app_id=?,page_name=?,instagram_user_id=?,instagram_username=?,token_key=?,status='connected',last_verified_at=?,account_id=COALESCE(?,account_id) WHERE id=?""",
                     (account_name,app_id,page_name,instagram_user_id,instagram_username,token_key,now,account_id,int(existing['id'])))
        cid=int(existing['id'])
    else:
        conn.execute("""INSERT INTO meta_connections(account_name,app_id,page_id,page_name,instagram_user_id,instagram_username,token_key,status,created_at,last_verified_at,account_id)
                       VALUES(?,?,?,?,?,?,?,'connected',?,?,?)""",
                     (account_name,app_id,page_id,page_name,instagram_user_id,instagram_username,token_key,now,now,account_id))
        cid=int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id'])
    conn.commit()
    row=conn.execute("SELECT * FROM meta_connections WHERE id=?",(cid,)).fetchone()
    conn.close()
    return dict(row)

def update_meta_connection_status(connection_id, status, last_verified_at=None):
    conn=get_conn()
    if last_verified_at:
        conn.execute("UPDATE meta_connections SET status=?,last_verified_at=? WHERE id=?",(status,last_verified_at,int(connection_id)))
    else:
        conn.execute("UPDATE meta_connections SET status=? WHERE id=?",(status,int(connection_id)))
    conn.commit(); conn.close()

def delete_meta_connection(connection_id):
    conn=get_conn(); conn.execute("DELETE FROM meta_connections WHERE id=?",(int(connection_id),)); conn.commit(); conn.close()


def database_health():
    conn = get_conn()
    checks = {}
    try:
        conn.execute("PRAGMA quick_check").fetchone()
        checks["quick_check"] = "ok"
        required = {
            "users", "youtube_accounts", "youtube_channels", "video_inventory",
            "youtube_remote_videos", "youtube_video_metrics_history",
            "upload_schedules", "upload_jobs", "upload_attempts", "audit_log",
            "ai_analysis_runs", "scheduler_runs", "app_meta"
        }
        existing = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        checks["missing_tables"] = sorted(required - existing)
        checks["schema_version"] = (conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone() or {"value": "unknown"})["value"]
        return {"ok": not checks["missing_tables"], **checks}
    finally:
        conn.close()

def upsert_schedule(channel_id, weekday, time_local, enabled=True, privacy_status="private", category_id="22",
                    title_template="{filename}", description_template="", tags="", made_for_kids=False,
                    ai_provider="gemini", ai_connection_id=None, contains_synthetic_media=True,
                    video_language="hi", title_description_language="hi", caption_certification="none",
                    recording_date="", video_location="India",
                    publish_facebook_video=False, publish_facebook_reel=False, publish_instagram_reel=False, meta_connection_id=None,
                    thumbnail_engine="flow", ai_secondary_connection_id=None):

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    if meta_connection_id is None:
        ch_meta = conn.execute("SELECT meta_connection_id FROM youtube_channels WHERE channel_id=?", (channel_id,)).fetchone()
        if ch_meta and ch_meta["meta_connection_id"]:
            meta_connection_id = ch_meta["meta_connection_id"]
            if not (publish_facebook_video or publish_facebook_reel or publish_instagram_reel):
                publish_facebook_video = True
                publish_facebook_reel = True
                m_conn = conn.execute("SELECT instagram_user_id, instagram_username FROM meta_connections WHERE id=?", (meta_connection_id,)).fetchone()
                if m_conn and (m_conn["instagram_user_id"] or m_conn["instagram_username"]):
                    publish_instagram_reel = True
    conn.execute("""
        INSERT INTO upload_schedules
        (channel_id,weekday,time_local,enabled,privacy_status,category_id,title_template,description_template,tags,made_for_kids,ai_provider,ai_connection_id,ai_secondary_connection_id,contains_synthetic_media,video_language,title_description_language,caption_certification,recording_date,video_location,publish_facebook_video,publish_facebook_reel,publish_instagram_reel,meta_connection_id,thumbnail_engine,last_run_date,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id,weekday,time_local) DO UPDATE SET
          enabled=excluded.enabled, privacy_status=excluded.privacy_status, category_id=excluded.category_id,
          title_template=excluded.title_template, description_template=excluded.description_template, tags=excluded.tags,
          made_for_kids=excluded.made_for_kids, ai_provider=excluded.ai_provider, ai_connection_id=excluded.ai_connection_id,
          ai_secondary_connection_id=excluded.ai_secondary_connection_id,
          contains_synthetic_media=excluded.contains_synthetic_media, video_language=excluded.video_language,
          title_description_language=excluded.title_description_language, caption_certification=excluded.caption_certification,
          recording_date=excluded.recording_date, video_location=excluded.video_location,
          publish_facebook_video=excluded.publish_facebook_video,
          publish_facebook_reel=excluded.publish_facebook_reel, publish_instagram_reel=excluded.publish_instagram_reel,
          meta_connection_id=excluded.meta_connection_id,
          thumbnail_engine=excluded.thumbnail_engine,
          updated_at=excluded.updated_at
    """, (channel_id,weekday,time_local,int(enabled),privacy_status,str(category_id),title_template,description_template,tags,int(made_for_kids),ai_provider,ai_connection_id,ai_secondary_connection_id,int(contains_synthetic_media),video_language,title_description_language,caption_certification,recording_date,video_location,int(publish_facebook_video),int(publish_facebook_reel),int(publish_instagram_reel),meta_connection_id,thumbnail_engine or 'flow','',now,now))
    conn.commit()
    row=conn.execute("SELECT * FROM upload_schedules WHERE channel_id=? AND weekday=? AND time_local=?",(channel_id,weekday,time_local)).fetchone()
    conn.close()
    return dict(row)

def replace_day_schedules(channel_id, weekday, slots):
    conn=get_conn()
    conn.execute("DELETE FROM upload_schedules WHERE channel_id=? AND weekday=?",(channel_id,weekday))
    conn.commit(); conn.close()
    rows=[]
    for slot in slots:
        rows.append(upsert_schedule(channel_id,weekday,slot["time_local"],slot.get("enabled",True),slot.get("privacy_status","private"),slot.get("category_id","22"),slot.get("title_template","{filename}"),slot.get("description_template",""),slot.get("tags",""),slot.get("made_for_kids",False),slot.get("ai_provider","gemini"),slot.get("ai_connection_id"),
                    slot.get("contains_synthetic_media",True),slot.get("video_language","hi"),slot.get("title_description_language","hi"),
                    slot.get("caption_certification","none"),slot.get("recording_date",""),slot.get("video_location","India"),
                    slot.get("publish_facebook_video",False),slot.get("publish_facebook_reel",False),slot.get("publish_instagram_reel",False),slot.get("meta_connection_id"),
                    slot.get("thumbnail_engine","flow"),slot.get("ai_secondary_connection_id")))
    return rows

def delete_schedule(channel_id, weekday, time_local=None):
    conn=get_conn()
    if time_local:
        conn.execute("DELETE FROM upload_schedules WHERE channel_id=? AND weekday=? AND time_local=?",(channel_id,weekday,time_local))
    else:
        conn.execute("DELETE FROM upload_schedules WHERE channel_id=? AND weekday=?",(channel_id,weekday))
    conn.commit(); conn.close()

def mark_schedule_run(schedule_id, run_date):
    from datetime import datetime, timezone
    conn=get_conn(); conn.execute("UPDATE upload_schedules SET last_run_date=?,updated_at=? WHERE id=?",(run_date,datetime.now(timezone.utc).isoformat(),schedule_id)); conn.commit(); conn.close()


def get_inventory_count(channel_id=None):
    conn = get_conn()
    if channel_id:
        row = conn.execute("SELECT COUNT(*) AS n FROM video_inventory WHERE channel_id=?", (channel_id,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM video_inventory").fetchone()
    conn.close()
    return int(row["n"] or 0)

def get_pending_videos(channel_id, limit=5000):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM video_inventory
        WHERE channel_id=? AND status='pending'
        ORDER BY modified_at ASC, filename COLLATE NOCASE ASC
        LIMIT ?
    """, (channel_id, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def list_inventory_paths(channel_id):
    conn = get_conn()
    rows = conn.execute("SELECT file_path, size_bytes, modified_at, sha256, status FROM video_inventory WHERE channel_id=?", (channel_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def stats(channel_id=None):
    conn = get_conn()
    params = []
    where = ""
    if channel_id:
        where = "WHERE channel_id=?"
        params = [channel_id]
    rows = conn.execute(f"""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
          SUM(CASE WHEN status='uploaded' THEN 1 ELSE 0 END) AS uploaded,
          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
          SUM(CASE WHEN status='duplicate' THEN 1 ELSE 0 END) AS duplicate
        FROM video_inventory {where}
    """, params).fetchone()
    conn.close()
    return {k: (v or 0) for k, v in dict(rows).items()}


def add_audit_log(username, action, resource='', resource_id='', details=None):
    import json
    conn=get_conn(); conn.execute("INSERT INTO audit_log(username,action,resource,resource_id,details_json,created_at) VALUES(?,?,?,?,?,?)",(username,action,resource,str(resource_id or ''),json.dumps(details or {},ensure_ascii=False),now_iso())); conn.commit(); conn.close()

def list_audit_log(limit=300):
    conn=get_conn(); rows=conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall(); conn.close(); return [dict(r) for r in rows]

def create_upload_job(video_id, channel_id, schedule_id=None, scheduled_at='', priority=100):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM upload_jobs WHERE video_id=? AND status IN ('pending', 'uploading', 'retry_pending', 'quota_paused') ORDER BY id DESC LIMIT 1",
        (video_id,)
    ).fetchone()
    if existing:
        jid = existing["id"]
        conn.close()
        return int(jid)
    now = now_iso()
    conn.execute(
        "INSERT INTO upload_jobs(video_id,channel_id,schedule_id,status,priority,scheduled_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (video_id, channel_id, schedule_id, 'pending', priority, scheduled_at, now, now)
    )
    jid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
    conn.commit()
    conn.close()
    return int(jid)

def get_upload_job(job_id):
    conn=get_conn(); row=conn.execute("SELECT * FROM upload_jobs WHERE id=?",(job_id,)).fetchone(); conn.close(); return dict(row) if row else None

def update_upload_job(job_id, **fields):
    if not fields: return get_upload_job(job_id)
    from datetime import datetime, timezone
    fields['updated_at']=datetime.now(timezone.utc).isoformat(); conn=get_conn(); keys=list(fields); conn.execute(f"UPDATE upload_jobs SET {', '.join(k+'=?' for k in keys)} WHERE id=?",[fields[k] for k in keys]+[job_id]); conn.commit(); row=conn.execute("SELECT * FROM upload_jobs WHERE id=?",(job_id,)).fetchone(); conn.close(); return dict(row) if row else None

def add_upload_attempt(job_id, attempt_no, status, error_message='', youtube_video_id='', started_at=None, finished_at=None):
    conn=get_conn(); conn.execute("INSERT INTO upload_attempts(job_id,attempt_no,started_at,finished_at,status,error_message,youtube_video_id) VALUES(?,?,?,?,?,?,?)",(job_id,attempt_no,started_at or now_iso(),finished_at or '',status,error_message,youtube_video_id)); conn.commit(); conn.close()

def list_upload_jobs(status=None, limit=200):
    conn=get_conn();
    if status: rows=conn.execute("SELECT * FROM upload_jobs WHERE status=? ORDER BY priority,scheduled_at,id LIMIT ?",(status,int(limit))).fetchall()
    else: rows=conn.execute("SELECT * FROM upload_jobs ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def record_scheduler_run(schedule_id, run_date, status, message=''):
    conn=get_conn(); now=now_iso(); conn.execute("INSERT INTO scheduler_runs(schedule_id,run_date,status,started_at,completed_at,message) VALUES(?,?,?,?,?,?) ON CONFLICT(schedule_id,run_date) DO UPDATE SET status=excluded.status,completed_at=excluded.completed_at,message=excluded.message",(schedule_id,run_date,status,now,now,message)); conn.commit(); conn.close()

def add_ai_analysis_run(video_id, provider, model, status, started_at, completed_at='', error_message=''):
    conn=get_conn(); conn.execute("INSERT INTO ai_analysis_runs(video_id,provider,model,status,started_at,completed_at,error_message) VALUES(?,?,?,?,?,?,?)",(video_id,provider,model,status,started_at,completed_at,error_message)); conn.commit(); conn.close()


def add_community_post(video_id, channel_id, platform, content_text, poll_json="[]", media_url="", external_post_id="", external_post_url="", status="published", error_message=""):
    conn = get_conn()
    now = now_iso()
    cursor = conn.execute(
        """INSERT INTO community_posts(video_id,channel_id,platform,post_type,content_text,poll_json,media_url,external_post_id,external_post_url,status,error_message,created_at,published_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (video_id, channel_id or "", platform, "teaser", content_text, poll_json, media_url, external_post_id, external_post_url, status, error_message, now, now)
    )
    conn.commit()
    post_id = cursor.lastrowid
    conn.close()
    return int(post_id)


def list_community_posts(video_id=None, limit=50):
    conn = get_conn()
    if video_id:
        rows = conn.execute("SELECT * FROM community_posts WHERE video_id=? ORDER BY id DESC LIMIT ?", (video_id, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM community_posts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

