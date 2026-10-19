import json
from pathlib import Path
import threading
import keyring
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from .config import CLIENT_SECRET_PATH, KEYRING_SERVICE, TOKEN_PREFIX, TOKEN_DIR

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

def _key(channel_id):
    safe = "".join(c if c.isalnum() else "_" for c in channel_id)
    return TOKEN_PREFIX + safe

def _token_file(channel_id):
    return TOKEN_DIR / f"{_key(channel_id)}.json"

def save_credentials(channel_id, creds):
    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    raw = json.dumps(payload)
    # Keep a file-based copy so the application also works as a headless
    # systemd service without a desktop Secret Service/keyring session.
    path = _token_file(channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    try:
        keyring.set_password(KEYRING_SERVICE, _key(channel_id), raw)
    except Exception:
        pass

def load_credentials_for_channel(channel_id):
    from google.oauth2.credentials import Credentials
    from .database import get_channel
    
    keys_to_try = [_key(channel_id), f"channel:{channel_id}", channel_id]
    ch = get_channel(channel_id)
    if ch and ch.get("token_key"):
        keys_to_try.insert(0, ch["token_key"])

    raw = None
    # File storage is the primary path for systemd/headless operation.
    path = _token_file(channel_id)
    try:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
    except Exception:
        raw = None

    # Backward compatibility: migrate credentials from desktop keyring on the
    # next interactive run. save_credentials() also writes the file copy.
    if not raw:
        for k in keys_to_try:
            try:
                raw = keyring.get_password(KEYRING_SERVICE, k)
                if raw:
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(raw, encoding="utf-8")
                        path.chmod(0o600)
                    except Exception:
                        pass
                    try:
                        keyring.set_password(KEYRING_SERVICE, _key(channel_id), raw)
                    except Exception:
                        pass
                    break
            except Exception:
                continue

    if not raw:
        return None
    data = json.loads(raw)
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )

def delete_credentials(channel_id):
    from .database import get_channel
    keys_to_delete = [_key(channel_id), f"channel:{channel_id}", channel_id]
    ch = get_channel(channel_id)
    if ch and ch.get("token_key"):
        keys_to_delete.append(ch["token_key"])
    try:
        _token_file(channel_id).unlink(missing_ok=True)
    except Exception:
        pass
    for k in set(keys_to_delete):
        try:
            keyring.delete_password(KEYRING_SERVICE, k)
        except Exception:
            pass

def start_oauth(client_secret_path=None):
    import logging
    logging.getLogger(__name__).info("Starting Google OAuth in headless/manual-browser mode (open_browser=False).")
    secret_path = Path(client_secret_path) if client_secret_path else CLIENT_SECRET_PATH
    if not secret_path.is_absolute():
        from .config import BASE_DIR
        secret_path = BASE_DIR / secret_path
    if not secret_path.exists():
        raise FileNotFoundError(f"Missing OAuth client JSON: {secret_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    # Do not attempt to launch a browser from the server/systemd process.
    # The OAuth URL is printed to the application log; the user can open it
    # from a browser on the same machine and complete the localhost callback.
    return flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=False,
        browser=None,
        access_type="offline",
        prompt="consent",
    )

def get_youtube_service(channel_id):
    creds = load_credentials_for_channel(channel_id)
    if not creds:
        from .database import mark_youtube_account_reauth_required, get_channel
        ch = get_channel(channel_id)
        if ch and ch.get('account_id'):
            mark_youtube_account_reauth_required(ch['account_id'])
        raise RuntimeError("No saved Google credentials for this channel. Re-authentication is required.")
    # Refresh proactively and persist the new access token so background syncs
    # and performance requests do not repeatedly start from an expired token.
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            from google.auth.exceptions import RefreshError
            from .database import get_channel, mark_youtube_account_reauth_required
            ch=get_channel(channel_id)
            if ch and ch.get('account_id'):
                mark_youtube_account_reauth_required(ch['account_id'])
            if isinstance(exc, RefreshError) and 'invalid_grant' in str(exc):
                raise RuntimeError("Google credentials have expired or been revoked. Re-authentication is required.") from exc
            raise
        try:
            save_credentials(channel_id, creds)
        except Exception:
            pass
    elif creds.expired and not creds.refresh_token:
        from .database import get_channel, mark_youtube_account_reauth_required
        ch=get_channel(channel_id)
        if ch and ch.get('account_id'):
            mark_youtube_account_reauth_required(ch['account_id'])
        raise RuntimeError("Google credentials are expired and no refresh token is available. Re-authentication is required.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

def get_google_account_email(creds):
    """Return the Google account email associated with these OAuth credentials."""
    oauth2 = build("oauth2", "v2", credentials=creds, cache_discovery=False)
    info = oauth2.userinfo().get().execute()
    return str(info.get("email") or "").strip()


def discover_channels(creds):
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    response = youtube.channels().list(
        part="id,snippet",
        mine=True,
        maxResults=50,
    ).execute()
    return response.get("items", [])


def test_youtube_account(account_id):
    """Test every connected channel belonging to a YouTube account without removing it."""
    from .database import list_channels_for_account, update_youtube_account_status, now_iso
    channels = list_channels_for_account(account_id)
    if not channels:
        raise RuntimeError("This YouTube account has no connected channel.")
    results=[]
    all_ok=True
    for ch in channels:
        cid=ch["channel_id"]
        try:
            service=get_youtube_service(cid)
            resp=service.channels().list(part="id,snippet", id=cid).execute()
            items=resp.get("items",[])
            if not items:
                raise RuntimeError("Channel is not accessible with the saved Google credentials.")
            results.append({"channel_id":cid,"channel_name":ch.get("channel_name", ""),"ok":True})
        except Exception as exc:
            all_ok=False
            results.append({"channel_id":cid,"channel_name":ch.get("channel_name", ""),"ok":False,"error":str(exc)})
    if all_ok:
        from .database import mark_youtube_account_auth_success
        mark_youtube_account_auth_success(account_id)
    else:
        # A credential failure is tracked as reauth-required; other API failures remain error.
        if any('re-authentication is required' in str(r.get('error','')).lower() or 'invalid_grant' in str(r.get('error','')).lower() for r in results if not r['ok']):
            from .database import mark_youtube_account_reauth_required
            mark_youtube_account_reauth_required(account_id)
        else:
            update_youtube_account_status(account_id, "error", now_iso())
    if not all_ok:
        failed="; ".join(f'{r["channel_name"]}: {r.get("error","test failed")}' for r in results if not r["ok"])
        raise RuntimeError(f"YouTube connection test failed. {failed}")
    return {"ok":True,"channels":results}


def logout_youtube_account(account_id):
    """Log an account out of this uploader while preserving account, channel, folder and schedule data."""
    from .database import list_channels_for_account, update_youtube_account_status
    import requests
    channels=list_channels_for_account(account_id)
    revoked=0
    for ch in channels:
        try:
            creds=load_credentials_for_channel(ch["channel_id"])
            token=(creds.refresh_token if creds else None) or (creds.token if creds else None)
            if token:
                try:
                    requests.post("https://oauth2.googleapis.com/revoke", params={"token":token}, timeout=8)
                    revoked += 1
                except Exception:
                    pass
        except Exception:
            pass
        delete_credentials(ch["channel_id"])
    update_youtube_account_status(account_id, "logged_out", None)
    return {"ok":True,"channels":len(channels),"revoked":revoked}
