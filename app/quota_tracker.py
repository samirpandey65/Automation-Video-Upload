"""
Proactive Quota Tracker & Rate Limit Daemon for OmniCast Studio.
Monitors daily YouTube Data API v3 consumption (10,000 units/day default project quota)
and proactively pauses automated uploads before hitting the hard 403 uploadLimitExceeded wall.
Resets automatically at midnight Pacific Time (PT).
"""

import logging
import os
from datetime import datetime, timezone, timedelta
try:
    import zoneinfo
    PT_TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
except Exception:
    PT_TZ = timezone(timedelta(hours=-7))  # Fallback PDT offset

from .config import DB_PATH
from .database import get_conn

log = logging.getLogger(__name__)

# Standard YouTube Data API v3 Quota Costs
COST_VIDEO_UPLOAD = 1600
COST_THUMBNAIL_SET = 50
COST_PLAYLIST_ITEM = 50
COST_PLAYLIST_CREATE = 50
COST_VIDEO_UPDATE = 50
COST_SEARCH = 100
COST_READ_OPERATION = 1

DEFAULT_MAX_DAILY_QUOTA = int(os.getenv("YLA_MAX_DAILY_QUOTA", "10000"))
DEFAULT_SAFETY_BUFFER = int(os.getenv("YLA_QUOTA_SAFETY_BUFFER", "200"))

_LAST_QUOTA_ALERT_DATE = None


def get_pacific_now():
    """Returns the current datetime in America/Los_Angeles timezone."""
    return datetime.now(PT_TZ)


def get_pacific_date_str():
    """Returns current date string in Pacific Time YYYY-MM-DD."""
    return get_pacific_now().date().isoformat()


def get_next_midnight_pt():
    """Calculates the upcoming midnight Pacific Time (quota reset moment)."""
    pt_now = get_pacific_now()
    next_midnight = (pt_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight


def ensure_quota_table():
    """Ensures api_quota_logs table exists in SQLite database."""
    try:
        conn = get_conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_quota_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL DEFAULT 'youtube',
            operation TEXT NOT NULL,
            units_used INTEGER NOT NULL DEFAULT 1,
            channel_id TEXT DEFAULT '',
            account_name TEXT DEFAULT '',
            date_pt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            details TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_quota_date ON api_quota_logs(service, date_pt);
        """)
        conn.close()
    except Exception as e:
        log.warning("[QUOTA] ensure_quota_table error: %s", e)


def record_api_quota(service="youtube", operation="videos.insert", units=1600, channel_id="", details="", account_name=""):
    """
    Records an API quota usage event for the current Pacific day.
    """
    ensure_quota_table()
    date_pt = get_pacific_date_str()
    now_utc = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_conn()
        conn.execute("""
            INSERT INTO api_quota_logs (service, operation, units_used, channel_id, account_name, date_pt, created_at, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (service, operation, units, channel_id or "", account_name or "", date_pt, now_utc, details or ""))
        conn.commit()
        conn.close()
        log.info("[QUOTA] Recorded %d units for %s:%s (channel: %s)", units, service, operation, channel_id)
    except Exception as e:
        log.warning("[QUOTA] Failed to record quota event: %s", e)


def get_daily_quota_summary(service="youtube", channel_id=None):
    """
    Computes quota consumption for today (Pacific Time).
    When channel_id is provided, scopes quota usage and ceiling to the channel's
    specific YouTube Account (Google Cloud Project) rather than pooling all accounts.
    """
    ensure_quota_table()
    date_pt = get_pacific_date_str()
    pt_now = get_pacific_now()
    next_midnight = get_next_midnight_pt()
    seconds_remaining = (next_midnight - pt_now).total_seconds()
    hours_remaining = round(max(0.0, seconds_remaining / 3600.0), 1)

    logged_units = 0
    breakdown = {}
    completed_today = 0
    account_id = None

    try:
        from .database import get_channel, list_channels_for_account, list_youtube_accounts, get_app_setting

        target_channels = []
        if channel_id:
            ch = get_channel(channel_id)
            if ch:
                account_id = ch.get("account_id")
                if account_id:
                    acct_channels = list_channels_for_account(account_id)
                    target_channels = [c["channel_id"] for c in acct_channels]
                else:
                    target_channels = [channel_id]

        conn = get_conn()

        if target_channels:
            q_marks = ",".join("?" for _ in target_channels)
            rows = conn.execute(f"""
                SELECT operation, SUM(units_used) as total_units, COUNT(*) as cnt
                FROM api_quota_logs
                WHERE service = ? AND date_pt = ? AND channel_id IN ({q_marks})
                GROUP BY operation
            """, [service, date_pt] + target_channels).fetchall()
        else:
            rows = conn.execute("""
                SELECT operation, SUM(units_used) as total_units, COUNT(*) as cnt
                FROM api_quota_logs
                WHERE service = ? AND date_pt = ?
                GROUP BY operation
            """, (service, date_pt)).fetchall()

        for r in rows:
            op = r["operation"]
            u = r["total_units"] or 0
            logged_units += u
            breakdown[op] = u

        pt_start_utc = pt_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        if target_channels:
            q_marks = ",".join("?" for _ in target_channels)
            job_rows = conn.execute(f"""
                SELECT COUNT(*) as completed_count
                FROM upload_jobs
                WHERE status = 'completed' AND completed_at >= ? AND channel_id IN ({q_marks})
            """, [pt_start_utc] + target_channels).fetchone()
        else:
            job_rows = conn.execute("""
                SELECT COUNT(*) as completed_count
                FROM upload_jobs
                WHERE status = 'completed' AND completed_at >= ?
            """, (pt_start_utc,)).fetchone()

        completed_today = job_rows["completed_count"] if job_rows else 0
        min_video_units = completed_today * (COST_VIDEO_UPLOAD + COST_THUMBNAIL_SET)
        effective_used = max(logged_units, min_video_units)
        conn.close()

        num_accounts = len(list_youtube_accounts() or [1])
        base_quota = int(os.getenv("YLA_MAX_DAILY_QUOTA", "10000"))
        if account_id:
            custom_quota = get_app_setting(f"max_daily_quota_{account_id}") or get_app_setting("max_daily_quota")
            max_quota = int(custom_quota) if custom_quota and str(custom_quota).isdigit() else base_quota
        else:
            custom_quota = get_app_setting("max_daily_quota")
            if custom_quota and str(custom_quota).isdigit():
                max_quota = int(custom_quota)
            else:
                max_quota = base_quota * max(1, num_accounts)
    except Exception as e:
        log.warning("[QUOTA] Error calculating quota summary: %s", e)
        effective_used = 0
        completed_today = 0
        max_quota = DEFAULT_MAX_DAILY_QUOTA

    safety_buffer = DEFAULT_SAFETY_BUFFER
    safe_ceiling = max_quota - safety_buffer
    remaining = max(0, max_quota - effective_used)
    est_uploads_remaining = max(0, int((safe_ceiling - effective_used) // 1700))
    can_upload = (effective_used + COST_VIDEO_UPLOAD) <= safe_ceiling

    return {
        "service": service,
        "date_pt": date_pt,
        "used_units": effective_used,
        "max_units": max_quota,
        "safe_ceiling": safe_ceiling,
        "remaining_units": remaining,
        "safety_buffer": safety_buffer,
        "used_percentage": round(min(100.0, (effective_used / max(1, max_quota)) * 100), 1),
        "can_upload": can_upload,
        "videos_uploaded_today": completed_today,
        "est_uploads_remaining": est_uploads_remaining,
        "reset_hours_remaining": hours_remaining,
        "reset_moment_utc": next_midnight.astimezone(timezone.utc).isoformat(),
        "operations_breakdown": breakdown,
        "account_id": account_id,
        "channel_id": channel_id,
    }


def can_perform_upload(channel_id=None, estimated_cost=COST_VIDEO_UPLOAD):
    """
    Proactive Gate: Determines whether an automated video upload should proceed
    or be paused until the Pacific Time midnight quota reset.
    Returns: (can_proceed: bool, summary_dict: dict)
    """
    summary = get_daily_quota_summary(service="youtube", channel_id=channel_id)
    projected_use = summary["used_units"] + estimated_cost

    if projected_use > summary["safe_ceiling"]:
        acct_label = f" for channel {channel_id}" if channel_id else ""
        reason = (
            f"Daily YouTube quota limit approaching{acct_label} ({summary['used_units']:,} / {summary['max_units']:,} units used). "
            f"Upload safely held to prevent 403 uploadLimitExceeded Google errors. "
            f"Resets at midnight Pacific Time in {summary['reset_hours_remaining']} hours."
        )
        summary["rejection_reason"] = reason
        return False, summary

    return True, summary


def notify_quota_paused_if_needed(summary):
    """
    Sends an alerting notification via Telegram when daily quota threshold is hit.
    Throttled to at most once per calendar day (Pacific Time).
    """
    global _LAST_QUOTA_ALERT_DATE
    today_pt = summary.get("date_pt")
    if _LAST_QUOTA_ALERT_DATE == today_pt:
        return  # Already alerted for today's quota window

    _LAST_QUOTA_ALERT_DATE = today_pt
    try:
        from .telegram import send_telegram_sync
        msg = (
            "⚠️ <b>OmniCast Proactive Quota Tracker</b>\n\n"
            f"Daily YouTube API quota limit approaching: <b>{summary['used_units']:,} / {summary['max_units']:,} units used</b> "
            f"({summary['used_percentage']}%).\n\n"
            f"⏳ <b>Upload Queue Paused:</b> Paused to prevent Google API 403 lockouts.\n"
            f"🔄 <b>Next Quota Reset:</b> In ~{summary['reset_hours_remaining']} hours (Midnight Pacific Time / ~07:00 UTC).\n"
            "The system will automatically resume queued uploads as soon as the quota resets."
        )
        send_telegram_sync(msg)
        log.info("[QUOTA] Sent daily quota alert notification via Telegram.")
    except Exception as e:
        log.warning("[QUOTA] Could not send Telegram quota alert: %s", e)
