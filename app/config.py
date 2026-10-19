import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    """Load simple KEY=VALUE settings without adding a runtime dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value and value[0:1] == value[-1:] and value.startswith(("\"", "'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def get_app_env() -> str:
    """
    Returns the active environment: 'development' or 'production'.
    Checks os.environ (YLA_APP_ENV, APP_ENV, ENV), then database setting 'app_env'.
    Defaults to 'development'.
    """
    raw = os.getenv("YLA_APP_ENV", os.getenv("APP_ENV", os.getenv("ENV", ""))).strip().lower()
    if raw in ("prod", "production"):
        return "production"
    if raw in ("dev", "development", "local", "test", "testing"):
        return "development"

    try:
        from .database import get_app_setting
        db_env = (get_app_setting("app_env") or "").strip().lower()
        if db_env in ("prod", "production"):
            return "production"
        if db_env in ("dev", "development", "local", "test", "testing"):
            return "development"
    except Exception:
        pass

    return "development"


def is_dev_env() -> bool:
    """True if running in development / test / local environment."""
    return get_app_env() == "development"


def is_prod_env() -> bool:
    """True only if running in production."""
    return get_app_env() == "production"


APP_ENV = get_app_env()
APP_HOST = os.getenv("HOST", os.getenv("YLA_HOST", "0.0.0.0")).strip() or "0.0.0.0"
APP_PORT = int(os.getenv("PORT", os.getenv("YLA_PORT", "8080")))
PUBLIC_BASE_URL = os.getenv("YLA_PUBLIC_BASE_URL", os.getenv("APP_URL", "")).strip().rstrip("/")
bin_dir = BASE_DIR / "bin"
if bin_dir.exists() and str(bin_dir) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
CREDENTIALS_DIR = BASE_DIR / "credentials"
TOKEN_DIR = CREDENTIALS_DIR / "tokens"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"

for d in (WEB_DIR, DATA_DIR, LOG_DIR, CREDENTIALS_DIR, TOKEN_DIR, THUMBNAILS_DIR):
    d.mkdir(parents=True, exist_ok=True)

def get_client_secret_path():
    candidates = [
        CREDENTIALS_DIR / "client_secret.json",
        CREDENTIALS_DIR / "client_secret.json.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    cred_matches = list(CREDENTIALS_DIR.glob("client_secret*.json"))
    if cred_matches:
        return cred_matches[0]
    root_matches = list(BASE_DIR.glob("client_secret*.json"))
    if root_matches:
        return root_matches[0]
    return CREDENTIALS_DIR / "client_secret.json"

def get_db_path() -> Path:
    env_path = os.getenv("YLA_DB_PATH", os.getenv("DB_PATH", "")).strip()
    if env_path:
        p = Path(env_path)
        target = p if p.is_absolute() else (BASE_DIR / p)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return target
    # Default: outside from the folder (at project root)
    root_db = BASE_DIR / "uploader.db"
    if not root_db.exists():
        legacy_db = DATA_DIR / "uploader.db"
        if legacy_db.exists():
            try:
                import shutil
                shutil.copy2(str(legacy_db), str(root_db))
            except Exception:
                return legacy_db
    return root_db

DB_PATH = get_db_path()
CLIENT_SECRET_PATH = get_client_secret_path()

KEYRING_SERVICE = "YouTubeLocalAutoUploader"
TOKEN_PREFIX = "youtube_token_"
GEMINI_TOKEN_PREFIX = "gemini_token_"
GEMINI_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/generative-language.retriever",
    "https://www.googleapis.com/auth/cloud-platform",
]
GEMINI_DEFAULT_MODEL = os.getenv("YLA_GEMINI_MODEL", "gemini-flash-latest").strip() or "gemini-flash-latest"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Meta Graph API version used by the Facebook/Instagram connection flow.
# Keep this overridable so deployments can upgrade independently.
META_GRAPH_VERSION = os.getenv("YLA_META_GRAPH_VERSION", "v23.0").strip() or "v23.0"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000

# AI configuration: cloud Gemini only. Local LLM/Ollama support has been removed.
FFMPEG_EXE = "ffmpeg"
FFPROBE_EXE = os.getenv("YLA_FFPROBE_EXE", "ffprobe")
GEMINI_ANALYSIS_TIMEOUT = int(os.getenv("YLA_GEMINI_ANALYSIS_TIMEOUT", "300"))
MAX_TRANSCRIPT_CHARS = 12000
