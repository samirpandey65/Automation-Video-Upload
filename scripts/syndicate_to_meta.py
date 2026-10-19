#!/usr/bin/env python3
"""
scripts/syndicate_to_meta.py

Universal syndication utility to publish previously uploaded YouTube videos
to connected Facebook Pages (Video & Reel) and Instagram Professional accounts (Reel).

Usage examples:
    # Syndicate a specific video by ID:
    python scripts/syndicate_to_meta.py --video-id 3261

    # Syndicate all videos for a specific YouTube channel missing Meta:
    python scripts/syndicate_to_meta.py --channel-id UC63hKHobZMgC_muYECQt4FA

    # Syndicate all uploaded videos in the entire library missing Meta:
    python scripts/syndicate_to_meta.py --all-missing

    # Dry-run inspection without publishing:
    python scripts/syndicate_to_meta.py --all-missing --dry-run
"""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import (
    init_db, get_conn, get_channel, list_channels,
    get_inventory_by_id, list_meta_connections, get_meta_connection,
    mark_platform_published, is_video_published_on_platform,
    link_meta_connection_to_channels
)
from app.thumbnail import resolve_local_video_path, resolve_thumbnail_path
from app.scheduler import _run_meta_publish

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
log = logging.getLogger("syndicate_to_meta")


def get_videos_for_syndication(video_id=None, channel_id=None, all_missing=False):
    conn = get_conn()
    if video_id:
        row = conn.execute("SELECT * FROM video_inventory WHERE id=?", (int(video_id),)).fetchone()
        conn.close()
        return [dict(row)] if row else []

    query = """
        SELECT * FROM video_inventory
        WHERE COALESCE(youtube_video_id, '') != ''
    """
    params = []
    if channel_id:
        query += " AND channel_id = ?"
        params.append(channel_id)

    if all_missing:
        query += """ AND (
            COALESCE(facebook_video_id, '') = ''
            OR COALESCE(facebook_reel_id, '') = ''
            OR COALESCE(instagram_media_id, '') = ''
        )"""

    query += " ORDER BY id ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_channel_meta(ch):
    if not ch:
        return None
    meta_cid = ch.get("meta_connection_id")
    if meta_cid:
        conn = get_meta_connection(int(meta_cid))
        if conn and conn.get("status") == "connected":
            return conn

    # Match by name or account
    conns = list_meta_connections()
    ch_name = (ch.get("channel_name") or "").strip().lower()
    for c in conns:
        if c.get("status") == "connected":
            c_name = (c.get("page_name") or "").strip().lower()
            if ch_name and c_name and (ch_name == c_name or ch_name in c_name or c_name in ch_name):
                return c
            if ch.get("account_id") and c.get("account_id") and ch["account_id"] == c["account_id"]:
                return c
    return None


def syndicate_video(video, dry_run=False, destination="all", force=False):
    vid_id = video["id"]
    filename = video.get("filename") or ""
    ch_id = video.get("channel_id")
    ch = get_channel(ch_id)
    ch_name = (ch.get("channel_name") if ch else ch_id) or "Unknown Channel"

    meta_conn = resolve_channel_meta(ch)
    if not meta_conn:
        log.warning("❌ Video %d (%s): No active Meta connection for channel '%s'. Skipping.", vid_id, filename, ch_name)
        return False

    page_name = meta_conn.get("page_name", "")
    page_id = meta_conn.get("page_id", "")
    ig_user = meta_conn.get("instagram_username") or meta_conn.get("instagram_user_id") or "None"
    has_ig = bool(meta_conn.get("instagram_user_id") or meta_conn.get("instagram_username"))

    # Determine destinations
    do_fb_vid = destination in ("all", "facebook_video")
    do_fb_reel = destination in ("all", "facebook_reel")
    do_ig_reel = destination in ("all", "instagram_reel") and has_ig

    raw_path = video.get("file_path") or ""
    resolved_file = resolve_local_video_path(raw_path) or raw_path
    if not Path(resolved_file).exists():
        # Check channel folder
        from app.database import get_channel_folder
        cf = get_channel_folder(ch_id)
        if cf:
            candidate = Path(cf) / filename
            if candidate.exists():
                resolved_file = str(candidate)

    if not Path(resolved_file).exists():
        if dry_run:
            log.info("   [DRY RUN] Local file not present on current host (remote path=%s); proceeding with dry-run simulation.", raw_path)
        else:
            log.warning("❌ Video %d (%s): Local file not found on disk at '%s'. Skipping.", vid_id, filename, raw_path)
            return False

    log.info(
        "🚀 [%s] Video %d | File: %s | Channel: '%s' -> Meta Page: '%s' (ID: %s) | IG: %s",
        "DRY RUN" if dry_run else "PUBLISHING",
        vid_id, filename, ch_name, page_name, page_id, ig_user
    )

    if dry_run:
        log.info(
            "   [DRY RUN] Would publish: Facebook Video=%s, Facebook Reel=%s, Instagram Reel=%s",
            do_fb_vid, do_fb_reel, do_ig_reel
        )
        return True

    sched_row = {
        "channel_id": ch_id,
        "meta_connection_id": meta_conn["id"],
        "publish_facebook_video": 1 if do_fb_vid else 0,
        "publish_facebook_reel": 1 if do_fb_reel else 0,
        "publish_instagram_reel": 1 if do_ig_reel else 0,
    }

    hashtags = [x.strip().lstrip("#").lower() for x in str(video.get("hashtags") or "").replace(",", " ").split() if x.strip()]
    title = str(video.get("title") or Path(filename).stem).strip()
    description = str(video.get("description") or title).strip()
    thumb_path = video.get("thumbnail_path") or None

    try:
        _run_meta_publish(
            sched_row,
            video,
            title,
            description,
            hashtags,
            resolved_file,
            thumbnail_path=thumb_path
        )
        # Check newly updated video
        updated = get_inventory_by_id(vid_id)
        log.info(
            "✅ Video %d completed. FB Vid: %s | FB Reel: %s | IG Reel: %s",
            vid_id,
            updated.get("facebook_video_id") or "none",
            updated.get("facebook_reel_id") or "none",
            updated.get("instagram_media_id") or "none"
        )
        return True
    except Exception as exc:
        log.exception("❌ Error syndicating video %d: %s", vid_id, exc)
        return False


def main():
    parser = argparse.ArgumentParser(description="Syndicate uploaded YouTube videos to Meta (Facebook & Instagram)")
    parser.add_argument("--video-id", type=int, help="Single video ID in video_inventory to syndicate")
    parser.add_argument("--channel-id", type=str, help="YouTube channel ID to syndicate videos for")
    parser.add_argument("--all-missing", action="store_true", help="Syndicate all YouTube-uploaded videos missing Meta posts")
    parser.add_argument("--destination", choices=["all", "facebook_video", "facebook_reel", "instagram_reel"], default="all", help="Target destination platform")
    parser.add_argument("--dry-run", action="store_true", help="Simulate syndication without making network requests")
    parser.add_argument("--force", action="store_true", help="Force publication even if already published")
    args = parser.parse_args()

    if not (args.video_id or args.channel_id or args.all_missing):
        parser.print_help()
        print("\nError: Please specify --video-id <ID>, --channel-id <ID>, or --all-missing")
        sys.exit(1)

    # Initialize DB & ensure schema / linkages are current
    init_db()
    link_meta_connection_to_channels()

    videos = get_videos_for_syndication(
        video_id=args.video_id,
        channel_id=args.channel_id,
        all_missing=args.all_missing
    )

    if not videos:
        log.info("No matching videos found for syndication.")
        return

    log.info("Found %d video(s) for Meta syndication.", len(videos))
    success_count = 0

    for idx, vid in enumerate(videos, 1):
        log.info("--- Processing %d of %d (Video ID %d) ---", idx, len(videos), vid["id"])
        ok = syndicate_video(vid, dry_run=args.dry_run, destination=args.destination, force=args.force)
        if ok:
            success_count += 1

    log.info("🎉 Syndication finished. Succeeded: %d / %d", success_count, len(videos))


if __name__ == "__main__":
    main()
