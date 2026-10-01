import asyncio
import logging
import os
import time
import shutil
import tempfile
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .config import WEB_DIR, LOG_DIR, GEMINI_DEFAULT_MODEL
from .database import (
    init_db, list_channels, get_channel, upsert_channel, delete_channel,
    get_inventory, get_inventory_by_id, get_inventory_by_hash, get_inventory_by_path, upsert_video, update_video, stats,
    set_channel_folder, get_channel_folder,
    list_schedules, upsert_schedule, replace_day_schedules, delete_schedule,
    list_ai_connections, get_ai_connection, upsert_ai_connection, delete_ai_connection,
    list_youtube_accounts, get_youtube_account, upsert_youtube_account, update_youtube_account_status, set_channel_account, list_channels_for_account,
    count_users, create_user, get_user, update_user_password, list_users, touch_user_login, add_audit_log, list_audit_log, create_upload_job, get_upload_job, update_upload_job, list_upload_jobs, add_upload_attempt,
    upsert_remote_video, list_remote_videos, get_remote_video_by_hash, get_inventory_by_youtube_id, get_remote_video, update_channel_sync, add_remote_metrics_snapshot,
    database_health, get_schedule
)
from .auth import (
    start_oauth, discover_channels, get_google_account_email, save_credentials,
    delete_credentials, test_youtube_account, logout_youtube_account
)
from .local_auth import ensure_admin, setup_available, create_first_admin, make_session, read_session, verify_password, hash_password, SESSION_COOKIE
from .video_utils import scan_folder, sha256_file
from .youtube import upload_video, list_channel_videos, extract_hash_marker, get_video_stats
from .scheduler import start_scheduler, stop_scheduler
from .analytics import get_video_analytics
from .ai_validation import validate_metadata
from .gemini import (
    connect_account as connect_gemini_account,
    delete_credentials as delete_gemini_credentials,
    logout_account as logout_gemini_account,
    analyze as analyze_gemini,
    test_connection as test_gemini_connection,
    generate_creator_quotes,
)

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "app.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    force=True,
)
log = logging.getLogger(__name__)

app = FastAPI(title="YouTube Local Auto Uploader")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

_LOGIN_FAILURES = {}
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 8

def _login_allowed(ip):
    now=time.time()
    failures=[t for t in _LOGIN_FAILURES.get(ip, []) if now-t < _LOGIN_WINDOW_SECONDS]
    _LOGIN_FAILURES[ip]=failures
    return len(failures) < _LOGIN_MAX_FAILURES

def _record_login_failure(ip):
    now=time.time()
    failures=[t for t in _LOGIN_FAILURES.get(ip, []) if now-t < _LOGIN_WINDOW_SECONDS]
    failures.append(now)
    _LOGIN_FAILURES[ip]=failures



def current_user(request):
    return getattr(request, "state", None) and getattr(request.state, "user", None)

def require_role(request, *roles):
    user=current_user(request)
    if not user or user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return user

def audit(request, action, resource='', resource_id='', details=None):
    user=current_user(request)
    if user: add_audit_log(user.get("username",""), action, resource, resource_id, details)


def compose_description(description, hashtags):
    base=str(description or '').strip()
    hs=[str(x).strip().lstrip('#').lower() for x in (hashtags or []) if str(x).strip()]
    suffix=' '.join('#'+x for x in hs)
    if suffix and suffix.lower() not in base.lower():
        base=(base+'\n\n'+suffix).strip()
    return base[:5000]

def validate_channel_path(channel_id, raw_path, must_exist=True):
    path=Path(raw_path).expanduser().resolve()
    folder=get_channel_folder(channel_id)
    if not folder:
        raise HTTPException(status_code=400, detail="Configure a video folder for this channel first.")
    root=Path(folder).expanduser().resolve()
    try: path.relative_to(root)
    except ValueError: raise HTTPException(status_code=403, detail="File is outside the channel's configured video folder.")
    if must_exist and (not path.exists() or not path.is_file()):
        raise HTTPException(status_code=400, detail="Video file not found.")
    return path

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logging.warning("Validation error on %s %s: %s", request.method, request.url.path, exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

class ScanRequest(BaseModel):
    channel_id: str
    folder: str


class ScheduleRequest(BaseModel):
    channel_id: str
    weekday: int = Field(ge=0, le=6)
    time_local: str
    enabled: bool = True
    privacy_status: str = "private"
    category_id: str = "24"
    title_template: str = "{filename}"
    description_template: str = ""
    tags: str = ""
    made_for_kids: bool = False
    ai_provider: str = "gemini"
    ai_connection_id: int | None = None
    contains_synthetic_media: bool = True
    video_language: str = "hi"
    title_description_language: str = "hi"
    caption_certification: str = "none"
    recording_date: str = ""
    video_location: str = "India"

class ScheduleDayRequest(BaseModel):
    channel_id: str
    weekday: int = Field(ge=0, le=6)
    enabled: bool = True
    videos_per_day: int = Field(default=1, ge=1, le=12)
    start_time: str = "19:00"
    interval_minutes: int = Field(default=240, ge=1, le=1440)
    privacy_status: str = "private"
    category_id: str = "24"
    title_template: str = "{filename}"
    description_template: str = ""
    tags: str = ""
    made_for_kids: bool = False
    ai_provider: str = "gemini"
    ai_connection_id: int | None = None
    contains_synthetic_media: bool = True
    video_language: str = "hi"
    title_description_language: str = "hi"
    caption_certification: str = "none"
    recording_date: str = ""
    video_location: str = "India"

class ScheduleManualDayRequest(BaseModel):
    channel_id: str
    weekday: int = Field(ge=0, le=6)
    enabled: bool = True
    times: list[str] = Field(default_factory=list, min_length=0, max_length=24)
    privacy_status: str = "private"
    category_id: str = "24"
    title_template: str = "{filename}"
    description_template: str = ""
    tags: str = ""
    made_for_kids: bool = False
    ai_provider: str = "gemini"
    ai_connection_id: int | None = None
    contains_synthetic_media: bool = True
    video_language: str = "hi"
    title_description_language: str = "hi"
    caption_certification: str = "none"
    recording_date: str = ""
    video_location: str = "India"

class AnalyzeRequest(BaseModel):
    channel_id: str
    file_path: str = ""
    path: str = ""
    provider: str = "gemini"
    connection_id: int | None = None
    model: str | None = None
    vision_model: str | None = None

    @model_validator(mode="after")
    def check_file_path(self):
        if not self.file_path and self.path:
            self.file_path = self.path
        if not self.file_path:
            raise ValueError("file_path is required.")
        return self

class UploadRequest(BaseModel):
    channel_id: str
    file_path: str = ""
    path: str = ""
    title: str
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    privacy_status: str = "private"
    category_id: str = "24"
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    video_language: str = "hi"
    title_description_language: str = "hi"
    caption_certification: str = "none"
    recording_date: str = ""
    video_location: str = "India"

    @model_validator(mode="after")
    def check_file_path(self):
        if not self.file_path and self.path:
            self.file_path = self.path
        if not self.file_path:
            raise ValueError("file_path is required.")
        return self

@app.middleware("http")
async def local_login_guard(request: Request, call_next):
    path=request.url.path
    if path.startswith("/static/") or path in {"/login","/setup","/favicon.ico"} or path.startswith("/api/auth/"):
        return await call_next(request)
    user=read_session(request.cookies.get(SESSION_COOKIE))
    if not user:
        if path.startswith("/api/"):
            response=JSONResponse(status_code=401, content={"detail":"Login required."})
        else:
            response=RedirectResponse(url="/login", status_code=303)
    else:
        request.state.user=user
        response=await call_next(request)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Cache-Control"]="no-store"
    return response

@app.on_event("startup")
def startup():
    init_db()
    if ensure_admin():
        logging.info("Created local admin from YLA_ADMIN_USER/YLA_ADMIN_PASSWORD configuration.")
    elif setup_available():
        logging.info("First-run setup required. Open /setup to create the local administrator.")
    logging.info("========== YouTube Local Auto Uploader STARTING ==========")
    logging.info("Python=%s | PID=%s | Base=%s", os.sys.version.split()[0], os.getpid(), str(Path(__file__).resolve().parent.parent))
    logging.info("Database=%s | Log=%s", str(__import__('app.config', fromlist=['DB_PATH']).DB_PATH), str(LOG_DIR / 'app.log'))
    logging.info("Scheduler starting...")
    start_scheduler()
    logging.info("Application startup complete.")

@app.on_event("shutdown")
def shutdown():
    logging.info("Application shutdown requested.")
    stop_scheduler()
    logging.info("========== YouTube Local Auto Uploader STOPPED ==========")

def page(name):
    return (WEB_DIR / name).read_text(encoding="utf-8")

@app.get("/login", response_class=HTMLResponse)
def login_page():
    if setup_available():
        return RedirectResponse(url="/setup", status_code=303)
    return page("login.html")

@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    if not setup_available():
        return RedirectResponse(url="/login", status_code=303)
    return page("setup.html")

@app.get("/api/auth/setup-status")
def setup_status():
    return {"setup_required": setup_available()}

@app.post("/api/auth/setup")
def api_setup(payload: dict, request: Request):
    if not setup_available():
        raise HTTPException(status_code=409, detail="Administrator setup has already been completed.")
    username=str(payload.get("username") or "").strip()
    password=str(payload.get("password") or "")
    confirm=str(payload.get("confirm_password") or "")
    if password != confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    try:
        user=create_first_admin(username, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    resp=JSONResponse({"ok":True,"user":{"username":user["username"],"role":user["role"]}})
    resp.set_cookie(SESSION_COOKIE, make_session(user["username"]), httponly=True, samesite="lax", max_age=7*24*3600)
    return resp

@app.post("/api/auth/login")
async def api_login(payload: dict, request: Request):
    username=str(payload.get("username") or "").strip()
    password=str(payload.get("password") or "")
    ip=request.client.host if request.client else "local"
    if not _login_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")
    user=get_user(username)
    if not user or not verify_password(password,user["password_hash"]):
        _record_login_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    _LOGIN_FAILURES.pop(ip, None)
    touch_user_login(username)
    resp=JSONResponse({"ok":True,"user":{"username":user["username"],"role":user["role"]}})
    resp.set_cookie(SESSION_COOKIE, make_session(username), httponly=True, samesite="lax", max_age=7*24*3600)
    return resp

@app.post("/api/auth/logout")
def api_logout():
    resp=JSONResponse({"ok":True}); resp.delete_cookie(SESSION_COOKIE); return resp

@app.get("/api/auth/me")
def api_me(request: Request):
    user=getattr(request.state,"user",None) or read_session(request.cookies.get(SESSION_COOKIE))
    if not user: raise HTTPException(status_code=401,detail="Login required.")
    return {"user":{"username":user["username"],"role":user["role"]}}

@app.get("/api/users")
def api_users(request: Request):
    user=require_role(request,"admin")
    return {"users":list_users()}

@app.post("/api/users")
def api_create_user(payload: dict, request: Request):
    user=require_role(request,"admin")
    username=str(payload.get("username") or "").strip()
    password=str(payload.get("password") or "")
    role=str(payload.get("role") or "editor")
    if len(username)<3 or len(password)<8: raise HTTPException(status_code=400,detail="Username must be 3+ chars and password 8+ chars.")
    if role not in {"admin","editor"}: raise HTTPException(status_code=400,detail="Invalid role.")
    if get_user(username): raise HTTPException(status_code=400,detail="Username already exists.")
    return {"user":create_user(username,hash_password(password),role)}

@app.post("/api/users/password")
def api_change_password(payload: dict, request: Request):
    username=(getattr(request.state,"user",{}) or {}).get("username")
    old=str(payload.get("old_password") or ""); new=str(payload.get("new_password") or "")
    user=get_user(username)
    if not user or not verify_password(old,user["password_hash"]): raise HTTPException(status_code=400,detail="Current password is incorrect.")
    if len(new)<8: raise HTTPException(status_code=400,detail="New password must be at least 8 characters.")
    update_user_password(username,hash_password(new)); return {"ok":True}

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return page("dashboard.html")

@app.get("/upload", response_class=HTMLResponse)
def upload_page():
    return page("upload.html")

@app.get("/library", response_class=HTMLResponse)
def library_page():
    return page("library.html")

@app.get("/channels", response_class=HTMLResponse)
def channels_page():
    return page("channels.html")

@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    return page("settings.html")

@app.get("/connections", response_class=HTMLResponse)
def connections_page():
    return page("connections.html")

@app.get("/scheduler", response_class=HTMLResponse)
def scheduler_page():
    return page("scheduler.html")

@app.get("/logs", response_class=HTMLResponse)
def logs_page():
    return page("logs.html")

@app.get("/jobs", response_class=HTMLResponse)
def jobs_page():
    return page("jobs.html")

@app.get("/analytics", response_class=HTMLResponse)
def analytics_page():
    return page("analytics.html")

@app.get("/api/youtube/accounts")
def api_youtube_accounts(request: Request):
    require_role(request,"admin","editor")
    accounts=[]
    for a in list_youtube_accounts():
        aa=dict(a); aa["channels"]=list_channels_for_account(a["id"]); accounts.append(aa)
    return {"accounts":accounts}

@app.post("/api/youtube/accounts/{account_id}/test")
async def api_test_youtube_account(account_id: int, request: Request):
    require_role(request,"admin")
    account=get_youtube_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="YouTube account not found.")
    if account.get("status") == "logged_out":
        raise HTTPException(status_code=400, detail="This YouTube account is logged out. Reconnect it first, then test again.")
    try:
        result=await asyncio.to_thread(test_youtube_account, account_id)
        return {"ok":True,"account_name":account["account_name"],**result}
    except Exception as e:
        logging.exception("YouTube account connection test failed: account=%s", account_id)
        update_youtube_account_status(account_id, "error")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/youtube/accounts/{account_id}/logout")
async def api_logout_youtube_account(account_id: int, request: Request):
    require_role(request,"admin")
    account=get_youtube_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="YouTube account not found.")
    try:
        result=await asyncio.to_thread(logout_youtube_account, account_id)
        return {"ok":True,"account_name":account["account_name"],**result}
    except Exception as e:
        logging.exception("YouTube account logout failed: account=%s", account_id)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/youtube/accounts/client-secret/upload")
async def api_upload_client_secret(payload: dict, request: Request):
    require_role(request,"admin")
    name = str(payload.get("filename") or "client_secret.json").strip()
    content = str(payload.get("content_base64") or "")
    if not content:
        raise HTTPException(status_code=400, detail="No client_secret.json content received.")
    if not name.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files are allowed.")
    try:
        raw = base64.b64decode(content, validate=True)
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="The selected file is not valid UTF-8 JSON.")
    if not isinstance(data, dict) or not (data.get("installed") or data.get("web")):
        raise HTTPException(status_code=400, detail="This does not look like a Google OAuth client_secret.json file.")
    from .config import CREDENTIALS_DIR
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "client_secret.json"
    if not safe.lower().endswith(".json"):
        safe += ".json"
    path = CREDENTIALS_DIR / safe
    # Avoid accidentally overwriting a different OAuth client with the same filename.
    if path.exists():
        try:
            if json.loads(path.read_text(encoding="utf-8")) != data:
                path = CREDENTIALS_DIR / f"{path.stem}_{int(time.time())}{path.suffix}"
        except Exception:
            path = CREDENTIALS_DIR / f"{path.stem}_{int(time.time())}{path.suffix}"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try: path.chmod(0o600)
    except Exception: pass
    return {"ok": True, "path": str(path.resolve()), "filename": path.name}

@app.post("/api/connections/gemini/client-secret/upload")
async def api_upload_gemini_client_secret(payload: dict, request: Request):
    return await api_upload_client_secret(payload, request)

@app.get("/api/channels")
def api_channels():
    return {"channels": list_channels()}

@app.post("/api/backup")
def api_backup(request: Request):
    require_role(request,"admin")
    from .config import DB_PATH
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    out=Path(tempfile.gettempdir()) / f"yla_backup_{stamp}.db"
    src=__import__('sqlite3').connect(DB_PATH); dst=__import__('sqlite3').connect(out); src.backup(dst); dst.close(); src.close()
    audit(request,"backup_database","database",str(out))
    return FileResponse(out, filename=out.name, media_type="application/octet-stream")

@app.get("/api/audit")
def api_audit(request: Request, limit: int = 300):
    require_role(request,"admin")
    return {"items":list_audit_log(min(max(limit,1),1000))}

@app.get("/api/jobs")
def api_jobs(request: Request, status: str | None = None, limit: int = 200):
    return {"jobs":list_upload_jobs(status, min(max(limit,1),500))}

@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: int, request: Request):
    require_role(request,"admin","editor")
    job=get_upload_job(job_id)
    if not job: raise HTTPException(status_code=404,detail="Job not found.")
    if job["status"] not in {"failed","retry_pending","cancelled"}: raise HTTPException(status_code=400,detail="Only failed/retryable jobs can be retried.")
    update_upload_job(job_id,status="pending",last_error="")
    audit(request,"retry_upload_job","upload_job",job_id)
    return {"ok":True,"job":get_upload_job(job_id)}

@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: int, request: Request):
    require_role(request,"admin","editor")
    job=get_upload_job(job_id)
    if not job: raise HTTPException(status_code=404,detail="Job not found.")
    update_upload_job(job_id,status="cancelled")
    audit(request,"cancel_upload_job","upload_job",job_id)
    return {"ok":True,"job":get_upload_job(job_id)}

@app.get("/api/health")
def api_health():
    from .config import DB_PATH, FFMPEG_EXE
    import shutil
    db_check=database_health()
    ffmpeg=shutil.which(FFMPEG_EXE)
    scheduler_running = bool(__import__("app.scheduler", fromlist=["_thread"])._thread and __import__("app.scheduler", fromlist=["_thread"])._thread.is_alive())
    checks={
        "database": db_check.get("ok",False) and DB_PATH.exists(),
        "ffmpeg": bool(ffmpeg),
        "scheduler": scheduler_running,
        "ai_provider": True,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "pid": os.getpid(),
        "python": os.sys.version.split()[0],
        "database": str(DB_PATH),
        "database_exists": DB_PATH.exists(),
        "ffmpeg": ffmpeg or "not_found",
        "ai_provider": "gemini",
        "scheduler": "running" if scheduler_running else "stopped",
        "schema_version": db_check.get("schema_version"),
        "database_check": db_check,
    }


@app.get("/api/dashboard")
def api_dashboard(channel_id: str | None = None):
    channels=list_channels()
    if channel_id:
        channels=[c for c in channels if c["channel_id"]==channel_id]
    remote=list_remote_videos(channel_id, 5000)
    enriched=[]
    for r in remote:
        local=get_inventory_by_youtube_id(r["channel_id"],r["youtube_video_id"])
        rr=dict(r)
        rr.update({"local_id":local["id"] if local else None,"local_file_path":local["file_path"] if local else "","local_filename":local["filename"] if local else ""})
        enriched.append(rr)
    return {"stats": stats(channel_id), "channels": channels, "remote_videos": enriched, "remote_count": len(enriched)}

@app.get("/api/youtube/sync")
@app.post("/api/youtube/sync")
async def api_youtube_sync(channel_id: str | None = None, account_id: int | None = None):
    # When an account is selected, sync only that account's channels.
    if account_id is not None:
        account = get_youtube_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="YouTube account not found.")
        channels = list_channels_for_account(account_id)
        if channel_id:
            channels=[c for c in channels if c["channel_id"]==channel_id]
    else:
        channels = list_channels()
        if channel_id:
            channels=[c for c in channels if c["channel_id"]==channel_id]
    if not channels:
        raise HTTPException(status_code=400, detail="No connected YouTube channel found.")
    totals={"channels":0,"remote_videos":0,"matched_by_hash":0,"matched_by_video_id":0,"remote_only":0}
    channel_results=[]
    for c in channels:
        cid=c["channel_id"]
        try:
            items=await asyncio.to_thread(list_channel_videos,cid)
            matched_hash=matched_id=0
            for item in items:
                vid=item.get("id","")
                if not vid: continue
                sn=item.get("snippet",{}); st=item.get("status",{}); marker=extract_hash_marker(item)
                statistics=item.get("statistics",{}) or {}
                upsert_remote_video(cid,vid,sn.get("title",""),sn.get("description",""),sn.get("publishedAt",""),st.get("privacyStatus",""),sn.get("tags",[]),marker,__import__("json").dumps(statistics),__import__("json").dumps(item.get("contentDetails",{})))
                # Keep lightweight metric snapshots so the app can show a local trend even when
                # the separate YouTube Analytics API is disabled.
                try:
                    add_remote_metrics_snapshot(cid, vid, statistics)
                except Exception:
                    logging.exception("Failed to save YouTube metrics snapshot for %s", vid)
                local=get_inventory_by_youtube_id(cid,vid)
                if local:
                    update_video(local["id"], status="uploaded", youtube_video_id=vid, title=sn.get("title",local.get("title","")), description=sn.get("description",local.get("description","")), privacy_status=st.get("privacyStatus",local.get("privacy_status","")), error_message="")
                    matched_id+=1
                elif marker:
                    local=get_inventory_by_hash(cid,marker)
                    if local:
                        update_video(local["id"], status="uploaded", youtube_video_id=vid, title=sn.get("title",local.get("title","")), description=sn.get("description",local.get("description","")), privacy_status=st.get("privacyStatus",local.get("privacy_status","")), error_message="")
                        matched_hash+=1
            remote_count=len(items)
            update_channel_sync(cid,"success","",remote_count)
            totals["channels"]+=1; totals["remote_videos"]+=remote_count; totals["matched_by_hash"]+=matched_hash; totals["matched_by_video_id"]+=matched_id
            totals["remote_only"] += max(0, remote_count-matched_hash-matched_id)
            channel_results.append({"channel_id":cid,"channel_name":c["channel_name"],"status":"success","remote_videos":remote_count,"matched_by_hash":matched_hash,"matched_by_video_id":matched_id})
            logging.info("YouTube sync complete: channel=%s remote_videos=%d matched_hash=%d matched_id=%d",cid,remote_count,matched_hash,matched_id)
        except Exception as exc:
            update_channel_sync(cid,"error",str(exc))
            logging.exception("YouTube sync failed for channel=%s",cid)
            channel_results.append({"channel_id":cid,"channel_name":c["channel_name"],"status":"error","error":str(exc)})
    if totals["channels"]==0:
        detail="; ".join(f'{x["channel_name"]}: {x.get("error","sync failed")}' for x in channel_results)
        raise HTTPException(status_code=400, detail=detail or "YouTube sync failed.")
    return {"ok":True,**totals,"channels":channel_results}

@app.get("/api/library")
def api_library(channel_id: str | None = None):
    if not channel_id:
        channels = list_channels()
        if not channels:
            return {"videos": []}
        channel_id = channels[0]["channel_id"]
    videos = get_inventory(channel_id)
    for v in videos:
        fp = v.get("file_path") or v.get("path") or ""
        v["path"] = fp
        v["file_path"] = fp
    return {"videos": videos}

@app.post("/api/connect")
async def api_connect(payload: dict, request: Request):
    require_role(request,"admin")
    account_name=str(payload.get("account_name") or "").strip()
    client_secret_path=str(payload.get("client_secret_path") or "").strip()
    if not account_name: raise HTTPException(status_code=400,detail="Account name is required.")
    if not client_secret_path or not Path(client_secret_path).exists(): raise HTTPException(status_code=400,detail="Select a valid client_secret.json file.")
    try:
        creds = await asyncio.to_thread(start_oauth, client_secret_path)
        items = discover_channels(creds)
        if not items: raise HTTPException(status_code=400, detail="No YouTube channel was returned for this Google account.")
        account_email = get_google_account_email(creds)
        app.state.pending_creds = creds
        app.state.pending_youtube_account = {
            "account_name": account_name,
            "account_email": account_email,
            "client_secret_path": str(Path(client_secret_path).resolve()),
        }
        candidates=[]
        for item in items:
            snippet=item.get("snippet",{})
            candidates.append({"channel_id":item["id"],"channel_name":snippet.get("title",""),"channel_handle":snippet.get("customUrl","") or ""})
        return {"channels":candidates,"account_name":account_name,"account_email":account_email}
    except HTTPException: raise
    except Exception as e:
        logging.exception("OAuth failed")
        raise HTTPException(status_code=400,detail=str(e))

@app.post("/api/connect/complete")
async def api_connect_complete(payload: dict, request: Request):
    require_role(request,"admin")
    channel_id=payload.get("channel_id"); channel_name=payload.get("channel_name",""); channel_handle=payload.get("channel_handle","")
    creds=getattr(app.state,"pending_creds",None); meta=getattr(app.state,"pending_youtube_account",None)
    if not creds or not meta: raise HTTPException(status_code=400,detail="No pending Google login. Click Connect again.")
    account=upsert_youtube_account(
        meta["account_name"],
        meta["client_secret_path"],
        meta.get("account_email", ""),
    )
    save_credentials(channel_id,creds)
    token_key=channel_id
    upsert_channel(
        channel_id,
        channel_name,
        channel_handle,
        meta.get("account_email", ""),
        token_key,
    )
    set_channel_account(channel_id,account["id"])
    app.state.pending_creds=None; app.state.pending_youtube_account=None
    return {"ok":True,"account":account}

@app.delete("/api/channels/{channel_id}")
def api_delete_channel(channel_id: str, request: Request):
    require_role(request,"admin")
    delete_credentials(channel_id)
    delete_channel(channel_id)
    return {"ok": True}


@app.get("/api/connections")
def api_connections(request: Request):
    require_role(request,"admin")
    return {"connections": list_ai_connections()}


class GeminiConnectRequest(BaseModel):
    account_name: str = ""
    client_secret_path: str

@app.post("/api/connections/gemini/connect")
async def api_connect_gemini(payload: GeminiConnectRequest, request: Request):
    require_role(request,"admin")
    try:
        path = Path(payload.client_secret_path).expanduser()
        if not path.is_absolute():
            path = (Path(__file__).resolve().parent.parent / path).resolve()
        if not path.exists():
            raise RuntimeError(f"Gemini client_secret.json not found: {path}")
        email, project_id, resolved_path = await asyncio.to_thread(connect_gemini_account, str(path))
        token_key = "gemini_token_" + "".join(c if c.isalnum() else "_" for c in email.lower())
        name = payload.account_name.strip() or email
        row = upsert_ai_connection("gemini", email, token_key, GEMINI_DEFAULT_MODEL, project_id, name, resolved_path)
        return {"ok": True, "connection": row}
    except Exception as e:
        logging.exception("Gemini OAuth failed")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/connections/gemini/logout/{connection_id}")
async def api_logout_gemini(connection_id: int, request: Request):
    require_role(request,"admin")
    try:
        return await asyncio.to_thread(logout_gemini_account, connection_id)
    except Exception as e:
        logging.exception("Gemini logout failed")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/connections/gemini/test/{connection_id}")
async def api_test_gemini(connection_id: int, request: Request):
    require_role(request,"admin")
    try:
        result = await asyncio.to_thread(test_gemini_connection, connection_id)
        return result
    except Exception as e:
        logging.exception("Gemini connection test failed")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/connections/{connection_id}")
def api_delete_connection(connection_id: int, request: Request):
    require_role(request,"admin")
    row = get_ai_connection(connection_id)
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if row.get("provider") == "gemini":
        delete_gemini_credentials(row)
    delete_ai_connection(connection_id)
    return {"ok": True}

@app.get("/api/quotes/fresh")
async def api_fresh_quotes():
    """Fetch fresh dynamic creator quotes & YouTube tips generated by Google Gemini AI."""
    try:
        quotes = await asyncio.to_thread(generate_creator_quotes, 6)
        return {"ok": True, "quotes": quotes}
    except Exception as e:
        log.warning("Fresh quotes endpoint error: %s", e)
        return {"ok": False, "quotes": []}

@app.get("/api/folder")
def api_get_folder(channel_id: str):
    if not get_channel(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    return {"folder": get_channel_folder(channel_id)}

@app.get("/api/folder/browse")
async def api_browse_folder(path: str | None = None, channel_id: str | None = None, request: Request = None):
    if request is not None:
        require_role(request,"admin","editor")
    if channel_id and not get_channel(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    from .config import BASE_DIR
    requested = Path(path).expanduser() if path else (Path(get_channel_folder(channel_id)) if channel_id and get_channel_folder(channel_id) else Path.home())
    try:
        current = requested.resolve()
    except Exception:
        current = Path.home().resolve()
    if not current.exists() or not current.is_dir():
        current = Path.home().resolve()
    entries=[]
    try:
        for p in sorted(current.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.is_dir() and not p.name.startswith('.'):
                try:
                    entries.append({"name":p.name,"path":str(p.resolve())})
                except Exception:
                    pass
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {current}")
    parent = str(current.parent) if current != current.parent else str(current)
    return {"current":str(current),"parent":parent,"entries":entries}

@app.post("/api/folder/select")
async def api_select_folder(payload: dict, request: Request):
    require_role(request,"admin","editor")
    channel_id=str(payload.get("channel_id") or "").strip()
    folder=str(payload.get("folder") or "").strip()
    if not channel_id or not get_channel(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    p=Path(folder).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Selected folder does not exist or is not a directory.")
    if not os.access(p, os.R_OK):
        raise HTTPException(status_code=403, detail="Selected folder is not readable by the uploader service.")
    set_channel_folder(channel_id, str(p))
    return {"ok":True,"folder":str(p)}

@app.post("/api/scan")
async def api_scan(payload: ScanRequest):
    if not get_channel(payload.channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    try:
        folder_path=Path(payload.folder).expanduser().resolve()
        if not folder_path.exists() or not folder_path.is_dir():
            raise HTTPException(status_code=400, detail="Video folder does not exist or is not a directory.")
        if not os.access(folder_path, os.R_OK):
            raise HTTPException(status_code=403, detail="Video folder is not readable.")
        set_channel_folder(payload.channel_id, str(folder_path))
        logging.info("Folder scan requested: channel=%s folder=%s", payload.channel_id, folder_path)
        files = await asyncio.to_thread(scan_folder, payload.folder)
        result = []
        hashed_new = 0
        reused = 0
        for f in files:
            # Fast path: unchanged file at the same path does not need to be re-hashed.
            existing_path = get_inventory_by_path(payload.channel_id, f["path"])
            if existing_path and int(existing_path.get("size_bytes", -1)) == int(f["size_bytes"]) and abs(float(existing_path.get("modified_at", -1)) - float(f["modified_at"])) < 1e-6:
                row = existing_path
                reused += 1
                result.append({
                    "id": row["id"], "filename": f["filename"], "path": f["path"], "file_path": f["path"],
                    "size_bytes": f["size_bytes"], "sha256": row["sha256"],
                    "status": row["status"], "youtube_video_id": row.get("youtube_video_id", ""),
                    "title": row.get("title", ""), "description": row.get("description", ""),
                    "hashtags": row.get("hashtags", ""), "tags": row.get("tags", ""),
                    "ai_status": row.get("ai_status", "not_analyzed"), "ai_summary": row.get("ai_summary", ""),
                    "analysis_json": row.get("analysis_json", "")
                })
                continue

            sha = await asyncio.to_thread(sha256_file, f["path"])
            hashed_new += 1
            existing_hash = get_inventory_by_hash(payload.channel_id, sha)
            status = existing_hash["status"] if existing_hash else "pending"
            row = upsert_video(payload.channel_id, f["path"], f["filename"], sha, f["size_bytes"], f["modified_at"], status)
            result.append({
                "id": row["id"], "filename": f["filename"], "path": f["path"], "file_path": f["path"],
                "size_bytes": f["size_bytes"], "sha256": sha, "status": status,
                "youtube_video_id": row.get("youtube_video_id", ""),
                "title": row.get("title", ""), "description": row.get("description", ""),
                "hashtags": row.get("hashtags", ""), "tags": row.get("tags", ""),
                "ai_status": row.get("ai_status", "not_analyzed"), "ai_summary": row.get("ai_summary", ""),
                "analysis_json": row.get("analysis_json", "")
            })
        logging.info("Folder scan complete: files=%d reused=%d hashed=%d channel=%s", len(files), reused, hashed_new, payload.channel_id)
        return {"folder": payload.folder, "videos": result, "reused": reused, "hashed": hashed_new, "incremental": True}
    except Exception as e:
        logging.exception("Folder scan failed")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/logs")
def api_logs(lines: int = 300):
    lines = max(1, min(int(lines), 2000))
    log_path = LOG_DIR / "app.log"
    if not log_path.exists():
        return {"path": str(log_path), "lines": []}
    data = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"path": str(log_path), "lines": data[-lines:]}

@app.get("/api/dashboard/details")
def api_dashboard_details(status: str | None = None, channel_id: str | None = None):
    allowed = {None, "pending", "uploaded", "failed"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status.")
    channels = list_channels()
    rows = []
    for c in channels:
        if channel_id and c["channel_id"] != channel_id:
            continue
        for v in get_inventory(c["channel_id"], limit=5000):
            if status and v["status"] != status:
                continue
            rows.append({"channel_name": c["channel_name"], "local_file": True, **v})
        # Uploaded card/details should also show remote uploads that were
        # discovered from YouTube but have no matching local inventory row.
        if status in (None, "uploaded"):
            for r in list_remote_videos(c["channel_id"],5000):
                if get_inventory_by_youtube_id(c["channel_id"],r["youtube_video_id"]):
                    continue
                rows.append({"channel_name":c["channel_name"],"channel_id":c["channel_id"],"id":None,
                             "filename":r["title"],"title":r["title"],"file_path":"Remote YouTube video",
                             "status":"uploaded","ai_status":"remote","youtube_video_id":r["youtube_video_id"],
                             "privacy_status":r["privacy_status"],"local_file":False})
    return {"videos": rows}

@app.get("/api/dashboard/upcoming")
def api_dashboard_upcoming():
    from datetime import datetime, timedelta
    now=datetime.now(); end=now+timedelta(days=1); items=[]
    schedules=list_schedules()
    # Predict assignment without mutating inventory: each slot consumes the next pending video.
    by_channel={}
    for row in schedules:
        by_channel.setdefault(row["channel_id"], sorted([v for v in get_inventory(row["channel_id"],limit=5000) if v["status"]=="pending"], key=lambda v:(v.get("modified_at",0),v.get("filename","").lower())))
    offsets={k:0 for k in by_channel}
    for row in schedules:
        if not row["enabled"]: continue
        target=int(row["weekday"]); days_ahead=(target-now.weekday())%7
        candidate=(now+timedelta(days=days_ahead)).replace(hour=int(row["time_local"][:2]),minute=int(row["time_local"][3:]),second=0,microsecond=0)
        if candidate<=now: candidate+=timedelta(days=7)
        if candidate<=end:
            pending=by_channel.get(row["channel_id"],[]); idx=offsets.get(row["channel_id"],0); video=pending[idx] if idx<len(pending) else None
            offsets[row["channel_id"]]=idx+1
            items.append({"schedule_id":row["id"],"channel_id":row["channel_id"],"channel_name":(get_channel(row["channel_id"]) or {}).get("channel_name",row["channel_id"]),"datetime":candidate.isoformat(timespec="minutes"),"time_local":row["time_local"],"weekday":target,"privacy_status":row["privacy_status"],"ai_provider":row.get("ai_provider","gemini"),"video":video["filename"] if video else "No pending video","video_id":video["id"] if video else None})
    items.sort(key=lambda x:x["datetime"])
    return {"upcoming":items}

@app.get("/api/performance/overview")
async def api_performance_overview(channel_id: str | None = None, account_id: int | None = None):
    # Performance is account-scoped. Never fall back to every connected account
    # when the UI has selected an account. This prevents one account's videos
    # from appearing after another account is selected.
    if account_id is not None:
        account = get_youtube_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="YouTube account not found.")
        channels = list_channels_for_account(account_id)
        if channel_id:
            channels = [c for c in channels if c["channel_id"] == channel_id]
    elif channel_id:
        channels = [c for c in list_channels() if c["channel_id"] == channel_id]
    else:
        channels = []
    result=[]
    for c in channels:
        remotes=list_remote_videos(c["channel_id"],limit=100)
        for r in remotes:
            try:
                a=await asyncio.to_thread(get_video_analytics,c["channel_id"],r["youtube_video_id"],28)
                summary=a.get("summary",{})
                err=""
            except Exception as exc:
                summary={}
            # The list page must show the same live basic numbers as YouTube.
            # Analytics API is optional; it must never zero-out Data API stats.
            try:
                fresh = await asyncio.to_thread(get_video_stats, c["channel_id"], r["youtube_video_id"])
                statistics = fresh.get("statistics", {})
                if not summary or all(summary.get(k) in (None, 0, "0") for k in ("views","likes","comments")):
                    summary = {
                        "views": int(statistics.get("viewCount") or 0),
                        "likes": int(statistics.get("likeCount") or 0),
                        "comments": int(statistics.get("commentCount") or 0),
                    }
            except Exception:
                statistics=__import__("json").loads(r.get("statistics_json") or "{}")
            local=get_inventory_by_youtube_id(c["channel_id"],r["youtube_video_id"])
            result.append({"channel_id":c["channel_id"],"channel_name":c["channel_name"],"video_id":local["id"] if local else None,"filename":local["filename"] if local else r["title"],"youtube_video_id":r["youtube_video_id"],"title":r["title"],"published_at":r["published_at"],"privacy_status":r["privacy_status"],"statistics":statistics,"summary":summary,"error":err,"remote_only":local is None})
    return {"videos":result,"count":len(result)}

@app.get("/api/remote/{channel_id}/{youtube_video_id}/analytics")
async def api_remote_video_analytics(channel_id: str, youtube_video_id: str, days: int = 28):
    if not get_channel(channel_id): raise HTTPException(status_code=404, detail="Channel not found.")
    remote=get_remote_video(channel_id,youtube_video_id)
    if not remote: raise HTTPException(status_code=404, detail="Video not found in synced YouTube index. Click Sync YouTube first.")
    try:
        data=await asyncio.to_thread(get_video_analytics,channel_id,youtube_video_id,days)
        return {"ok":True,"video":remote,"analytics":data}
    except Exception as exc:
        logging.exception("YouTube analytics failed for remote video %s",youtube_video_id)
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/ai/status")
def api_ai_status():
    conns=[c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
    return {"provider":"gemini","connected":bool(conns),"connections":len(conns),"model":GEMINI_DEFAULT_MODEL}

@app.post("/api/analyze")
async def api_analyze(payload: AnalyzeRequest, request: Request):
    audit(request,"analyze_video","video",payload.file_path or payload.path)
    channel = get_channel(payload.channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found.")
    path = validate_channel_path(payload.channel_id, payload.file_path)

    try:
        st = path.stat()
        row = get_inventory_by_path(payload.channel_id, str(path))
        if row and int(row.get("size_bytes", -1)) == int(st.st_size) and abs(float(row.get("modified_at", -1)) - float(st.st_mtime)) < 1e-6:
            logging.info("Analyze reused stored SHA-256: %s", path.name)
        else:
            sha = await asyncio.to_thread(sha256_file, str(path))
            existing_hash = get_inventory_by_hash(payload.channel_id, sha)
            row = existing_hash or upsert_video(
                payload.channel_id, str(path), path.name, sha,
                st.st_size, st.st_mtime, "pending"
            )
        update_video(row["id"], ai_status="analyzing", error_message="")
        if not payload.connection_id:
            conns=[c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
            if len(conns)==1: payload.connection_id=conns[0]["id"]
            else: raise RuntimeError("Select a connected Gemini account first. Open Connections.")
        result = await asyncio.to_thread(analyze_gemini, str(path), path.name, payload.connection_id, payload.model or None)
        md = result["metadata"]
        update_video(
            row["id"],
            title=md["title"],
            description=md["description"],
            hashtags=", ".join(md["hashtags"]),
            tags=", ".join(md["tags"]),
            ai_status="completed",
            ai_summary=md["summary"],
            transcript=result["transcript"],
            analysis_json=__import__("json").dumps(result, ensure_ascii=False),
            error_message=""
        )
        return {"ok": True, "video_id": row["id"], **result}
    except Exception as exc:
        logging.exception("AI analysis failed")
        if 'row' in locals():
            update_video(row["id"], ai_status="failed", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/account-automation")
def api_account_automation(account_id: int, channel_id: str | None = None):
    account=get_youtube_account(account_id)
    if not account: raise HTTPException(status_code=404,detail="YouTube account not found.")
    channels=list_channels_for_account(account_id)
    if channel_id and any(c["channel_id"]==channel_id for c in channels): selected=next(c for c in channels if c["channel_id"]==channel_id)
    else: selected=channels[0] if channels else None
    if not selected: return {"account":account,"channels":[],"selected_channel":None,"stats":{},"schedules":[]}
    return {"account":account,"channels":channels,"selected_channel":selected,"stats":stats(selected["channel_id"]),"schedules":list_schedules(selected["channel_id"])}

@app.get("/api/schedules")
def api_schedules(channel_id: str | None = None):
    return {"schedules": list_schedules(channel_id)}

@app.post("/api/schedules")
def api_save_schedule(payload: ScheduleRequest):
    if not get_channel(payload.channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    import re
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", payload.time_local):
        raise HTTPException(status_code=400, detail="Time must be HH:MM.")
    if payload.privacy_status not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="Invalid privacy status.")
    payload.tags=", ".join(str(x).strip().lstrip('#').lower() for x in str(payload.tags or '').split(',') if str(x).strip())
    if payload.ai_provider != "gemini":
        raise HTTPException(status_code=400, detail="Only Gemini AI is supported in this release.")
    if payload.ai_provider == "gemini":
        if not payload.ai_connection_id:
            raise HTTPException(status_code=400, detail="Select a Gemini connection for this schedule.")
        conn = get_ai_connection(payload.ai_connection_id)
        if not conn or conn.get("provider") != "gemini":
            raise HTTPException(status_code=400, detail="Selected Gemini connection was not found.")
    row = upsert_schedule(payload.channel_id, payload.weekday, payload.time_local, payload.enabled, payload.privacy_status, payload.category_id, payload.title_template, payload.description_template, payload.tags, payload.made_for_kids, payload.ai_provider, payload.ai_connection_id, payload.contains_synthetic_media, payload.video_language, payload.title_description_language, payload.caption_certification, payload.recording_date, payload.video_location)
    return {"schedule": row}

@app.post("/api/schedules/day")
def api_save_schedule_day(payload: ScheduleDayRequest):
    if not get_channel(payload.channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    import re
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", payload.start_time):
        raise HTTPException(status_code=400, detail="Start time must be HH:MM.")
    if payload.privacy_status not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="Invalid privacy status.")
    payload.tags=", ".join(str(x).strip().lstrip('#').lower() for x in str(payload.tags or '').split(',') if str(x).strip())
    if payload.ai_provider != "gemini":
        raise HTTPException(status_code=400, detail="Only Gemini AI is supported in this release.")
    if payload.ai_provider == "gemini":
        if not payload.ai_connection_id:
            raise HTTPException(status_code=400, detail="Select a Gemini connection for this schedule.")
        conn = get_ai_connection(payload.ai_connection_id)
        if not conn or conn.get("provider") != "gemini":
            raise HTTPException(status_code=400, detail="Selected Gemini connection was not found.")
    from datetime import datetime, timedelta
    base = datetime.strptime(payload.start_time, "%H:%M")
    slots=[]
    if (payload.videos_per_day - 1) * payload.interval_minutes >= 1440 - (base.hour * 60 + base.minute):
        raise HTTPException(status_code=400, detail="Generated schedule crosses midnight. Reduce videos/day or interval, or choose an earlier start time.")
    for i in range(payload.videos_per_day):
        t=(base+timedelta(minutes=i*payload.interval_minutes)).strftime("%H:%M")
        if t == payload.start_time and i>0: raise HTTPException(status_code=400, detail="Interval creates duplicate daily times. Increase interval.")
        slots.append({"time_local":t,"enabled":payload.enabled,"privacy_status":payload.privacy_status,"category_id":payload.category_id,"title_template":payload.title_template,"description_template":payload.description_template,"tags":payload.tags,"made_for_kids":payload.made_for_kids,"ai_provider":payload.ai_provider,"ai_connection_id":payload.ai_connection_id,
                   "contains_synthetic_media":payload.contains_synthetic_media,"video_language":payload.video_language,
                   "title_description_language":payload.title_description_language,"caption_certification":payload.caption_certification,
                   "recording_date":payload.recording_date,"video_location":payload.video_location})
    rows=replace_day_schedules(payload.channel_id,payload.weekday,slots) if payload.enabled else (delete_schedule(payload.channel_id,payload.weekday) or [])
    return {"schedules": rows if isinstance(rows,list) else []}

@app.post("/api/schedules/day/manual")
def api_save_schedule_day_manual(payload: ScheduleManualDayRequest):
    """Save exact, user-selected upload times for one weekday.

    Unlike the legacy /api/schedules/day endpoint, this endpoint never
    calculates times from an interval. Each time in payload.times becomes
    one independent upload slot, so users can choose arbitrary times such as
    09:00, 13:30 and 21:15 on the same day.
    """
    if not get_channel(payload.channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    import re
    if payload.privacy_status not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="Invalid privacy status.")
    payload.tags=", ".join(str(x).strip().lstrip('#').lower() for x in str(payload.tags or '').split(',') if str(x).strip())
    if payload.ai_provider != "gemini":
        raise HTTPException(status_code=400, detail="Only Gemini AI is supported in this release.")
    if payload.ai_provider == "gemini":
        if not payload.ai_connection_id:
            raise HTTPException(status_code=400, detail="Select a Gemini connection for this schedule.")
        conn = get_ai_connection(payload.ai_connection_id)
        if not conn or conn.get("provider") != "gemini":
            raise HTTPException(status_code=400, detail="Selected Gemini connection was not found.")

    times=[]
    for raw in payload.times:
        t=(raw or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t):
            raise HTTPException(status_code=400, detail=f"Invalid time: {t or '[empty]'}. Use HH:MM.")
        if t not in times:
            times.append(t)
    times.sort()

    if payload.enabled and not times:
        raise HTTPException(status_code=400, detail="Add at least one upload time or disable this day.")

    if not payload.enabled:
        delete_schedule(payload.channel_id, payload.weekday)
        return {"schedules": [], "videos_per_day": 0}

    slots=[{
        "time_local": t, "enabled": True,
        "privacy_status": payload.privacy_status,
        "category_id": payload.category_id,
        "title_template": payload.title_template or "{filename}",
        "description_template": payload.description_template,
        "tags": payload.tags, "made_for_kids": payload.made_for_kids,
        "ai_provider": payload.ai_provider, "ai_connection_id": payload.ai_connection_id,
        "contains_synthetic_media": payload.contains_synthetic_media, "video_language": payload.video_language,
        "title_description_language": payload.title_description_language, "caption_certification": payload.caption_certification,
        "recording_date": payload.recording_date, "video_location": payload.video_location
    } for t in times]
    rows=replace_day_schedules(payload.channel_id, payload.weekday, slots)
    log.info("Manual daily schedule saved: channel=%s weekday=%s slots=%s", payload.channel_id, payload.weekday, times)
    return {"schedules": rows, "videos_per_day": len(rows)}

@app.delete("/api/schedules/{channel_id}/{weekday}")
def api_delete_schedule(channel_id: str, weekday: int):
    delete_schedule(channel_id, weekday)
    return {"ok": True}

@app.get("/api/video/{video_id}/stream")
def api_video_stream(video_id: int):
    row=get_inventory_by_id(video_id)
    if not row: raise HTTPException(status_code=404, detail="Video not found in library.")
    path=validate_channel_path(row["channel_id"], row["file_path"])
    allowed={".mp4":"video/mp4",".webm":"video/webm",".m4v":"video/mp4",".mov":"video/quicktime",".mkv":"video/x-matroska",".avi":"video/x-msvideo"}
    return FileResponse(str(path), media_type=allowed.get(path.suffix.lower(),"application/octet-stream"), filename=path.name)

@app.get("/api/youtube/remote")
def api_youtube_remote(channel_id: str | None = None):
    return {"videos": list_remote_videos(channel_id, 5000)}

@app.get("/api/video/{video_id}/analytics")
async def api_video_analytics(video_id: int, days: int = 28):
    row=get_inventory_by_id(video_id)
    if not row: raise HTTPException(status_code=404, detail="Video not found.")
    if row.get("status") != "uploaded" or not row.get("youtube_video_id"):
        raise HTTPException(status_code=400, detail="This video has not been uploaded to YouTube yet.")
    try:
        data=await asyncio.to_thread(get_video_analytics,row["channel_id"],row["youtube_video_id"],days)
        return {"ok":True,"video":row,"analytics":data}
    except Exception as exc:
        logging.exception("YouTube analytics failed for video %s",video_id)
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/upload")
async def api_upload(payload: UploadRequest, request: Request):
    audit(request,"upload_video","video",payload.file_path or payload.path)
    channel = get_channel(payload.channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found.")

    path = validate_channel_path(payload.channel_id, payload.file_path)

    if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}:
        raise HTTPException(status_code=400, detail="Unsupported video format.")

    try:
        payload.hashtags=[str(x).strip().lstrip('#').lower() for x in payload.hashtags if str(x).strip()]
        payload.tags=[str(x).strip().lstrip('#').lower() for x in payload.tags if str(x).strip()]
        payload.description=compose_description(payload.description,payload.hashtags)
        validation = validate_metadata(payload.title, payload.description, payload.hashtags, payload.tags)
        if not validation["passed"]:
            raise HTTPException(status_code=400, detail="Metadata quality gate failed: " + "; ".join(validation["errors"]))
        st = path.stat()
        existing = get_inventory_by_path(payload.channel_id, str(path))
        unchanged = bool(existing and int(existing.get("size_bytes", -1)) == int(st.st_size) and abs(float(existing.get("modified_at", -1)) - float(st.st_mtime)) < 1e-6)
        if unchanged:
            sha = existing["sha256"]
        else:
            sha = await asyncio.to_thread(sha256_file, str(path))
            existing = get_inventory_by_hash(payload.channel_id, sha)

        remote = get_remote_video_by_hash(payload.channel_id, sha)
        if remote:
            if existing:
                update_video(existing["id"], status="uploaded", youtube_video_id=remote["youtube_video_id"], title=remote.get("title",existing.get("title","")), description=remote.get("description",existing.get("description","")), error_message="")
            return {"ok": False, "duplicate": True, "message": "This exact video was already uploaded to YouTube (matched by SHA-256 sync marker).", "youtube_video_id": remote["youtube_video_id"]}

        if existing and existing["status"] == "uploaded":
            return {
                "ok": False,
                "duplicate": True,
                "message": "This exact video is already uploaded to this channel.",
                "youtube_video_id": existing.get("youtube_video_id", ""),
            }

        row = upsert_video(
            payload.channel_id, str(path), path.name, sha,
            path.stat().st_size, path.stat().st_mtime, "uploading"
        )
        update_video(
            row["id"],
            title=payload.title,
            description=payload.description,
            hashtags=" ".join(payload.hashtags),
            tags=", ".join(payload.tags),
            privacy_status=payload.privacy_status,
            error_message=""
        )

        job_id=create_upload_job(row["id"], payload.channel_id, None, scheduled_at=datetime.now(timezone.utc).isoformat())
        update_upload_job(job_id,status="uploading",started_at=datetime.now(timezone.utc).isoformat(),attempt_count=1)
        attempt_started=datetime.now(timezone.utc).isoformat()
        result = await asyncio.to_thread(
            upload_video,
            payload.channel_id,
            str(path),
            payload.title,
            payload.description,
            payload.tags,
            payload.privacy_status,
            payload.category_id,
            payload.made_for_kids,
            max_retries=3,
            file_hash=sha,
            contains_synthetic_media=payload.contains_synthetic_media,
            video_language=payload.video_language,
            title_description_language=payload.title_description_language,
            recording_date=payload.recording_date,
        )

        video_id = result.get("id", "")
        update_video(row["id"], status="uploaded", youtube_video_id=video_id, error_message="")
        add_upload_attempt(job_id,1,"success",youtube_video_id=video_id,started_at=attempt_started,finished_at=datetime.now(timezone.utc).isoformat())
        update_upload_job(job_id,status="completed",completed_at=datetime.now(timezone.utc).isoformat(),youtube_video_id=video_id,last_error="")
        upsert_remote_video(payload.channel_id, video_id, payload.title, payload.description, "", payload.privacy_status, list(payload.tags) + ["yla_hash_" + sha], sha, "{}", "{}")
        return {"ok": True, "youtube_video_id": video_id}

    except Exception as e:
        logging.exception("Upload failed")
        if 'row' in locals():
            update_video(row["id"], status="failed", error_message=str(e))
        if 'job_id' in locals():
            add_upload_attempt(job_id,1,"failed",error_message=str(e),started_at=locals().get("attempt_started"),finished_at=datetime.now(timezone.utc).isoformat())
            update_upload_job(job_id,status="retry_pending",completed_at=datetime.now(timezone.utc).isoformat(),last_error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
