"""
Unified credential-refresh agent.

* Reads a JSON "vault" that defines for each logical account
  – what kind of service (youtube|meta)
  – where the token files live
  – any extra parameters needed for the refresh flow
* Refreshes the token, persists it and optionally notifies
  the user via Telegram (only when <= 2 days remain).

The agent is deliberately lightweight – it does not depend
on any external secret-store libraries, but can be extended to
pull secrets from Vault, AWS Secrets Manager, etc.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import BASE_DIR, CREDENTIALS_DIR, CLIENT_SECRET_PATH
from .telegram import notify_token_expiring
from .meta import refresh_meta_token, load_app_secret

log = logging.getLogger(__name__)


def _days_until(expiry_ts: float | int) -> float:
    """Return days (fractional) until expiry_ts (unix epoch)."""
    return (float(expiry_ts) - time.time()) / 86400.0


def _resolve_path(raw_path: str | Path, base_dirs: tuple[Path, ...] = (CREDENTIALS_DIR, BASE_DIR)) -> Path:
    """Resolve a path whether absolute or relative to credentials/workspace dir."""
    p = Path(raw_path)
    if p.is_absolute():
        return p
    for b in base_dirs:
        cand = b / p
        if cand.exists():
            return cand
    # Fallback to credentials dir if not yet created or workspace root
    return CREDENTIALS_DIR / p


def _safe_chmod(path: Path, mode: int = 0o600) -> None:
    """Safely apply file mode without failing on platforms like Windows."""
    try:
        os.chmod(path, mode)
    except Exception as exc:
        log.debug("chmod %o on %s skipped or not supported: %s", mode, path, exc)


class CredentialVault:
    DEFAULT_PATH = CREDENTIALS_DIR / "vault.json"

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else self.DEFAULT_PATH
        if not self.path.is_file():
            # If default path doesn't exist, attempt auto-generation or raise
            if not self._try_create_default_vault():
                raise FileNotFoundError(f"Vault file not found: {self.path}")
        self._load()

    def _try_create_default_vault(self) -> bool:
        """Create a default vault.json auto-populated from discovered tokens if none exists."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            auto_accounts = self.discover_accounts()
            self.path.write_text(json.dumps(auto_accounts, indent=2), encoding="utf-8")
            _safe_chmod(self.path, 0o600)
            log.info("Initialized default credential vault at %s with %d accounts", self.path, len(auto_accounts))
            return True
        except Exception as exc:
            log.warning("Could not auto-create vault at %s: %s", self.path, exc)
            return False

    @staticmethod
    def discover_accounts() -> Dict[str, Dict[str, Any]]:
        """Discover existing tokens in the credentials directory and build vault config."""
        accounts: Dict[str, Dict[str, Any]] = {}

        # 1. YouTube tokens
        tokens_dir = CREDENTIALS_DIR / "tokens"
        if tokens_dir.is_dir():
            for p in sorted(tokens_dir.glob("youtube_token_*.json")):
                key = p.stem.replace("youtube_token_", "")
                accounts[f"youtube_{key}"] = {
                    "type": "youtube",
                    "token_path": str(p.relative_to(BASE_DIR) if p.is_relative_to(BASE_DIR) else p),
                    "client_secrets_path": str(CLIENT_SECRET_PATH.relative_to(BASE_DIR) if CLIENT_SECRET_PATH.exists() and CLIENT_SECRET_PATH.is_relative_to(BASE_DIR) else CLIENT_SECRET_PATH),
                    "scopes": [
                        "https://www.googleapis.com/auth/youtube.upload",
                        "https://www.googleapis.com/auth/youtube",
                    ],
                }

            # 2. Meta tokens
            for p in sorted(tokens_dir.glob("meta_page_*.json")):
                key = p.stem.replace("meta_page_", "")
                # Find matching app secret if any
                app_id = ""
                meta_app_files = list(CREDENTIALS_DIR.glob("meta_app_*.json"))
                if meta_app_files:
                    app_id = meta_app_files[0].stem.replace("meta_app_", "")

                accounts[f"meta_{key}"] = {
                    "type": "meta",
                    "app_id": app_id,
                    "token_path": str(p.relative_to(BASE_DIR) if p.is_relative_to(BASE_DIR) else p),
                }

        return accounts

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            self.accounts: Dict[str, Dict[str, Any]] = json.load(f)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def refresh_all(self) -> Dict[str, Any]:
        """Refresh every account defined in the vault. Returns status summary."""
        results = {}
        for name, cfg in self.accounts.items():
            try:
                service_type = cfg.get("type", "").lower()
                if service_type == "youtube":
                    res = self._refresh_youtube(name, cfg)
                elif service_type == "meta":
                    res = self._refresh_meta(name, cfg)
                else:
                    raise ValueError(f"Unsupported account type {service_type!r}")
                results[name] = {"status": "ok", "detail": res}
                log.info("[CredentialVault] Successfully refreshed %s (%s)", name, service_type)
            except Exception as exc:
                # Do not raise - allow the entire batch to continue.
                log.error("[CredentialVault] Failed to refresh %s: %s", name, exc)
                results[name] = {"status": "error", "error": str(exc)}
        return results

    # ------------------------------------------------------------------
    # YouTube Refresh Flow
    # ------------------------------------------------------------------
    def _refresh_youtube(self, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Refresh a YouTube OAuth token.

        Expected fields in cfg:
            - token_path (file containing the OAuth token JSON)
            - client_secrets_path (optional, Google client secret file)
            - scopes (list of oauth scopes)
        """
        token_path = _resolve_path(cfg["token_path"])
        if not token_path.is_file():
            raise FileNotFoundError(f"YouTube token file missing: {token_path}")

        with token_path.open("r", encoding="utf-8") as f:
            token_data = json.load(f)

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(f"YouTube token file lacks a refresh_token: {token_path}")

        # Resolve client_id and client_secret
        client_id = cfg.get("client_id") or token_data.get("client_id")
        client_secret = cfg.get("client_secret") or token_data.get("client_secret")

        cs_path = None
        if not (client_id and client_secret):
            if cfg.get("client_secrets_path"):
                cs_path = _resolve_path(cfg["client_secrets_path"])
            elif CLIENT_SECRET_PATH.exists():
                cs_path = CLIENT_SECRET_PATH

            if cs_path and cs_path.is_file():
                client_id = client_id or self._extract_client_id(cs_path)
                client_secret = client_secret or self._extract_client_secret(cs_path)

        # Use the token's granted scopes to avoid invalid_scope errors from Google
        scopes = token_data.get("scopes") or cfg.get("scopes")

        # Google OAuth 2 token refresh
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        current_token = token_data.get("access_token") or token_data.get("token", "")
        creds = Credentials(
            token=current_token,
            refresh_token=refresh_token,
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )

        request = Request()
        try:
            creds.refresh(request)
        except Exception as exc:
            if "invalid_scope" in str(exc).lower():
                log.warning("[CredentialVault] Retrying %s with scopes=None due to scope mismatch", name)
                creds = Credentials(
                    token=current_token,
                    refresh_token=refresh_token,
                    token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=None,
                )
                creds.refresh(request)
            else:
                raise

        # Persist the refreshed token back to disk (chmod 600)
        fresh = dict(token_data)
        fresh["token"] = creds.token
        fresh["access_token"] = creds.token
        fresh["refresh_token"] = creds.refresh_token
        fresh["client_id"] = client_id
        fresh["client_secret"] = client_secret
        fresh["token_uri"] = creds.token_uri or "https://oauth2.googleapis.com/token"
        fresh["scopes"] = scopes
        if creds.expiry:
            import datetime as _dt
            exp_dt = creds.expiry.replace(tzinfo=_dt.timezone.utc) if creds.expiry.tzinfo is None else creds.expiry
            fresh["token_expiry"] = int(exp_dt.timestamp())
        else:
            fresh["token_expiry"] = None

        token_path.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
        _safe_chmod(token_path, 0o600)

        # Notify via Telegram ONLY when reauth_due_at has <= 2 days remaining
        days = None
        reauth_due = cfg.get("reauth_due_at") or token_data.get("reauth_due_at")
        if not reauth_due:
            try:
                from .database import get_conn
                conn = get_conn()
                key = token_path.stem.replace("youtube_token_", "")
                row = conn.execute(
                    "SELECT a.reauth_due_at FROM youtube_channels c JOIN youtube_accounts a ON c.account_id = a.id WHERE c.token_key = ? OR c.channel_id = ?",
                    (key, key),
                ).fetchone()
                conn.close()
                if row and row["reauth_due_at"]:
                    reauth_due = row["reauth_due_at"]
            except Exception:
                pass

        if reauth_due:
            try:
                import datetime as _dt
                dt = _dt.datetime.fromisoformat(str(reauth_due).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                days = (dt - _dt.datetime.now(_dt.timezone.utc)).total_seconds() / 86400.0
                if days <= 2.0:
                    notify_token_expiring(name, days, service="YouTube")
            except Exception:
                pass

        return {
            "expires_in_days": round(days, 2) if days is not None else None,
            "token_path": str(token_path),
        }

    def _extract_client_id(self, cs_path: Path) -> str:
        with cs_path.open("r", encoding="utf-8") as f:
            secrets = json.load(f)
        key = "installed" if "installed" in secrets else "web"
        return secrets[key]["client_id"]

    def _extract_client_secret(self, cs_path: Path) -> str:
        with cs_path.open("r", encoding="utf-8") as f:
            secrets = json.load(f)
        key = "installed" if "installed" in secrets else "web"
        return secrets[key]["client_secret"]

    # ------------------------------------------------------------------
    # Meta Refresh Flow
    # ------------------------------------------------------------------
    def _refresh_meta(self, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Refresh a Meta long-lived token.

        Expected fields in cfg:
            - app_id, app_secret (or app_secret can be loaded automatically)
            - token_path (file containing the stored token JSON)
        """
        token_path = _resolve_path(cfg["token_path"])
        if not token_path.is_file():
            raise FileNotFoundError(f"Meta token file missing: {token_path}")

        with token_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        long_lived = payload.get("access_token")
        if not long_lived:
            raise RuntimeError(f"Meta token file does not contain access_token: {token_path}")

        app_id = cfg.get("app_id")
        app_secret = cfg.get("app_secret")
        if not app_secret and app_id:
            app_secret = load_app_secret(app_id)

        if not app_id or not app_secret:
            raise RuntimeError(f"Meta app_id and app_secret required for {name}")

        new_token, expires_in = refresh_meta_token(app_id, app_secret, long_lived)
        if not new_token:
            raise RuntimeError(f"Meta token refresh returned no token for {name}")

        fresh = dict(payload)
        fresh["access_token"] = new_token
        fresh["expires_in"] = expires_in
        fresh["refreshed_at"] = int(time.time())

        token_path.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
        _safe_chmod(token_path, 0o600)

        # Notify if <= 2 days remain
        days = None
        if expires_in:
            days = expires_in / 86400.0
            if days <= 2.0:
                notify_token_expiring(name, days, service="Meta")

        return {
            "expires_in_days": round(days, 2) if days is not None else None,
            "token_path": str(token_path),
        }


def refresh_all_credentials(vault_path: Path | str | None = None) -> Dict[str, Any]:
    """
    Entry-point used by the scheduler or a manual CLI command.

    Example usage:
        from app.credential_manager import refresh_all_credentials
        refresh_all_credentials()
    """
    try:
        vault = CredentialVault(vault_path)
        return vault.refresh_all()
    except Exception as exc:
        log.error("[CredentialVault] Execution failed: %s", exc)
        return {"error": str(exc)}
