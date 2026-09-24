import os
import re
import time
import logging
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
        except (HttpError, OSError, TimeoutError) as exc:
            attempt += 1
            logging.exception("YouTube upload chunk failed (attempt %d/%d): %s", attempt, max_retries, exc)
            if attempt >= max_retries:
                raise
            time.sleep(min(2 ** attempt, 15))
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
