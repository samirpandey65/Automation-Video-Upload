import base64
import json
import logging
import mimetypes
import time
from pathlib import Path

import keyring
import requests
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from .config import (
    CLIENT_SECRET_PATH, GEMINI_SCOPES, GEMINI_TOKEN_PREFIX,
    GEMINI_DEFAULT_MODEL, GEMINI_BASE_URL, FFMPEG_EXE, TOKEN_DIR,
)
from .database import get_ai_connection, list_ai_connections
from .ai_validation import validate_metadata

SERVICE = "YouTubeLocalAutoUploader"
log = logging.getLogger(__name__)


def _key(email):
    return GEMINI_TOKEN_PREFIX + "".join(c if c.isalnum() else "_" for c in email.lower())


def _project_id(client_secret_path=None):
    path = Path(client_secret_path) if client_secret_path else Path(CLIENT_SECRET_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("installed") or data.get("web") or {}).get("project_id", "")
    except Exception:
        return ""


def start_oauth(client_secret_path=None):
    path = Path(client_secret_path) if client_secret_path else Path(CLIENT_SECRET_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    if not path.exists():
        raise FileNotFoundError(f"Missing Gemini OAuth client JSON: {path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(path), GEMINI_SCOPES)
    # Do not attempt to launch a browser from the server/systemd process.
    # The OAuth URL is printed to the application log; open it manually in a
    # browser on the same machine and complete the localhost callback.
    return flow.run_local_server(
        host="127.0.0.1", port=0, open_browser=False, browser=None,
        access_type="offline", prompt="consent"
    )


def _email_from_id_token(id_token):
    if not id_token:
        return ""
    try:
        parts = id_token.split(".")
        if len(parts) >= 2:
            padding = "=" * ((4 - len(parts[1]) % 4) % 4)
            payload = base64.urlsafe_b64decode(parts[1] + padding)
            data = json.loads(payload.decode("utf-8"))
            return str(data.get("email", "")).strip()
    except Exception as exc:
        log.warning("Could not decode OAuth ID token: %s", exc)
    return ""


def _userinfo(creds):
    # The ID token is the preferred identity source. This avoids the previous
    # 401 problem when the access token did not carry a userinfo scope.
    email = _email_from_id_token(getattr(creds, "id_token", None))
    if email:
        return {"email": email}

    headers = {"Authorization": f"Bearer {creds.token}"}
    for endpoint in (
        "https://www.googleapis.com/oauth2/v3/userinfo",
        "https://openidconnect.googleapis.com/v1/userinfo",
    ):
        try:
            r = requests.get(endpoint, headers=headers, timeout=20)
            if r.ok and r.json().get("email"):
                return r.json()
        except Exception as exc:
            log.info("UserInfo fallback failed: %s", exc)
    raise RuntimeError(
        "Could not determine the Google account email. Reconnect Gemini and allow the requested profile/email permissions."
    )


def _token_file(email):
    return TOKEN_DIR / f"{_key(email)}.json"

def save_credentials(email, creds):
    expiry_iso = creds.expiry.isoformat() if getattr(creds, "expiry", None) else None
    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": expiry_iso,
    }
    raw = json.dumps(payload)
    path = _token_file(email)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    try: path.chmod(0o600)
    except Exception: pass
    try: keyring.set_password(SERVICE, _key(email), raw)
    except Exception: pass


def load_credentials(conn):
    raw = None
    path = _token_file(conn["account_email"])
    try:
        if path.exists(): raw = path.read_text(encoding="utf-8")
    except Exception:
        raw = None
    if not raw:
        try:
            raw = keyring.get_password(SERVICE, conn["token_key"])
            if raw:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(raw, encoding="utf-8")
                try: path.chmod(0o600)
                except Exception: pass
        except Exception:
            raw = None
    if not raw:
        return None
    d = json.loads(raw)
    expiry = None
    if d.get("expiry"):
        try:
            from datetime import datetime
            expiry = datetime.fromisoformat(d["expiry"])
        except Exception:
            pass
    creds = Credentials(
        token=d.get("token"), refresh_token=d.get("refresh_token"),
        token_uri=d.get("token_uri"), client_id=d.get("client_id"),
        client_secret=d.get("client_secret"), scopes=d.get("scopes"),
        expiry=expiry,
    )
    if creds.refresh_token and (creds.expired or not creds.valid):
        try:
            creds.refresh(Request())
            save_credentials(conn["account_email"], creds)
        except Exception as exc:
            log.warning("Failed refreshing Gemini credentials: %s", exc)
    return creds


def delete_credentials(conn):
    try: _token_file(conn["account_email"]).unlink(missing_ok=True)
    except Exception: pass
    try: keyring.delete_password(SERVICE, conn["token_key"])
    except Exception: pass


def logout_account(connection_id):
    """Log out a Gemini account without deleting its database record/settings."""
    from .database import get_conn, now_iso
    conn = get_ai_connection(connection_id)
    if not conn or conn.get("provider") != "gemini":
        raise RuntimeError("Gemini connection not found.")
    try:
        creds = load_credentials(conn)
        token = (creds.refresh_token if creds else None) or (creds.token if creds else None)
        if token:
            try:
                requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=8)
            except Exception:
                pass
    finally:
        delete_credentials(conn)
    db = get_conn()
    db.execute("UPDATE ai_connections SET status='logged_out', last_verified_at=? WHERE id=?", (now_iso(), connection_id))
    db.commit(); db.close()
    return {"ok": True, "connection_id": connection_id, "status": "logged_out"}


def connect_account(client_secret_path=None):
    creds = start_oauth(client_secret_path)
    info = _userinfo(creds)
    email = str(info.get("email", "")).strip()
    if not email:
        raise RuntimeError("Could not determine Google account email.")
    save_credentials(email, creds)
    return email, _project_id(client_secret_path), str(Path(client_secret_path).resolve()) if client_secret_path else str(Path(CLIENT_SECRET_PATH).resolve())


def _base_headers(creds, project):
    headers = {"Authorization": f"Bearer {creds.token}"}
    if project:
        headers["x-goog-user-project"] = project
    return headers


def _list_models(creds, project, email=None):
    headers = _base_headers(creds, project)
    r = requests.get(f"{GEMINI_BASE_URL}/models", headers=headers, timeout=30)
    if r.status_code == 401 and creds.refresh_token:
        try:
            creds.refresh(Request())
            if email:
                save_credentials(email, creds)
            headers = _base_headers(creds, project)
            r = requests.get(f"{GEMINI_BASE_URL}/models", headers=headers, timeout=30)
        except Exception as exc:
            log.warning("Refresh on 401 failed: %s", exc)
    if r.status_code == 401:
        raise RuntimeError("Gemini OAuth token is unauthorized. Disconnect and reconnect this Gemini account.")
    if r.status_code == 403:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise RuntimeError(
            f"Gemini API 403 Forbidden: {msg}. Enable the Generative Language API in the Google Cloud project and check OAuth permissions."
        )
    r.raise_for_status()
    models = []
    for m in r.json().get("models", []):
        name = str(m.get("name", "")).replace("models/", "")
        methods = m.get("supportedGenerationMethods") or []
        if name and (not methods or "generateContent" in methods):
            models.append(name)
    return models


def test_connection(connection_id):
    from .database import get_conn, now_iso
    conn = get_ai_connection(connection_id)
    if not conn or conn.get("provider") != "gemini":
        raise RuntimeError("Gemini connection not found.")

    def _mark_failed(status_val):
        try:
            db = get_conn()
            db.execute(
                "UPDATE ai_connections SET status=?, last_verified_at=? WHERE id=?",
                (status_val, now_iso(), connection_id),
            )
            db.commit()
            db.close()
        except Exception:
            pass

    creds = load_credentials(conn)
    if not creds:
        _mark_failed("reauth_required")
        raise RuntimeError("No saved Gemini credentials found for this account. Reconnect this account.")

    project = conn.get("project_id") or _project_id(conn.get("client_secret_path"))
    try:
        models = _list_models(creds, project, email=conn.get("account_email"))
    except Exception as e:
        err_msg = str(e).lower()
        if "unauthorized" in err_msg or "expired" in err_msg or "revoked" in err_msg or "invalid_grant" in err_msg or "401" in err_msg:
            _mark_failed("reauth_required")
        else:
            _mark_failed("error")
        raise

    preferred_models = [
        "gemini-flash-latest", "gemini-flash-lite-latest",
        "gemini-3.5-flash-lite", "gemini-3.5-flash",
        "gemini-3-flash-preview", "gemini-3.1-flash-lite",
        "gemini-3.6-flash", "gemini-3.7-flash"
    ]
    configured = conn.get("model") or GEMINI_DEFAULT_MODEL
    if configured in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash") or configured not in models:
        usable = next((m for m in preferred_models if m in models), GEMINI_DEFAULT_MODEL)
    else:
        usable = configured

    db = get_conn()
    db.execute(
        "UPDATE ai_connections SET status='connected', model=?, last_verified_at=? WHERE id=?",
        (usable, now_iso(), connection_id),
    )
    db.commit(); db.close()

    return {
        "ok": True, "email": conn["account_email"], "project_id": project,
        "status": "connected", "models_count": len(models),
        "default_model": usable, "models": models,
    }


def _upload_file_api(path, creds, project):
    """Upload larger media through Gemini Files API using the OAuth bearer token."""
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    size = path.stat().st_size
    headers = {**_base_headers(creds, project),
               "X-Goog-Upload-Protocol": "resumable",
               "X-Goog-Upload-Command": "start",
               "X-Goog-Upload-Header-Content-Length": str(size),
               "X-Goog-Upload-Header-Content-Type": mime,
               "Content-Type": "application/json"}
    start = requests.post(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        headers=headers, json={"file": {"display_name": path.name}}, timeout=60
    )
    if not start.ok:
        raise RuntimeError(f"Gemini Files API upload initialization failed ({start.status_code}): {start.text[:500]}")
    upload_url = start.headers.get("X-Goog-Upload-URL") or start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini Files API did not return an upload URL.")
    with path.open("rb") as fh:
        up = requests.post(
            upload_url,
            headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
            data=fh, timeout=900
        )
    if not up.ok:
        raise RuntimeError(f"Gemini file upload failed ({up.status_code}): {up.text[:500]}")
    info = up.json().get("file") or {}
    name = info.get("name")
    uri = info.get("uri")
    if not name or not uri:
        raise RuntimeError("Gemini Files API returned no file URI.")
    # Wait until Gemini has processed the video.
    for _ in range(120):
        r = requests.get(f"{GEMINI_BASE_URL}/{name}", headers=_base_headers(creds, project), timeout=30)
        r.raise_for_status()
        state = str((r.json().get("state") or "")).upper()
        if state == "ACTIVE":
            return {"file_data": {"mime_type": mime, "file_uri": uri}}
        if state == "FAILED":
            raise RuntimeError("Gemini Files API video processing failed.")
        time.sleep(2)
    raise RuntimeError("Gemini Files API video processing timed out.")

def _prepare_video_part(path, creds=None, project=""):
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    size = path.stat().st_size
    # Google recommends inline input for videos under 20MB; use Files API above that with compression fallback
    if size > 18 * 1024 * 1024:
        if creds is not None:
            try:
                return _upload_file_api(path, creds, project)
            except Exception as exc:
                log.warning("Gemini Files API upload failed (%s); compressing lightweight preview", exc)
        try:
            import tempfile, subprocess
            from .config import FFMPEG_EXE
            temp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            temp_file.close()
            cmd = [
                FFMPEG_EXE, "-y", "-i", str(path),
                "-vf", "scale=-2:480",
                "-r", "15",
                "-c:v", "libx264", "-crf", "32", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "64k",
                temp_file.name
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=90)
            data = Path(temp_file.name).read_bytes()
            try:
                Path(temp_file.name).unlink()
            except Exception:
                pass
            return {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(data).decode("ascii")}}
        except Exception as e:
            log.warning("FFmpeg video preview compression failed: %s", e)
    data = path.read_bytes()
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}}


def _extract_text(resp):
    return "\n".join(
        part.get("text", "")
        for candidate in resp.get("candidates", [])
        for part in (candidate.get("content") or {}).get("parts", [])
        if part.get("text")
    ).strip()


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise RuntimeError("Gemini did not return valid JSON metadata.")
    try:
        return json.loads(text[a:b+1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned malformed JSON: {exc}")


def _build_prompt(filename):
    return f'''You are an expert video-understanding and YouTube/Instagram/Facebook SEO engine.

Analyze the ACTUAL VIDEO, including the visual sequence and audio when available. The filename is only a reference and is NOT evidence.
Never invent names, cities, relationships, dialogue, plot points, brands, events, or details that are not supported by the video.

HARD LANGUAGE RULE:
- ALL generated natural-language fields MUST be English or Roman Hinglish only.
- NEVER use Devanagari/Hindi script anywhere.
- Roman Hinglish examples are allowed: "Papa ne phone check kiya aur investigation shuru ho gayi!"
- Do not output Hindi characters even if the video's speech is Hindi.

TITLE:
- Under 70 characters (optimal for mobile YouTube Shorts & Reels feeds without truncation).
- CRITICAL: Place the main emotional subject or action in the FIRST 30 CHARACTERS before mobile screen cutoff.
- Specific, highly engaging, CTR-optimized title tailored strictly to the actual video action, climax, or humor.
- Strong YouTube Shorts hook: curiosity, humor, surprise, emotion, or tension supported strictly by visual events.
- Truthful. No invented plot.
- ZERO META-COMMENTARY: NEVER output prompt instructions, prefix notes, or commentary like 'Lead immediately with...', 'Suggested title:', 'Hook:', or 'Title:'. Output ONLY the final publish-ready title.
- NO CLICHÉ BOILERPLATE: NEVER use generic repetitive boilerplate suffixes or prefixes like "What ACTUALLY Happened?", "The Shocking Truth About", "Wait For The End", "The Moment Everything Changed", or "You Won't Believe".
- Do not begin with "This video is about".
- Do not simply copy the filename.

DESCRIPTION:
- 1-2 compact natural paragraphs.
- Describe what actually happens.
- English/Roman Hinglish only.
- Light CTA at the end.
- No keyword stuffing.

CHAPTERS & TIMESTAMPS:
- If the video contains multiple scenes, topics, or is longer than 60 seconds, output realistic timestamps starting with 00:00 (e.g. 00:00 Intro, 00:45 The Twist, 01:30 Final Moments).
- For short meme reels/shorts under 60 seconds, output an empty list [].

HASHTAGS: 3-6 relevant hashtags, including #shorts when appropriate.
TAGS: 8-15 relevant YouTube search phrases.

Return ONLY valid JSON with exactly these fields:
{{
  "visual_summary":"factual visual/audio-supported summary",
  "setting":"actual setting",
  "subjects":["actual visible subjects"],
  "style":"actual visual style",
  "title":"clickable truthful title",
  "description":"SEO-friendly natural description",
  "chapters":["00:00 Hook", "00:30 Highlight"],
  "hashtags":["shorts"],
  "tags":["relevant search phrase"],
  "summary":"one-sentence English/Roman Hinglish summary",
  "confidence":"high|medium|low"
}}

Filename: {filename}

IMPORTANT METADATA RULE: hashtags and tags must contain lowercase ASCII letters/numbers only. Never output capital letters. Hashtags must include no spaces and should be returned without the # prefix. Tags must also be lowercase and without #.'''


def analyze(video_path, filename, connection_id, model=None):
    conn = get_ai_connection(connection_id)
    if not conn or conn.get("provider") != "gemini":
        raise RuntimeError("Gemini connection not found.")
    creds = load_credentials(conn)
    if not creds:
        raise RuntimeError("No saved Gemini credentials for this account.")
    project = conn.get("project_id") or _project_id(conn.get("client_secret_path"))
    if not project:
        raise RuntimeError("Google Cloud project_id missing from OAuth client JSON.")

    models = _list_models(creds, project)
    selected = model or conn.get("model") or GEMINI_DEFAULT_MODEL
    preferred_models = [
        "gemini-flash-latest", "gemini-flash-lite-latest",
        "gemini-3.5-flash-lite", "gemini-3.5-flash",
        "gemini-3-flash-preview", "gemini-3.1-flash-lite",
        "gemini-3.6-flash", "gemini-3.7-flash"
    ]
    if selected in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash") or (models and selected not in models):
        selected = next((m for m in preferred_models if m in models), selected)

    prompt = _build_prompt(filename)
    body = {
        "contents": [{"role": "user", "parts": [_prepare_video_part(video_path, creds, project), {"text": prompt}]}],
        "generationConfig": {"temperature": 0.15, "responseMimeType": "application/json"},
    }

    candidates = [selected]
    for alt in preferred_models:
        if alt not in candidates and (not models or alt in models):
            candidates.append(alt)

    resp_json = None; used_model = selected; last_error = ""
    for mdl in candidates:
        for attempt in range(2):
            try:
                r = requests.post(
                    f"{GEMINI_BASE_URL}/models/{mdl}:generateContent",
                    headers={**_base_headers(creds, project), "Content-Type": "application/json"},
                    json=body, timeout=300,
                )
                if r.ok:
                    resp_json = r.json(); used_model = mdl; break
                if r.status_code == 401 and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        save_credentials(conn["account_email"], creds)
                        r = requests.post(
                            f"{GEMINI_BASE_URL}/models/{mdl}:generateContent",
                            headers={**_base_headers(creds, project), "Content-Type": "application/json"},
                            json=body, timeout=300,
                        )
                        if r.ok:
                            resp_json = r.json(); used_model = mdl; break
                    except Exception as exc:
                        log.warning("Refresh on 401 in analyze failed: %s", exc)
                if r.status_code == 401:
                    raise RuntimeError("Gemini OAuth token unauthorized. Reconnect the Gemini account from Connections.")
                if r.status_code == 403:
                    try: msg = r.json().get("error", {}).get("message", r.text)
                    except Exception: msg = r.text
                    last_error = f"Gemini API 403 Forbidden: {msg}"
                    log.warning("[AI] Gemini model %s access denied (403): %s. Falling back to next candidate model...", mdl, msg)
                    time.sleep(1)
                    # Fall back to next model rather than aborting immediately
                    break
                if r.status_code == 429:
                    last_error = r.text[:300]
                    log.warning("[AI] Gemini model %s quota exceeded (429). Falling back to next candidate model...", mdl)
                    time.sleep(2)
                    # Break out immediately so next model in candidates is tried
                    break
                if r.status_code in (500, 502, 503, 504):
                    last_error = r.text[:300]
                    time.sleep(2 * (attempt + 1)); continue
                r.raise_for_status()
            except RuntimeError:
                raise
            except requests.RequestException as exc:
                last_error = str(exc); time.sleep(2 * (attempt + 1))
        if resp_json:
            log.info("[AI] Gemini analysis succeeded with model: %s", used_model)
            break
    if not resp_json:
        if "429" in str(last_error) or "quota" in str(last_error).lower():
            raise RuntimeError("Gemini API Rate Limit / Quota Exceeded (429): Free/current tier quota limit reached. Please wait a minute, switch models in Connections, or try another provider.")
        if "403" in str(last_error):
            raise RuntimeError(f"{last_error}. Check that Generative Language API is enabled in Google Cloud Console.")
        raise RuntimeError(f"Gemini analysis failed after retries: {last_error or 'no response'}")

    data = _parse_json(_extract_text(resp_json))
    from .thumbnail import _clean_title_hook
    raw_title = str(data.get("title", "")).strip()
    title = _clean_title_hook(raw_title) or raw_title
    description = str(data.get("description", "")).strip()
    hashtags = [str(x).strip().lstrip("#").lower() for x in data.get("hashtags", []) if str(x).strip()]
    tags = [str(x).strip().lstrip("#").lower() for x in data.get("tags", []) if str(x).strip()]
    summary = str(data.get("summary", "")).strip()
    validation = validate_metadata(title, description, hashtags, tags)
    if not validation["passed"]:
        raise RuntimeError("Gemini quality gate failed: " + "; ".join(validation["errors"]))

    chapters = [str(c).strip() for c in data.get("chapters", []) if str(c).strip()]
    if chapters and "00:00" in chapters[0]:
        chapter_block = "\n\nTimestamps:\n" + "\n".join(chapters)
        if chapter_block not in description and len(description) + len(chapter_block) <= 4900:
            description = description + chapter_block

    visual = {
        "visual_summary": str(data.get("visual_summary", "")).strip(),
        "setting": str(data.get("setting", "")).strip(),
        "subjects": data.get("subjects", []),
        "style": str(data.get("style", "")).strip(),
        "confidence": str(data.get("confidence", "")).strip(),
    }
    return {
        "metadata": {
            "title": title[:100], "description": description[:5000],
            "hashtags": hashtags[:6], "tags": tags[:15], "summary": summary[:2000],
            "chapters": chapters,
        },
        "transcript": "",
        "whisper_status": "gemini_multimodal_understanding",
        "frame_count": 0,
        "video_info": {},
        "visual_analysis": visual,
        "vision_model": used_model,
        "analysis_quality": "passed",
        "validation": validation,
        "ai_provider": "gemini",
        "ai_connection_id": connection_id,
    }


def generate_creator_quotes(count=5):
    """
    Generate fresh YouTube creator tips and inspirational quotes using connected Gemini account.
    Returns a list of clean quote strings, or [] if unavailable.
    """
    try:
        conns = list_ai_connections("gemini")
        if not conns:
            return []
        conn = conns[0]
        creds = load_credentials(conn)
        if not creds:
            return []
        project = conn.get("project_id") or _project_id(conn.get("client_secret_path"))
        prompt = (
            f"Generate {count} unique, punchy YouTube creator tips or inspirational quotes for a creator dashboard ticker. "
            "Cover YouTube CTR & thumbnail secrets, title SEO, audience retention (first 30s hook, pattern interrupts), "
            "storytelling suspense, and Hindi/Hinglish creator wisdom. "
            "Each tip should end with a relevant emoji. "
            "Output ONLY a raw JSON array of strings without markdown fences or additional commentary. "
            "Example: [\"Tip 1 🚀\", \"Tip 2 💡\"]"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.85, "responseMimeType": "application/json"}
        }
        for model in ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest"):
            try:
                r = requests.post(
                    f"{GEMINI_BASE_URL}/models/{model}:generateContent",
                    headers={**_base_headers(creds, project), "Content-Type": "application/json"},
                    json=body,
                    timeout=12,
                )
                if r.status_code == 401 and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        save_credentials(conn["account_email"], creds)
                        r = requests.post(
                            f"{GEMINI_BASE_URL}/models/{model}:generateContent",
                            headers={**_base_headers(creds, project), "Content-Type": "application/json"},
                            json=body,
                            timeout=12,
                        )
                    except Exception as exc:
                        err_str = str(exc)
                        if "invalid_grant" in err_str or "Token has been expired" in err_str:
                            # Token is permanently revoked — no point trying other models
                            log.warning("Gemini token invalid/revoked for quotes, skipping: %s", conn.get("account_email",""))
                            return []
                        log.warning("Refresh on 401 in generate_creator_quotes failed: %s", exc)
                        break  # stop trying other models if refresh fails
                if r.ok:
                    data = r.json()
                    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if text:
                        text = text.strip()
                        if text.startswith("```"):
                            text = text.split("\n", 1)[1] if "\n" in text else text
                            text = text.rsplit("```", 1)[0].strip()
                        parsed = json.loads(text)
                        if isinstance(parsed, list):
                            return [str(q).strip() for q in parsed if str(q).strip()][:count]
            except Exception as e:
                log.debug("Optional Gemini quote attempt on %s failed: %s", model, e)
                continue
    except Exception as exc:
        log.warning("generate_creator_quotes error: %s", exc)
    return []

