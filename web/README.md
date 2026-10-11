# YouTube Local Auto Uploader

**Release: 3.2.0 (V3.1.1 Hotfix + V3.2 Cleanup)**

Local Windows creator workstation for YouTube inventory, duplicate-safe uploads, scheduling, Gemini video analysis, analytics, and multi-account/channel management.

## AI policy
- Gemini is the only supported AI provider; obsolete local Ollama/Qwen implementation has been removed.
- Gemini is the only AI provider.
- Connect one or more Gemini accounts from **Connections**.
- Metadata hashtags and tags are normalized to lowercase before validation/upload.

## Security
- First run uses the browser-based `/setup` page to create the local administrator. There is no default password. For unattended setup, set `YLA_ADMIN_USER` and `YLA_ADMIN_PASSWORD` (minimum 12 characters).
- Sessions use a per-install secret in `data/.session_secret` unless `YLA_SESSION_SECRET` is explicitly configured.
- Admin-only controls protect account connections, channel deletion, user management, backups, and audit logs.
- Editors can operate normal upload/scheduling workflows but cannot manage security-sensitive connections/users.

## Reliability
- SHA-256 duplicate protection and remote YouTube hash indexing.
- Incremental folder scanning based on path/size/mtime.
- Upload jobs and attempt history.
- Failed scheduled uploads remain retryable instead of being marked as completed.
- Scheduler run history and audit log.

## Backup
Use the admin **Backup Database** action/API. Do not distribute `data/uploader.db`, credentials, logs, or `.session_secret` with release packages.

## Installation
1. Install Python 3.11+ (or the version supported by your environment).
2. Install FFmpeg/FFprobe and ensure they are on PATH.
3. Run `pip install -r requirements.txt`.
4. Configure Google/YouTube OAuth and Gemini OAuth client files locally.
5. Start with `start.bat` or `python run.py`.

## Metadata convention
All hashtags/tags are lowercase. Example: `#shorts #animatedbaby #funnyshorts #hindishorts #chalaakkahaniya`.

## Release packaging
The release package excludes `.venv`, `__pycache__`, logs, credentials, session secrets, and the operational SQLite database.


## Production hardening
- Default bind address is `127.0.0.1` for local-only operation.
- Set `HOST` explicitly only when LAN access is required.
- Login attempts are rate-limited per client IP for the running process.
- `/api/health` reports database integrity, schema version, FFmpeg and scheduler state.
- Retryable upload jobs are processed by the background scheduler with a maximum of 3 attempts.
- Run `python -m unittest discover -s tests -v` before releasing a build.

## Recovery
If the application is restarted while a job is retryable, the persisted job remains in SQLite and the scheduler can resume it. Jobs that reach the retry limit become `failed` and require manual action.

## Local-only recommendation
Keep the default `HOST=127.0.0.1`. If you intentionally expose the app to another device on your LAN, set `HOST` to the machine's LAN address and use Windows Firewall rules to restrict access.


## Release package
The release ZIP intentionally excludes runtime data and machine-specific artifacts. On first start, the application creates `data/uploader.db` and `logs/app.log` locally. Do not copy credentials or session secrets into a release archive.

## Windows development -> Ubuntu deployment (same code)

The application is OS-portable. Keep machine-specific values in `.env`; do not edit Python files when moving between Windows and Ubuntu.

1. Copy `.env.example` to `.env`.
2. Windows: run `start.bat`.
3. Ubuntu: install `python3`, `python3-venv`, and `ffmpeg`, then run `chmod +x start.sh install_systemd.sh && ./start.sh` once.
4. Ubuntu service: `sudo ./install_systemd.sh`.
5. For Nginx/Apache, copy the example under `deploy/` and set `YLA_PUBLIC_BASE_URL=https://your-hostname` in `.env`.

Runtime files (`data/*.db`, OAuth credentials/tokens, `.env`, logs, `.venv`) are intentionally not source-controlled. Back them up separately when migrating an existing installation.
