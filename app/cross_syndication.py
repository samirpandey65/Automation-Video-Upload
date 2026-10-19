import logging
import json
from pathlib import Path
from datetime import datetime

from .database import (
    get_conn, now_iso, get_inventory_by_id, update_video,
    get_channel, list_meta_connections, get_meta_connection,
    get_app_setting, set_app_setting
)

log = logging.getLogger(__name__)


def generate_cross_platform_links(item: dict, connection: dict = None, channel: dict = None) -> dict:
    """Extract all available platform links and handles for an inventory item."""
    yt_id = item.get("youtube_video_id") or ""
    fb_vid = item.get("facebook_video_id") or ""
    fb_reel = item.get("facebook_reel_id") or ""
    ig_mid = item.get("instagram_media_id") or ""

    yt_url = f"https://youtu.be/{yt_id}" if yt_id else ""
    fb_url = ""
    if fb_reel:
        fb_url = f"https://www.facebook.com/reel/{fb_reel}"
    elif fb_vid:
        fb_url = f"https://www.facebook.com/watch/?v={fb_vid}"

    ig_username = ""
    page_name = ""
    if connection:
        ig_username = (connection.get("instagram_username") or "").strip().lstrip("@")
        page_name = (connection.get("page_name") or "").strip()

    if not ig_username:
        # Fallback to connected Meta accounts
        conns = [c for c in list_meta_connections() if c.get("status") != "logged_out"]
        if conns:
            ig_username = (conns[0].get("instagram_username") or "").strip().lstrip("@")
            if not page_name:
                page_name = (conns[0].get("page_name") or "").strip()

    ig_url = ""
    if ig_username:
        ig_url = f"https://www.instagram.com/{ig_username}/"

    ch_name = ""
    if channel:
        ch_name = (channel.get("channel_name") or "").strip()
    elif item.get("channel_id"):
        ch = get_channel(item["channel_id"])
        if ch:
            ch_name = (ch.get("channel_name") or "").strip()

    return {
        "youtube_id": yt_id,
        "youtube_url": yt_url,
        "facebook_id": fb_reel or fb_vid,
        "facebook_url": fb_url,
        "instagram_id": ig_mid,
        "instagram_url": ig_url,
        "instagram_username": ig_username,
        "page_name": page_name,
        "channel_name": ch_name,
    }


def format_cross_platform_block(target_platform: str, links: dict) -> str:
    """Generate platform-specific cross-linking text to append to descriptions/captions."""
    target_platform = target_platform.lower().strip()
    lines = []

    if target_platform == "youtube":
        fb_url = links.get("facebook_url")
        ig_url = links.get("instagram_url")
        if fb_url or ig_url:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("📱 Connect & Follow Across Platforms:")
            if fb_url:
                lines.append(f"📘 Facebook: {fb_url}")
            if ig_url:
                lines.append(f"📸 Instagram: {ig_url}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    elif target_platform in {"facebook", "facebook_reel", "facebook_video"}:
        yt_url = links.get("youtube_url")
        ig_url = links.get("instagram_url")
        if yt_url or ig_url:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            if yt_url:
                lines.append(f"🔴 Subscribe & Watch in Full HD on YouTube: {yt_url}")
            if ig_url:
                lines.append(f"📸 Follow our Instagram Reels: {ig_url}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    elif target_platform in {"instagram", "instagram_reel"}:
        yt_url = links.get("youtube_url")
        page_name = links.get("page_name")
        if yt_url or page_name:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            if yt_url:
                lines.append(f"🔴 Full Video & Subscribe on YouTube: {yt_url} (Link in bio)")
            if page_name:
                fb_url = links.get("facebook_url") or f"https://www.facebook.com/{page_name.replace(' ', '')}"
                lines.append(f"📘 Watch & Follow on Facebook: {fb_url}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


def syndicate_cross_platform_links_for_video(video_id: int, update_descriptions: bool = True, post_comments: bool = True):
    """
    Synchronize cross-platform links across YouTube, Facebook, and Instagram for an uploaded video.
    Updates descriptions and posts top comments so followers on one platform discover the others.
    """
    item = get_inventory_by_id(video_id)
    if not item:
        log.warning("[CROSS-SYNDICATION] Inventory item %s not found", video_id)
        return {"ok": False, "error": "Item not found"}

    yt_id = item.get("youtube_video_id") or ""
    fb_id = item.get("facebook_reel_id") or item.get("facebook_video_id") or ""
    ig_id = item.get("instagram_media_id") or ""

    # At least two platforms needed for cross-linking
    platform_count = (1 if yt_id else 0) + (1 if fb_id else 0) + (1 if ig_id else 0)
    if platform_count < 2:
        log.info("[CROSS-SYNDICATION] Video %s has only %d platform published; skipping cross-linking", video_id, platform_count)
        return {"ok": True, "status": "insufficient_platforms", "platform_count": platform_count}

    channel = get_channel(item.get("channel_id")) if item.get("channel_id") else None
    conns = [c for c in list_meta_connections() if c.get("status") != "logged_out"]
    connection = conns[0] if conns else None

    links = generate_cross_platform_links(item, connection=connection, channel=channel)
    results = {"youtube": None, "facebook": None, "instagram": None}

    # 1. Update YouTube Video Description & Comment
    if yt_id and (links.get("facebook_url") or links.get("instagram_url")):
        try:
            from .youtube import update_video_metadata, post_video_comment
            channel_id = item.get("channel_id") or (channel.get("channel_id") if channel else "")
            if channel_id:
                yt_block = format_cross_platform_block("youtube", links)
                curr_desc = item.get("description") or ""

                # Check if links already present
                if yt_block and (links.get("facebook_url", "___") not in curr_desc):
                    new_desc = (curr_desc + "\n" + yt_block).strip()[:5000]
                    if update_descriptions:
                        try:
                            update_video_metadata(channel_id, yt_id, description=new_desc)
                            update_video(video_id, description=new_desc)
                            results["youtube"] = "description_updated"
                            log.info("[CROSS-SYNDICATION] YouTube video %s description updated with Meta links", yt_id)
                        except Exception as yt_desc_err:
                            log.warning("[CROSS-SYNDICATION] Failed to update YouTube description for %s: %s", yt_id, yt_desc_err)

                # Post pinned / top comment on YouTube
                if post_comments:
                    comment_text = (
                        f"📱 Connect with us across platforms!\n"
                        f"{'📘 Facebook: ' + links['facebook_url'] + chr(10) if links.get('facebook_url') else ''}"
                        f"{'📸 Instagram: ' + links['instagram_url'] if links.get('instagram_url') else ''}"
                    ).strip()
                    try:
                        post_video_comment(channel_id, yt_id, comment_text)
                        results["youtube_comment"] = "posted"
                        log.info("[CROSS-SYNDICATION] Posted cross-platform comment on YouTube video %s", yt_id)
                    except Exception as yt_c_err:
                        log.warning("[CROSS-SYNDICATION] Non-fatal: could not post YouTube comment on %s: %s", yt_id, yt_c_err)
        except Exception as yt_err:
            log.warning("[CROSS-SYNDICATION] YouTube cross-linking encountered error: %s", yt_err)

    # 2. Update Facebook with YouTube link
    if fb_id and links.get("youtube_url") and connection:
        try:
            from .meta import load_page_token, post_facebook_comment
            token_data = load_page_token(connection["token_key"]) or {}
            tok = token_data.get("access_token")
            if tok and post_comments:
                fb_comment = f"🔴 Watch in Full HD & Subscribe on YouTube: {links['youtube_url']}"
                if links.get("instagram_url"):
                    fb_comment += f"\n📸 Follow our Instagram Reels: {links['instagram_url']}"
                try:
                    post_facebook_comment(connection["page_id"], tok, fb_id, fb_comment)
                    results["facebook_comment"] = "posted"
                    log.info("[CROSS-SYNDICATION] Posted cross-platform comment on Facebook video/reel %s", fb_id)
                except Exception as fb_c_err:
                    log.warning("[CROSS-SYNDICATION] Non-fatal: Facebook comment post on %s failed: %s", fb_id, fb_c_err)
        except Exception as fb_err:
            log.warning("[CROSS-SYNDICATION] Facebook cross-linking error: %s", fb_err)

    # 3. Update Instagram with YouTube link
    if ig_id and links.get("youtube_url") and connection:
        try:
            from .meta import load_page_token, post_instagram_comment
            token_data = load_page_token(connection["token_key"]) or {}
            tok = token_data.get("access_token")
            if tok and post_comments:
                ig_comment = f"🔴 Full Video & Subscribe on YouTube: {links['youtube_url']} (Link in bio)"
                try:
                    post_instagram_comment(tok, ig_id, ig_comment)
                    results["instagram_comment"] = "posted"
                    log.info("[CROSS-SYNDICATION] Posted cross-platform comment on Instagram reel %s", ig_id)
                except Exception as ig_c_err:
                    log.warning("[CROSS-SYNDICATION] Non-fatal: Instagram comment post on %s failed: %s", ig_id, ig_c_err)
        except Exception as ig_err:
            log.warning("[CROSS-SYNDICATION] Instagram cross-linking error: %s", ig_err)

    # Record timestamp
    update_video(video_id, cross_syndicated_at=now_iso())
    log.info("[CROSS-SYNDICATION] Cross-platform syndication completed for video ID %s: %s", video_id, results)
    return {"ok": True, "video_id": video_id, "results": results, "links": links}
