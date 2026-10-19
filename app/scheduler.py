import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from .database import (
    list_schedules, get_schedule, get_channel, get_youtube_account, get_channel_folder, get_inventory_by_hash, get_inventory_by_path, get_inventory_by_id,
    upsert_video, update_video, mark_schedule_run, get_remote_video_by_hash, list_channels, upsert_remote_video, get_inventory_by_youtube_id,
    update_channel_sync, add_remote_metrics_snapshot, list_ai_connections, create_upload_job, update_upload_job, add_upload_attempt, record_scheduler_run,
    get_meta_connection, mark_platform_published, get_app_setting, set_app_setting, list_pending_inventory, get_last_upload_time
)
from .config import BASE_DIR, THUMBNAILS_DIR
from .video_utils import scan_folder, sha256_file
from .youtube import upload_video, list_channel_videos, extract_hash_marker, upload_thumbnail_to_youtube
from .playlist import assign_playlist
from .gemini import analyze as analyze_gemini
from .meta import load_page_token, upload_facebook_page_video, upload_facebook_page_reel, upload_instagram_reel
from .telegram import notify_scheduler_upload_success, notify_scheduler_upload_failed, notify_meta_publish_success, notify_meta_publish_failed
from .credential_manager import refresh_all_credentials

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

def _render(template, filename):
    template = template or ""
    stem = Path(filename).stem
    return template.replace("{filename}", stem).replace("{filename_with_ext}", filename)

def _format_channel(channel_id, channel_name=None):
    if not channel_id:
        return "None"
    if not channel_name:
        try:
            ch = get_channel(channel_id)
            channel_name = ch.get("channel_name") if ch else ""
        except Exception:
            channel_name = ""
    return f"{channel_id} ({channel_name})" if channel_name else str(channel_id)

def _format_ai_conn(conn_id, gemini_conns_map=None):
    if not conn_id:
        return "None"
    if gemini_conns_map and conn_id in gemini_conns_map:
        c = gemini_conns_map[conn_id]
        name = c.get("account_name") or c.get("account_email") or ""
        return f"{conn_id} ({name})" if name else str(conn_id)
    try:
        from .database import get_ai_connection
        c = get_ai_connection(conn_id)
        name = (c.get("account_name") or c.get("account_email")) if c else ""
        return f"{conn_id} ({name})" if name else str(conn_id)
    except Exception:
        return str(conn_id)

def _format_job(job_id, filename=None):
    return f"{job_id} ({filename})" if filename else str(job_id)

def _execute_gemini_with_fallback(file_path, filename, primary_id, secondary_id=None, context_label="AI"):
    gemini_conns = [c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
    if not gemini_conns and not primary_id:
        raise RuntimeError("Scheduled Gemini analysis requires a connected Gemini account. Please open Connections and select one.")
    conns_map = {c["id"]: c for c in gemini_conns}

    candidate_cids = []
    if primary_id and int(primary_id) in conns_map:
        candidate_cids.append(int(primary_id))
    elif gemini_conns:
        candidate_cids.append(gemini_conns[0]["id"])

    if secondary_id is not None and int(secondary_id) != -1:
        sec_id = int(secondary_id)
        if sec_id in conns_map and sec_id not in candidate_cids:
            candidate_cids.append(sec_id)
    elif secondary_id is None:
        for c in gemini_conns:
            if c["id"] not in candidate_cids:
                candidate_cids.append(c["id"])

    ai = None
    last_err = None
    used_cid = None
    for idx, cid in enumerate(candidate_cids):
        try:
            ai = analyze_gemini(file_path, filename, int(cid))
            used_cid = cid
            if idx > 0:
                log.info(
                    "[%s Fallback] Successfully analyzed '%s' using fallback account %s",
                    context_label, filename, _format_ai_conn(cid, conns_map)
                )
            break
        except Exception as err:
            last_err = err
            if idx + 1 < len(candidate_cids):
                next_cid = candidate_cids[idx + 1]
                log.warning(
                    "[%s Fallback] Primary/Active connection %s failed (%s). Switching to secondary/fallback connection %s...",
                    context_label, _format_ai_conn(cid, conns_map), err, _format_ai_conn(next_cid, conns_map)
                )
            else:
                log.warning(
                    "[%s] Connection %s failed: %s (no further fallback accounts available)",
                    context_label, _format_ai_conn(cid, conns_map), err
                )
    if not ai:
        raise last_err or RuntimeError("All selected Gemini accounts failed analysis.")
    return ai, used_cid, conns_map

def select_next_continuity_video(channel_id, candidates):
    """
    Select the next video for upload adhering to continuity, pacing and multi-part rules:
    1. Multi-part series continuity: If previous upload was Part 1, pick Part 2 of that same series.
    2. Content isolation: Finish all videos of the current series/genre before switching so content does not mix.
    3. Seamless transition: When current genre/series completes, pick earliest part of the next type.
    """
    if not candidates:
        return None

    from .playlist import extract_series_and_part, classify_genre_full
    from .database import get_conn

    # Unpack candidate items
    is_tuple = isinstance(candidates[0], tuple)

    def get_dict(c):
        return c[1] if is_tuple else c

    # 1. Fetch channel's last uploaded video
    last_uploaded = None
    try:
        conn = get_conn()
        row = conn.execute("""
            SELECT * FROM video_inventory
            WHERE channel_id=? AND status='uploaded' AND COALESCE(youtube_video_id, '') != ''
            ORDER BY updated_at DESC LIMIT 1
        """, (channel_id,)).fetchone()
        conn.close()
        if row:
            last_uploaded = dict(row)
    except Exception as err:
        log.warning("[SCHEDULER CONTINUITY] Could not query last uploaded video: %s", err)

    # 2. Enrich candidates
    enriched = []
    for c in candidates:
        it = get_dict(c)
        fn = it.get("filename") or ""
        tit = it.get("title") or fn
        fpath = it.get("file_path") or ""
        is_s, s_name, p_num, p_type = extract_series_and_part(fn, tit)
        g_info = classify_genre_full(
            analysis_json=it.get("analysis_json"),
            filename=fn,
            file_path=fpath,
            title=tit,
            tags=it.get("tags")
        )
        enriched.append({
            "raw": c,
            "dict": it,
            "is_series": is_s,
            "series_name": (s_name or "").strip(),
            "part_number": float(p_num) if p_num > 0 else 999999.0,
            "genre": g_info.get("genre", "Entertainment"),
            "modified_at": float(it.get("modified_at") or 0.0)
        })

    # 3. If last uploaded video exists, attempt continuity
    if last_uploaded:
        l_fn = last_uploaded.get("filename") or ""
        l_tit = last_uploaded.get("title") or l_fn
        l_is_s, l_s_name, l_p_num, _ = extract_series_and_part(l_fn, l_tit)
        l_g_info = classify_genre_full(
            analysis_json=last_uploaded.get("analysis_json"),
            filename=l_fn,
            file_path=last_uploaded.get("file_path") or "",
            title=l_tit,
            tags=last_uploaded.get("tags")
        )
        l_genre = l_g_info.get("genre", "Entertainment")
        l_part_val = float(l_p_num) if l_p_num > 0 else 0.0

        # Step 3A: Same series - find next sequential part (e.g. Part 2 after Part 1)
        if l_is_s and l_s_name:
            same_series = [e for e in enriched if e["series_name"].lower() == l_s_name.lower()]
            if same_series:
                next_parts = [e for e in same_series if e["part_number"] > l_part_val]
                if next_parts:
                    next_parts.sort(key=lambda x: (x["part_number"], x["modified_at"]))
                    chosen = next_parts[0]
                    log.info(
                        "[SCHEDULER CONTINUITY] Multi-part continuity: previous upload was '%s' (Part %.0f). Next scheduled: Part %.0f ('%s')",
                        l_s_name, l_part_val, chosen["part_number"], chosen["dict"].get("title") or chosen["dict"].get("filename")
                    )
                    return chosen["raw"]
                else:
                    same_series.sort(key=lambda x: (x["part_number"], x["modified_at"]))
                    chosen = same_series[0]
                    log.info(
                        "[SCHEDULER CONTINUITY] Multi-part continuity: selecting remaining part %.0f of '%s'",
                        chosen["part_number"], l_s_name
                    )
                    return chosen["raw"]

        # Step 3B: Same genre / content type to avoid mixing content
        same_genre = [e for e in enriched if e["genre"].lower() == l_genre.lower()]
        if same_genre:
            same_genre.sort(key=lambda x: (
                x["series_name"].lower(),
                x["part_number"],
                x["modified_at"]
            ))
            chosen = same_genre[0]
            log.info(
                "[SCHEDULER CONTINUITY] Content isolation: staying in genre '%s'. Selected '%s' (Part %.0f)",
                l_genre, chosen["dict"].get("title") or chosen["dict"].get("filename"), chosen["part_number"]
            )
            return chosen["raw"]

    # 4. Fallback / New Queue: Sort by (genre, series_name, part_number ASC, modified_at ASC)
    enriched.sort(key=lambda x: (
        x["genre"].lower(),
        x["series_name"].lower(),
        x["part_number"],
        x["modified_at"]
    ))
    chosen = enriched[0]
    log.info(
        "[SCHEDULER CONTINUITY] Queue sequenced: selected '%s' (Genre: %s, Series: '%s', Part: %.0f)",
        chosen["dict"].get("title") or chosen["dict"].get("filename"), chosen["genre"], chosen["series_name"], chosen["part_number"]
    )
    return chosen["raw"]


def _run_schedule(row, specific_item=None):
    channel_id = row["channel_id"]
    channel = get_channel(channel_id)
    if not channel:
        return "channel_missing"
    channel_name = channel.get("channel_name", "")
    account = get_youtube_account(channel.get('account_id')) if channel.get('account_id') else None
    if account and str(account.get('status','')).lower() == 'reauth_required':
        log.warning("Scheduler paused for channel=%s because Google re-authentication is required for account=%s", _format_channel(channel_id, channel_name), account.get('account_name',''))
        return "reauth_required"

    if specific_item:
        item = specific_item
        v_path = item.get("file_path") or item.get("path")
        f = {
            "path": v_path,
            "filename": item.get("filename") or (Path(v_path).name if v_path else f"video_{item.get('id')}"),
            "size_bytes": item.get("size_bytes", 0),
            "modified_at": item.get("modified_at", 0),
            "id": item.get("id")
        }
    else:
        folder = get_channel_folder(channel_id)
        if not folder or not Path(folder).exists():
            log.warning("Scheduler: folder missing for channel %s: %s", _format_channel(channel_id, channel_name), folder)
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
                if existing["status"] not in ("uploading", "uploaded", "quota_paused"):
                    pending.append((f, existing))
                continue

            sha = sha256_file(f["path"])
            hashed += 1
            existing_hash = get_inventory_by_hash(channel_id, sha)
            status = existing_hash["status"] if existing_hash else "pending"
            if status in ("uploading", "uploaded", "quota_paused"):
                continue
            item = upsert_video(channel_id, f["path"], f["filename"], sha, f["size_bytes"], f["modified_at"], status)
            pending.append((f, item))

        log.info("Scheduler incremental scan: channel=%s files=%d reused=%d hashed=%d", _format_channel(channel_id, channel_name), len(files), reused, hashed)

        if not pending:
            log.info("Scheduler: no pending videos for channel %s", _format_channel(channel_id, channel_name))
            return "nothing_to_upload"

        # Content Continuity Selection: strict multi-part ordering (Part 1 -> Part 2) and content isolation
        f, item = select_next_continuity_video(channel_id, pending)


    # Cross-system duplicate protection: if YouTube was synced previously and
    # contains this exact source SHA marker or was already uploaded, never upload it again.
    from .database import is_video_published_on_platform
    remote = get_remote_video_by_hash(channel_id, item.get("sha256", ""))
    if remote or is_video_published_on_platform(sha256=item.get("sha256", ""), platform="youtube", channel_id=channel_id):
        yt_id = remote["youtube_video_id"] if remote else item.get("youtube_video_id", "")
        update_video(item["id"], status="uploaded", youtube_video_id=yt_id, error_message="")
        if yt_id:
            mark_platform_published(item["id"], "youtube", yt_id)
        log.info("Scheduler skipped duplicate: %s (sha256=%s) already exists on YouTube as %s", f["filename"], (item.get("sha256") or "")[:12], yt_id)
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
            prov = (row.get("ai_provider") or "gemini").lower()
            if prov in ("openai", "chatgpt"):
                from .ai_multiprovider import analyze_chatgpt
                ai = analyze_chatgpt(f["path"], f["filename"])
            elif prov in ("claude", "anthropic"):
                from .ai_multiprovider import analyze_claude
                ai = analyze_claude(f["path"], f["filename"])
            else:
                connection_id = row.get("ai_connection_id")
                secondary_id = row.get("ai_secondary_connection_id")
                ai, used_cid, conns_map = _execute_gemini_with_fallback(f["path"], f["filename"], connection_id, secondary_id, context_label="AI")

            md = ai["metadata"]
            title = md["title"]
            description = md["description"]
            hashtags = [str(x).strip().lstrip('#').lower() for x in md.get("hashtags", []) if str(x).strip()]
            tags = [str(x).strip().lstrip('#').lower() for x in md["tags"] if str(x).strip()]
            update_video(
                item["id"], ai_status="completed", ai_summary=md["summary"],
                transcript=ai.get("transcript", ""), title=title, description=description,
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

    # AI Director QC Reviewer Quality Gate
    # Enforces: without approval of reviewer do not move or mark completed
    try:
        from .qc_reviewer import run_ai_review
        qc_res = run_ai_review(
            video_id=item["id"],
            title=title,
            description=description,
            tags=", ".join(tags) if isinstance(tags, (list, tuple)) else str(tags),
            filename=f["filename"],
            video_path=f["path"],
            force=False
        )
        if not qc_res.get("approved"):
            verdict = qc_res.get("professional_verdict", "HELD FOR REVIEW")
            log.warning(
                "[QC GATE] Video %s (ID %s) held for reviewer approval. Verdict: %s, Score: %s. Upload withheld.",
                f["filename"], item["id"], verdict, qc_res.get("scores", {}).get("overall_score")
            )
            update_video(
                item["id"],
                status="held_for_review",
                error_message=f"Held by AI Reviewer QC: {verdict}. Review PDF report before publishing."
            )
            update_upload_job(
                job_id,
                status="held_for_review",
                last_error=f"QC Gate: {verdict}"
            )
            return "held_for_review"
    except Exception as qc_err:
        log.warning("[QC GATE] QC Reviewer check encountered non-fatal error: %s", qc_err)

    # Proactive Daily Quota Check
    try:
        from .quota_tracker import can_perform_upload, record_api_quota, notify_quota_paused_if_needed
        can_proceed, quota_summary = can_perform_upload(channel_id=channel_id)
        if not can_proceed:
            err_msg = quota_summary.get("rejection_reason", "Daily YouTube quota limit approaching.")
            log.warning("[QUOTA PAUSE] Job %s channel=%s held: %s", _format_job(job_id, f["filename"]), _format_channel(channel_id, channel_name), err_msg)
            update_upload_job(job_id, status="quota_paused", last_error=err_msg)
            update_video(item["id"], status="quota_paused", error_message=err_msg)
            notify_quota_paused_if_needed(quota_summary)
            return "quota_paused"
    except Exception as q_check_err:
        log.warning("[QUOTA GATE] Quota check non-fatal error: %s", q_check_err)

    update_upload_job(job_id, status="uploading", started_at=datetime.now().isoformat(), attempt_count=1)
    attempt_started=datetime.now().isoformat()
    try:
        upload_path = f["path"]
        enable_outros = True
        try:
            from .database import get_app_setting
            enable_outros = (get_app_setting("enable_platform_outros") or "1") == "1"
        except Exception:
            enable_outros = True
        if enable_outros:
            try:
                from .video_utils import append_platform_outro
                upload_path = append_platform_outro(f["path"], platform="youtube", channel_name=channel_name)
                log.info("Appended YouTube 'Like & Subscribe' outro to %s: %s", f["filename"], upload_path)
            except Exception as outro_err:
                log.warning("YouTube outro append skipped (non-fatal): %s", outro_err)
                upload_path = f["path"]

        result = upload_video(
            channel_id=channel_id,
            file_path=upload_path,
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
        vid_id = result.get("id", "")
        update_video(item["id"], status="uploaded", youtube_video_id=vid_id, error_message="")
        mark_platform_published(item["id"], "youtube", vid_id)
        try:
            from .quota_tracker import record_api_quota
            record_api_quota(service="youtube", operation="videos.insert", units=1600, channel_id=channel_id, details=vid_id)
        except Exception:
            pass
        add_upload_attempt(job_id,1,"success",youtube_video_id=vid_id,started_at=attempt_started,finished_at=datetime.now().isoformat())
        update_upload_job(job_id,status="completed",completed_at=datetime.now().isoformat(),youtube_video_id=vid_id,last_error="")
        log.info("Scheduler upload success: channel=%s %s -> %s", _format_channel(channel_id, channel_name), f["filename"], vid_id)

        # Auto-create thumbnail if missing, then upload to YouTube using selected engine (flow or gemini)
        t_path_str = item.get("thumbnail_path") or ""
        if not t_path_str:
            try:
                from .thumbnail import generate_thumbnails
                sched_engine = row.get("thumbnail_engine") or "gemini"
                paths = generate_thumbnails(
                    item["id"],
                    item.get("analysis_json") or "",
                    f["filename"],
                    video_path=f["path"],
                    engine=sched_engine
                )
                if paths:
                    t_path_str = paths[0]
                    update_video(item["id"], thumbnail_path=t_path_str, thumbnail_variants_json=__import__("json").dumps(paths))
                    log.info("Scheduler automatically generated thumbnail for %s (engine=%s): %s", vid_id, sched_engine, t_path_str)
            except Exception as t_gen_err:
                log.warning("Scheduler auto-generating thumbnail failed: %s", t_gen_err)

        if t_path_str:
            from .thumbnail import resolve_thumbnail_path
            resolved_t = resolve_thumbnail_path(t_path_str, video_id=f.get("id"))
            if resolved_t and resolved_t.exists():
                try:
                    upload_thumbnail_to_youtube(channel_id, vid_id, str(resolved_t))
                    log.info("Scheduler auto-uploaded thumbnail for %s", vid_id)
                except Exception as t_err:
                    log.warning("Scheduler auto-upload thumbnail failed (non-fatal): %s", t_err)

        # Package 3 A/B test titles and thumbnails for YouTube Studio Test & Compare
        try:
            from .thumbnail import generate_ab_test_titles
            ab_titles = generate_ab_test_titles(title, item.get("analysis_json") or "", f.get("filename") or "")
            update_video(item["id"], ab_test_titles_json=__import__("json").dumps(ab_titles, ensure_ascii=False))
            log.info("Scheduler packaged 3 A/B test titles for %s: %s", vid_id, ab_titles)
        except Exception as ab_err:
            log.warning("Scheduler A/B test packaging failed (non-fatal): %s", ab_err)

        # Auto-assign playlist based on genre
        try:
            pl_res = assign_playlist(
                channel_id,
                vid_id,
                item.get("analysis_json") or "",
                channel.get("channel_name") or "",
                filename=f["filename"],
                file_path=f.get("path") or "",
                title=title,
                description=description,
                tags=tags
            )
            update_video(item["id"], youtube_playlist_id=pl_res["playlist_id"])
            log.info("Scheduler auto-assigned playlist '%s' for %s", pl_res.get("playlist_title"), vid_id)
        except Exception as pl_err:
            log.warning("Scheduler auto-assign playlist failed (non-fatal): %s", pl_err)

        notify_scheduler_upload_success(channel.get("channel_name",""), title, vid_id, f["filename"])
        _run_meta_publish(row, item, title, description, hashtags, f["path"], thumbnail_path=t_path_str)

        # Feature 10.5: Automated Initial Velocity Engine (community teaser & poll: Long-form videos only)
        try:
            from .community import publish_community_announcement, is_short_or_reel
            if not is_short_or_reel(item, f.get("path")):
                publish_community_announcement(item["id"])
                log.info("Scheduler automatically triggered community announcement for long-form video %s", vid_id)
            else:
                log.info("Scheduler skipped community announcement for %s: video is Short/Reel", vid_id)
        except Exception as comm_err:
            log.warning("Scheduler community announcement failed (non-fatal): %s", comm_err)

        return "uploaded"
    except Exception as exc:
        update_video(item["id"], status="failed", error_message=str(exc))
        add_upload_attempt(job_id,1,"failed",error_message=str(exc),started_at=attempt_started,finished_at=datetime.now().isoformat())
        update_upload_job(job_id,status="retry_pending",completed_at=datetime.now().isoformat(),last_error=str(exc))
        log.exception("Scheduler upload failed for %s", f["filename"])
        notify_scheduler_upload_failed(channel.get("channel_name",""), f["filename"], str(exc))
        return "failed"


def _run_meta_publish(row, item, title, description, hashtags, file_path, thumbnail_path=None):
    """Publish to Facebook Video, Facebook Reel and/or Instagram Reel after a scheduled YouTube upload."""
    ch_id = item.get("channel_id") or (row.get("channel_id") if isinstance(row, dict) else "")
    ch = get_channel(ch_id) if ch_id else None

    meta_cid = row.get("meta_connection_id") if isinstance(row, dict) else None
    if not meta_cid and ch and ch.get("meta_connection_id"):
        meta_cid = ch["meta_connection_id"]

    # Auto-resolve from matching active Meta connections if still unset
    if not meta_cid:
        try:
            from .database import list_meta_connections
            conns = list_meta_connections()
            ch_name = (ch.get("channel_name") if ch else "").strip().lower()
            for c in conns:
                if c.get("status") == "connected":
                    c_name = (c.get("page_name") or "").strip().lower()
                    if ch_name and c_name and (ch_name == c_name or ch_name in c_name or c_name in ch_name):
                        meta_cid = c["id"]
                        log.info("Auto-resolved Meta connection %s ('%s') for channel '%s'", meta_cid, c.get("page_name"), ch_name)
                        break
        except Exception as res_err:
            log.warning("Auto-resolving Meta connection encountered non-fatal error: %s", res_err)

    if not meta_cid:
        log.info("Scheduler Meta publish skipped: no Meta connection linked or resolved for channel %s", ch_id)
        return

    fb_video = bool(row.get("publish_facebook_video")) if isinstance(row, dict) else False
    fb_reel  = bool(row.get("publish_facebook_reel")) if isinstance(row, dict) else False
    ig_reel  = bool(row.get("publish_instagram_reel")) if isinstance(row, dict) else False

    # Auto-enable if Meta connection exists but all flags were 0 (e.g. freshly connected or unconfigured schedule)
    if not (fb_video or fb_reel or ig_reel):
        conn_check = get_meta_connection(int(meta_cid))
        if conn_check:
            fb_video = True
            fb_reel = True
            if conn_check.get("instagram_user_id") or conn_check.get("instagram_username"):
                ig_reel = True
            log.info("Auto-enabled Meta publishing (FB Video=%s, FB Reel=%s, IG Reel=%s) for connection %s (%s)",
                     fb_video, fb_reel, ig_reel, meta_cid, conn_check.get("page_name"))

    if not (fb_video or fb_reel or ig_reel):
        return

    from .thumbnail import resolve_local_video_path
    resolved_file = resolve_local_video_path(file_path) or file_path
    if not str(resolved_file).lower().endswith(".mp4"):
        log.warning("Scheduler Meta publish skipped: not an MP4 file: %s", file_path)
        return
    if not Path(resolved_file).exists():
        log.warning("Scheduler Meta publish skipped: file does not exist: %s", resolved_file)
        return

    connection = get_meta_connection(int(meta_cid))
    if not connection or connection.get("status") == "logged_out":
        log.warning("Scheduler Meta publish skipped: connection %s not available", meta_cid)
        return
    token_data = load_page_token(connection["token_key"]) or {}
    token = token_data.get("access_token")
    if not token:
        log.warning("Scheduler Meta publish skipped: no access token for connection %s", meta_cid)
        return
    caption = description
    if hashtags:
        hs = " ".join("#" + h.lstrip("#") for h in hashtags)
        if hs.lower() not in caption.lower():
            caption = (caption + "\n\n" + hs).strip()

    # Resolve thumbnail if provided
    resolved_thumb = None
    if thumbnail_path:
        from .thumbnail import resolve_thumbnail_path
        t_cand = resolve_thumbnail_path(thumbnail_path, video_id=item.get("id"))
        if t_cand and t_cand.exists():
            resolved_thumb = str(t_cand)

    # Feature toggles
    enable_outros = True
    enable_cross_links = True
    try:
        from .database import get_app_setting
        enable_outros = (get_app_setting("enable_platform_outros") or "1") == "1"
        enable_cross_links = (get_app_setting("enable_cross_platform_links") or "1") == "1"
    except Exception:
        enable_outros = True
        enable_cross_links = True

    # Global cross-system duplicate check: ensure this video (or any copy with matching SHA)
    # has not already been published to Facebook or Instagram
    from .database import is_video_published_on_platform
    item_sha = item.get("sha256") or ""
    already_fb_video = bool(item.get("facebook_video_id")) or is_video_published_on_platform(sha256=item_sha, video_id=item.get("id"), platform="facebook_video")
    already_fb_reel  = bool(item.get("facebook_reel_id")) or is_video_published_on_platform(sha256=item_sha, video_id=item.get("id"), platform="facebook_reel")
    already_ig_reel  = bool(item.get("instagram_media_id")) or is_video_published_on_platform(sha256=item_sha, video_id=item.get("id"), platform="instagram_reel")

    # Prepare Facebook file & caption with outro and cross-links
    fb_file = resolved_file
    if enable_outros and (fb_video or fb_reel) and not (already_fb_video and already_fb_reel):
        try:
            from .video_utils import append_platform_outro
            fb_file = append_platform_outro(resolved_file, platform="facebook", channel_name=connection.get("page_name",""))
            log.info("Appended Facebook 'Follow Page' outro: %s", fb_file)
        except Exception as fb_out_err:
            log.warning("Facebook outro append skipped (non-fatal): %s", fb_out_err)
            fb_file = resolved_file

    fb_caption = caption
    if enable_cross_links:
        try:
            from .cross_syndication import generate_cross_platform_links, format_cross_platform_block
            c_links = generate_cross_platform_links(item, connection=connection)
            fb_block = format_cross_platform_block("facebook", c_links)
            if fb_block and c_links.get("youtube_url") and (c_links["youtube_url"] not in fb_caption):
                fb_caption = (fb_caption + "\n" + fb_block).strip()
        except Exception as fb_c_err:
            log.warning("Facebook cross-link formatting skipped: %s", fb_c_err)

    if fb_video and not already_fb_video:
        try:
            result = upload_facebook_page_video(connection["page_id"], token, fb_file, title, fb_caption, True, thumbnail_path=resolved_thumb)
            pub_id = str(result.get("id",""))
            mark_platform_published(item["id"], "facebook_video", pub_id)
            log.info("Scheduler Facebook Video published: page=%s video_id=%s file=%s",
                     connection["page_id"], pub_id, Path(fb_file).name)
            notify_meta_publish_success("facebook_video", connection.get("page_name",""), title, Path(fb_file).name, pub_id)
        except Exception as exc:
            log.error("Scheduler Facebook Video failed: %s", exc)
            notify_meta_publish_failed("facebook_video", connection.get("page_name",""), Path(fb_file).name, str(exc))
    elif fb_video and already_fb_video:
        log.info("Scheduler Facebook Video skipped: video %s (sha=%s) is already published", Path(resolved_file).name, item_sha[:10])

    if fb_reel and not already_fb_reel:
        if fb_video:
            time.sleep(5)  # Enforce pacing gap between video and reel
        try:
            result = upload_facebook_page_reel(connection["page_id"], token, fb_file, title, fb_caption, thumbnail_path=resolved_thumb)
            pub_id = str(result.get("post_id") or result.get("id") or "")
            mark_platform_published(item["id"], "facebook_reel", pub_id)
            log.info("Scheduler Facebook Reel published: page=%s video_id=%s file=%s",
                     connection["page_id"], pub_id, Path(fb_file).name)
            notify_meta_publish_success("facebook_reel", connection.get("page_name",""), title, Path(fb_file).name, pub_id)
        except Exception as exc:
            log.error("Scheduler Facebook Reel failed: %s", exc)
            notify_meta_publish_failed("facebook_reel", connection.get("page_name",""), Path(fb_file).name, str(exc))
    elif fb_reel and already_fb_reel:
        log.info("Scheduler Facebook Reel skipped: video %s (sha=%s) is already published", Path(resolved_file).name, item_sha[:10])

    if ig_reel and not already_ig_reel:
        if not connection.get("instagram_user_id"):
            log.warning("Scheduler Instagram Reel skipped: no Instagram account linked to page %s", connection["page_id"])
            return

        ig_file = resolved_file
        if enable_outros:
            try:
                from .video_utils import append_platform_outro
                ig_file = append_platform_outro(resolved_file, platform="instagram", handle=connection.get("instagram_username",""))
                log.info("Appended Instagram 'Follow' outro: %s", ig_file)
            except Exception as ig_out_err:
                log.warning("Instagram outro append skipped (non-fatal): %s", ig_out_err)
                ig_file = resolved_file

        ig_caption = caption
        if enable_cross_links:
            try:
                from .cross_syndication import generate_cross_platform_links, format_cross_platform_block
                c_links = generate_cross_platform_links(item, connection=connection)
                ig_block = format_cross_platform_block("instagram", c_links)
                if ig_block and c_links.get("youtube_url") and (c_links["youtube_url"] not in ig_caption):
                    ig_caption = (ig_caption + "\n" + ig_block).strip()
            except Exception as ig_c_err:
                log.warning("Instagram cross-link formatting skipped: %s", ig_c_err)

        try:
            result = upload_instagram_reel(connection["instagram_user_id"], token, ig_file, ig_caption, True)
            pub_id = str(result.get("id",""))
            mark_platform_published(item["id"], "instagram_reel", pub_id)
            log.info("Scheduler Instagram Reel published: ig_user=%s media_id=%s file=%s",
                     connection["instagram_user_id"], pub_id, Path(ig_file).name)
            notify_meta_publish_success("instagram_reel", connection.get("page_name",""), title, Path(ig_file).name, pub_id)
        except Exception as exc:
            log.error("Scheduler Instagram Reel failed: %s", exc)
            notify_meta_publish_failed("instagram_reel", connection.get("page_name",""), Path(ig_file).name, str(exc))
    elif ig_reel and already_ig_reel:
        log.info("Scheduler Instagram Reel skipped: video %s (sha=%s) is already published", Path(resolved_file).name, item_sha[:10])

    # Now backlink all platform URLs into YouTube description and comments
    if enable_cross_links:
        try:
            from .cross_syndication import syndicate_cross_platform_links_for_video
            syndicate_cross_platform_links_for_video(item["id"])
            log.info("Scheduler synchronized cross-platform links on YouTube for video ID %s", item["id"])
        except Exception as syn_err:
            log.warning("Scheduler cross-platform link syndication encountered non-fatal error: %s", syn_err)


def _process_retry_jobs():
    """Durable retry queue for AI analysis and YouTube uploads.

    Rules:
      - Never lose a scheduled video because Gemini/network failed.
      - Retry with exponential backoff.
      - Check the YouTube hash marker before every upload retry.
      - Do not permanently mark a video failed until the retry budget is exhausted.
      - When an upload retry completes, also execute Meta publishing (Facebook/Instagram),
        update playlist/A/B testing, and mark the schedule run.
    """
    from datetime import timedelta
    import json as _json
    import time as _time

    # Periodic auto-recovery check: revive failed jobs whose issue has resolved
    now_ts = _time.time()
    if not hasattr(_process_retry_jobs, "_last_reconcile"):
        _process_retry_jobs._last_reconcile = 0.0
    if now_ts - _process_retry_jobs._last_reconcile >= 180:  # every 3 minutes
        _process_retry_jobs._last_reconcile = now_ts
        try:
            from .failure_tracker import reconcile_failed_jobs
            rec = reconcile_failed_jobs()
            if rec.get("recovered_count", 0) > 0:
                log.info("[AutoRecovery] Re-queued %d previously failed jobs whose issues resolved", rec["recovered_count"])
        except Exception as _rec_err:
            log.warning("[AutoRecovery] Reconciliation error: %s", _rec_err)

    from .database import list_upload_jobs
    retry_jobs = list_upload_jobs("retry_pending", limit=20)
    pending_jobs = list_upload_jobs("pending", limit=max(1, 20 - len(retry_jobs))) if len(retry_jobs) < 20 else []
    seen_ids = set()
    jobs = []
    for j in retry_jobs + pending_jobs:
        if j["id"] not in seen_ids:
            seen_ids.add(j["id"])
            jobs.append(j)

    now = datetime.now()

    for job in jobs:
        item = get_inventory_by_id(job.get("video_id")) if job.get("video_id") else None
        channel = get_channel(job["channel_id"])
        if not item or not channel:
            update_upload_job(job["id"], status="failed", last_error="Video or channel no longer exists.")
            continue

        schedule = get_schedule(job["schedule_id"]) if job.get("schedule_id") else {}

        if item.get("status") == "uploaded":
            update_upload_job(job["id"], status="completed",
                              completed_at=datetime.now().isoformat(),
                              youtube_video_id=item.get("youtube_video_id",""), last_error="")
            # Check if Meta platforms need publishing:
            meta_sched_row = dict(schedule) if schedule else {}
            if not meta_sched_row.get("channel_id"):
                meta_sched_row["channel_id"] = item.get("channel_id")
            t_p = item.get("thumbnail_path") or ""
            h_tags = [x.strip().lstrip("#").lower() for x in str(item.get("hashtags") or "").replace(",", " ").split() if x.strip()]
            _run_meta_publish(meta_sched_row, item, item.get("title") or item["filename"], item.get("description") or "", h_tags, item["file_path"], thumbnail_path=t_p)
            continue

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
        from .thumbnail import resolve_local_video_path
        resolved_file_path = resolve_local_video_path(item["file_path"]) or item["file_path"]
        if not Path(resolved_file_path).exists():
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
            mark_platform_published(item["id"], "youtube", remote["youtube_video_id"])
            update_upload_job(job["id"], status="completed",
                              completed_at=datetime.now().isoformat(),
                              youtube_video_id=remote["youtube_video_id"], last_error="")
            log.info("Retry queue found already-uploaded video: job=%s youtube=%s",
                     job["id"], remote["youtube_video_id"])
            # Publish to Meta if configured or auto-resolved:
            meta_sched_row = dict(schedule) if schedule else {}
            if not meta_sched_row.get("channel_id"):
                meta_sched_row["channel_id"] = item.get("channel_id")
            t_p = item.get("thumbnail_path") or ""
            h_tags = [x.strip().lstrip("#").lower() for x in str(item.get("hashtags") or "").replace(",", " ").split() if x.strip()]
            _run_meta_publish(meta_sched_row, item, item.get("title") or item["filename"], item.get("description") or "", h_tags, resolved_file_path, thumbnail_path=t_p)
            continue

        # If AI metadata is not available, retry AI first.
        if not item.get("title") or item.get("ai_status") != "completed":
            try:
                prov = ((schedule or {}).get("ai_provider") or "gemini").lower()
                if prov in ("openai", "chatgpt"):
                    from .ai_multiprovider import analyze_chatgpt
                    ai = analyze_chatgpt(resolved_file_path, item["filename"])
                elif prov in ("claude", "anthropic"):
                    from .ai_multiprovider import analyze_claude
                    ai = analyze_claude(resolved_file_path, item["filename"])
                else:
                    connection_id = (schedule or {}).get("ai_connection_id")
                    secondary_id = (schedule or {}).get("ai_secondary_connection_id")
                    ai, used_cid, conns_map = _execute_gemini_with_fallback(resolved_file_path, item["filename"], connection_id, secondary_id, context_label="AI Retry")
                md = ai["metadata"]
                hashtags = [str(x).strip().lstrip("#").lower() for x in md.get("hashtags", []) if str(x).strip()]
                tags = [str(x).strip().lstrip("#").lower() for x in md.get("tags", []) if str(x).strip()]
                update_video(
                    item["id"], ai_status="completed", ai_summary=md["summary"],
                    transcript=ai.get("transcript", ""), title=md["title"],
                    description=md["description"], hashtags=", ".join(md["hashtags"]),
                    tags=", ".join(tags), analysis_json=_json.dumps(ai, ensure_ascii=False),
                    status="pending", error_message=""
                )
                item = get_inventory_by_id(item["id"])
                log.info("Retry AI analysis succeeded: job=%s video=%s provider=%s account=%s", _format_job(job["id"], item["filename"]), item["filename"], prov, _format_ai_conn(used_cid, conns_map))
            except Exception as exc:
                msg = f"AI retry failed: {exc}"
                update_upload_job(job["id"], status="retry_pending",
                                  attempt_count=attempt_no, last_error=msg)
                update_video(item["id"], status="pending", ai_status="failed",
                             error_message=msg)
                log.warning("AI retry failed: job=%s channel=%s attempt=%s: %s", _format_job(job["id"], item.get("filename")), _format_channel(job["channel_id"]), attempt_no, exc)
                continue

        tags = [x.strip().lstrip("#").lower() for x in str(item.get("tags") or "").split(",") if x.strip()]
        hashtags = [x.strip().lstrip("#").lower() for x in str(item.get("hashtags") or "").replace(",", " ").split() if x.strip()]
        title = item.get("title") or Path(item["filename"]).stem
        description = compose_description(item.get("description",""), hashtags)

        # Proactive Daily Quota Check
        try:
            from .quota_tracker import can_perform_upload, record_api_quota, notify_quota_paused_if_needed
            can_proceed, quota_summary = can_perform_upload(channel_id=job["channel_id"])
            if not can_proceed:
                err_msg = quota_summary.get("rejection_reason", "Daily YouTube quota limit approaching.")
                log.warning("[QUOTA PAUSE] Job %s channel=%s held: %s", _format_job(job["id"], item.get("filename")), _format_channel(job["channel_id"]), err_msg)
                update_upload_job(job["id"], status="quota_paused", last_error=err_msg)
                update_video(item["id"], status="quota_paused", error_message=err_msg)
                notify_quota_paused_if_needed(quota_summary)
                return "quota_paused"
        except Exception as q_check_err:
            log.warning("[QUOTA GATE] Quota check non-fatal error: %s", q_check_err)

        started = datetime.now().isoformat()
        update_upload_job(job["id"], status="uploading", started_at=started,
                          attempt_count=attempt_no, last_error="")
        update_video(item["id"], status="uploading", error_message="")
        add_upload_attempt(job["id"], attempt_no, "started", started_at=started)
        try:
            result = upload_video(
                channel_id=job["channel_id"], file_path=resolved_file_path, title=title,
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
            mark_platform_published(item["id"], "youtube", vid)
            try:
                from .quota_tracker import record_api_quota
                record_api_quota(service="youtube", operation="videos.insert", units=1600, channel_id=job["channel_id"], details=vid)
            except Exception:
                pass
            add_upload_attempt(job["id"], attempt_no, "success", youtube_video_id=vid,
                               started_at=started, finished_at=datetime.now().isoformat())
            update_upload_job(job["id"], status="completed",
                              completed_at=datetime.now().isoformat(),
                              youtube_video_id=vid, last_error="")
            log.info("Retry upload succeeded: job=%s channel=%s youtube=%s", _format_job(job["id"], item.get("filename")), _format_channel(job["channel_id"]), vid)

            # Auto-upload thumbnail if present
            t_path_str = item.get("thumbnail_path") or ""
            from .thumbnail import resolve_thumbnail_path
            resolved_t = resolve_thumbnail_path(t_path_str, video_id=item['id']) if t_path_str else None
            if not resolved_t:
                resolved_t = resolve_thumbnail_path(f"video_{item['id']}_v1.png", video_id=item['id'])
            if resolved_t and resolved_t.exists():
                try:
                    upload_thumbnail_to_youtube(item["channel_id"], vid, str(resolved_t))
                    log.info("Scheduler retry auto-uploaded thumbnail for %s", vid)
                except Exception as t_err:
                    log.warning("Scheduler retry auto-upload thumbnail failed (non-fatal): %s", t_err)

            # Package 3 A/B test titles
            try:
                from .thumbnail import generate_ab_test_titles
                ab_titles = generate_ab_test_titles(title, item.get("analysis_json") or "", item.get("filename") or "")
                update_video(item["id"], ab_test_titles_json=_json.dumps(ab_titles, ensure_ascii=False))
                log.info("Scheduler retry packaged 3 A/B test titles for %s: %s", vid, ab_titles)
            except Exception as ab_err:
                log.warning("Scheduler retry A/B test packaging failed (non-fatal): %s", ab_err)

            # Auto-assign playlist
            try:
                ch = get_channel(item.get("channel_id"))
                pl_res = assign_playlist(
                    item["channel_id"],
                    vid,
                    item.get("analysis_json") or "",
                    (ch or {}).get("channel_name") or "",
                    filename=item.get("filename") or "",
                    file_path=resolved_file_path,
                    title=title,
                    description=description,
                    tags=tags
                )
                update_video(item["id"], youtube_playlist_id=pl_res["playlist_id"])
                log.info("Scheduler retry auto-assigned playlist '%s' for %s", pl_res.get("playlist_title"), vid)
            except Exception as pl_err:
                log.warning("Scheduler retry auto-assign playlist failed (non-fatal): %s", pl_err)

            # Send Telegram notification
            notify_scheduler_upload_success((channel or {}).get("channel_name",""), title, vid, item["filename"])

            # Publish to Meta (Facebook Video, Facebook Reel, Instagram Reel)
            meta_sched_row = dict(schedule) if schedule else {}
            if not meta_sched_row.get("channel_id"):
                meta_sched_row["channel_id"] = item.get("channel_id")
            _run_meta_publish(meta_sched_row, item, title, item.get("description",""), hashtags, resolved_file_path, thumbnail_path=t_path_str)

            # Feature 10.5: Automated Initial Velocity Engine (Long-form videos only)
            try:
                from .community import publish_community_announcement, is_short_or_reel
                if not is_short_or_reel(item, resolved_file_path):
                    publish_community_announcement(item["id"])
                    log.info("Retry upload triggered community announcement for long-form video %s", vid)
                else:
                    log.info("Retry upload skipped community announcement for %s: video is Short/Reel", vid)
            except Exception as comm_err:
                log.warning("Retry upload community announcement failed (non-fatal): %s", comm_err)

            # Mark schedule run
            if job.get("schedule_id"):
                today_s = datetime.now().strftime("%Y-%m-%d")
                mark_schedule_run(job["schedule_id"], today_s)
                record_scheduler_run(job["schedule_id"], today_s, "success", "uploaded")
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
            log.warning("Retry upload failed: job=%s channel=%s attempt=%s/%s: %s",
                        _format_job(job["id"], item.get("filename")), _format_channel(job["channel_id"]), attempt_no, 5, msg)


def get_smart_traffic_window_info(dt=None):
    """
    Evaluates audience traffic availability for peak Indian viewer windows (IST/Local).
    Windows:
      - Morning Peak: 08:30 – 10:30 (Score: 85)
      - Lunch Break Peak: 12:30 – 14:30 (Score: 95)
      - Evening Peak: 17:30 – 19:30 (Score: 100)
      - Night Prime Peak: 20:30 – 22:30 (Score: 90)
      - Standard Daytime: 08:00 – 21:00 outside peaks (Score: 65)
      - Night Rest / Paused: 22:30 – 08:00 (Score: 0 - Deferred to protect CTR & satisfaction)
    """
    if dt is None:
        dt = datetime.now()
    cur_m = dt.hour * 60 + dt.minute

    windows = [
        {"name": "Morning Peak", "start": "08:30", "end": "10:30", "start_m": 510, "end_m": 630, "score": 85, "badge": "🌅 High Traffic"},
        {"name": "Lunch Break Peak", "start": "12:30", "end": "14:30", "start_m": 750, "end_m": 870, "score": 95, "badge": "🍱 Peak Engagement"},
        {"name": "Evening Peak", "start": "17:30", "end": "19:30", "start_m": 1050, "end_m": 1170, "score": 100, "badge": "🔥 Maximum Traffic"},
        {"name": "Night Prime Peak", "start": "20:30", "end": "22:30", "start_m": 1230, "end_m": 1350, "score": 90, "badge": "🌙 Prime Time"}
    ]

    for w in windows:
        if w["start_m"] <= cur_m < w["end_m"]:
            mins_left = w["end_m"] - cur_m
            return {
                "in_peak": True,
                "window_name": w["name"],
                "badge": w["badge"],
                "score": w["score"],
                "start": w["start"],
                "end": w["end"],
                "time_range": f"{w['start']} – {w['end']}",
                "minutes_remaining_in_window": mins_left,
                "can_upload": True,
                "status_text": f"Active Traffic Peak: {w['name']} ({w['start']}–{w['end']}) · Score: {w['score']}/100"
            }

    from .optimizer import is_optimizer_time_window_active
    is_daytime, _ = is_optimizer_time_window_active()

    next_window = None
    min_delta = 99999
    for w in windows:
        delta = w["start_m"] - cur_m
        if delta < 0:
            delta += 1440
        if delta < min_delta:
            min_delta = delta
            next_window = w

    if is_daytime and 480 <= cur_m <= 1260:
        return {
            "in_peak": False,
            "window_name": "Standard Daytime",
            "badge": "⚡ Moderate Traffic",
            "score": 65,
            "start": "08:00",
            "end": "21:00",
            "time_range": "08:00 – 21:00",
            "next_peak_name": next_window["name"] if next_window else "Morning Peak",
            "next_peak_in_minutes": min_delta,
            "next_peak_time": next_window["start"] if next_window else "08:30",
            "can_upload": True,
            "status_text": f"Standard Daytime Traffic · Next Peak: {next_window['name']} in {min_delta}m"
        }

    return {
        "in_peak": False,
        "window_name": "Night Rest Window",
        "badge": "⏸️ Off-Peak (Paused)",
        "score": 0,
        "start": "22:30",
        "end": "08:00",
        "time_range": "22:30 – 08:00",
        "next_peak_name": next_window["name"] if next_window else "Morning Peak",
        "next_peak_in_minutes": min_delta,
        "next_peak_time": next_window["start"] if next_window else "08:30",
        "can_upload": False,
        "status_text": f"Night Off-Peak Paused (Protects CTR) · Next Upload Window: {next_window['name']} at {next_window['start']}"
    }


def is_upload_held(now=None):
    """
    Checks if uploads are temporarily held (e.g. hold today's uploads, resume tomorrow).
    Returns (is_held: bool, hold_until_date: str or None).
    """
    from datetime import datetime
    if now is None:
        now = datetime.now()
    hold_until = (get_app_setting("scheduler_hold_until_date") or "").strip()
    if not hold_until:
        return False, None
    today_str = now.strftime("%Y-%m-%d")
    if hold_until > today_str:
        return True, hold_until
    return False, None


def snap_to_peak_traffic_window(cand_time):
    """
    Snaps a candidate upload datetime to the current or nearest upcoming peak traffic window.
    Peak Windows (IST / Local):
      - Morning: 08:30 – 10:30 (Score: 85)
      - Lunch:   12:30 – 14:30 (Score: 95)
      - Evening: 17:30 – 19:30 (Score: 100)
      - Night:   20:30 – 22:30 (Score: 90)
    Off-peaks / Valleys:
      - 22:30 to 08:30 -> snap to tomorrow 08:30
      - 10:30 to 12:30 -> snap to 12:30
      - 14:30 to 17:30 -> snap to 17:30
      - 19:30 to 20:30 -> snap to 20:30
    """
    from datetime import datetime, timedelta
    cur_m = cand_time.hour * 60 + cand_time.minute
    d = cand_time.date()

    if cur_m < 510:  # Before 08:30 AM
        return datetime.combine(d, datetime.strptime("08:30", "%H:%M").time())
    elif 510 <= cur_m < 630:  # Inside Morning Peak (08:30 - 10:30)
        return cand_time
    elif 630 <= cur_m < 750:  # Between 10:30 and 12:30
        return datetime.combine(d, datetime.strptime("12:30", "%H:%M").time())
    elif 750 <= cur_m < 870:  # Inside Lunch Peak (12:30 - 14:30)
        return cand_time
    elif 870 <= cur_m < 1050:  # Between 14:30 and 17:30
        return datetime.combine(d, datetime.strptime("17:30", "%H:%M").time())
    elif 1050 <= cur_m < 1170:  # Inside Evening Peak (17:30 - 19:30)
        return cand_time
    elif 1170 <= cur_m < 1230:  # Between 19:30 and 20:30
        return datetime.combine(d, datetime.strptime("20:30", "%H:%M").time())
    elif 1230 <= cur_m < 1350:  # Inside Night Peak (20:30 - 22:30)
        return cand_time
    else:  # >= 1350 (After 22:30 Night Rest)
        return datetime.combine(d + timedelta(days=1), datetime.strptime("08:30", "%H:%M").time())


def get_smart_scheduler_status():
    """Returns the comprehensive real-time status of the Smart Traffic Agent and conflict collision guardrails."""
    mode = (get_app_setting("scheduler_mode") or "manual").lower().strip()
    traffic = get_smart_traffic_window_info()
    now_dt = datetime.now()
    held, hold_date = is_upload_held(now_dt)

    CROSS_GAP = 45   # Minimum 45 min gap across any channel
    SAME_GAP = int(get_app_setting("same_channel_gap_minutes") or 270)   # Minimum 4-5 hours (default 270m / 4.5h) gap on same channel
    MAX_DAILY_PER_CHANNEL = int(get_app_setting("max_daily_uploads_per_channel") or 2)
    today_str = now_dt.strftime("%Y-%m-%d")

    last_global_iso = get_last_upload_time()
    mins_since_global = 9999.0
    if last_global_iso:
        try:
            from .optimizer import parse_iso_datetime
            from datetime import timezone
            dt_g = parse_iso_datetime(last_global_iso)
            if dt_g:
                mins_since_global = (datetime.now(timezone.utc) - dt_g).total_seconds() / 60.0
        except Exception:
            mins_since_global = 9999.0

    cross_cooldown_active = (mins_since_global < CROSS_GAP)
    cross_mins_remaining = max(0, round(CROSS_GAP - mins_since_global, 1)) if cross_cooldown_active else 0

    from .database import count_channel_uploads_on_date
    channels_data = []
    channels = list_channels()
    total_pending = 0
    ready_channels = []

    for ch in channels:
        cid = ch["channel_id"]
        cname = ch.get("channel_name") or cid
        pending_list = list_pending_inventory(channel_id=cid, limit=20)
        pending_count = len(pending_list)
        total_pending += pending_count

        today_uploads = count_channel_uploads_on_date(cid, today_str)
        daily_cap_reached = (today_uploads >= MAX_DAILY_PER_CHANNEL)

        last_ch_iso = get_last_upload_time(channel_id=cid)
        mins_since_ch = 9999.0
        if last_ch_iso:
            try:
                from .optimizer import parse_iso_datetime
                from datetime import timezone
                dt_ch = parse_iso_datetime(last_ch_iso)
                if dt_ch:
                    mins_since_ch = (datetime.now(timezone.utc) - dt_ch).total_seconds() / 60.0
            except Exception:
                mins_since_ch = 9999.0

        same_cooldown = (mins_since_ch < SAME_GAP)
        same_mins_left = max(0, round(SAME_GAP - mins_since_ch, 1)) if same_cooldown else 0

        is_ready = (not held) and (not cross_cooldown_active) and (not same_cooldown) and (not daily_cap_reached) and (pending_count > 0) and traffic["can_upload"]
        if is_ready:
            ready_channels.append(cid)

        status_badge = "Ready for Upload"
        if held:
            status_badge = f"On Hold (Resumes {hold_date})"
        elif not traffic["can_upload"]:
            status_badge = "Night Off-Peak Paused"
        elif daily_cap_reached:
            status_badge = f"Daily Cap Reached ({today_uploads}/{MAX_DAILY_PER_CHANNEL} today)"
        elif cross_cooldown_active:
            status_badge = f"Global Cooling ({cross_mins_remaining:.0f}m)"
        elif same_cooldown:
            status_badge = f"Channel Cooling ({same_mins_left:.0f}m)"
        elif pending_count == 0:
            status_badge = "No Pending Videos"

        channels_data.append({
            "channel_id": cid,
            "channel_name": cname,
            "last_upload_at": last_ch_iso,
            "minutes_since_last_upload": round(mins_since_ch, 1) if mins_since_ch < 9999 else None,
            "cooldown_active": same_cooldown,
            "minutes_remaining": same_mins_left,
            "today_uploaded_count": today_uploads,
            "max_daily_uploads": MAX_DAILY_PER_CHANNEL,
            "daily_cap_reached": daily_cap_reached,
            "pending_count": pending_count,
            "is_ready": is_ready,
            "status_badge": status_badge,
            "next_video_title": pending_list[0].get("title") or pending_list[0].get("filename") if pending_list else None
        })

    from .config import get_app_env, is_dev_env
    env_name = get_app_env()
    is_dev = is_dev_env()

    return {
        "mode": mode,
        "is_auto": mode == "auto",
        "environment": env_name,
        "is_dev": is_dev,
        "is_prod": not is_dev,
        "background_tasks_active": (not is_dev) and (get_app_setting("scheduler_paused") or "0") != "1" and (not held),
        "env_notice": "Development mode active: automatic background tasks are disabled. Single-shot manual testing is enabled." if is_dev else None,
        "hold": {
            "is_held": held,
            "hold_until": hold_date,
            "today": now_dt.strftime("%Y-%m-%d")
        },
        "traffic": traffic,
        "cross_channel": {
            "min_gap_minutes": CROSS_GAP,
            "last_upload_at": last_global_iso,
            "minutes_since_last_upload": round(mins_since_global, 1) if mins_since_global < 9999 else None,
            "cooldown_active": cross_cooldown_active,
            "minutes_remaining": cross_mins_remaining,
            "is_clear": not cross_cooldown_active
        },
        "channels": channels_data,
        "ready_channels_count": len(ready_channels),
        "total_pending_inventory": total_pending,
        "next_recommended_channel": ready_channels[0] if ready_channels else None,
        "evaluation_timestamp": now_dt.strftime("%Y-%m-%d %I:%M:%S %p")
    }


def _run_smart_auto_agent_step(now=None):
    """Autonomous execution step for Smart Auto Traffic Agent."""
    if now is None:
        now = datetime.now()

    held, hold_date = is_upload_held(now)
    if held:
        log.info("[SMART_AGENT] Uploads on hold until %s. Today's uploads are paused.", hold_date)
        return "hold_active_deferred"

    status = get_smart_scheduler_status()
    if not status["traffic"]["can_upload"]:
        return "off_peak_deferred"

    if status["cross_channel"]["cooldown_active"]:
        return "cross_channel_cooldown"

    eligible_channels = [c for c in status["channels"] if c["is_ready"]]
    if not eligible_channels:
        return "no_eligible_channel"

    # Prioritize channel with longest wait since last upload, then most pending
    eligible_channels.sort(
        key=lambda c: (
            -(c["minutes_since_last_upload"] if c["minutes_since_last_upload"] is not None else 99999),
            -c["pending_count"]
        )
    )

    chosen = eligible_channels[0]
    ch_id = chosen["channel_id"]
    pending_items = list_pending_inventory(channel_id=ch_id, limit=200)
    if not pending_items:
        return "no_pending_video"

    target_item = select_next_continuity_video(ch_id, pending_items) or pending_items[0]
    ch = get_channel(ch_id) or {}

    log.info(
        "[SMART_AGENT] Auto Traffic Window active (%s - Score: %d/100). Selected channel '%s' for conflict-free upload: '%s'",
        status["traffic"]["window_name"], status["traffic"]["score"],
        ch.get("channel_name", ch_id), target_item.get("title") or target_item.get("filename")
    )

    synth_row = {
        "id": None,
        "channel_id": ch_id,
        "privacy_status": ch.get("privacy_status") or "public",
        "category_id": ch.get("default_category_id") or "24",
        "made_for_kids": 0,
        "contains_synthetic_media": 1,
        "video_language": "hi",
        "title_description_language": "hi",
        "time_local": now.strftime("%H:%M"),
        "ai_provider": "gemini",
        "ai_connection_id": None,
        "thumbnail_engine": "flow",
        "publish_facebook_video": 1,
        "publish_facebook_reel": 1,
        "publish_instagram_reel": 1,
        "meta_connection_id": ch.get("meta_connection_id")
    }

    if not _lock.acquire(blocking=False):
        log.info("[SMART_AGENT] Upload worker currently busy; waiting for next loop")
        return "busy"

    try:
        res = _run_schedule(synth_row, specific_item=target_item)
        log.info("[SMART_AGENT] Smart Auto upload completed for video ID %s: %s", target_item["id"], res)
        return res
    finally:
        _lock.release()


def _loop():
    _last_expiry_check = [0.0]
    _last_notified: dict[str, float] = {}
    _NOTIFY_INTERVAL = 86400  # 24 hours
    _last_dev_notice = 0.0
    _last_hold_notice = 0.0

    while not _stop.wait(15):
        import time as _time
        try:
            from .database import get_app_setting
            from .config import is_dev_env, get_app_env

            # 1. Respect manual/emergency pause setting
            if (get_app_setting("scheduler_paused") or "0") == "1":
                continue

            # 2. Check hold setting (e.g. hold today's uploads, resume tomorrow)
            held, hold_date = is_upload_held()
            if held:
                now_ts = _time.time()
                if now_ts - _last_hold_notice >= 3600:
                    log.info("[SCHEDULER] Uploads are currently held until %s (Holding today's uploads). Automatic tasks will resume on %s.", hold_date, hold_date)
                    _last_hold_notice = now_ts
                continue

            # 3. Strict Environment Rule:
            # If running in DEV environment, DO NOT execute any automatic/scheduled tasks in background.
            # In DEV, tasks are only performed ONCE when explicitly triggered for testing.
            if is_dev_env():
                now_ts = _time.time()
                if now_ts - _last_dev_notice >= 3600:
                    log.info("[SCHEDULER] Environment is '%s': Automatic scheduled background tasks are disabled. (One-shot testing available via manual trigger).", get_app_env())
                    _last_dev_notice = now_ts
                continue
        except Exception:
            pass
        except Exception:
            pass

        now = datetime.now()
        # Check token expiry and run automated vault refresh once per hour
        if _time.time() - _last_expiry_check[0] >= 3600:
            try:
                try:
                    refresh_all_credentials()
                except Exception as _vault_err:
                    log.warning("Automated credential vault refresh failed: %s", _vault_err)

                from .database import get_youtube_reauth_alerts, list_meta_connections
                from .meta import load_page_token
                from .telegram import notify_token_expiring, notify_token_expired
                now_ts = _time.time()
                for alert in get_youtube_reauth_alerts(threshold_days=2):
                    key = f"yt_{alert['id']}"
                    days = alert.get("days_remaining")
                    needs_notify = alert.get("reauth_required") or (days is not None and days <= 2.0)
                    if needs_notify:
                        if now_ts - _last_notified.get(key, 0) >= _NOTIFY_INTERVAL:
                            if alert.get("reauth_required"):
                                notify_token_expired(alert["account_name"])
                            elif days is not None and days <= 2.0:
                                notify_token_expiring(alert["account_name"], days, service="YouTube")
                            _last_notified[key] = now_ts
                    else:
                        _last_notified.pop(key, None)
                for mc in list_meta_connections():
                    key = f"meta_{mc['id']}"
                    if mc.get("status") == "logged_out" or not load_page_token(mc["token_key"]):
                        if now_ts - _last_notified.get(key, 0) >= _NOTIFY_INTERVAL:
                            from .telegram import notify_meta_token_expiring
                            notify_meta_token_expiring(mc.get("page_name",""))
                            _last_notified[key] = now_ts
                    else:
                        _last_notified.pop(key, None)
            except Exception:
                log.exception("Token expiry check failed")
            _last_expiry_check[0] = _time.time()

        # Check and run 7-15 day automated performance audit & A/B optimization every 6 hours in background thread
        if not hasattr(_loop, "_last_perf_audit"):
            _loop._last_perf_audit = _time.time()
        if _time.time() - _loop._last_perf_audit >= 21600:
            _loop._last_perf_audit = _time.time()
            def _bg_perf_audit():
                try:
                    from .database import get_app_setting
                    if (get_app_setting("scheduler_paused") or "0") == "1":
                        log.info("[SCHEDULER] Background performance audit skipped: scheduler is paused.")
                        return
                    if (get_app_setting("enable_auto_optimizer") or "0") != "1":
                        log.info("[SCHEDULER] Background performance audit skipped: auto-optimizer is disabled.")
                        return
                    from .optimizer import run_global_performance_audit, is_optimizer_time_window_active
                    is_active, win_msg = is_optimizer_time_window_active()
                    if not is_active:
                        log.info("[SCHEDULER] Video optimizer background audit deferred (night hours): %s", win_msg)
                        return
                    log.info("Running scheduled 7-15 day video performance audit in background (daytime window)...")
                    audit_summary = run_global_performance_audit(max_videos_per_channel=1)
                    log.info("Scheduled performance audit finished: %s", audit_summary)
                except Exception as opt_err:
                    log.warning("Scheduled performance audit encountered an error: %s", opt_err)
            threading.Thread(target=_bg_perf_audit, name="scheduler-perf-audit", daemon=True).start()

        try:
            _process_retry_jobs()
        except Exception:
            log.exception("Retry queue processing failed")

        scheduler_mode = (get_app_setting("scheduler_mode") or "manual").lower().strip()
        if scheduler_mode == "auto":
            # Smart Auto Traffic Agent mode
            if not hasattr(_loop, "_last_auto_check"):
                _loop._last_auto_check = 0.0
            if _time.time() - _loop._last_auto_check >= 45:
                _loop._last_auto_check = _time.time()
                try:
                    _run_smart_auto_agent_step(now)
                except Exception as auto_err:
                    log.warning("[SMART_AGENT] Auto execution error: %s", auto_err)
        else:
            # Manual mode: process user-configured weekday and time slots
            weekday = now.weekday()
            current_minute = now.hour * 60 + now.minute
            today = now.strftime("%Y-%m-%d")
            schedules = list_schedules()

            due_schedules = []
            for row in schedules:
                if not row.get("enabled") or int(row.get("weekday", -1)) != weekday:
                    continue
                if row.get("last_run_date") == today:
                    continue
                time_str = str(row.get("time_local") or "").strip()
                if not time_str or ":" not in time_str:
                    continue
                try:
                    sh, sm = map(int, time_str.split(":"))
                    slot_minute = sh * 60 + sm
                except Exception:
                    continue
                delay_minutes = current_minute - slot_minute
                if 0 <= delay_minutes <= 120:
                    due_schedules.append((slot_minute, row))
                elif delay_minutes > 120:
                    mark_schedule_run(row["id"], today)
                    record_scheduler_run(row["id"], today, "skipped", f"Missed window (scheduled for {time_str})")

            due_schedules.sort(key=lambda x: x[0])
            for _, row in due_schedules:
                if not _lock.acquire(blocking=False):
                    break
                try:
                    fresh_row = get_schedule(row["id"])
                    if fresh_row and fresh_row.get("last_run_date") == today:
                        continue
                    result = _run_schedule(row)
                    if result in {"uploaded", "duplicate_synced", "nothing_to_upload", "ai_retry_pending", "held_for_review", "quota_paused"}:
                        mark_schedule_run(row["id"], today)
                        record_scheduler_run(row["id"], today, "paused" if result == "quota_paused" else ("success" if result != "ai_retry_pending" else "pending"), result)
                    else:
                        record_scheduler_run(row["id"], today, "failed", result)
                    log.info("Schedule %s (%s) completed: %s", row["id"], _format_channel(row["channel_id"]), result)
                finally:
                    _lock.release()
                break


def run_schedule_for_test(schedule_id: int) -> dict:
    """
    Executes a schedule exactly ONCE for testing purposes.
    Works in both DEV and PROD environments.
    Does not loop or re-arm background cycles.
    """
    row = get_schedule(schedule_id)
    if not row:
        return {"ok": False, "error": f"Schedule {schedule_id} not found."}

    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "Upload worker is currently busy with another operation."}

    try:
        from .config import is_dev_env, get_app_env
        env = get_app_env()
        log.info("[TEST-RUN] Executing schedule %s once for testing (Environment: %s)...", schedule_id, env)
        result = _run_schedule(row)
        today = datetime.now().strftime("%Y-%m-%d")
        if result in {"uploaded", "duplicate_synced", "nothing_to_upload", "ai_retry_pending", "held_for_review", "quota_paused"}:
            mark_schedule_run(row["id"], today)
            record_scheduler_run(row["id"], today, "paused" if result == "quota_paused" else ("success" if result != "ai_retry_pending" else "pending"), f"[test_run] {result}")
        else:
            record_scheduler_run(row["id"], today, "failed", f"[test_run] {result}")

        log.info("[TEST-RUN] Schedule %s single test run finished with status: %s", schedule_id, result)
        return {"ok": True, "schedule_id": schedule_id, "result": result, "environment": env, "one_shot": True}
    finally:
        _lock.release()


def start_scheduler():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="youtube-scheduler", daemon=True)
    _thread.start()
    from .config import get_app_env, is_dev_env
    env = get_app_env()
    if is_dev_env():
        log.info("[SCHEDULER] Local scheduler started in DEV mode (Env: %s). Automatic background tasks are paused; one-shot testing enabled.", env)
    else:
        log.info("[SCHEDULER] Local scheduler started in PROD mode (Env: %s). Automated background tasks and schedules are active.", env)


def stop_scheduler():
    _stop.set()
