"""
Failure Tracker & Auto-Recovery Agent for Video Uploads.

Diagnoses failed YouTube & Meta uploads, categorizes root causes,
tracks resolution state, and automatically re-queues videos once
the underlying issue (expired token, daily quota, network drop, etc.)
has been resolved.
"""

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import BASE_DIR
from .database import (
    get_conn, get_upload_job, update_upload_job, get_inventory_by_id,
    update_video, get_channel, list_upload_jobs, add_upload_attempt
)

log = logging.getLogger(__name__)

# Category constants
CAT_TOKEN_EXPIRED = "TOKEN_EXPIRED"
CAT_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
CAT_META_PERMISSION = "META_PERMISSION"
CAT_NETWORK_TRANSIENT = "NETWORK_TRANSIENT"
CAT_AI_METADATA = "AI_METADATA_ERROR"
CAT_FILE_MISSING = "FILE_MISSING"
CAT_OTHER = "OTHER"


def classify_error(error_msg: str) -> Dict[str, Any]:
    """Classify an error message into actionable categories and suggestions."""
    err = str(error_msg or "").strip().lower()

    if not err:
        return {
            "category": CAT_OTHER,
            "title": "Unknown Error",
            "root_cause": "No detailed error message was recorded.",
            "suggested_action": "Check application logs for details and retry.",
            "auto_recoverable": True,
        }

    # 1. Token / Auth failures
    if any(k in err for k in [
        "invalid_grant", "expired or revoked", "token has been expired",
        "unauthorized", "oauth token is unauthorized", "reauth_required",
        "401", "token file lacks a refresh_token", "invalid_client",
        "login_required", "access_denied"
    ]):
        return {
            "category": CAT_TOKEN_EXPIRED,
            "title": "Account Token Expired / Revoked",
            "root_cause": "The OAuth access or refresh token is no longer valid or has expired.",
            "suggested_action": "Tokens are refreshed automatically by Credential Vault. If revoked, re-authenticate the account in Connections.",
            "auto_recoverable": True,
        }

    # 2. Daily Quota / Rate limit failures
    if any(k in err for k in [
        "uploadlimitexceeded", "quotaexceeded", "rate_limit", "429",
        "exceeded your current quota", "resource_exhausted",
        "daily upload limit", "user_rate_limit_exceeded",
        "quota_paused", "quota limit approaching", "daily youtube quota"
    ]):
        return {
            "category": CAT_QUOTA_EXCEEDED,
            "title": "Daily Quota / Rate Limit Exceeded",
            "root_cause": "YouTube API daily upload limit (10,000 units/day) or Gemini rate limit was hit.",
            "suggested_action": "Wait for daily quota reset at midnight Pacific Time (07:00 UTC). The system will auto-retry.",
            "auto_recoverable": True,
        }

    # 3. Meta / Facebook / Instagram permission failures
    if any(k in err for k in [
        "pages_read_engagement", "pages_manage_posts", "permission",
        "oauthexception", "(#200)", "subject does not have permission",
        "instagram_basic", "instagram_content_publish"
    ]):
        return {
            "category": CAT_META_PERMISSION,
            "title": "Meta Page Permission Missing",
            "root_cause": "Meta user or page lacks permissions required to post videos (e.g. pages_read_engagement).",
            "suggested_action": "Log in to Meta Business Suite / App settings and grant 'pages_read_engagement' and 'pages_manage_posts'.",
            "auto_recoverable": False,
        }

    # 4. Missing video or thumbnail files
    if any(k in err for k in [
        "file no longer exists", "no such file or directory", "file not found",
        "missing video file", "video file missing"
    ]):
        return {
            "category": CAT_FILE_MISSING,
            "title": "Video File Missing",
            "root_cause": "The local video file path cannot be found on disk.",
            "suggested_action": "Verify external drive connection or restore the video file to its configured folder.",
            "auto_recoverable": False,
        }

    # 5. Network / Timeout transient errors
    if any(k in err for k in [
        "connectionreseterror", "connection refused", "timed out", "timeout",
        "ssl", "eof occurred", "502", "503", "504", "server disconnected",
        "remote end closed", "broken pipe", "socket.timeout"
    ]):
        return {
            "category": CAT_NETWORK_TRANSIENT,
            "title": "Network / Connection Drop",
            "root_cause": "A transient internet disconnection, network timeout, or remote gateway error occurred.",
            "suggested_action": "Ensure stable internet connection. The system will automatically retry.",
            "auto_recoverable": True,
        }

    # 6. AI Metadata Generation Failures
    if any(k in err for k in [
        "gemini", "chatgpt", "claude", "ai retry failed", "analysis failed",
        "failed to parse ai response", "403 forbidden"
    ]):
        return {
            "category": CAT_AI_METADATA,
            "title": "AI Metadata Generation Failed",
            "root_cause": "AI model (Gemini/ChatGPT/Claude) encountered an error generating title/description.",
            "suggested_action": "Verify AI account connection or fill in title/description manually in Video Library.",
            "auto_recoverable": True,
        }

    return {
        "category": CAT_OTHER,
        "title": "Upload Error",
        "root_cause": error_msg[:250],
        "suggested_action": "Review error details and retry upload.",
        "auto_recoverable": True,
    }


def is_issue_resolved(category: str, channel_id: str, video_id: Optional[int] = None, last_error_time: str = "") -> Tuple[bool, str]:
    """Check whether the underlying issue that caused a failure is currently resolved."""

    # 1. Check Token Health
    if category == CAT_TOKEN_EXPIRED:
        try:
            from .auth import load_credentials_for_channel
            creds = load_credentials_for_channel(channel_id)
            if not creds:
                return False, "Channel credentials file not found"
            if not creds.refresh_token:
                return False, "No refresh token available; re-auth required"

            # Check if token is valid or can refresh
            if creds.valid:
                return True, "Channel token is active and valid"

            from google.auth.transport.requests import Request
            req = Request()
            creds.refresh(req)
            if creds.valid:
                return True, "Channel token successfully refreshed"
        except Exception as exc:
            return False, f"Token validation failed: {exc}"
        return False, "Token could not be validated"

    # 2. Check Daily Quota
    if category == CAT_QUOTA_EXCEEDED:
        try:
            from .quota_tracker import can_perform_upload
            can_up, q_summary = can_perform_upload()
            if can_up:
                return True, f"Daily YouTube API quota has reset ({q_summary['used_units']:,} / {q_summary['max_units']:,} units used, ready for uploads)"
        except Exception:
            pass
        # YouTube quota resets at midnight Pacific Time (approx 07:00 UTC)
        if last_error_time:
            try:
                err_dt = datetime.fromisoformat(last_error_time.replace("Z", "+00:00")).astimezone(timezone.utc)
                now_utc = datetime.now(timezone.utc)
                # If error was on a different UTC calendar day and now past 07:00 UTC, quota has reset
                if (now_utc.date() > err_dt.date()) and now_utc.hour >= 7:
                    return True, "Daily YouTube API quota has reset (midnight PT passed)"
                # If more than 24 hours have passed regardless
                if (now_utc - err_dt).total_seconds() > 86400:
                    return True, "24-hour quota reset window has elapsed"
            except Exception:
                pass
        return False, "Waiting for daily quota reset at midnight Pacific Time"

    # 3. Check Network Transient
    if category == CAT_NETWORK_TRANSIENT:
        # After 5 minutes, consider network ready for retry
        if last_error_time:
            try:
                err_dt = datetime.fromisoformat(last_error_time.replace("Z", "+00:00")).astimezone(timezone.utc)
                if (datetime.now(timezone.utc) - err_dt).total_seconds() >= 300:
                    return True, "Network cool-off period elapsed; ready to retry"
            except Exception:
                pass
        return True, "Ready to test connection and retry"

    # 4. Check Missing File
    if category == CAT_FILE_MISSING and video_id:
        item = get_inventory_by_id(video_id)
        if item and item.get("file_path"):
            from .thumbnail import resolve_local_video_path
            resolved = resolve_local_video_path(item["file_path"])
            if resolved and Path(resolved).is_file():
                return True, "Video file is now accessible on disk"
        return False, "Video file is still missing from disk"

    # 5. Check AI Metadata
    if category == CAT_AI_METADATA and video_id:
        item = get_inventory_by_id(video_id)
        if item and item.get("title") and item.get("ai_status") == "completed":
            return True, "Video has completed title and description metadata"
        conn = get_conn()
        recent_err = conn.execute("SELECT last_error FROM upload_jobs WHERE video_id=? ORDER BY id DESC LIMIT 1", (video_id,)).fetchone()
        conn.close()
        last_err_text = (recent_err["last_error"] if recent_err else "").lower()
        if any(w in last_err_text for w in ("denied access", "project has been denied", "403 forbidden", "api key not valid")):
            return False, "AI project denied access (403). Requires reconnecting a valid account or switching provider."

        # Check if AI provider has a connected account
        from .database import list_ai_connections
        conns = [c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
        if conns:
            return True, "Gemini AI connection is healthy; ready to retry metadata analysis"
        return False, "No active AI connection found"

    # Other / Manual
    return True, "Ready for retry"


def diagnose_job(job_id: int) -> Dict[str, Any]:
    """Provide a comprehensive diagnostic on a failed or retryable job."""
    job = get_upload_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    item = get_inventory_by_id(job.get("video_id")) if job.get("video_id") else None
    channel = get_channel(job.get("channel_id")) if job.get("channel_id") else None

    last_error = job.get("last_error") or (item.get("error_message") if item else "")
    classification = classify_error(last_error)

    resolved, resolve_reason = is_issue_resolved(
        category=classification["category"],
        channel_id=job.get("channel_id"),
        video_id=job.get("video_id"),
        last_error_time=job.get("updated_at") or job.get("completed_at") or ""
    )

    return {
        "job_id": job["id"],
        "video_id": job.get("video_id"),
        "channel_id": job.get("channel_id"),
        "channel_name": (channel or {}).get("channel_name", ""),
        "filename": (item or {}).get("filename", ""),
        "file_path": (item or {}).get("file_path", ""),
        "job_status": job.get("status"),
        "attempt_count": job.get("attempt_count", 0),
        "last_error": last_error,
        "category": classification["category"],
        "category_title": classification["title"],
        "root_cause": classification["root_cause"],
        "suggested_action": classification["suggested_action"],
        "is_resolved": resolved,
        "resolve_reason": resolve_reason,
        "can_retry": resolved or job.get("status") in {"failed", "retry_pending", "cancelled"},
        "updated_at": job.get("updated_at"),
    }


def list_failed_diagnostics(limit: int = 100) -> List[Dict[str, Any]]:
    """List all failed and retry_pending jobs with detailed diagnostic analysis."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT j.*, i.filename, i.file_path, i.status AS video_status,
               i.error_message AS video_error, c.channel_name
        FROM upload_jobs j
        LEFT JOIN video_inventory i ON j.video_id = i.id
        LEFT JOIN youtube_channels c ON j.channel_id = c.channel_id
        WHERE j.status IN ('failed', 'retry_pending')
        ORDER BY j.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    diagnostics = []
    for r in rows:
        d = dict(r)
        last_error = d.get("last_error") or d.get("video_error") or ""
        classification = classify_error(last_error)
        resolved, resolve_reason = is_issue_resolved(
            category=classification["category"],
            channel_id=d.get("channel_id"),
            video_id=d.get("video_id"),
            last_error_time=d.get("updated_at") or d.get("completed_at") or ""
        )
        diagnostics.append({
            "job_id": d["id"],
            "video_id": d.get("video_id"),
            "channel_id": d.get("channel_id"),
            "channel_name": d.get("channel_name") or d.get("channel_id"),
            "filename": d.get("filename") or f"video_{d.get('video_id')}",
            "job_status": d.get("status"),
            "attempt_count": d.get("attempt_count", 0),
            "last_error": last_error,
            "category": classification["category"],
            "category_title": classification["title"],
            "root_cause": classification["root_cause"],
            "suggested_action": classification["suggested_action"],
            "is_resolved": resolved,
            "resolve_reason": resolve_reason,
            "updated_at": d.get("updated_at"),
        })

    return diagnostics


def retry_job_safely(job_id: int) -> Dict[str, Any]:
    """
    Safely reset a failed job and its corresponding video inventory record
    for immediate re-processing.
    """
    job = get_upload_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    # Reset upload_job
    update_upload_job(
        job_id,
        status="retry_pending",
        attempt_count=0,
        last_error=""
    )

    # Reset video_inventory status
    if job.get("video_id"):
        update_video(
            job["video_id"],
            status="pending",
            error_message=""
        )

    log.info("[AutoRecovery] Job #%d (video #%s) reset to retry_pending", job_id, job.get("video_id"))
    return {"ok": True, "job_id": job_id, "status": "retry_pending"}


def reconcile_failed_jobs() -> Dict[str, Any]:
    """
    Scans failed jobs and automatically re-queues those whose blocking
    issues (e.g. expired token now refreshed, quota reset, file restored)
    have been resolved.
    """
    conn = get_conn()
    failed_rows = conn.execute("""
        SELECT id, video_id, channel_id, last_error, updated_at
        FROM upload_jobs
        WHERE status = 'failed'
        ORDER BY id ASC
        LIMIT 50
    """).fetchall()
    conn.close()

    recovered = []
    blocked = []

    for r in failed_rows:
        job_id = r["id"]
        channel_id = r["channel_id"]
        video_id = r["video_id"]
        last_error = r["last_error"] or ""

        classification = classify_error(last_error)
        resolved, reason = is_issue_resolved(
            category=classification["category"],
            channel_id=channel_id,
            video_id=video_id,
            last_error_time=r["updated_at"] or ""
        )

        if resolved:
            retry_job_safely(job_id)
            recovered.append({
                "job_id": job_id,
                "video_id": video_id,
                "category": classification["category"],
                "reason": reason
            })
            log.info("[AutoRecovery] Auto-recovered job #%d: %s", job_id, reason)
        else:
            blocked.append({
                "job_id": job_id,
                "category": classification["category"],
                "reason": reason
            })

    return {
        "scanned": len(failed_rows),
        "recovered_count": len(recovered),
        "recovered_jobs": recovered,
        "blocked_count": len(blocked),
        "blocked_jobs": blocked,
    }
