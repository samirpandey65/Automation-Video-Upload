import os
import re
import time
import logging
from pathlib import Path
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from .auth import get_youtube_service
from .config import MAX_TITLE_LENGTH, MAX_DESCRIPTION_LENGTH

def clean_title(value):
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value[:MAX_TITLE_LENGTH].strip() or "New YouTube Video"

def upload_video(channel_id, file_path, title, description="", tags=None,
                 privacy_status="private", category_id="22",
                 made_for_kids=False, progress_callback=None, max_retries=3, file_hash="",
                 contains_synthetic_media=True, video_language="hi", title_description_language="hi",
                 recording_date=""):
    # Store the exact source SHA in a YouTube tag so a fresh installation can
    # sync YouTube and prevent re-uploading the same local file.
    tags = [str(t).strip().lstrip('#').lower() for t in (tags or []) if str(t).strip()]
    if file_hash:
        marker = "yla_hash_" + str(file_hash).lower()
        if marker not in [str(t).strip().lstrip('#').lower() for t in tags]:
            tags = tags[:499] + [marker]
    youtube = get_youtube_service(channel_id)

    body = {
        "snippet": {
            "title": clean_title(title),
            "description": (description or "")[:MAX_DESCRIPTION_LENGTH],
            "categoryId": str(category_id or "24"),
            "defaultLanguage": str(title_description_language or video_language or "hi"),
            "defaultAudioLanguage": str(video_language or "hi"),
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(made_for_kids),
            "containsSyntheticMedia": bool(contains_synthetic_media),
        },
    }

    if recording_date:
        body["recordingDetails"] = {"recordingDate": str(recording_date)}

    if tags:
        body["snippet"]["tags"] = [
            str(t).strip().lstrip("#").lower() for t in tags
            if str(t).strip()
        ][:500]

    media = MediaFileUpload(
        file_path,
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status,recordingDetails",
        body=body,
        media_body=media,
    )

    response = None
    attempt = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status and progress_callback:
                progress_callback(status.progress())
        except (HttpError, OSError, TimeoutError, ConnectionError) as exc:
            attempt += 1
            # Resumable upload requests are normally safe to retry. Keep the
            # same MediaFileUpload/request so the client can resume from the
            # last acknowledged chunk.
            logging.warning("YouTube upload chunk failed (attempt %d/%d): %s", attempt, max_retries, exc)
            if attempt >= max_retries:
                raise
            time.sleep(min(2 ** attempt, 30))
    return response


def list_channel_videos(channel_id):
    """Fetch the authenticated channel's uploaded videos and metadata."""
    youtube = get_youtube_service(channel_id)
    ch = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = ch.get("items", [])
    if not items:
        raise RuntimeError("YouTube channel was not found for this account.")
    uploads_id = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads_id:
        return []
    out=[]; token=None
    while True:
        resp=youtube.playlistItems().list(part="contentDetails", playlistId=uploads_id, maxResults=50, pageToken=token or "").execute()
        ids=[x.get("contentDetails",{}).get("videoId") for x in resp.get("items",[]) if x.get("contentDetails",{}).get("videoId")]
        for i in range(0,len(ids),50):
            if not ids[i:i+50]: continue
            vresp=youtube.videos().list(part="snippet,status,statistics,contentDetails", id=','.join(ids[i:i+50])).execute()
            out.extend(vresp.get("items",[]))
        token=resp.get("nextPageToken")
        if not token: break
    return out

def get_video_stats(channel_id, video_id):
    """Fetch current basic statistics using the YouTube Data API. This works even when
    the separate YouTube Analytics API is disabled, and is used as a graceful fallback."""
    youtube = get_youtube_service(channel_id)
    resp = youtube.videos().list(part="snippet,status,statistics,contentDetails", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("YouTube video was not found or is no longer accessible.")
    return items[0]

def extract_hash_marker(video):
    for tag in (video.get("snippet",{}).get("tags") or []):
        t=str(tag).strip().lstrip('#')
        if t.lower().startswith("yla_hash_") and len(t) >= len("yla_hash_")+64:
            return t[len("yla_hash_"):].lower()
    return ""


def list_playlists(channel_id):
    """Return all playlists owned by the channel."""
    youtube = get_youtube_service(channel_id)
    out = []; token = None
    while True:
        resp = youtube.playlists().list(
            part="snippet,status", mine=True, maxResults=50,
            pageToken=token or ""
        ).execute()
        out.extend(resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def create_playlist(channel_id, title, description, privacy="public"):
    """Create a new YouTube playlist and return the API item."""
    youtube = get_youtube_service(channel_id)
    resp = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        }
    ).execute()
    return resp


def add_to_playlist(channel_id, playlist_id, youtube_video_id):
    """Insert a video into a playlist and return the API item."""
    youtube = get_youtube_service(channel_id)
    resp = youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": youtube_video_id},
            }
        }
    ).execute()
    return resp


def update_playlist(channel_id, playlist_id, title, description=None):
    """Update title and/or description of an existing YouTube playlist."""
    youtube = get_youtube_service(channel_id)
    snippet = {"title": title}
    if description is not None:
        snippet["description"] = description
    resp = youtube.playlists().update(
        part="snippet",
        body={
            "id": playlist_id,
            "snippet": snippet
        }
    ).execute()
    return resp



def format_thumbnail_for_youtube(thumbnail_path):
    """
    Ensure the thumbnail image conforms strictly to YouTube Data API specs:
    - 16:9 widescreen canvas (1280x720)
    - If input is a 9:16 vertical Short thumbnail (height > width):
      creates a 16:9 canvas with blurred ambient background and crisp centered 9:16 artwork.
      This prevents YouTube Studio and CDN from displaying a blank grey icon / 3-dots error.
    - Compresses to high-quality PNG under 2MB.
    """
    from PIL import Image, ImageFilter, ImageEnhance
    from pathlib import Path
    from .config import THUMBNAILS_DIR
    from .thumbnail import resolve_thumbnail_path

    resolved = resolve_thumbnail_path(thumbnail_path)
    p = resolved if resolved else Path(thumbnail_path)
    if not p.exists():
        raise FileNotFoundError(f"Thumbnail image file not found on disk: {thumbnail_path}")

    try:
        with Image.open(p) as im:
            im = im.convert("RGB")
            w, h = im.size
            target_w, target_h = 1280, 720

            current_ratio = w / h
            is_vertical = h > w
            
            # If already ~16:9 and reasonable size, keep it
            if not is_vertical and abs(current_ratio - (16 / 9)) < 0.05 and p.stat().st_size < 1_900_000:
                return p

            canvas = Image.new("RGB", (target_w, target_h), (12, 16, 26))

            if is_vertical:
                # Background: blurred ambient fill from the vertical thumbnail
                bg_scale = max(target_w / w, target_h / h)
                bg_w, bg_h = int(w * bg_scale), int(h * bg_scale)
                bg = im.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
                crop_x = (bg_w - target_w) // 2
                crop_y = (bg_h - target_h) // 2
                bg_crop = bg.crop((crop_x, crop_y, crop_x + target_w, crop_y + target_h))
                bg_blurred = bg_crop.filter(ImageFilter.GaussianBlur(radius=26))
                bg_final = ImageEnhance.Brightness(bg_blurred).enhance(0.55)
                canvas.paste(bg_final, (0, 0))

                # Foreground: centered 9:16 vertical thumbnail fitted to full height 720
                fg_scale = target_h / h
                fg_w = int(w * fg_scale)
                fg = im.resize((fg_w, target_h), Image.Resampling.LANCZOS)
                fg_x = (target_w - fg_w) // 2
                canvas.paste(fg, (fg_x, 0))
            else:
                # Landscape: scale to fit 1280x720
                resized = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
                canvas.paste(resized, (0, 0))

            out_formatted = p.parent / f"{p.stem}_yt16x9.png"
            canvas.save(str(out_formatted), "PNG", optimize=True)
            return out_formatted
    except Exception as exc:
        logging.warning("[YOUTUBE] Failed to reformat thumbnail %s for YouTube: %s", thumbnail_path, exc)
        if p.exists() and p.stat().st_size > 0:
            return p
        raise


def upload_thumbnail_to_youtube(channel_id, youtube_video_id, thumbnail_path, is_vertical=None, force=False):
    """Upload a thumbnail image to YouTube for the given video.
    
    NOTE ON YOUTUBE SHORTS:
    YouTube Data API's thumbnails().set endpoint overrides the video frame with a 16:9
    image on desktop, but YouTube's mobile app and YouTube Studio Shorts tabs do NOT
    render API thumbnails for Shorts (<60s vertical) and display a blank grey box.
    Unless force=True, we safely skip API thumbnail upload for vertical Shorts to avoid
    the grey box bug. (Short covers can be set via frame selection in the mobile app or
    by baking the thumbnail into the first frame of the video).
    """
    from PIL import Image
    from .config import THUMBNAILS_DIR
    from .thumbnail import resolve_thumbnail_path

    resolved = resolve_thumbnail_path(thumbnail_path)
    p = resolved if resolved else Path(thumbnail_path)

    # Detect if thumbnail or video is vertical (Short)
    detected_vertical = is_vertical
    if detected_vertical is None and p.exists() and p.stat().st_size > 0:
        try:
            with Image.open(p) as _im:
                detected_vertical = _im.height > _im.width
        except Exception:
            detected_vertical = False

    if detected_vertical and not force:
        logging.info(
            "[YOUTUBE] Video %s is a vertical YouTube Short. Skipping thumbnails().set API upload "
            "to prevent YouTube mobile grey box bug. (YouTube Shorts require mobile frame selection "
            "or baking the thumbnail into the video).",
            youtube_video_id
        )
        return {
            "ok": True,
            "skipped": True,
            "is_short": True,
            "message": "Skipped thumbnails().set for YouTube Short to prevent mobile grey box. Use mobile frame selection or bake thumbnail into video."
        }

    safe_path = format_thumbnail_for_youtube(p)
    if not safe_path or not Path(safe_path).exists() or Path(safe_path).stat().st_size == 0:
        raise ValueError(f"Thumbnail image at {safe_path} does not exist or is empty.")
    youtube = get_youtube_service(channel_id)
    resp = youtube.thumbnails().set(
        videoId=youtube_video_id,
        media_body=MediaFileUpload(str(safe_path), mimetype="image/png")
    ).execute()
    return resp


def update_video_metadata(channel_id, youtube_video_id, title=None, description=None, tags=None, category_id=None):
    """Update title, description, tags and metadata for an existing YouTube video without re-uploading."""
    youtube = get_youtube_service(channel_id)
    # Fetch existing snippet first to keep non-modified fields
    resp = youtube.videos().list(part="snippet,status", id=youtube_video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"Video {youtube_video_id} was not found on YouTube.")

    snippet = items[0]["snippet"]
    if title:
        snippet["title"] = clean_title(title)
    if description is not None:
        snippet["description"] = description[:MAX_DESCRIPTION_LENGTH]
    if category_id:
        snippet["categoryId"] = str(category_id)
    if tags is not None:
        clean_tags = [str(t).strip().lstrip("#").lower() for t in tags if str(t).strip()]
        # Preserve any existing yla_hash sync markers
        existing_markers = [t for t in (snippet.get("tags") or []) if str(t).startswith("yla_hash_")]
        for m in existing_markers:
            if m not in clean_tags:
                clean_tags.append(m)
        snippet["tags"] = clean_tags[:500]

    body = {
        "id": youtube_video_id,
        "snippet": snippet
    }
    update_resp = youtube.videos().update(part="snippet", body=body).execute()
    logging.info("[YOUTUBE] Successfully updated metadata for video %s: title=%r", youtube_video_id, snippet.get("title"))
    return update_resp


def post_video_comment(channel_id, youtube_video_id, comment_text):
    """Post a top-level comment on a YouTube video (used for cross-platform linking)."""
    youtube = get_youtube_service(channel_id)
    body = {
        "snippet": {
            "videoId": str(youtube_video_id),
            "topLevelComment": {
                "snippet": {
                    "textOriginal": str(comment_text)
                }
            }
        }
    }
    resp = youtube.commentThreads().insert(part="snippet", body=body).execute()
    logging.info("[YOUTUBE] Posted comment on video %s: %s", youtube_video_id, comment_text[:60])
    return resp

