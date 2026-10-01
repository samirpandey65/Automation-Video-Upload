import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from .database import (
    list_schedules, get_schedule, get_channel, get_youtube_account, get_channel_folder, get_inventory_by_hash, get_inventory_by_path, get_inventory_by_id,
    upsert_video, update_video, mark_schedule_run, get_remote_video_by_hash, list_channels, upsert_remote_video, get_inventory_by_youtube_id,
    update_channel_sync, add_remote_metrics_snapshot, list_ai_connections, create_upload_job, update_upload_job, add_upload_attempt, record_scheduler_run
)
from .video_utils import scan_folder, sha256_file
from .youtube import upload_video, list_channel_videos, extract_hash_marker
from .gemini import analyze as analyze_gemini

def compose_description(description, hashtags):
    base=str(description or '').strip()
    hs=[str(x).strip().lstrip('#').lower() for x in (hashtags or []) if str(x).strip()]
    suffix=' '.join('#'+x for x in hs)
    if suffix and suffix.lower() not in base.lower(): base=(base+'\n\n'+suffix).strip()
    return base[:5000]

log = logging.getLogger(__name__)
_lock = threading.Lock()
_thread = None
_stop = threading.Event()
_last_youtube_sync = 0.0

def _render(template, filename):
    template = template or ""
    stem = Path(filename).stem
    return template.replace("{filename}", stem).replace("{filename_with_ext}", filename)

def _run_schedule(row):
    channel_id = row["channel_id"]
    channel = get_channel(channel_id)
    if not channel:
        return "channel_missing"
    account = get_youtube_account(channel.get('account_id')) if channel.get('account_id') else None
    if account and str(account.get('status','')).lower() == 'reauth_required':
        log.warning("Scheduler paused for channel=%s because Google re-authentication is required for account=%s", channel_id, account.get('account_name',''))
        return "reauth_required"

    folder = get_channel_folder(channel_id)
    if not folder or not Path(folder).exists():
        log.warning("Scheduler: folder missing for channel %s: %s", channel_id, folder)
        return "folder_missing"

    files = scan_folder(folder)
    pending = []
    reused = 0
    hashed = 0

    # Incremental scheduler scan: never hash an unchanged file. This keeps
    # the background worker cheap even with hundreds/thousands of videos.
    for f in files:
        existing = get_inventory_by_path(channel_id, f["path"])
        if existing and int(existing.get("size_bytes", -1)) == int(f["size_bytes"]) and float(existing.get("modified_at", -1)) == float(f["modified_at"]):
            reused += 1
            if existing["status"] not in ("uploading", "uploaded"):
                pending.append((f, existing))
            continue

        sha = sha256_file(f["path"])
        hashed += 1
        existing_hash = get_inventory_by_hash(channel_id, sha)
        status = existing_hash["status"] if existing_hash else "pending"
        if status in ("uploading", "uploaded"):
            continue
        item = upsert_video(channel_id, f["path"], f["filename"], sha, f["size_bytes"], f["modified_at"], status)
        pending.append((f, item))

    log.info("Scheduler incremental scan: channel=%s files=%d reused=%d hashed=%d", channel_id, len(files), reused, hashed)

    if not pending:
        log.info("Scheduler: no pending videos for %s", channel_id)
        return "nothing_to_upload"

    # Deterministic selection: oldest file first.
    pending.sort(key=lambda x: (x[0]["modified_at"], x[0]["filename"].lower()))
    f, item = pending[0]

    # Cross-system duplicate protection: if YouTube was synced previously and
    # contains this exact source SHA marker, never upload it again.
    remote = get_remote_video_by_hash(channel_id, item.get("sha256", ""))
    if remote:
        update_video(item["id"], status="uploaded", youtube_video_id=remote["youtube_video_id"], error_message="")
        log.info("Scheduler skipped duplicate: %s already exists as %s", f["filename"], remote["youtube_video_id"])
        return "duplicate_synced"

    # Persist the upload job BEFORE AI analysis so a transient AI/network failure
    # cannot lose the scheduled video. The job is used as a durable retry queue.
    job_id = create_upload_job(item["id"], channel_id, row.get("id"), scheduled_at=f"{datetime.now().date()}T{row['time_local']}:00")
    # Phase 3.1: analyze before scheduled upload with quality gate enforcement.
    if item.get("ai_status") == "completed" and item.get("title"):
        log.info("Scheduler: reusing existing verified AI metadata for %s", f["filename"])
        title = item["title"]
        description = item.get("description", "")
        hashtags = [x.strip().lstrip('#').lower() for x in str(item.get("hashtags") or '').replace(',', ' ').split() if x.strip()]
        raw_tags = item.get("tags") or ""
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if isinstance(raw_tags, str) else list(raw_tags)
        update_video(
            item["id"], status="uploading", privacy_status=row["privacy_status"],
            error_message=""
        )
    else:
        try:
            connection_id = row.get("ai_connection_id")
            gemini_conns = [c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
            cid = connection_id or (gemini_conns[0]["id"] if len(gemini_conns)==1 else None)
            if not cid:
                raise RuntimeError("Scheduled Gemini analysis requires a connected Gemini account. Please open Connections and select one.")
            ai = analyze_gemini(f["path"], f["filename"], int(cid))

            md = ai["metadata"]
            title = md["title"]
            description = md["description"]
            hashtags = [str(x).strip().lstrip('#').lower() for x in md.get("hashtags", []) if str(x).strip()]
            tags = [str(x).strip().lstrip('#').lower() for x in md["tags"] if str(x).strip()]
            update_video(
                item["id"], ai_status="completed", ai_summary=md["summary"],
                transcript=ai["transcript"], title=title, description=description,
                hashtags=", ".join(md["hashtags"]), tags=", ".join(tags),
                analysis_json=__import__("json").dumps(ai, ensure_ascii=False),
                status="uploading", privacy_status=row["privacy_status"],
                error_message=""
            )
        except Exception as exc:
            # AI/network failures are transient much more often than permanent.
            # Keep the video pending and put the durable job into retry_pending.
            # This prevents a single Gemini timeout from permanently losing a
            # scheduled upload.
            msg = f"AI analysis quality gate failed: {exc}"
            log.warning("Scheduler AI analysis failed for %s; queued for retry: %s", f["filename"], exc)
            update_video(
                item["id"], ai_status="failed", status="pending",
                error_message=msg
            )
            update_upload_job(
                job_id, status="retry_pending", attempt_count=0,
                last_error=msg
            )
            return "ai_retry_pending"

    update_upload_job(job_id, status="uploading", started_at=datetime.now().isoformat(), attempt_count=1)
    attempt_started=datetime.now().isoformat()
    try:
        result = upload_video(
            channel_id=channel_id,
            file_path=f["path"],
            title=title,
            description=compose_description(description, hashtags),
            tags=tags,
            privacy_status=row["privacy_status"],
            category_id=row["category_id"],
            made_for_kids=bool(row["made_for_kids"]),
            contains_synthetic_media=bool(row.get("contains_synthetic_media", 1)),
            video_language=row.get("video_language", "hi"),
            title_description_language=row.get("title_description_language", "hi"),
            recording_date=row.get("recording_date", ""),
            max_retries=3,
            file_hash=item.get("sha256", ""),
        )
        update_video(item["id"], status="uploaded", youtube_video_id=result.get("id", ""), error_message="")
        add_upload_attempt(job_id,1,"success",youtube_video_id=result.get("id",""),started_at=attempt_started,finished_at=datetime.now().isoformat())
        update_upload_job(job_id,status="completed",completed_at=datetime.now().isoformat(),youtube_video_id=result.get("id",""),last_error="")
        log.info("Scheduler upload success: %s -> %s", f["filename"], result.get("id"))
        return "uploaded"
    except Exception as exc:
        update_video(item["id"], status="failed", error_message=str(exc))
        add_upload_attempt(job_id,1,"failed",error_message=str(exc),started_at=attempt_started,finished_at=datetime.now().isoformat())
        update_upload_job(job_id,status="retry_pending",completed_at=datetime.now().isoformat(),last_error=str(exc))
        log.exception("Scheduler upload failed for %s", f["filename"])
        return "failed"


def _process_retry_jobs():
    """Durable retry queue for AI analysis and YouTube uploads.

    Rules:
      - Never lose a scheduled video because Gemini/network failed.
      - Retry with exponential backoff.
      - Check the YouTube hash marker before every upload retry.
      - Do not permanently mark a video failed until the retry budget is exhausted.
    """
    from datetime import timedelta
    import json as _json

    jobs = [j for j in __import__("app.database", fromlist=["list_upload_jobs"]).list_upload_jobs("retry_pending", limit=20)]
    now = datetime.now()

    for job in jobs:
        item = get_inventory_by_id(job.get("video_id")) if job.get("video_id") else None
        channel = get_channel(job["channel_id"])
        if not item or not channel:
            update_upload_job(job["id"], status="failed", last_error="Video or channel no longer exists.")
            continue
        if item.get("status") == "uploaded":
            update_upload_job(job["id"], status="completed",
                              completed_at=datetime.now().isoformat(),
                              youtube_video_id=item.get("youtube_video_id",""), last_error="")
            continue

        schedule = get_schedule(job["schedule_id"]) if job.get("schedule_id") else {}
        attempt_no = int(job.get("attempt_count") or 0) + 1

        # Maximum 5 durable attempts. A retry is attempted at most once per
        # scheduler tick, with a backoff so a failing API cannot be hammered.
        if attempt_no > 5:
            msg = job.get("last_error") or "Maximum retry attempts reached."
            update_upload_job(job["id"], status="failed", last_error=msg)
            update_video(item["id"], status="failed", error_message=msg)
            log.error("Retry budget exhausted: job=%s video=%s", job["id"], item.get("filename"))
            continue

        # Exponential backoff: 1, 2, 4, 8, 16 minutes.
        last_update = job.get("updated_at") or job.get("created_at") or ""
        try:
            last_dt = datetime.fromisoformat(last_update.replace("Z", "+00:00")).replace(tzinfo=None)
            wait_minutes = min(16, 2 ** max(0, attempt_no - 1))
            if (now - last_dt).total_seconds() < wait_minutes * 60:
                continue
        except Exception:
            pass

        # Never retry a file that disappeared.
        if not Path(item["file_path"]).exists():
            msg = f"Video file no longer exists: {item['file_path']}"
            update_upload_job(job["id"], status="failed", last_error=msg)
            update_video(item["id"], status="failed", error_message=msg)
            log.error("Retry cancelled: %s", msg)
            continue

        # Recheck YouTube before doing any work. This makes retries idempotent.
        remote = get_remote_video_by_hash(job["channel_id"], item.get("sha256",""))
        if remote:
            update_video(item["id"], status="uploaded",
                         youtube_video_id=remote["youtube_video_id"], error_message="")
            update_upload_job(job["id"], status="completed",
                              completed_at=datetime.now().isoformat(),
                              youtube_video_id=remote["youtube_video_id"], last_error="")
            log.info("Retry queue found already-uploaded video: job=%s youtube=%s",
                     job["id"], remote["youtube_video_id"])
            continue

        # If AI metadata is not available, retry AI first.
        if not item.get("title") or item.get("ai_status") != "completed":
            try:
                connection_id = (schedule or {}).get("ai_connection_id")
                gemini_conns = [c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
                cid = connection_id or (gemini_conns[0]["id"] if len(gemini_conns) == 1 else None)
                if not cid:
                    raise RuntimeError("No connected Gemini account for scheduled AI analysis.")
                ai = analyze_gemini(item["file_path"], item["filename"], int(cid))
                md = ai["metadata"]
                hashtags = [str(x).strip().lstrip("#").lower() for x in md.get("hashtags", []) if str(x).strip()]
                tags = [str(x).strip().lstrip("#").lower() for x in md.get("tags", []) if str(x).strip()]
                update_video(
                    item["id"], ai_status="completed", ai_summary=md["summary"],
                    transcript=ai["transcript"], title=md["title"],
                    description=md["description"], hashtags=", ".join(md["hashtags"]),
                    tags=", ".join(tags), analysis_json=_json.dumps(ai, ensure_ascii=False),
                    status="pending", error_message=""
                )
                item = get_inventory_by_id(item["id"])
                log.info("Retry AI analysis succeeded: job=%s video=%s", job["id"], item["filename"])
            except Exception as exc:
                msg = f"AI retry failed: {exc}"
                update_upload_job(job["id"], status="retry_pending",
                                  attempt_count=attempt_no, last_error=msg)
                update_video(item["id"], status="pending", ai_status="failed",
                             error_message=msg)
                log.warning("AI retry failed: job=%s attempt=%s: %s", job["id"], attempt_no, exc)
                continue

        tags = [x.strip().lstrip("#").lower() for x in str(item.get("tags") or "").split(",") if x.strip()]
        hashtags = [x.strip().lstrip("#").lower() for x in str(item.get("hashtags") or "").replace(",", " ").split() if x.strip()]
        title = item.get("title") or Path(item["filename"]).stem
        description = compose_description(item.get("description",""), hashtags)
        started = datetime.now().isoformat()
        update_upload_job(job["id"], status="uploading", started_at=started,
                          attempt_count=attempt_no, last_error="")
        update_video(item["id"], status="uploading", error_message="")
        add_upload_attempt(job["id"], attempt_no, "started", started_at=started)
        try:
            result = upload_video(
                channel_id=job["channel_id"], file_path=item["file_path"], title=title,
                description=description, tags=tags,
                privacy_status=(schedule or {}).get("privacy_status") or item.get("privacy_status","private"),
                category_id=(schedule or {}).get("category_id","24"),
                made_for_kids=bool((schedule or {}).get("made_for_kids",0)),
                contains_synthetic_media=bool((schedule or {}).get("contains_synthetic_media",1)),
                video_language=(schedule or {}).get("video_language","hi"),
                title_description_language=(schedule or {}).get("title_description_language","hi"),
                recording_date=(schedule or {}).get("recording_date",""),
                max_retries=5, file_hash=item.get("sha256","")
            )
            vid = result.get("id","")
            update_video(item["id"], status="uploaded", youtube_video_id=vid, error_message="")
            add_upload_attempt(job["id"], attempt_no, "success", youtube_video_id=vid,
                               started_at=started, finished_at=datetime.now().isoformat())
            update_upload_job(job["id"], status="completed",
                              completed_at=datetime.now().isoformat(),
                              youtube_video_id=vid, last_error="")
            log.info("Retry upload succeeded: job=%s video=%s", job["id"], vid)
        except Exception as exc:
            msg = str(exc)
            # Leave the item retryable unless the durable attempt budget is exhausted.
            retry_status = "retry_pending" if attempt_no < 5 else "failed"
            video_status = "pending" if attempt_no < 5 else "failed"
            update_video(item["id"], status=video_status, error_message=msg)
            add_upload_attempt(job["id"], attempt_no, "failed", error_message=msg,
                               started_at=started, finished_at=datetime.now().isoformat())
            update_upload_job(job["id"], status=retry_status,
                              completed_at=datetime.now().isoformat(),
                              last_error=msg)
            log.warning("Retry upload failed: job=%s attempt=%s/%s: %s",
                        job["id"], attempt_no, 5, msg)

def _loop():
    global _last_youtube_sync
    while not _stop.wait(15):
        now = datetime.now()
        # Refresh the remote YouTube index every 30 minutes so a new machine
        # can inherit duplicate protection without scanning the local library.
        import time
        if time.time() - _last_youtube_sync >= 1800:
            try:
                for ch in list_channels():
                    try:
                        acct = get_youtube_account(ch.get('account_id')) if ch.get('account_id') else None
                        if acct and str(acct.get('status','')).lower() == 'reauth_required':
                            update_channel_sync(ch['channel_id'], 'error', 'Google re-authentication required; scheduler sync paused.')
                            continue
                        items = list_channel_videos(ch["channel_id"])
                    except Exception as exc:
                        update_channel_sync(ch["channel_id"], "error", str(exc))
                        log.exception("Background YouTube sync failed for channel=%s", ch["channel_id"])
                        continue
                    for item in items:
                        sn=item.get("snippet",{}); st=item.get("status",{}); marker=extract_hash_marker(item)
                        statistics=item.get("statistics",{}) or {}
                        upsert_remote_video(ch["channel_id"], item.get("id",""), sn.get("title",""), sn.get("description",""), sn.get("publishedAt",""), st.get("privacyStatus",""), sn.get("tags",[]), marker, __import__("json").dumps(statistics), __import__("json").dumps(item.get("contentDetails",{})))
                        try:
                            add_remote_metrics_snapshot(ch["channel_id"], item.get("id",""), statistics)
                        except Exception:
                            log.exception("Failed to save background YouTube metrics snapshot")
                        if marker:
                            local = get_inventory_by_hash(ch["channel_id"], marker)
                            if local and local.get("status") != "uploaded":
                                update_video(local["id"], status="uploaded", youtube_video_id=item.get("id",""), title=sn.get("title",local.get("title","")), description=sn.get("description",local.get("description","")), privacy_status=st.get("privacyStatus",local.get("privacy_status","")), error_message="")
                    update_channel_sync(ch["channel_id"], "success", "", len(items))
                    log.info("Background YouTube sync: channel=%s videos=%d", ch["channel_id"], len(items))
                _last_youtube_sync=time.time()
            except Exception:
                log.exception("Background YouTube sync failed")
        try:
            _process_retry_jobs()
        except Exception:
            log.exception("Retry queue processing failed")
        weekday = now.weekday()
        hhmm = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")

        # A one-minute tolerance makes the scheduler resilient to small delays.
        schedules = list_schedules()
        for row in schedules:
            if not row["enabled"] or row["weekday"] != weekday:
                continue
            if row["time_local"] != hhmm or row["last_run_date"] == today:
                continue
            if not _lock.acquire(blocking=False):
                continue
            try:
                result = _run_schedule(row)
                if result in {"uploaded", "duplicate_synced", "nothing_to_upload"}:
                    mark_schedule_run(row["id"], today)
                    record_scheduler_run(row["id"], today, "success", result)
                else:
                    record_scheduler_run(row["id"], today, "failed", result)
                log.info("Schedule %s completed: %s", row["id"], result)
            finally:
                _lock.release()

def start_scheduler():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="youtube-scheduler", daemon=True)
    _thread.start()
    log.info("Local scheduler started.")

def stop_scheduler():
    _stop.set()
