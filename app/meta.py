import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlencode
import requests

from .config import BASE_DIR, CREDENTIALS_DIR, META_GRAPH_VERSION

log = logging.getLogger(__name__)
META_GRAPH = f"https://graph.facebook.com/{META_GRAPH_VERSION}"
META_OAUTH = "https://www.facebook.com/" + META_GRAPH_VERSION + "/dialog/oauth"
META_TOKEN = META_GRAPH + "/oauth/access_token"
META_SCOPES = [
    # Legacy fallback only. Facebook Login for Business configurations should
    # supply permissions via config_id instead of a scope query parameter.
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
    "business_management",
]


def _secret_file(app_id):
    p=CREDENTIALS_DIR / f"meta_app_{''.join(c for c in str(app_id) if c.isalnum())}.json"
    return p


def save_app_secret(app_id, app_secret):
    p=_secret_file(app_id); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"app_id":app_id,"app_secret":app_secret}), encoding="utf-8")
    try: p.chmod(0o600)
    except Exception: pass


def load_app_secret(app_id):
    p=_secret_file(app_id)
    if not p.exists(): return ""
    try: return json.loads(p.read_text(encoding="utf-8")).get("app_secret","")
    except Exception: return ""


def delete_app_secret(app_id):
    try: _secret_file(app_id).unlink(missing_ok=True)
    except Exception: pass


def build_oauth_url(app_id, redirect_uri, state, config_id=""):
    params={
        "client_id":app_id,
        "redirect_uri":redirect_uri,
        "state":state,
        "response_type":"code",
        "override_default_response_type":"true",
    }
    if config_id:
        # Facebook Login for Business: permissions are defined by the
        # configuration in Meta, so do NOT send a scope parameter.
        params["config_id"] = config_id
    else:
        # Legacy/classic Facebook Login fallback.
        params["scope"] = ",".join(META_SCOPES)
    return META_OAUTH + "?" + urlencode(params)


def _json_response(resp):
    try:
        data=resp.json()
    except Exception:
        data={"error":{"message":resp.text[:1000]}}
    if not resp.ok or data.get("error"):
        err=data.get("error") or {}
        message=err.get("message") or f"Meta API error HTTP {resp.status_code}"
        details=[]
        for key in ("type", "code", "error_subcode", "fbtrace_id"):
            if err.get(key) is not None:
                details.append(f"{key}={err.get(key)}")
        if details:
            message += " (" + ", ".join(details) + ")"
        raise RuntimeError(message)
    return data


def _clean_secret(value):
    # Remove BOM/zero-width characters that can be introduced when copying
    # an App Secret from a browser. Meta secrets themselves do not contain
    # whitespace, so this only normalizes accidental copy/paste characters.
    return str(value or "").replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\u2060", "").strip()


def exchange_code(app_id, app_secret, redirect_uri, code):
    app_id=str(app_id or "").strip()
    app_secret=_clean_secret(app_secret)
    code=str(code or "").strip()
    if not app_id or not app_secret or not code:
        raise RuntimeError("Meta OAuth exchange requires App ID, App Secret, and authorization code.")

    # Use the authorization_code grant explicitly and send credentials in the
    # POST body. This avoids accidental URL encoding/copy issues and follows
    # the OAuth token-exchange form used by Meta integrations.
    r=requests.post(
        META_TOKEN,
        data={
            "client_id":app_id,
            "client_secret":app_secret,
            "redirect_uri":redirect_uri,
            "code":code,
            "grant_type":"authorization_code",
        },
        headers={"Accept":"application/json"},
        timeout=30,
    )
    try:
        short=_json_response(r)
    except RuntimeError as exc:
        raise RuntimeError(f"Meta rejected the App ID/App Secret during authorization-code exchange: {exc}") from exc
    short_token=short.get("access_token")
    if not short_token:
        raise RuntimeError("Meta OAuth did not return an access token.")

    # Request a long-lived user token. If Meta does not allow the extension for
    # this configuration, retain the valid short-lived token rather than
    # treating an otherwise successful authorization as a failure.
    try:
        r=requests.get(
            META_TOKEN,
            params={
                "grant_type":"fb_exchange_token",
                "client_id":app_id,
                "client_secret":app_secret,
                "fb_exchange_token":short_token,
            },
            headers={"Accept":"application/json"},
            timeout=30,
        )
        long=_json_response(r)
        return long.get("access_token") or short_token, int(long.get("expires_in") or short.get("expires_in") or 0)
    except Exception as exc:
        log.warning("Meta long-lived token exchange was not accepted; using authorization token: %s", exc)
        return short_token, int(short.get("expires_in") or 0)


def refresh_meta_token(app_id, app_secret, long_lived_token):
    """Refresh a long-lived Meta token (valid for ~60 days)."""
    app_id = str(app_id or "").strip()
    app_secret = _clean_secret(app_secret)
    r = requests.get(
        META_TOKEN,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": long_lived_token,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    data = _json_response(r)
    return data.get("access_token"), int(data.get("expires_in", 0))



def get_pages(user_token):
    # Primary: pages the user directly admins
    r=requests.get(META_GRAPH+"/me/accounts", params={"fields":"id,name,access_token,instagram_business_account{id,username}","access_token":user_token,"limit":100}, timeout=30)
    pages=_json_response(r).get("data",[])
    if pages:
        return pages

    # Fallback: pages owned by the user's Business Portfolios.
    # When a Page is managed exclusively through a Business Portfolio,
    # /me/accounts returns an empty list even after the user grants
    # pages_show_list. Fetching via /me/businesses covers that case.
    log.info("Meta /me/accounts returned 0 pages; trying /me/businesses fallback")
    seen=set()
    try:
        biz_r=requests.get(META_GRAPH+"/me/businesses", params={"fields":"id,name","access_token":user_token,"limit":50}, timeout=30)
        businesses=_json_response(biz_r).get("data",[])
        for biz in businesses:
            biz_id=biz.get("id")
            if not biz_id:
                continue
            owned_r=requests.get(
                META_GRAPH+f"/{biz_id}/owned_pages",
                params={"fields":"id,name,access_token,instagram_business_account{id,username}","access_token":user_token,"limit":100},
                timeout=30,
            )
            try:
                for p in _json_response(owned_r).get("data",[]):
                    if p.get("id") and p["id"] not in seen:
                        seen.add(p["id"])
                        pages.append(p)
            except Exception as exc:
                log.warning("Meta owned_pages fetch failed for business %s: %s", biz_id, exc)
    except Exception as exc:
        log.warning("Meta /me/businesses fallback failed: %s", exc)
    return pages


def save_page_token(token_key, payload):
    p=CREDENTIALS_DIR / "tokens" / f"{token_key}.json"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    try: p.chmod(0o600)
    except Exception: pass


def load_page_token(token_key):
    p=CREDENTIALS_DIR / "tokens" / f"{token_key}.json"
    if not p.exists(): return None
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return None


def delete_page_token(token_key):
    try: (CREDENTIALS_DIR / "tokens" / f"{token_key}.json").unlink(missing_ok=True)
    except Exception: pass


def graph_post(path, token, data=None, files=None, timeout=300):
    r=requests.post(META_GRAPH+path, params={"access_token":token}, data=data, files=files, timeout=timeout)
    return _json_response(r)


def upload_facebook_page_video(page_id, token, file_path, title, description, published=True, thumbnail_path=None):
    size = os.path.getsize(file_path)
    log.info("Facebook Page Video upload START: page_id=%s file=%s size_bytes=%d title=%r", page_id, file_path, size, title[:80])
    files_payload = {}
    with open(file_path,"rb") as fh:
        files_payload["source"] = (Path(file_path).name, fh, "video/mp4")
        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                with open(thumbnail_path, "rb") as tfh:
                    files_payload["thumb"] = (Path(thumbnail_path).name, tfh.read(), "image/png")
                log.info("Facebook Page Video attached custom thumbnail: %s", thumbnail_path)
            except Exception as terr:
                log.warning("Could not attach thumbnail to Facebook video: %s", terr)
        result = graph_post(f"/{page_id}/videos", token, data={"title":title[:255],"description":description[:10000],"published":"true" if published else "false"}, files=files_payload, timeout=1800)
    log.info("Facebook Page Video upload DONE: page_id=%s meta_id=%s file=%s", page_id, result.get("id",""), file_path)
    return result


def set_facebook_video_thumbnail(video_id, token, thumbnail_path):
    """Set custom thumbnail cover for a Facebook video or Facebook Reel."""
    if not thumbnail_path:
        return None
    from .thumbnail import resolve_thumbnail_path
    resolved_tp = resolve_thumbnail_path(thumbnail_path)
    tp = resolved_tp if resolved_tp else Path(thumbnail_path)
    if not tp.exists():
        log.warning("[META] Thumbnail file does not exist: %s", thumbnail_path)
        return None
    try:
        mime = "image/png" if tp.suffix.lower() == ".png" else "image/jpeg"
        with open(tp, "rb") as tfh:
            r = requests.post(
                META_GRAPH + f"/{video_id}/thumbnails",
                params={"access_token": token, "is_preferred": "true"},
                files={"source": (tp.name, tfh.read(), mime)},
                timeout=60,
            )
        res = _json_response(r)
        log.info("[META] Custom thumbnail set successfully on Facebook video/reel %s: %s", video_id, res)
        return res
    except Exception as exc:
        log.warning("[META] Failed to set thumbnail for Facebook video/reel %s: %s", video_id, exc)
        return None


def upload_facebook_page_reel(page_id, token, file_path, title="", description="", thumbnail_path=None):
    size = os.path.getsize(file_path)
    log.info("Facebook Reel upload START: page_id=%s file=%s size_bytes=%d title=%r thumbnail=%s", page_id, file_path, size, title[:80], thumbnail_path)
    start=graph_post(f"/{page_id}/video_reels", token, data={"upload_phase":"start"})
    video_id=start.get("video_id"); upload_url=start.get("upload_url")
    if not video_id or not upload_url: raise RuntimeError("Meta did not return a Facebook Reel upload session.")
    log.info("Facebook Reel session: page_id=%s video_id=%s uploading %d bytes", page_id, video_id, size)
    with open(file_path,"rb") as fh:
        r=requests.post(upload_url, headers={"Authorization":f"OAuth {token}","offset":"0","file_size":str(size),"Content-Type":"application/octet-stream"}, data=fh, timeout=1800)
        _json_response(r)
    log.info("Facebook Reel bytes uploaded: page_id=%s video_id=%s — sending finish+publish phase", page_id, video_id)
    finish_data={"upload_phase":"finish","video_id":video_id,"video_state":"PUBLISHED","description":description[:10000]}
    if title: finish_data["title"]=title[:255]
    finish=graph_post(f"/{page_id}/video_reels", token, data=finish_data)
    post_id=finish.get("post_id","")
    log.info("Facebook Reel finish accepted: page_id=%s video_id=%s post_id=%s — polling for READY status", page_id, video_id, post_id)

    # Attach custom thumbnail immediately
    if thumbnail_path:
        set_facebook_video_thumbnail(video_id, token, thumbnail_path)

    # Poll until Meta finishes transcoding. The finish phase returns immediately
    # with 'Video is Processing'; the reel only appears on the page once the
    # status reaches READY or PUBLISHED.
    deadline=time.time()+900  # 15 min max
    poll_interval=10
    while time.time()<deadline:
        try:
            r=requests.get(
                META_GRAPH+f"/{video_id}",
                params={"fields":"status,description","access_token":token},
                timeout=30,
            )
            st=_json_response(r)
            code=str((st.get("status") or {}).get("video_status") or "").upper()
            log.info("Facebook Reel status poll: video_id=%s status=%s", video_id, code)
            if code in {"READY","PUBLISHED"}:
                log.info("Facebook Reel is LIVE: page_id=%s video_id=%s post_id=%s", page_id, video_id, post_id)
                # Ensure thumbnail is set if transcode just finished
                if thumbnail_path:
                    set_facebook_video_thumbnail(video_id, token, thumbnail_path)
                break
            if code in {"ERROR","INVALID"}:
                raise RuntimeError(f"Facebook Reel processing failed with status: {code}")
        except RuntimeError: raise
        except Exception as exc:
            log.warning("Facebook Reel status poll error (will retry): %s", exc)
        time.sleep(poll_interval)
        poll_interval=min(poll_interval+5, 30)  # back off gradually up to 30s
    else:
        log.warning("Facebook Reel processing timed out after 15 min: video_id=%s — reel may still appear later", video_id)
    return {"id":video_id,"post_id":post_id}


def upload_instagram_reel(ig_user_id, token, file_path, caption="", share_to_feed=True):
    size=os.path.getsize(file_path)
    log.info("Instagram Reel upload START: ig_user_id=%s file=%s size_bytes=%d caption_len=%d", ig_user_id, file_path, size, len(caption))
    if size > 1024*1024*1024: raise RuntimeError("Instagram Reel exceeds the 1 GB API limit.")
    init=graph_post(f"/{ig_user_id}/media", token, data={"media_type":"REELS","upload_type":"resumable","caption":caption[:2200],"share_to_feed":"true" if share_to_feed else "false"})
    container=init.get("id")
    upload_uri=init.get("uri") or init.get("upload_uri")
    if not container or not upload_uri: raise RuntimeError("Meta did not return an Instagram Reel upload session.")
    log.info("Instagram Reel container created: ig_user_id=%s container_id=%s uploading %d bytes", ig_user_id, container, size)
    with open(file_path,"rb") as fh:
        r=requests.post(upload_uri, headers={"Authorization":f"OAuth {token}","offset":"0","file_size":str(size),"Content-Type":"application/octet-stream"}, data=fh, timeout=1800)
        _json_response(r)
    log.info("Instagram Reel bytes uploaded: container_id=%s — polling for FINISHED status", container)
    deadline=time.time()+900
    while time.time()<deadline:
        r=requests.get(META_GRAPH+f"/{container}", params={"fields":"status_code,status","access_token":token}, timeout=30)
        status=_json_response(r)
        code=str(status.get("status_code") or "")
        log.info("Instagram Reel status poll: container_id=%s status_code=%s", container, code)
        if code == "FINISHED": break
        if code in {"ERROR","EXPIRED"}: raise RuntimeError(status.get("status") or f"Instagram container failed: {code}")
        time.sleep(5)
    else: raise RuntimeError("Instagram Reel processing timed out after 15 minutes.")
    published=graph_post(f"/{ig_user_id}/media_publish", token, data={"creation_id":container})
    log.info("Instagram Reel published: ig_user_id=%s container_id=%s media_id=%s", ig_user_id, container, published.get("id",""))
    return {"id":published.get("id",""),"container_id":container}


def test_meta_connection(connection):
    from .database import update_meta_connection_status, now_iso
    payload=load_page_token(connection["token_key"])
    if not payload or not payload.get("access_token"): raise RuntimeError("Saved Meta access token is missing. Reconnect the Meta account.")
    token=payload["access_token"]
    r=requests.get(META_GRAPH+f"/{connection['page_id']}", params={"fields":"id,name","access_token":token}, timeout=30)
    _json_response(r)
    update_meta_connection_status(connection["id"],"connected",now_iso())
    return {"ok":True,"page_name":connection.get("page_name",""),"instagram_username":connection.get("instagram_username","")}


def post_facebook_comment(page_id, token, target_id, message):
    """Post a comment on a Facebook video or post with cross-platform links."""
    return graph_post(f"/{target_id}/comments", token, data={"message": str(message)})


def post_instagram_comment(token, media_id, message):
    """Post a comment on an Instagram media item/reel."""
    return graph_post(f"/{media_id}/comments", token, data={"message": str(message)})


def post_facebook_page_feed(page_id, token, message, link=None, photo_path=None):
    """
    Publish a post or photo directly to the Facebook Page feed.
    If photo_path exists, uploads photo with message as caption.
    Otherwise posts text update/teaser with optional companion link.
    """
    if photo_path and Path(photo_path).exists():
        url = f"{META_GRAPH}/{page_id}/photos"
        with open(photo_path, "rb") as f:
            data = {"caption": str(message), "access_token": token}
            files = {"source": f}
            r = requests.post(url, data=data, files=files, timeout=60)
            res = _json_response(r)
            return {"id": res.get("id") or res.get("post_id"), "post_id": res.get("post_id")}
    else:
        data = {"message": str(message)}
        if link:
            data["link"] = str(link)
        res = graph_post(f"/{page_id}/feed", token, data=data)
        return {"id": res.get("id"), "post_id": res.get("id")}

