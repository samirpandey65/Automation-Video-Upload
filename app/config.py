import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
bin_dir = BASE_DIR / "bin"
if bin_dir.exists() and str(bin_dir) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
CREDENTIALS_DIR = BASE_DIR / "credentials"
TOKEN_DIR = CREDENTIALS_DIR / "tokens"

for d in (WEB_DIR, DATA_DIR, LOG_DIR, CREDENTIALS_DIR, TOKEN_DIR):
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

DB_PATH = DATA_DIR / "uploader.db"
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
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Meta Graph API version used by the Facebook/Instagram connection flow.
# Keep this overridable so deployments can upgrade independently.
META_GRAPH_VERSION = os.getenv("YLA_META_GRAPH_VERSION", "v23.0").strip() or "v23.0"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000

# AI configuration: cloud Gemini only. Local LLM/Ollama support has been removed.
FFMPEG_EXE = "ffmpeg"
GEMINI_ANALYSIS_TIMEOUT = int(os.getenv("YLA_GEMINI_ANALYSIS_TIMEOUT", "300"))
MAX_TRANSCRIPT_CHARS = 12000
