from datetime import date, timedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .auth import load_credentials_for_channel
from .youtube import get_video_stats
from .database import get_remote_metrics_history, get_remote_video

CORE_METRICS = (
    "views,likes,comments,shares,estimatedMinutesWatched,"
    "averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost"
)
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

def _service(channel_id):
    creds = load_credentials_for_channel(channel_id)
    if not creds:
        raise RuntimeError("No saved Google credentials for this channel.")
    scopes = set(creds.scopes or [])
    if ANALYTICS_SCOPE not in scopes:
        raise RuntimeError("YouTube Analytics permission is missing. Reconnect this YouTube channel from YouTube Channels to grant Analytics access.")
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

def _rows_to_dict(headers, rows):
    return [dict(zip(headers, row)) for row in rows]

def _analytics_api_disabled(exc):
    text=str(exc)
    return ("youtubeanalytics.googleapis.com" in text and "accessNotConfigured" in text) or "YouTube Analytics API has not been used" in text or "is disabled" in text

def get_video_basic_fallback(channel_id, video_id, days=28, analytics_error=""):
    # Always prefer the live YouTube Data API count. If that request itself is
    # unavailable, use the latest synced remote snapshot instead of returning 0.
    item = None
    data_error = ""
    try:
        item = get_video_stats(channel_id, video_id)
    except Exception as exc:
        data_error = str(exc)
    if item:
        st = item.get("statistics", {})
    else:
        import json
        remote = get_remote_video(channel_id, video_id) or {}
        try:
            st = json.loads(remote.get("statistics_json") or "{}")
        except Exception:
            st = {}
    hist=get_remote_metrics_history(channel_id, video_id, limit=max(60, int(days)*3))
    # Prefer persisted sync snapshots for a local trend when Analytics API is unavailable.
    trend=[]
    for r in hist:
        trend.append({
            "day": (r.get("captured_at") or "")[:10],
            "views": int(r.get("view_count") or 0),
            "likes": int(r.get("like_count") or 0),
            "comments": int(r.get("comment_count") or 0),
        })
    return {
        "mode":"basic",
        "analytics_available":False,
        "analytics_error":analytics_error,
        "data_api_error":data_error,
        "video_id":video_id,
        "start_date":(date.today()-timedelta(days=max(1,min(int(days),90)))).isoformat(),
        "end_date":date.today().isoformat(),
        "summary":{
            "views":int(st.get("viewCount") or 0),
            "likes":int(st.get("likeCount") or 0),
            "comments":int(st.get("commentCount") or 0),
        },
        "trend":trend,
    }

def get_video_analytics(channel_id, video_id, days=28):
    try:
        service = _service(channel_id)
        end = date.today()
        start = end - timedelta(days=max(1, min(int(days), 90)))
        base = dict(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), metrics=CORE_METRICS, filters=f"video=={video_id}")
        summary_resp = service.reports().query(**base).execute()
        headers = [h.get("name") for h in summary_resp.get("columnHeaders", [])]
        summary_rows = _rows_to_dict(headers, summary_resp.get("rows", []))
        summary = summary_rows[0] if summary_rows else {h: 0 for h in headers}
        trend_resp = service.reports().query(**base, dimensions="day", sort="day").execute()
        trend_headers = [h.get("name") for h in trend_resp.get("columnHeaders", [])]
        trend = _rows_to_dict(trend_headers, trend_resp.get("rows", []))

        # Overlay real-time public stats from YouTube Data API (handles newly uploaded videos and Analytics delays)
        try:
            live_item = get_video_stats(channel_id, video_id)
            if live_item:
                st = live_item.get("statistics", {})
            else:
                import json
                remote = get_remote_video(channel_id, video_id) or {}
                st = json.loads(remote.get("statistics_json") or "{}")
            live_v = int(st.get("viewCount") or 0)
            live_l = int(st.get("likeCount") or 0)
            live_c = int(st.get("commentCount") or 0)
            if live_v >= int(summary.get("views") or 0):
                summary["views"] = live_v
            if live_l >= int(summary.get("likes") or 0):
                summary["likes"] = live_l
            if live_c >= int(summary.get("comments") or 0):
                summary["comments"] = live_c
        except Exception:
            pass

        # Retention & Hook Diagnosis
        avg_pct = float(summary.get("averageViewPercentage", 0) or 0)
        avg_duration = float(summary.get("averageViewDuration", 0) or 0)
        
        # Determine performance bottleneck
        if avg_pct > 0 and avg_pct < 35.0:
            retention_diagnosis = "Critical Hook Drop-off: Viewers leave in early seconds (<35% retention). Intro needs faster pacing, immediate visual pattern interrupts, or shorter opening teaser."
            bottleneck = "hook_dropoff"
        elif avg_pct >= 55.0:
            retention_diagnosis = "Excellent Hook & Retention: Viewers stay engaged (>55% retention). If views remain low, the bottleneck is purely Click-Through-Rate (CTR / Title / Thumbnail)."
            bottleneck = "low_ctr"
        else:
            retention_diagnosis = "Moderate Retention: Viewers stay for typical duration (~35-50%). Pacing is average; consider testing higher contrast thumbnails and more punchy title hooks."
            bottleneck = "balanced"

        retention_data = {
            "average_view_percentage": avg_pct,
            "average_view_duration_seconds": avg_duration,
            "bottleneck": bottleneck,
            "diagnosis": retention_diagnosis,
        }

        return {
            "mode":"detailed",
            "analytics_available":True,
            "video_id":video_id,
            "start_date":start.isoformat(),
            "end_date":end.isoformat(),
            "summary":summary,
            "trend":trend,
            "retention":retention_data
        }
    except Exception as exc:
        return get_video_basic_fallback(channel_id, video_id, days, str(exc))
