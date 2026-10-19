#!/usr/bin/env python3
"""
Batch Playlist Retrofit Utility
--------------------------------
Automatically evaluates videos in `video_inventory` across channels, detects
multi-part series and content genres, respects all existing YouTube playlists,
and sequences videos chronologically (Part 1 -> Part 2 -> Part 3) to prevent
content mixing.

Usage:
    python scripts/retrofit_playlists.py --dry-run
    python scripts/retrofit_playlists.py --all
    python scripts/retrofit_playlists.py --channel <channel_id> --limit 100
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.database import get_conn, list_channels, get_channel, update_video
from app.playlist import (
    assign_playlist,
    extract_series_and_part,
    classify_genre_full,
    get_channel_existing_playlists,
    GENRE_TAXONOMY
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("retrofit_playlists")


def run_playlist_retrofit(
    channel_id: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    create_series_playlists: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Evaluate previously uploaded videos in `video_inventory` and assign them
    to matching playlists on YouTube.

    Pacing & Content Isolation:
    - Same-type and genre videos are grouped together sequentially.
    - Multi-part series (Part 1, Part 2, Part 3) are strictly ordered so continuity is preserved.
    - All existing channel playlists on YouTube are checked first to prevent duplicate creation.
    """
    start_time = time.time()
    all_channels = list_channels()
    if channel_id:
        target_channels = [c for c in all_channels if c["channel_id"] == channel_id]
        if not target_channels:
            # Maybe provided as channel name or fallback
            target_channels = [c for c in all_channels if c.get("channel_name", "").lower() == channel_id.lower()]
    else:
        target_channels = all_channels

    if not target_channels:
        return {
            "status": "error",
            "message": "No matching YouTube channels found in database.",
            "total_evaluated": 0,
            "total_assigned": 0
        }

    summary: Dict[str, Any] = {
        "status": "running",
        "dry_run": dry_run,
        "total_evaluated": 0,
        "total_assigned": 0,
        "total_already_in_playlist": 0,
        "playlists_reused": 0,
        "playlists_created": 0,
        "series_detected": 0,
        "channels_processed": 0,
        "channel_reports": [],
        "logs": []
    }

    def emit_log(msg: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        summary["logs"].append(entry)
        if level == "warning":
            log.warning(msg)
        elif level == "error":
            log.error(msg)
        else:
            log.info(msg)
        if progress_callback:
            try:
                progress_callback({
                    "type": "log",
                    "entry": entry,
                    "summary": summary
                })
            except Exception:
                pass

    emit_log(f"Starting Batch Playlist Retrofit (Dry Run: {dry_run}, Force Re-eval: {force}, Target Channels: {len(target_channels)})")

    conn = get_conn()
    quota_exceeded = False

    try:
        for ch in target_channels:
            if quota_exceeded:
                break

            cid = ch["channel_id"]
            cname = ch.get("channel_name") or cid
            emit_log(f"--- Processing Channel: {cname} ({cid}) ---")

            # 1. Inspect existing channel playlists on YouTube
            try:
                existing_pls = get_channel_existing_playlists(cid, force_refresh=True)
                pl_titles = [((p.get("snippet") or {}).get("title") or p.get("id")) for p in existing_pls]
                emit_log(f"Discovered {len(existing_pls)} existing playlist(s) on YouTube for {cname}: {', '.join(pl_titles) if pl_titles else 'None'}")
            except Exception as pl_err:
                emit_log(f"Could not fetch YouTube playlists for {cname}: {pl_err}", level="warning")
                existing_pls = []

            # 2. Query videos from video_inventory
            # Focus on uploaded videos with youtube_video_id
            if force:
                query = """
                    SELECT * FROM video_inventory
                    WHERE channel_id=? AND COALESCE(youtube_video_id, '') != ''
                    ORDER BY id ASC
                """
                rows = conn.execute(query, (cid,)).fetchall()
            else:
                query = """
                    SELECT * FROM video_inventory
                    WHERE channel_id=? AND COALESCE(youtube_video_id, '') != ''
                      AND (youtube_playlist_id IS NULL OR youtube_playlist_id = '')
                    ORDER BY id ASC
                """
                rows = conn.execute(query, (cid,)).fetchall()

            raw_videos = [dict(r) for r in rows]
            if limit and limit > 0:
                raw_videos = raw_videos[:limit]

            emit_log(f"Found {len(raw_videos)} uploaded video(s) to evaluate on {cname}")
            if not raw_videos:
                summary["channel_reports"].append({
                    "channel_id": cid,
                    "channel_name": cname,
                    "evaluated": 0,
                    "assigned": 0,
                    "status": "up_to_date"
                })
                summary["channels_processed"] += 1
                continue

            # 3. Analyze each video: detect series/parts and classify genre
            enriched_videos = []
            channel_series_set = set()

            for v in raw_videos:
                fname = v.get("filename") or ""
                vtitle = v.get("title") or ""
                fpath = v.get("file_path") or ""

                is_series, series_name, part_num, p_type = extract_series_and_part(filename=fname, title=vtitle)
                genre_info = classify_genre_full(
                    analysis_json=v.get("analysis_json"),
                    filename=fname,
                    file_path=fpath,
                    title=vtitle,
                    description=v.get("description") or "",
                    tags=v.get("tags"),
                    channel_name=cname
                )

                if is_series and series_name:
                    channel_series_set.add(series_name.lower())

                enriched_videos.append({
                    "db_item": v,
                    "is_series": is_series,
                    "series_name": series_name,
                    "part_number": part_num,
                    "part_type": p_type,
                    "genre": genre_info["genre"],
                    "clean_title": genre_info["clean_title"]
                })

            summary["series_detected"] += len(channel_series_set)

            # 4. Group & Sequence: Content Isolation and Continuity
            # Sorting rule:
            #  1. Group by genre and series name so same content stays together.
            #  2. Within each group, sort chronologically by part_number ASC (Part 1 -> Part 2 -> Part 3).
            enriched_videos.sort(
                key=lambda x: (
                    x["genre"].lower(),
                    (x["series_name"] or "").lower(),
                    x["part_number"] if x["part_number"] > 0 else 999999.0,
                    x["db_item"]["id"]
                )
            )

            ch_assigned = 0
            ch_already = 0
            ch_created_pls = 0
            initial_pl_count = len(existing_pls)

            # 5. Process assignments in sequence
            for idx, item in enumerate(enriched_videos, 1):
                v_db = item["db_item"]
                vid_id = v_db["id"]
                yt_id = v_db.get("youtube_video_id")
                fname = v_db.get("filename") or ""
                vtitle = v_db.get("title") or fname

                part_badge = f"[Part {item['part_number']:.0f}] " if item["is_series"] and item["part_number"] > 0 else ""
                emit_log(f"[{idx}/{len(enriched_videos)}] Evaluating: {part_badge}'{vtitle[:55]}' (Genre: {item['genre']})")

                try:
                    res = assign_playlist(
                        channel_id=cid,
                        youtube_video_id=yt_id,
                        analysis_json=v_db.get("analysis_json"),
                        channel_name=cname,
                        filename=fname,
                        file_path=v_db.get("file_path") or "",
                        title=vtitle,
                        description=v_db.get("description") or "",
                        tags=v_db.get("tags"),
                        create_series_playlists=create_series_playlists,
                        existing_playlists=existing_pls,
                        dry_run=dry_run
                    )

                    target_pl_id = res.get("playlist_id") or ""
                    target_pl_title = res.get("playlist_title") or item["clean_title"]

                    if res.get("created"):
                        ch_created_pls += 1
                        summary["playlists_created"] += 1
                        emit_log(f"   ★ Created new playlist on YouTube: '{target_pl_title}' (ID: {target_pl_id})")
                    else:
                        summary["playlists_reused"] += 1
                        emit_log(f"   ✔ Matched existing playlist: '{target_pl_title}' (ID: {target_pl_id})")

                    if res.get("already_in_playlist"):
                        ch_already += 1
                        summary["total_already_in_playlist"] += 1
                        emit_log(f"   ↳ Video already present in '{target_pl_title}'")
                    else:
                        ch_assigned += 1
                        summary["total_assigned"] += 1
                        action_label = "Would assign" if dry_run else "Assigned"
                        emit_log(f"   ↳ {action_label} video {yt_id} to '{target_pl_title}'")

                    # Update database if not dry run
                    if not dry_run and target_pl_id:
                        update_video(vid_id, youtube_playlist_id=target_pl_id)

                    summary["total_evaluated"] += 1

                except Exception as assign_err:
                    err_str = str(assign_err)
                    if "quotaExceeded" in err_str or "dailyLimitExceeded" in err_str:
                        emit_log(f"YouTube Data API quota reached: {err_str}", level="error")
                        quota_exceeded = True
                        summary["status"] = "quota_limited"
                        break
                    else:
                        emit_log(f"Error assigning video {yt_id} to playlist: {assign_err}", level="warning")

                # Progress ping
                if progress_callback:
                    try:
                        progress_callback({
                            "type": "progress",
                            "channel_id": cid,
                            "current": idx,
                            "total": len(enriched_videos),
                            "summary": summary
                        })
                    except Exception:
                        pass

                # Short pacing delay between YouTube API mutating calls to stay within rate limits
                if not dry_run:
                    time.sleep(0.35)

            summary["channel_reports"].append({
                "channel_id": cid,
                "channel_name": cname,
                "evaluated": len(enriched_videos),
                "assigned": ch_assigned,
                "already_in_playlist": ch_already,
                "new_playlists_created": ch_created_pls,
                "distinct_series": len(channel_series_set),
                "status": "quota_limited" if quota_exceeded else "completed"
            })
            summary["channels_processed"] += 1

    finally:
        conn.close()

    if summary["status"] != "quota_limited":
        summary["status"] = "completed"

    elapsed = round(time.time() - start_time, 2)
    summary["elapsed_seconds"] = elapsed
    emit_log(f"Retrofit finished in {elapsed}s: {summary['total_evaluated']} evaluated, {summary['total_assigned']} assigned, {summary['playlists_created']} created, {summary['playlists_reused']} reused.")

    if progress_callback:
        try:
            progress_callback({
                "type": "done",
                "summary": summary
            })
        except Exception:
            pass

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Retrofit and assign previously uploaded videos to matching YouTube playlists in chronological sequence."
    )
    parser.add_argument("--channel", type=str, default="", help="Specific channel ID or name to retrofit.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate playlist assignment without making changes on YouTube or in database.")
    parser.add_argument("--force", action="store_true", help="Re-evaluate videos even if they already have youtube_playlist_id.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of videos processed per channel.")
    parser.add_argument("--create-series-playlists", action="store_true", help="Create dedicated series playlists for multi-part stories.")
    parser.add_argument("--all", action="store_true", help="Explicitly retrofit all configured channels.")

    args = parser.parse_args()

    channel_target = args.channel if args.channel else None
    result = run_playlist_retrofit(
        channel_id=channel_target,
        dry_run=args.dry_run,
        force=args.force,
        limit=args.limit,
        create_series_playlists=args.create_series_playlists
    )

    print("\n" + "=" * 60)
    print(" BATCH PLAYLIST RETROFIT RESULTS SUMMARY")
    print("=" * 60)
    print(f" Status:             {result.get('status').upper()}")
    print(f" Dry Run:            {result.get('dry_run')}")
    print(f" Channels:           {result.get('channels_processed')}")
    print(f" Videos Evaluated:   {result.get('total_evaluated')}")
    print(f" Videos Assigned:    {result.get('total_assigned')}")
    print(f" Already In List:    {result.get('total_already_in_playlist')}")
    print(f" Playlists Reused:   {result.get('playlists_reused')}")
    print(f" Playlists Created:  {result.get('playlists_created')}")
    print(f" Series Detected:    {result.get('series_detected')}")
    print(f" Elapsed Time:       {result.get('elapsed_seconds', 0)}s")
    print("=" * 60)

    for ch_rep in result.get("channel_reports", []):
        print(f" • Channel '{ch_rep['channel_name']}': {ch_rep['assigned']} assigned, {ch_rep['new_playlists_created']} new playlists, {ch_rep['status']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
