import base64, hashlib, hmac, os, secrets, time
from .database import count_users, create_user, get_user, touch_user_login, update_user_password

SESSION_COOKIE = "yla_session"

def _load_session_secret():
    """Load a stable per-user secret without depending on a desktop session.

    The legacy data/.session_secret file is migrated when readable.  The
    canonical location is now ~/.config/youtube-local-auto-uploader/session_secret,
    which works for both interactive execution and systemd services running as
    the same user.
    """
    from pathlib import Path

    configured = os.environ.get("YLA_SESSION_SECRET", "").strip()
    if configured:
        return configured

    config_dir = Path.home() / ".config" / "youtube-local-auto-uploader"
    user_secret = config_dir / "session_secret"
    legacy_secret = Path(__file__).resolve().parent.parent / "data" / ".session_secret"

    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        config_dir.chmod(0o700)
    except Exception:
        pass

    if user_secret.exists():
        try:
            value = user_secret.read_text(encoding="utf-8").strip()
            if value:
                try: user_secret.chmod(0o600)
                except Exception: pass
                return value
        except (OSError, UnicodeError):
            pass

    # Preserve existing installations when the old secret is readable.
    try:
        if legacy_secret.exists():
            value = legacy_secret.read_text(encoding="utf-8").strip()
            if value:
                try:
                    user_secret.write_text(value, encoding="utf-8")
                    user_secret.chmod(0o600)
                except Exception:
                    pass
                return value
    except (OSError, UnicodeError):
        pass

    value = secrets.token_urlsafe(48)
    try:
        user_secret.write_text(value, encoding="utf-8")
        user_secret.chmod(0o600)
    except Exception:
        # Last-resort in-process secret. This is only reached if the user's
        # home/config directory is not writable; normal installs persist it.
        pass
    return value

SESSION_SECRET = _load_session_secret()

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 180000)
    return f"pbkdf2_sha256$180000${salt}${dk.hex()}"

def verify_password(password, encoded):
    try:
        algo, rounds, salt, digest = encoded.split("$", 3)
        if algo != "pbkdf2_sha256": return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(got, digest)
    except Exception:
        return False

def ensure_admin():
    """Create an admin only when an explicit password is configured.

    Otherwise first-run setup is completed through the local web UI.
    """
    if count_users():
        return False
    username=os.environ.get("YLA_ADMIN_USER", "admin").strip() or "admin"
    password=os.environ.get("YLA_ADMIN_PASSWORD", "")
    if len(password) < 12:
        return False
    create_user(username, hash_password(password), "admin")
    return True

def setup_available():
    return count_users() == 0

def create_first_admin(username, password):
    if count_users():
        raise ValueError("Administrator setup has already been completed.")
    username = (username or "").strip()
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if len(password or "") < 12:
        raise ValueError("Password must be at least 12 characters.")
    return create_user(username, hash_password(password), "admin")

def make_session(username):
    payload=f"{username}|{int(time.time())}"
    sig=hmac.new(SESSION_SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()

def read_session(value):
    if not value: return None
    try:
        raw=base64.urlsafe_b64decode(value.encode()).decode()
        username,ts,sig=raw.rsplit("|",2)
        payload=f"{username}|{ts}"
        expected=hmac.new(SESSION_SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected): return None
        if int(time.time())-int(ts) > 7*24*3600: return None
        return get_user(username)
    except Exception:
        return None
