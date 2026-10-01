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


def get_pages(user_token):
    r=requests.get(META_GRAPH+"/me/accounts", params={"fields":"id,name,access_token,instagram_business_account{id,username}","access_token":user_token,"limit":100}, timeout=30)
    return _json_response(r).get("data",[])


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


def upload_facebook_page_video(page_id, token, file_path, title, description, published=True):
    with open(file_path,"rb") as fh:
        return graph_post(f"/{page_id}/videos", token, data={"title":title[:255],"description":description[:10000],"published":"true" if published else "false"}, files={"source":(Path(file_path).name,fh,"video/mp4")}, timeout=1800)


def upload_facebook_page_reel(page_id, token, file_path, description=""):
    start=graph_post(f"/{page_id}/video_reels", token, data={"upload_phase":"start"})
    video_id=start.get("video_id"); upload_url=start.get("upload_url")
    if not video_id or not upload_url: raise RuntimeError("Meta did not return a Facebook Reel upload session.")
    size=os.path.getsize(file_path)
    with open(file_path,"rb") as fh:
        r=requests.post(upload_url, headers={"Authorization":f"OAuth {token}","offset":"0","file_size":str(size),"Content-Type":"application/octet-stream"}, data=fh, timeout=1800)
        _json_response(r)
    finish=graph_post(f"/{page_id}/video_reels", token, data={"upload_phase":"finish","video_id":video_id,"description":description[:10000]})
    return {"id":video_id, **finish}


def upload_instagram_reel(ig_user_id, token, file_path, caption="", share_to_feed=True):
    size=os.path.getsize(file_path)
    if size > 1024*1024*1024: raise RuntimeError("Instagram Reel exceeds the 1 GB API limit.")
    init=graph_post(f"/{ig_user_id}/media", token, data={"media_type":"REELS","upload_type":"resumable","caption":caption[:2200],"share_to_feed":"true" if share_to_feed else "false"})
    container=init.get("id")
    upload_uri=init.get("uri") or init.get("upload_uri")
    if not container or not upload_uri: raise RuntimeError("Meta did not return an Instagram Reel upload session.")
    with open(file_path,"rb") as fh:
        r=requests.post(upload_uri, headers={"Authorization":f"OAuth {token}","offset":"0","file_size":str(size),"Content-Type":"application/octet-stream"}, data=fh, timeout=1800)
        _json_response(r)
    deadline=time.time()+900
    while time.time()<deadline:
        r=requests.get(META_GRAPH+f"/{container}", params={"fields":"status_code,status","access_token":token}, timeout=30)
        status=_json_response(r)
        code=str(status.get("status_code") or "")
        if code == "FINISHED": break
        if code in {"ERROR","EXPIRED"}: raise RuntimeError(status.get("status") or f"Instagram container failed: {code}")
        time.sleep(5)
    else: raise RuntimeError("Instagram Reel processing timed out after 15 minutes.")
    published=graph_post(f"/{ig_user_id}/media_publish", token, data={"creation_id":container})
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
