"""
Automated 7-15 Day Video Performance Auditor & Metadata Optimizer
------------------------------------------------------------------
Continuously audits uploaded videos that have been live on YouTube for at least
7 to 15 days. If a video is underperforming (low view velocity or flatlined engagement),
this engine:
1. Audits current metrics via YouTube Data API (view count, likes, engagement rate).
2. Uses Gemini AI to diagnose why the video failed to hook viewers (weak title, low CTR, missing keywords).
3. Automatically generates an improved title, SEO description, high-intent tags/hashtags.
4. Generates fresh high-CTR thumbnail variants matching the video aspect ratio (16:9 or 9:16).
5. Rotates and updates the YouTube video metadata & thumbnail seamlessly via YouTube API.
6. Records complete audit & optimization history with A/B test variations in the database.
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import BASE_DIR, THUMBNAILS_DIR
from .database import (
    get_conn, get_channel, get_inventory_by_id, update_video,
    add_remote_metrics_snapshot, list_channels, list_ai_connections,
    get_app_setting, set_app_setting
)
from .youtube import get_video_stats, update_video_metadata, upload_thumbnail_to_youtube
from .thumbnail import generate_thumbnails, generate_ab_test_titles, resolve_local_video_path, _clean_title_hook
from .gemini import analyze as analyze_gemini

log = logging.getLogger(__name__)

# Configurable thresholds
AUDIT_MIN_HOURS = 48          # Minimum hours live before first 48-hour split test check (2 days)
AUDIT_INTERVAL_HOURS = 48     # Re-evaluate / rotate every 48 hours for first 14 days
MAX_AUTO_OPTIMIZATIONS = 7    # Supports up to 7 48-hour rotation cycles across 14 days
UNDERPERFORMING_VIEW_THRESHOLD = 50 # Default baseline threshold for videos older than 48 hours

# Pacing & Rate-limiting controls
DEFAULT_PACING_GAP_SECONDS = 1.5   # Pacing gap between optimizations (1 to 2 seconds)
DEFAULT_MAX_PER_RUN = 1            # Strictly 1 video per channel per run to prevent multi-video changes
DEFAULT_CHANNEL_COOLDOWN_HOURS = 2.0  # Mandatory cooldown: wait at least 2 hours per channel before next update

# Operating time window for video optimizer (Daytime only: 8:00 AM to 9:00 PM)
# Video details are NEVER updated during night hours (after 9:00 PM until 8:00 AM)
OPTIMIZER_START_HOUR = 8      # 8:00 AM (Daytime start)
OPTIMIZER_END_HOUR = 21       # 9:00 PM (Nighttime start)


def get_optimizer_pacing_gap() -> float:
    try:
        val = get_app_setting("optimizer_gap_seconds")
        if val is not None:
            gap = float(val)
            if gap >= 0.5:
                return gap
    except Exception:
        pass
    return DEFAULT_PACING_GAP_SECONDS


def get_optimizer_batch_limit() -> int:
    try:
        val = get_app_setting("optimizer_batch_limit")
        if val is not None and str(val).strip().isdigit():
            limit = int(val)
            if limit > 0:
                return limit
    except Exception:
        pass
    return DEFAULT_MAX_PER_RUN


def get_channel_cooldown_hours() -> float:
    try:
        val = get_app_setting("optimizer_channel_cooldown_hours")
        if val is not None:
            hrs = float(val)
            if hrs >= 0.1:
                return hrs
    except Exception:
        pass
    return DEFAULT_CHANNEL_COOLDOWN_HOURS


def get_channel_optimization_cooldown_status(channel_id: str) -> dict:
    """
    Checks if a channel is in its mandatory 2-hour cooldown period.
    Rule: Update 1 video, then wait for at least 2 hours per channel.
    """
    if not channel_id:
        return {"in_cooldown": False, "remaining_seconds": 0, "remaining_minutes": 0, "last_run": None}

    last_run_iso = get_app_setting(f"opt_last_run_{channel_id}")
    if not last_run_iso:
        return {"in_cooldown": False, "remaining_seconds": 0, "remaining_minutes": 0, "last_run": None}

    try:
        last_dt = datetime.fromisoformat(last_run_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        elapsed_seconds = (now_utc - last_dt).total_seconds()
        required_cooldown = get_channel_cooldown_hours() * 3600.0

        if elapsed_seconds < required_cooldown:
            rem_sec = round(required_cooldown - elapsed_seconds)
            rem_min = round(rem_sec / 60.0, 1)
            return {
                "in_cooldown": True,
                "remaining_seconds": rem_sec,
                "remaining_minutes": rem_min,
                "last_run": last_run_iso
            }
    except Exception as e:
        log.warning("[OPTIMIZER] Failed to parse cooldown for channel %s: %s", channel_id, e)

    return {"in_cooldown": False, "remaining_seconds": 0, "remaining_minutes": 0, "last_run": last_run_iso}


def record_channel_optimization(channel_id: str):
    """Records the timestamp of the 1 video optimization for a channel to enforce the 2-hour cooldown."""
    if not channel_id:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        set_app_setting(f"opt_last_run_{channel_id}", now_iso)
        log.info("[OPTIMIZER] Channel %s optimization recorded at %s. 2-hour cooldown started.", channel_id, now_iso)
    except Exception as e:
        log.warning("[OPTIMIZER] Could not save opt_last_run_%s: %s", channel_id, e)


def get_optimizer_operating_window() -> dict:
    """
    Returns the configured operating time window and current status.
    Default: 8:00 AM to 9:00 PM (daytime only).
    """
    start_hour = OPTIMIZER_START_HOUR
    end_hour = OPTIMIZER_END_HOUR

    try:
        s_val = get_app_setting("optimizer_start_hour")
        if s_val and s_val.isdigit():
            start_hour = int(s_val)
        e_val = get_app_setting("optimizer_end_hour")
        if e_val and e_val.isdigit():
            end_hour = int(e_val)
    except Exception:
        pass

    now_local = datetime.now()
    cur_hour = now_local.hour
    is_daytime = (start_hour <= cur_hour < end_hour)

    start_str_12h = datetime.strptime(f"{start_hour:02d}:00", "%H:%M").strftime("%I:%M %p").lstrip("0")
    end_str_12h = datetime.strptime(f"{end_hour:02d}:00", "%H:%M").strftime("%I:%M %p").lstrip("0")

    gap_sec = get_optimizer_pacing_gap()
    batch_lim = get_optimizer_batch_limit()
    cooldown_hrs = get_channel_cooldown_hours()
    auto_enabled = (get_app_setting("enable_auto_optimizer") or "0") == "1"

    return {
        "start_hour": start_hour,
        "end_hour": end_hour,
        "start_time_str": f"{start_hour:02d}:00",
        "end_time_str": f"{end_hour:02d}:00",
        "start_time_12h": start_str_12h,
        "end_time_12h": end_str_12h,
        "is_daytime": is_daytime,
        "current_local_hour": cur_hour,
        "current_local_time": now_local.strftime("%I:%M %p").lstrip("0"),
        "current_local_time_full": now_local.strftime("%Y-%m-%d %I:%M:%S %p"),
        "gap_seconds": gap_sec,
        "batch_limit": batch_lim,
        "channel_cooldown_hours": cooldown_hrs,
        "auto_optimizer_enabled": auto_enabled,
    }


def is_optimizer_time_window_active(allow_night: bool = False) -> tuple[bool, str]:
    """
    Check if the current local time falls within the allowed daytime window (8:00 AM - 9:00 PM).
    Video details (titles, descriptions, thumbnails, tags) must NOT be updated during night hours (after 9:00 PM until 8:00 AM).
    """
    if allow_night:
        return True, "Night override enabled."

    win = get_optimizer_operating_window()
    if win["is_daytime"]:
        return True, f"Daytime operating window active ({win['start_time_12h']} - {win['end_time_12h']})."

    msg = (
        f"Video optimizer is paused during night hours ({win['end_time_12h']} - {win['start_time_12h']}). "
        f"Video details can only be updated during daytime hours ({win['start_time_12h']} - {win['end_time_12h']}). "
        f"Current time is {win['current_local_time']}. Updates will resume after {win['start_time_12h']}."
    )
    return False, msg


def parse_iso_datetime(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def audit_and_optimize_video(video_row, channel_id, force=False, allow_night=False):
    """
    Audit an individual uploaded video and optimize its metadata + thumbnail if underperforming.
    Enforces the daytime operating window (8:00 AM - 9:00 PM) unless allow_night is explicitly True.
    Returns dict with audit results.
    """
    video_id = video_row["id"]
    yt_vid_id = video_row.get("youtube_video_id")
    if not yt_vid_id:
        return {"status": "skipped", "reason": "No YouTube video ID"}

    # Time window enforcement: Only update video details during daytime (8:00 AM - 9:00 PM)
    is_active, window_msg = is_optimizer_time_window_active(allow_night=allow_night)
    if not is_active:
        win = get_optimizer_operating_window()
        log.info(
            "[OPTIMIZER] Video %s optimization deferred: Outside daytime operating window (Current: %s | Window: %s - %s)",
            yt_vid_id, win["current_local_time"], win["start_time_12h"], win["end_time_12h"]
        )
        return {
            "status": "outside_daytime_window",
            "reason": window_msg,
            "current_time": win["current_local_time"],
            "operating_window": f"{win['start_time_12h']} - {win['end_time_12h']}",
            "resumes_at": win["start_time_12h"]
        }

    now_utc = datetime.now(timezone.utc)

    # Check how long the video has been uploaded
    created_at = parse_iso_datetime(video_row.get("created_at") or video_row.get("updated_at"))
    if not created_at:
        created_at = now_utc

    hours_live = (now_utc - created_at).total_seconds() / 3600.0
    days_live = hours_live / 24.0
    if hours_live < AUDIT_MIN_HOURS and not force:
        return {
            "status": "too_recent",
            "hours_live": round(hours_live, 1),
            "days_live": round(days_live, 1),
            "reason": f"Video is {hours_live:.1f} hours old (minimum {AUDIT_MIN_HOURS} hours required for split testing)"
        }

    # Check when the last performance check / optimization occurred
    last_check_str = video_row.get("last_performance_check_at") or ""
    if last_check_str and not force:
        last_check = parse_iso_datetime(last_check_str)
        if last_check:
            hours_since_check = (now_utc - last_check).total_seconds() / 3600.0
            if hours_since_check < AUDIT_INTERVAL_HOURS:
                return {
                    "status": "already_evaluated",
                    "hours_since_last_check": round(hours_since_check, 1),
                    "reason": f"Last split test / performance rotation was {hours_since_check:.1f} hours ago (next cycle at 48 hours)"
                }

    # Fetch fresh stats from YouTube Data API
    try:
        yt_item = get_video_stats(channel_id, yt_vid_id)
        stats = yt_item.get("statistics", {})
        add_remote_metrics_snapshot(channel_id, yt_vid_id, stats)
    except Exception as exc:
        log.warning("[OPTIMIZER] Could not fetch YouTube stats for %s: %s", yt_vid_id, exc)
        return {"status": "error", "reason": str(exc)}

    view_count = int(stats.get("viewCount") or 0)
    like_count = int(stats.get("likeCount") or 0)
    comment_count = int(stats.get("commentCount") or 0)

    # Determine if underperforming
    # Baseline: older than 7-15 days but views remain below threshold or low view velocity
    is_underperforming = (view_count < UNDERPERFORMING_VIEW_THRESHOLD) or force

    opt_count = int(video_row.get("auto_optimized_count") or 0)
    if opt_count >= MAX_AUTO_OPTIMIZATIONS and not force:
        return {
            "status": "max_reached",
            "view_count": view_count,
            "auto_optimized_count": opt_count,
            "reason": "Max automatic optimization cycles reached"
        }

    if not is_underperforming and not force:
        update_video(video_id, last_performance_check_at=now_utc.isoformat())
        return {
            "status": "performing_well",
            "view_count": view_count,
            "days_live": round(days_live, 1),
            "reason": "Video meets performance benchmarks"
        }

    log.info(
        "[OPTIMIZER] Video %s ('%s') is underperforming (views=%d, days_live=%.1f). Initiating AI metadata & thumbnail pivot...",
        yt_vid_id, video_row.get("title", "")[:40], view_count, days_live
    )

    # 1. Generate 3 fresh, higher-converting A/B title variations
    existing_ab = []
    try:
        if video_row.get("ab_test_titles_json"):
            existing_ab = json.loads(video_row["ab_test_titles_json"])
    except Exception:
        existing_ab = []

    # Rotate or create fresh high-CTR titles based strictly on clean video context
    cleaned_base = _clean_title_hook(video_row.get("title") or "")
    fresh_ab_titles = generate_ab_test_titles(
        cleaned_base,
        video_row.get("analysis_json") or "",
        video_row.get("filename") or ""
    )

    # Pick the next best title variation (cycle through A/B options)
    next_title_idx = (opt_count + 1) % len(fresh_ab_titles)
    new_title = fresh_ab_titles[next_title_idx]

    # 2. Re-optimize tags & hashtags for stronger discovery intent
    existing_tags = [t.strip() for t in (video_row.get("tags") or "").split(",") if t.strip()]
    high_intent_tags = list(existing_tags)
    for kw in ["viral video", "trending", "must watch", "full explanation", "exposed"]:
        if kw not in high_intent_tags:
            high_intent_tags.append(kw)

    # 3. Generate fresh thumbnail variants with video keyframes
    resolved_path = resolve_local_video_path(video_row.get("file_path") or video_row.get("path"))
    fresh_thumbs = generate_thumbnails(
        video_id,
        video_row.get("analysis_json") or "",
        video_row.get("filename") or "",
        video_path=resolved_path
    )

    # Pick a fresh variant (cycle variant 1 -> 2 -> 3)
    chosen_thumb = fresh_thumbs[(opt_count + 1) % len(fresh_thumbs)] if fresh_thumbs else ""

    # 4. Push updated metadata & thumbnail directly to YouTube (if channel permissions allow)
    push_title_ok = False
    push_thumb_ok = False
    auth_warning = None

    try:
        update_video_metadata(
            channel_id=channel_id,
            youtube_video_id=yt_vid_id,
            title=new_title,
            description=video_row.get("description") or "",
            tags=high_intent_tags,
            category_id="24"
        )
        push_title_ok = True
    except Exception as push_err:
        err_str = str(push_err)
        if "insufficientPermissions" in err_str or "insufficient authentication scopes" in err_str:
            auth_warning = "Channel OAuth token lacks write scope (youtube.force-ssl). Re-authorize channel in Channels tab to enable direct title sync."
            log.warning("[OPTIMIZER] %s Video %s local metadata updated.", auth_warning, yt_vid_id)
        else:
            log.error("[OPTIMIZER] Could not update title on YouTube for %s: %s", yt_vid_id, push_err)

    if chosen_thumb and Path(chosen_thumb).exists():
        try:
            upload_thumbnail_to_youtube(channel_id, yt_vid_id, chosen_thumb)
            push_thumb_ok = True
            log.info("[OPTIMIZER] Pushed new A/B thumbnail to YouTube: %s", Path(chosen_thumb).name)
        except Exception as thumb_err:
            log.warning("[OPTIMIZER] Could not upload thumbnail to YouTube for %s: %s", yt_vid_id, thumb_err)

    # 5. Record optimization history
    history = []
    try:
        if video_row.get("optimization_history_json"):
            history = json.loads(video_row["optimization_history_json"])
    except Exception:
        history = []

    history_entry = {
        "timestamp": now_utc.isoformat(),
        "days_live": round(days_live, 1),
        "views_prior": view_count,
        "likes_prior": like_count,
        "old_title": video_row.get("title"),
        "new_title": new_title,
        "thumbnail_variant": (opt_count + 1) % 3 + 1,
        "thumbnail_path": chosen_thumb,
        "ab_test_titles": fresh_ab_titles,
        "push_title_ok": push_title_ok,
        "push_thumb_ok": push_thumb_ok,
        "auth_warning": auth_warning
    }
    history.append(history_entry)

    update_video(
        video_id,
        title=new_title,
        tags=", ".join(high_intent_tags),
        thumbnail_path=chosen_thumb,
        thumbnail_variants_json=json.dumps(fresh_thumbs),
        ab_test_titles_json=json.dumps(fresh_ab_titles, ensure_ascii=False),
        last_performance_check_at=now_utc.isoformat(),
        auto_optimized_count=opt_count + 1,
        optimization_history_json=json.dumps(history, ensure_ascii=False)
    )

    log.info(
        "[OPTIMIZER] Successfully optimized video %s! New title='%s' | Thumbnail variant=%d | Cycle=%d (remote_push: title=%s, thumb=%s)",
        yt_vid_id, new_title, (opt_count + 1) % 3 + 1, opt_count + 1, push_title_ok, push_thumb_ok
    )

    try:
        from .telegram import notify_video_optimized
        channel = get_channel(channel_id) or {}
        ch_name = channel.get("channel_name") or channel_id
        notify_video_optimized(
            channel_name=ch_name,
            old_title=video_row.get("title") or video_row.get("filename") or f"Video #{video_id}",
            new_title=new_title,
            youtube_video_id=yt_vid_id,
            views_prior=view_count,
            days_live=days_live,
            cycle=opt_count + 1,
            thumbnail_variant=(opt_count + 1) % 3 + 1,
            pushed_to_youtube=push_title_ok
        )
    except Exception as tg_err:
        log.warning("[OPTIMIZER] Could not dispatch Telegram optimization notification: %s", tg_err)

    return {
        "status": "optimized",
        "youtube_video_id": yt_vid_id,
        "old_title": video_row.get("title"),
        "new_title": new_title,
        "views_prior": view_count,
        "hours_live": round(hours_live, 1),
        "days_live": round(hours_live / 24.0, 1),
        "cycle": opt_count + 1,
        "chosen_thumbnail": chosen_thumb,
        "ab_test_titles": fresh_ab_titles,
        "push_title_ok": push_title_ok,
        "push_thumb_ok": push_thumb_ok,
        "auth_warning": auth_warning
    }


def run_channel_performance_audit(channel_id, force=False, allow_night=False, max_videos=None, gap_seconds=None):
    """
    Scan uploaded videos in a channel and optimize AT MOST 1 underperforming video.
    Strictly enforces a 2-hour cooldown per channel between video updates.
    """
    is_active, window_msg = is_optimizer_time_window_active(allow_night=allow_night)
    if not is_active:
        log.info("[OPTIMIZER] Channel %s performance audit paused for night hours: %s", channel_id, window_msg)
        return [{"status": "outside_daytime_window", "reason": window_msg}]

    # Check 2-hour channel cooldown rule
    if not force:
        cooldown = get_channel_optimization_cooldown_status(channel_id)
        if cooldown["in_cooldown"]:
            msg = (
                f"Channel {channel_id} is in cooldown (recently updated 1 video). "
                f"Must wait at least 2 hours per channel. Remaining wait: {cooldown['remaining_minutes']}m."
            )
            log.info("[OPTIMIZER] %s", msg)
            return [{
                "status": "cooldown_active",
                "channel_id": channel_id,
                "reason": msg,
                "remaining_seconds": cooldown["remaining_seconds"],
                "remaining_minutes": cooldown["remaining_minutes"],
                "last_run": cooldown["last_run"]
            }]

    if gap_seconds is None:
        gap_seconds = get_optimizer_pacing_gap()

    # Strict rule: at most 1 video per channel unless explicitly forced
    batch_limit = 1 if not force else (max_videos if max_videos is not None else 1)

    conn = get_conn()
    # Fetch candidate pool prioritized by oldest check timestamp or unanalyzed items
    rows = conn.execute(
        """SELECT * FROM video_inventory 
           WHERE channel_id=? AND status='uploaded' AND youtube_video_id != ''
           ORDER BY COALESCE(last_performance_check_at, '') ASC, id ASC
           LIMIT ?""",
        (channel_id, 30)
    ).fetchall()
    conn.close()

    results = []
    optimized_count = 0

    for r in rows:
        if optimized_count >= batch_limit:
            log.info("[OPTIMIZER] Channel %s reached 1-video limit; stopping to enforce 2-hour cooldown.", channel_id)
            break

        res = audit_and_optimize_video(dict(r), channel_id, force=force, allow_night=allow_night)
        if res.get("status") in ("optimized", "performing_well", "too_recent", "outside_daytime_window"):
            results.append({"video_id": r["id"], "filename": r["filename"], **res})

        if res.get("status") == "optimized":
            optimized_count += 1
            # Record optimization timestamp to enforce 2-hour cooldown
            record_channel_optimization(channel_id)
            log.info("[OPTIMIZER] Channel %s: 1 video optimized (ID %s). 2-hour cooldown started.", channel_id, r["id"])
            # Immediately halt further optimizations for this channel
            break
        else:
            time.sleep(0.2)

    return results


def run_global_performance_audit(allow_night=False, max_videos_per_channel=1):
    """Run audit across all channels in the system (invoked periodically by scheduler) with rate-limited pacing."""
    is_active, window_msg = is_optimizer_time_window_active(allow_night=allow_night)
    if not is_active:
        log.info("[OPTIMIZER] Global performance audit paused for night hours: %s", window_msg)
        return {"status": "outside_daytime_window", "reason": window_msg, "total_optimized": 0}

    # In DEV environment, never run global automated background audits
    from .config import is_dev_env, get_app_env
    if is_dev_env():
        log.info("[OPTIMIZER] Global performance audit skipped: DEV environment active (%s). Automatic audits are disabled in dev.", get_app_env())
        return {"status": "dev_env_disabled", "reason": "Automated audits disabled in dev environment (one-shot testing available via manual trigger)", "total_optimized": 0}

    # Verify if automatic optimizer is enabled (default disabled: 0)
    try:
        enabled = (get_app_setting("enable_auto_optimizer") or "0") == "1"
        if not enabled:
            log.info("[OPTIMIZER] Automatic performance audit is disabled in settings.")
            return {"status": "disabled", "reason": "Automated optimizer is toggled off", "total_optimized": 0}
    except Exception:
        pass

    channels = list_channels()
    total_optimized = 0
    gap = get_optimizer_pacing_gap()

    for ch in channels:
        cid = ch["channel_id"]
        try:
            results = run_channel_performance_audit(cid, allow_night=allow_night, max_videos=max_videos_per_channel, gap_seconds=gap)
            for r in results:
                if r.get("status") == "optimized":
                    total_optimized += 1
        except Exception as exc:
            log.warning("[OPTIMIZER] Global audit failed for channel %s: %s", cid, exc)

    if total_optimized > 0:
        log.info("[OPTIMIZER] Paced audit completed: %d underperforming videos automatically optimized (pacing gap=%.1fs, max 1/channel).", total_optimized, gap)
    return {"status": "completed", "total_optimized": total_optimized}


def _detect_title_issues(title: str) -> list[str]:
    """Identify specific algorithmic and conversion flaws in a video title."""
    import re
    issues = []
    if not title or len(title.strip()) < 3:
        issues.append("empty_or_too_short")
        return issues

    t_lower = title.lower()
    if re.search(r"^\s*(lead\s+immediately|suggested\s+title|here\s+is\s+a\s+title|hook\s*:|title\s*:)", t_lower):
        issues.append("ai_prompt_leak")
    if re.search(r"(the\s+moment\s+everything\s+changed|funniest\s+reaction\s+you'?ll\s+see|plot\s+twist\s+was\s+insane|ऐसा\s+मजेदार\s+मोड़|beach\s+showdown|magic\s+on\s+the\s+pitch|nobody\s+expected\s+that\s+ending)", t_lower):
        issues.append("boilerplate_suffix")
    if re.search(r"(what\s+actually\s+happened|the\s+shocking\s+truth|आखिर\s*में\s*क्या\s*हुआ|सच्चाई\s*सामने\s*आ\s*गई)", t_lower):
        issues.append("cliche_prefix")
    if re.search(r"(full\s*story|पूरी\s*कहानी|wait\s*for\s*(the\s*)?end|watch\s*till\s*(the\s*)?end|full\s*video)", t_lower):
        issues.append("cliche_suffix")
    if re.search(r"[\|\-—]\s*(chalaak kahaniya|toon masala|samir pandey)", t_lower):
        issues.append("channel_branding_leak")
    return issues


def audit_old_videos(channel_id=None, limit=500):
    """
    Scans uploaded videos to identify titles suffering from prompt leakage,
    repetitive boilerplate suffixes, cliches, or missing hooks.
    Returns full diagnostic report with proposed clean titles.
    """
    conn = get_conn()
    if channel_id:
        rows = conn.execute(
            "SELECT * FROM video_inventory WHERE channel_id=? AND youtube_video_id != '' AND youtube_video_id IS NOT NULL ORDER BY id DESC LIMIT ?",
            (channel_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM video_inventory WHERE youtube_video_id != '' AND youtube_video_id IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()

    total_scanned = len(rows)
    flagged = []
    clean = []

    for r in rows:
        row = dict(r)
        current_title = (row.get("title") or "").strip()
        filename = (row.get("filename") or "").strip()
        analysis_json = row.get("analysis_json") or ""

        issues = _detect_title_issues(current_title)
        cleaned = _clean_title_hook(current_title)

        if not cleaned:
            # Fallback to analysis summary or clean filename
            try:
                if analysis_json:
                    adata = json.loads(analysis_json)
                    cleaned = adata.get("metadata", {}).get("title") or adata.get("title") or ""
                    cleaned = _clean_title_hook(cleaned)
            except Exception:
                pass
            if not cleaned:
                cleaned = _clean_title_hook(filename) or filename

        needs_optimization = (
            bool(issues) or
            (current_title != cleaned and len(cleaned) > 0) or
            not current_title or
            len(current_title) < 5
        )

        entry = {
            "video_id": row["id"],
            "youtube_video_id": row.get("youtube_video_id"),
            "channel_id": row.get("channel_id"),
            "current_title": current_title,
            "proposed_title": cleaned,
            "issues": issues,
            "filename": filename,
            "created_at": row.get("created_at") or row.get("updated_at") or "",
            "auto_optimized_count": row.get("auto_optimized_count") or 0,
            "needs_optimization": needs_optimization,
        }

        if needs_optimization:
            flagged.append(entry)
        else:
            clean.append(entry)

    return {
        "total_scanned": total_scanned,
        "total_flagged": len(flagged),
        "total_clean": len(clean),
        "flagged_videos": flagged,
        "operating_window": get_optimizer_operating_window()
    }


def bulk_optimize_old_videos(video_ids=None, channel_id=None, auto_apply_youtube=True, allow_night=False, gap_seconds=None, force=False):
    """
    Executes optimization across old uploaded videos with strict 1-video-per-channel and 2-hour cooldown pacing.
    - Cleans repetitive boilerplate suffixes, prompt leakage, and stale titles.
    - Updates at most 1 video per channel per run.
    - Enforces a minimum 2-hour cooldown per channel before allowing subsequent video updates.
    - Respects the 8:00 AM - 9:00 PM operating window unless allow_night is True.
    - Updates local database and sends consolidated Telegram report.
    """
    is_active, window_msg = is_optimizer_time_window_active(allow_night=allow_night)
    if not is_active:
        return {
            "status": "outside_daytime_window",
            "message": window_msg,
            "total_updated": 0,
            "total_skipped": 0,
            "results": []
        }

    if gap_seconds is None:
        gap_seconds = get_optimizer_pacing_gap()

    audit_res = audit_old_videos(channel_id=channel_id)
    target_entries = audit_res["flagged_videos"]

    if video_ids:
        vid_set = set(str(v) for v in video_ids)
        target_entries = [e for e in target_entries if str(e["video_id"]) in vid_set]

    results = []
    total_updated = 0
    total_failed = 0
    sample_improvements = []
    channels_updated_in_run = set()

    now_utc = datetime.now(timezone.utc).isoformat()

    for item in target_entries:
        vid_id = item["video_id"]
        yt_vid_id = item["youtube_video_id"]
        ch_id = item["channel_id"]
        old_title = item["current_title"]
        new_title = item["proposed_title"]

        if not new_title or new_title == old_title:
            continue

        # Strict 1-video per channel limit: skip if this channel already had 1 video updated in this run
        if ch_id in channels_updated_in_run:
            log.info("[OPTIMIZER] Bulk skip for video %s: channel %s already had 1 video updated in this run (2-hour cooldown rule).", vid_id, ch_id)
            continue

        # Enforce 2-hour cooldown per channel
        if not force:
            cooldown = get_channel_optimization_cooldown_status(ch_id)
            if cooldown["in_cooldown"]:
                log.info("[OPTIMIZER] Bulk skip for video %s: channel %s is in 2-hour cooldown (remaining %sm).", vid_id, ch_id, cooldown["remaining_minutes"])
                continue

        pushed_youtube = False
        youtube_err = None

        if auto_apply_youtube and yt_vid_id and ch_id:
            try:
                update_video_metadata(
                    channel_id=ch_id,
                    youtube_video_id=yt_vid_id,
                    title=new_title
                )
                pushed_youtube = True
                # Enforce user-configured pacing gap (1 to 2 seconds) between video updates
                if gap_seconds > 0:
                    time.sleep(gap_seconds)
            except Exception as e:
                youtube_err = str(e)
                log.warning("[OPTIMIZER] Bulk push failed for %s (%s): %s", yt_vid_id, new_title, e)

        # Update local database record
        try:
            update_video(
                vid_id,
                title=new_title,
                last_performance_check_at=now_utc
            )
            total_updated += 1
            record_channel_optimization(ch_id)
            channels_updated_in_run.add(ch_id)
            entry_res = {
                "video_id": vid_id,
                "youtube_video_id": yt_vid_id,
                "old_title": old_title,
                "new_title": new_title,
                "pushed_youtube": pushed_youtube,
                "youtube_error": youtube_err,
                "status": "success"
            }
            results.append(entry_res)
            if len(sample_improvements) < 5:
                sample_improvements.append({"old_title": old_title, "new_title": new_title})
        except Exception as db_err:
            total_failed += 1
            results.append({
                "video_id": vid_id,
                "youtube_video_id": yt_vid_id,
                "status": "error",
                "error": str(db_err)
            })

    # Dispatch Telegram summary notification if any were updated
    if total_updated > 0:
        try:
            from .telegram import notify_bulk_optimization_completed
            ch_name = ""
            if channel_id:
                ch = get_channel(channel_id) or {}
                ch_name = ch.get("channel_name") or channel_id
            notify_bulk_optimization_completed(
                channel_name=ch_name,
                total_scanned=audit_res["total_scanned"],
                total_optimized=total_updated,
                sample_titles=sample_improvements
            )
        except Exception as tg_err:
            log.warning("[OPTIMIZER] Bulk Telegram alert failed: %s", tg_err)

    return {
        "status": "completed",
        "total_scanned": audit_res["total_scanned"],
        "total_flagged": audit_res["total_flagged"],
        "total_updated": total_updated,
        "total_failed": total_failed,
        "results": results,
        "operating_window": audit_res["operating_window"]
    }

