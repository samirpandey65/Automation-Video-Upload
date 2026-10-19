# 📖 OmniCast Studio (Multi-Platform Publisher) — Comprehensive A to Z Feature Index & Future Roadmap

> **System Blueprint, Architecture & Feature Roadmap**  
> *Last Updated: October 2026 | Version: 3.3.0 (Multi-Platform Edition)*  
> *Format: Available in both Markdown ([FEATURES_ROADMAP.md](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/FEATURES_ROADMAP.md)) and Live Interactive HTML ([FEATURES_ROADMAP.html](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/FEATURES_ROADMAP.html) / [/features-roadmap](http://localhost:9000/features-roadmap))*

---

## 📊 Summary Status Dashboard

| Metric | Status / Count | Details |
| :--- | :--- | :--- |
| **System Identity** | **OmniCast Studio** | Multi-Platform Auto Publisher (YouTube, Facebook, Instagram) |
| **A to Z Implemented Subsystems** | **16 Core Subsystems** | Auth, Scheduler, Vault, Deduplication, Failure Recovery, etc. |
| **Database Architecture** | **Fully Decoupled** | Configurable via `YLA_DB_PATH` (defaults to root `uploader.db`) |
| **Total Tracked Videos** | **7,700+ Videos** | Full cryptographic SHA-256 fingerprinting & history |
| **Token Refresh Daemon** | **Active (< 2-Day Alerts)** | Automated Google & Meta refresh with silent 60-day cycles |
| **Auto-Recovery Agent** | **Active** | Periodically checks resolved blockers & auto-retries failed uploads |

---

## 📌 Table of Contents
1. [System Architecture & Database Decoupling](#1-system-architecture--database-decoupling)
2. [A to Z Implemented Features](#2-a-to-z-implemented-features)
   - [A — Authentication & Multi-Channel Account Management](#a--authentication--multi-channel-account-management)
   - [B — Background Scheduler & Multi-Slot Automation](#b--background-scheduler--multi-slot-automation)
   - [C — Credential Vault Agent & Automated Token Refresh](#c--credential-vault-agent--automated-token-refresh)
   - [D — Duplicate Protection & Cryptographic Indexing](#d--duplicate-protection--cryptographic-indexing)
   - [E — Error Tracking, Failure Diagnosis & Auto-Recovery](#e--error-tracking-failure-diagnosis--auto-recovery)
   - [F — File System Scanner & Incremental Discovery](#f--file-system-scanner--incremental-discovery)
   - [G — Genre & Category Playlist Auto-Assignment Engine](#g--genre--category-playlist-auto-assignment-engine)
   - [H — Health Monitoring, Backup & Hot Restore](#h--health-monitoring-backup--hot-restore)
   - [I — Inventory Management & Interactive Video Library](#i--inventory-management--interactive-video-library)
   - [M — Meta Multi-Platform Publishing (Facebook & Instagram)](#m--meta-multi-platform-publishing-facebook--instagram)
   - [O — Optimizer Engine & Automated Performance Audits](#o--optimizer-engine--automated-performance-audits)
   - [P — Platform & Brand Identity (OmniCast Studio)](#p--platform--brand-identity-omnicast-studio)
   - [Q — Quality Control & AI Compliance Reviewer](#q--quality-control--ai-compliance-reviewer)
   - [T — Telegram Notification & Throttled Alerting Bot](#t--telegram-notification--throttled-alerting-bot)
   - [U — Thumbnail Studio & Mobile Aspect-Ratio Canvas Engine](#u--thumbnail-studio--mobile-aspect-ratio-canvas-engine)
   - [Y — YouTube Data API v3 Resumable Upload Pipeline](#y--youtube-data-api-v3-resumable-upload-pipeline)
3. [Database Portability: Relocating `uploader.db` to Any Drive](#3-database-portability-relocating-uploaderdb-to-any-drive)
4. [Pending & Near-Term Enhancements](#4-pending--near-term-enhancements)
5. [Future Roadmap: Advanced Capabilities](#5-future-roadmap-advanced-capabilities)
6. [Developer Reference: CLI Tools & Key API Endpoints](#6-developer-reference-cli-tools--key-api-endpoints)

---

## 1. System Architecture & Database Decoupling

OmniCast Studio is built as a **local-first, privacy-respecting creator workstation** in Python (FastAPI backend + Vanilla JS/CSS Web UI) running on SQLite with zero external cloud dependencies.

```
                    ┌───────────────────────────────────────────────┐
                    │               Web UI & Dashboards             │
                    │   Dashboard · Library · Queue · Connections   │
                    └───────────────────────┬───────────────────────┘
                                            │ HTTP / JSON API
                    ┌───────────────────────▼───────────────────────┐
                    │            FastAPI Core (app/main.py)         │
                    └───────┬───────────────┬───────────────┬───────┘
                            │               │               │
            ┌───────────────▼──┐   ┌────────▼─────────┐   ┌─▼──────────────────┐
            │ Background Worker │   │ Credential Vault │   │ Failure Recovery   │
            │ (app/scheduler)  │   │ (app/credential) │   │ (failure_tracker)  │
            └───────┬──────────┘   └────────┬─────────┘   └───┬────────────────┘
                    │                       │                 │
    ┌───────────────┴───────────────────────┴─────────────────┴────────────────┐
    │                       SQLite Storage (uploader.db)                       │
    │   Configured via YLA_DB_PATH in .env (Supports any drive or external SSD)│
    └──────────────────────────────────────────────────────────────────────────┘
```

- **Dual-OS Portability**: Fully compatible across **Windows 11** and **Ubuntu Linux** with identical application code. OS-specific paths and variables are managed via `.env`.
- **Database Decoupling**: Database location is completely independent of the code tree. It defaults to `uploader.db` at the project root (outside `data/`) and can be relocated to any external drive or partition via `YLA_DB_PATH`.
- **RBAC Security**: Built-in User Role system (`admin`, `editor`, `viewer`) with bcrypt password hashing and tamper-proof cookie sessions.

---

## 2. A to Z Implemented Features

### A — Authentication & Multi-Channel Account Management
- **Multi-Account YouTube OAuth**: Connect multiple Google accounts (`youtube_accounts` table) and multiple channels (`youtube_channels` table) under distinct or shared client secrets.
- **Headless & Desktop Login**: Supports local interactive browser authentication (`InstalledAppFlow`) as well as desktop Keyring and headless token fallback.
- **Gemini AI OAuth Accounts**: Independent Google account connection manager for Gemini AI quotas (`ai_connections` table).
- **Meta Graph API Connections**: Connects Facebook Pages and linked Instagram Professional accounts with app ID and app secret verification (`meta_connections` table).
- **Zero Credential Storage in Git**: All token files reside in [`credentials/tokens/`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/credentials/tokens/) and are strictly ignored via `.gitignore`.
- **Files**: [`app/auth.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/auth.py), [`app/local_auth.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/local_auth.py), [`web/connections.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/connections.html).

### B — Background Scheduler & Multi-Slot Automation
- **Weekly Multi-Slot Scheduler**: Configure automated upload schedules per weekday (e.g., Monday 10:00, Monday 18:00, Friday 20:00) with independent channel mappings.
- **Durable Queue Processor**: Processes queued jobs sequentially, acquires non-blocking worker locks to avoid upload overlap, and checks YouTube hash markers before every upload.
- **Missed Window Protection**: 120-minute catch-up window logic skips stale runs if the computer was turned off, preventing burst backlogs.
- **Scheduled Meta Auto-Publishing**: Immediately triggers Facebook Video/Reel and Instagram Reel distribution upon successful YouTube upload completion.
- **Hourly Health Audit**: Runs hourly token expiry checks and triggers the automated Credential Vault refresh.
- **Files**: [`app/scheduler.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/scheduler.py), [`web/scheduler.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/scheduler.html).

### C — Credential Vault Agent & Automated Token Refresh
- **Unified JSON Vault**: Single configuration file [`credentials/vault.json`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/credentials/vault.json) indexing all channel tokens and client secret paths.
- **Google OAuth Refresh**: Refreshes short-lived Google access tokens via `google-auth` while strictly preserving granted scopes to avoid `invalid_scope` errors.
- **Meta Token Refresh**: Re-exchanges long-lived Meta page/user tokens via `fb_exchange_token` endpoint.
- **Restricted File Mode**: Automatically applies `chmod 0o600` on refreshed token files.
- **Telegram Notification Suppression ($\le 2$ Days)**: Tokens with $> 2$ days remaining refresh silently. Alerts are sent strictly when remaining time is $\le 2$ days, throttled to at most once per 24 hours.
- **CLI Utility**: Manual trigger via `python scripts/refresh_credentials.py`.
- **Files**: [`app/credential_manager.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/credential_manager.py), [`scripts/refresh_credentials.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/scripts/refresh_credentials.py).

### D — Duplicate Protection & Cryptographic Indexing
- **SHA-256 Fingerprinting**: Computes cryptographic content hashes for every video file.
- **Remote Tag Marker Indexing**: Embeds invisible `yla_hash_<sha256>` markers into uploaded YouTube video tags.
- **Sync & Remote Reconciliation**: Scans uploaded videos from YouTube Data API and populates `youtube_remote_videos`. If a local file has already been uploaded (even if database was reinstalled), the system detects the hash marker and prevents duplicate uploads.
- **Files**: [`app/video_utils.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/video_utils.py), [`app/youtube.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/youtube.py).

### E — Error Tracking, Failure Diagnosis & Auto-Recovery
- **Failure Taxonomy**: Classifies all upload errors into structured categories:
  - `TOKEN_EXPIRED`: OAuth token expired, revoked, or 401 Unauthorized.
  - `QUOTA_EXCEEDED`: YouTube 10,000 units/day limit or Gemini 429 rate limit.
  - `META_PERMISSION`: Missing `#200` permissions (`pages_read_engagement`, etc.).
  - `NETWORK_TRANSIENT`: Connection drops, timeouts, 502/503 gateway drops.
  - `AI_METADATA_ERROR`: LLM generation or parsing error.
  - `FILE_MISSING`: Video file or drive detached.
- **Dynamic Resolution Checker (`is_issue_resolved`)**: Automatically checks if the underlying issue has cleared (e.g. token refreshed, midnight PT quota window reset, network cool-off passed).
- **Background Auto-Recovery Reconciliation**: Scheduler periodically monitors failed jobs and automatically re-queues them (`status="retry_pending"`) once their blocking issue resolves.
- **Dedicated Web UI**: Located in [`web/jobs.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/jobs.html), featuring:
  - Real-time stat counters (*Total Failed*, *Ready to Auto-Retry*, *Action Required*, *Active Queue*).
  - Category filter dropdown.
  - One-click **"⚡ Auto-Retry All Fixed Issues"** button.
  - Per-item **"Re-check Issue"**, **"Retry Upload Now"**, and **"Cancel Job"** controls.
- **Files**: [`app/failure_tracker.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/failure_tracker.py), [`web/jobs.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/jobs.html).

### F — File System Scanner & Incremental Discovery
- **Incremental Scanning**: Compares file paths, size in bytes, and modification timestamps (`modified_at`). Only new or modified files are hashed with SHA-256, allowing large folders (thousands of videos) to scan in seconds.
- **Recursive Directory Support**: Scans channel video folders and registers items in `video_inventory`.
- **Files**: [`app/video_utils.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/video_utils.py).

### G — Genre & Category Playlist Auto-Assignment Engine
- **Multi-Signal Genre Classification**: Analyzes folder paths (e.g. `Fifa world cup edit`, `Viral-Desi-Food-Stories`, `ChalaakKahaniya`, `PC-Gaming-Videos`), video titles, tags, hashtags, and AI summaries.
- **Rich Genre Taxonomy**:
  - ⚽ **Football & Sports** (`Football & Sports`)
  - 🍲 **Food & Recipes** (`Food & Recipes`)
  - 📖 **Animated Stories & Kahaniya** (`Animated Stories & Kahaniya`)
  - 🎮 **Gaming** (`Gaming & Walkthroughs`)
  - 😂 **Comedy & Humor** (`Comedy & Fun`)
  - 🎬 **Movie & Story Recaps** (`Movie & Story Recaps`)
  - 👻 **Horror & Supernatural** (`Horror & Paranormal Stories`)
  - 💻 **Tech & Tutorials**, 🚀 **Motivation**, 🎵 **Music**, 🔍 **Crime & Mystery**, ✈️ **Travel**, 📰 **News**.
- **Smart Playlist Deduplication**: Matches existing channel playlists before creating new ones; uses clean, professional category titles.
- **Files**: [`app/playlist.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/playlist.py), [`app/youtube.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/youtube.py).

### H — Health Monitoring, Backup & Hot Restore
- **Live Health API (`/api/health`)**: Reports SQLite integrity, schema migration version, FFmpeg binary availability, database size, and scheduler thread heartbeat.
- **Hot Database Backup (`/api/backup`)**: Uses SQLite Online Backup API (`src.backup(dst)`) to stream a live, non-blocking `.db` backup archive without locking the database.
- **Safe Database Restore (`/api/restore`)**: Validates uploaded SQLite header, checks core schema tables, takes an automatic pre-restore backup, and hot-swaps the database.
- **Audit Logging**: Logs all administrative and upload actions in `audit_log` (`/api/audit`).
- **Files**: [`app/main.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/main.py), [`web/settings.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/settings.html).

### I — Inventory Management & Interactive Video Library
- **Centralized Library (`web/library.html`)**: Filter by channel, status (`pending`, `uploading`, `uploaded`, `failed`, `duplicate`), date range, or keyword search.
- **HTML5 In-Browser Video Player**: Preview local video files directly in the browser.
- **Metadata Editor**: Edit title, description, tags, hashtags, category ID, and privacy status.
- **Manual Actions**: One-click manual upload trigger, thumbnail re-generation, AI re-analysis, and playlist assignment.
- **Files**: [`web/library.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/library.html).

### M — Meta Multi-Platform Publishing (Facebook & Instagram)
- **Simultaneous Cross-Publishing**:
  - **YouTube**: Chunked resumable video upload with privacy status, tags, and category ID.
  - **Facebook Page Video**: Direct video upload with title and caption.
  - **Facebook Reel**: Chunked upload flow configured for reels.
  - **Instagram Reel**: Video container creation and state polling.
- **Deduplication Protection**: Tracks platform-specific IDs (`facebook_video_id`, `facebook_reel_id`, `instagram_media_id`).
- **Files**: [`app/meta.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/meta.py).

### O — Optimizer Engine & Automated Performance Audits
- **A/B Title Packaging**: Generates 3 psychological title variants (Curiosity Hook, Emotional/Dramatic, Question/Shock).
- **Scheduled Performance Audit**: Background worker audits 7–15 day performance.
- **Files**: [`app/optimizer.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/optimizer.py).

### P — Platform & Brand Identity (OmniCast Studio)
- **OmniCast Studio Identity**: Unified multi-platform branding reflecting YouTube, Facebook, and Instagram capabilities.
- **Brand Mark**: `⚡` paired with a vibrant multi-platform gradient (Indigo, Violet, and Pink) across all web UI templates.
- **Generalized Navigation**: Streamlined sidebar routing (`Upload Studio`, `Video Library`, `Channels & Accounts`, `Connections`, `Upload Queue`, `Meta Professional`).
- **Files**: [`web/layout.css`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/layout.css), [`web/*.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web), [`run.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/run.py), [`start.bat`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/start.bat).

### Q — Quality Control & Proactive Quota Tracker
- **Proactive Daily Quota Tracker**:
  - Live calculation of daily YouTube API quota consumption (10,000 units/day) with timezone-aware Pacific Time (PT) reset tracking.
  - Pre-upload safety gate (`can_perform_upload`): evaluates current daily usage + estimated upload cost (1,600 units) against safe ceiling (9,800 units) and automatically pauses the queue to prevent hard 403 `uploadLimitExceeded` lockouts.
  - Automatic Scheduler Pause & Telegram Alert: sends throttled notification and holds pending uploads until midnight Pacific Time.
  - Self-Healing Auto-Recovery: automatically resumes queue once midnight PT passes.
  - Live Web API & UI Progress Bar: `GET /api/quota/status` and live quota monitor in `web/jobs.html`.
- **Policy Compliance Checking**: Flags hate speech, violence, copyright indicators, and spam.
- **Structured QC Verdicts**: Generates review scores and detailed reports.
- **Automated PDF Reports**: Compiles compliance audit reports into PDF format via ReportLab (`reports/qc_report_video_*.pdf`).
- **Files**: [`app/quota_tracker.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/quota_tracker.py), [`app/qc_reviewer.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/qc_reviewer.py), [`web/jobs.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/jobs.html).

### T — Telegram Notification & Throttled Alerting Bot
- **Real-Time Dispatch**: Asynchronous notifications for YouTube uploads, Meta publications, and failure alerts.
- **Gated Expiry Reminders**: Strictly throttled notifications for tokens $\le 2$ days from expiration.
- **Files**: [`app/telegram.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/telegram.py).

### U — Thumbnail Studio & Mobile Aspect-Ratio Canvas Engine
- **Ambient Blurred Canvas**: Renders 16:9 canvas with blurred ambient background for 9:16 vertical Shorts, preventing grey boxes in YouTube Studio/CDN.
- **Thumbnail Variant Management**: Generates multiple variant thumbnails for manual review.
- **Files**: [`app/thumbnail.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/thumbnail.py).

### Y — YouTube Data API v3 Resumable Upload Pipeline
- **Resumable Chunked Protocol**: Uploads videos in chunks with exponential backoff on network drop.
- **Real-Time Progress Tracking**: Progress callbacks reporting percentage completion.
- **Comprehensive Metadata Support**: Configures titles, descriptions, tags, playlists, categories, and synthetic media disclosures.
- **Files**: [`app/youtube.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/youtube.py).

---

## 3. Database Portability: Relocating `uploader.db` to Any Drive

### **Can you move `uploader.db` to any path and set it in `.env`?**
**Yes, 100%!** The database location is fully dynamic and decoupled from the repository folder.

### **How to Move the Database:**
1. **Copy or move `uploader.db`** to your desired folder, external SSD, or data drive.  
   *Examples:*
   - `D:\YouTubeData\uploader.db`
   - `E:\Testing\uploader.db`
   - `/mnt/external_drive/uploader.db`

2. **Open `.env`** in the project root and set `YLA_DB_PATH`:
   ```env
   # Set to any absolute or relative path on your machine:
   YLA_DB_PATH=D:\YouTubeData\uploader.db
   ```
   *(Forward slashes or backslashes are both supported).*

3. **Restart the app** (or run `python run.py`).  
   - The application automatically verifies connectivity and will auto-create any missing parent folders.
   - If `YLA_DB_PATH` is left blank, the app defaults to `uploader.db` directly at the project root (outside `data/`).

---

## 4. Pending & Near-Term Enhancements

These are high-impact enhancements ready to be built on top of the current foundation:

| Feature | Description | Target Benefit | Status |
| **Batch Playlist Retrofit** | A one-click admin utility (`scripts/retrofit_playlists.py`) and UI modal to evaluate the ~7,700 videos in `video_inventory`, assign them to matching genre/series playlists, order multi-part series (Part 1 ➔ Part 2), and avoid duplicate playlists by strictly reusing channel playlists on YouTube. | Organizes channel history without manual work. | ✅ Implemented |
| **Proactive Quota Tracker** | Calculate daily quota consumption (~1,600 units/upload) and automatically pause queue before hitting the 10,000 unit hard wall. | Zero 403 `uploadLimitExceeded` rejections. | ✅ Implemented |
| **Smart Multi-Platform Cleaner & Archiver** | Multi-platform sync-verified cleaner where **deletion NEVER happens automatically**. Local files are verified across all targets (YouTube + Facebook + Instagram). Missing uploads are auto-scheduled at a paced **1–2 videos per day** (no mass-upload dumping), and eligible videos require explicit user approval in a dedicated library with account names and live links. | Zero accidental file loss, sustainable daily channel growth pacing, and total user deletion control. | ⏳ Planned |
| **Queue Drag-and-Drop Prioritization** | Visual drag-and-drop reordering of `upload_jobs` in `web/jobs.html`. | Immediate control over next-in-line video. | ⏳ Planned |

### Deep Dive: Smart Multi-Platform Cleaner & Archiver Specification
- **Zero Automatic / Silent Deletions (User Approval Required)**:
  - Deletion **NEVER executes automatically** in the background. Local files are never deleted without explicit manual confirmation.
  - When a video is confirmed live across all configured platforms, it enters an **"Eligible for Cleanup"** staging state.
  - The creator can review the staging list, inspect the live links, and click **"Confirm Delete / Move"** at their discretion.
- **Anti-Mass Upload Throttling (Strict Pacing of 1–2 Videos Per Day)**:
  - The system **strictly prohibits mass-upload dumping** of all queued or missing videos at once.
  - When auto-scheduling missing uploads to Facebook or Instagram (or queueing from local folders), the scheduler spaces them out to **1 to 2 videos per calendar day maximum**.
  - Videos are automatically assigned to peak audience viewing windows (e.g., Slot 1 at 14:00, Slot 2 at 19:30 local time), ensuring optimal audience retention, algorithmic favorability, and quota preservation.
- **Multi-Platform Verification Gate**: Before any video can even be marked eligible for cleanup, the background engine runs a sync audit querying:
  1. **YouTube Data API**: Confirms video ID exists, is processed, and status is `public` or `scheduled`.
  2. **Facebook Graph API**: Confirms video/reel ID exists on the target Facebook Page.
  3. **Instagram Graph API**: Confirms reel media container status is `FINISHED` on the linked Instagram Professional account.
- **Auto-Catchup Scheduling for Missing Destinations**:
  - If a video uploaded to YouTube but Facebook or Instagram publishing was missed, failed, or pending:
    - **Cleanup is strictly BLOCKED and LOCKED**.
    - The engine automatically queues paced catch-up jobs (1–2 per day) for the missing Meta account(s).
    - Only after the identical video is confirmed published across all target platforms is the cleanup action unlocked.
- **Dedicated "Deleted & Archived Videos" Library**:
  - Accessible via a dedicated tab in `web/library.html` or `/library?view=archived`.
  - Maintains complete immutable metadata for every cleaned video:
    - Original file name, video title, resolution, file size reclaimed, and cleanup timestamp.
    - **Published Platforms Manifest**: Shows account name, platform icon, and direct live URL (e.g. `https://youtu.be/...`, Facebook post URL, Instagram reel link).
    - **Pending Destinations Audit**: Displays any accounts still awaiting publication before cleanup is enabled.
  - User toggle between **Safe Soft-Archive** (move to secondary archive path) or **Permanent Deletion** (recycle bin or direct delete).

---

## 4.5. 🚀 YouTube Growth & Views Optimization Suite (Phase 10 / v3.4.0)

Engineered specifically to solve low impression and view velocity bottlenecks across all connected YouTube channels:

### 1. Hook-First Title Engine & Anti-Boilerplate Sanitizer ✅ [Implemented]
- **Complete Boilerplate Suffix Elimination**: Permanently removes repetitive, algorithm-penalizing static suffixes (e.g. `— The Moment Everything Changed! ⚡`, `— The Funniest Reaction You'll See! 🤣`, `— ऐसा मजेदार मोड़ किसी ने नहीं सोचा था! 🤣`).
- **30-Character Mobile Shorts Rule**: Guarantees that the central emotional hook, character, and action are positioned within the first 30 characters so mobile viewers see the full subject on YouTube Shorts feed before 35-character screen truncation.
- **Recursive Prefix Collapse Guard**: Automatically flattens and removes repetitive prefix stacking (e.g. `The Shocking Truth About The Shocking Truth About What ACTUALLY Happened?...` $\rightarrow$ `Cute Baby Feeds Gau Mata Roti! 🥺❤️`).
- **AI Prompt Leakage Shield**: Filters out raw prompt instructions (e.g. `Lead immediately with the emotional peak...`) with strict regex validation before saving or publishing.
- **Files**: [`app/thumbnail.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/thumbnail.py), [`app/gemini.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/gemini.py).

### 2. Old Video Bulk Optimization & Metadata Rescue Engine ✅ [Implemented]
- **Historical Channel Scan**: Scans all previously published YouTube videos across all connected accounts.
- **Boilerplate & Cliché Detection**: Automatically flags videos suffering from repetitive suffixes, stacked prefixes, or prompt leakage.
- **Live YouTube Metadata Update**: Generates fresh, high-CTR, hook-first titles and high-intent SEO tags, then updates live YouTube videos directly via YouTube Data API v3 (`videos().update`).
- **One-Click Bulk Action**: UI in library and analytics allows batch review and 1-click optimization of old videos with daytime window protection.
- **Files**: [`app/optimizer.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/optimizer.py), [`app/main.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/main.py), [`web/library.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/library.html).

### 3. Shorts High-Impact First-Frame Hook & Smart Thumbnail Integration ✅ [Implemented]
- **Mobile First-Frame Extraction**: Evaluates vertical Shorts (9:16) at 0.0s for luma and contrast to ensure they begin on a vibrant, expressive visual frame rather than a dark, blurry, or blank transition frame.
- **Bypasses Mobile Shorts Thumbnail Bug**: Since YouTube mobile app ignores custom uploaded thumbnails in the Shorts feed, verifying the hook frame at 0.0s guarantees high click-through rates.
- **Files**: [`app/video_utils.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/video_utils.py), [`app/thumbnail.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/thumbnail.py), [`app/main.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/main.py).

### 4. Dual-Mode Scheduler: Manual Slot Mode vs. Smart Auto Traffic Agent ✅ [Implemented]
- **Overview**: An enterprise-grade dual-mode publishing architecture that allows creators to switch seamlessly between fixed weekly calendar schedules and a fully autonomous, algorithmically-driven **Smart Auto Traffic Agent**.
- **1. Manual Schedule Slots**:
  - Creators define fixed weekday/time slots (e.g., Monday 10:00 AM, Friday 6:00 PM) per channel.
  - Useful for rigid programmatic releases or specific broadcast commitments.
- **2. Smart Auto Traffic Agent (Autonomous Audience-Driven Publishing)**:
  - **Empirical Viewer Traffic Heatmap & Scoring Matrix**:
    The agent dynamically measures audience availability across the Indian / South Asian subcontinent (IST/Local Time) using proven social media engagement heatmaps, assigning a real-time audience score:
    - 🔥 **Evening Peak (17:30 – 19:30 IST / 5:30 PM – 7:30 PM) · Score: 100/100**: Maximum daily traffic window across YouTube Shorts, Instagram Reels, and Facebook video feeds as workers, students, and commuters consume short-form video.
    - 🍱 **Lunch Break Peak (12:30 – 14:30 IST / 12:30 PM – 2:30 PM) · Score: 95/100**: Secondary daily high-engagement window driven by midday lunch breaks.
    - 🌙 **Night Prime Peak (20:30 – 22:30 IST / 8:30 PM – 10:30 PM) · Score: 90/100**: Post-dinner relaxation prime time with prolonged watch sessions.
    - 🌅 **Morning Peak (08:30 – 10:30 IST / 8:30 AM – 10:30 AM) · Score: 85/100**: Commute and wake-up browsing window.
    - ⚡ **Standard Daytime (08:00 – 21:00 IST outside peaks) · Score: 65/100**: Steady daytime traffic suitable for non-urgent uploads.
    - ⏸️ **Night Rest / Protection Window (22:30 – 08:00 IST) · Score: 0/100 (Deferred)**: Autonomous uploads are strictly paused during late-night hours to prevent publishing videos into a "dead zone" that damages initial 2-hour Click-Through-Rate (CTR) and algorithmic velocity.
  - **Triple-Lock Collision & Pacing Guardrails**:
    - **Daily Channel Upload Cap (1–2 Videos/Day, Max 2 per Channel)**: Strictly caps uploads to 1–2 videos per calendar day per channel (default: max 2). Once a channel publishes 2 videos in a day, it enters `Daily Cap Reached (2/2 today)` and is locked until the next morning's peak window, protecting algorithmic impressions and subscriber retention.
    - **Cross-Channel Global Collision Gap (45 Minutes)**: Enforces a strict minimum 45-minute pause between *any* two uploads across all connected channels. Prevents multi-account IP flooding, proxy rate limits, and YouTube API spam detection flags.
    - **Same-Channel Algorithmic Pacing Gap (4–5 Hours / 270 Minutes)**: Guarantees that a single channel never uploads more frequently than once every 4.5 hours (270 minutes), providing a minimum 4–5 hour gap between video 1 and video 2 to maximize individual video reach and recommendation carousel impressions.
  - **Channel Selection & Episode Continuity Engine**:
    - **Dynamic Channel Prioritization**: When multiple channels have pending videos, the agent sorts channels by longest idle duration since last upload (`minutes_since_last_upload DESC`), followed by pending queue depth (`pending_count DESC`).
    - **Continuous Story Sequencing**: Utilizes `select_next_continuity_video` to respect series episode numbers, filename integers, and chronological continuity rather than random selection.
  - **Live Web Radar & Operational Telemetry**:
    - Integrated real-time radar panel on `/scheduler` displaying active traffic window, minutes remaining in peak, global collision status, and individual channel cooldown progress bars.
    - **1-Click On-Demand Execution**: "Trigger Smart Auto Upload Now" allows instant manual evaluation and upload dispatch for immediate testing.
  - **Dynamic Multi-Horizon Dashboard Simulation**:
    - The `/api/dashboard/upcoming` endpoint simulates the Smart Traffic Agent's forward trajectory over 1h, 5h, 12h, 24h, 3d, 7d, 14d, and 30d horizons, displaying expected peak window tags and scores directly on the Dashboard.
  - **Environment-Aware Safety Guardrails**:
    - Automatically detects `YLA_APP_ENV=development` vs. `production`. In dev, all autonomous background loops pause to avoid unwanted uploads; in prod, full autonomous dispatch is active.
- **Files**: [`app/scheduler.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/scheduler.py), [`web/scheduler.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/scheduler.html), [`app/main.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/main.py), [`web/dashboard.html`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/web/dashboard.html).

### 5. Automated Initial Velocity & Cross-Platform Syndication Engine ✅ [Implemented]
- **First 15-Minute Velocity Trigger**: Immediately after YouTube upload completes, the smart agent:
  - Generates and publishes an engaging **YouTube Community Tab teaser/poll** to notify subscribers.
  - Cross-syndicates a teaser post to connected **Facebook Pages and Instagram accounts** with direct links to fuel initial 30–50 views.
- **Files**: [`app/scheduler.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/scheduler.py), [`app/community.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/community.py), [`app/cross_syndication.py`](file:///e:/Testing/YouTubeLocalAutoUploader/YouTubeLocalAutoUploader/app/cross_syndication.py).

---

## 5. Future Roadmap: Advanced Capabilities

Strategic long-term capabilities:

### 1. Additional Social Platforms
- **TikTok Publishing**: Direct upload using TikTok Content Posting API.
- **LinkedIn & Twitter/X Integration**: Expanding video distribution to business and micro-blogging audiences.

### 2. Automated AI Short-Form Clipper (`app/clipper.py`)
- Detect peak action, laughter, or dramatic dialogue in long videos (5–30 min).
- Automatically slice 30–60 second vertical clips (9:16) with burned-in animated subtitles for YouTube Shorts and Instagram Reels.

### 3. Multi-Platform Teasers & Community Announcements (YouTube, Facebook & Instagram)
- Schedule teaser keyframes, countdown graphics, polls, and premiere announcements across **YouTube Community Tab**, **Facebook Page Feed**, and **Instagram Feed/Stories** 2–3 hours before scheduled video uploads go live to maximize launch day audience turnout.

### 4. Multi-Platform Automated A/B Packaging Switcher (YouTube, Facebook & Instagram)
- **Live Performance Auditing**: Continuously monitors real performance metrics across connected platforms:
  - **YouTube**: Click-Through Rate (CTR) and initial 48h/7d views via YouTube Analytics API.
  - **Facebook & Instagram**: Hook retention (3s/1-minute view ratio) and engagement velocity via Meta Graph API Insights.
- **Complete Packaging Hot-Swap**: If Initial Package A performs below target thresholds (e.g. $< 4\%$ YouTube CTR or low Meta reach after 7 days), the optimizer automatically swaps the **complete presentation package** (not just the title):
  - **YouTube Packaging**: Updates Title variant (Curiosity/Shock vs. Direct), swaps Thumbnail image to High-Contrast Variant B or C, and refines top description hook lines.
  - **Facebook Packaging**: Hot-swaps post caption headline, primary thumbnail poster frame, and engagement CTA.
  - **Instagram Packaging**: Updates Reel caption hook, thumbnail cover frame, and top comment hashtags.
- **Rollback & Logging**: Retains audit logs of packaging swaps and automatically restores the best-performing variation if metrics do not improve.

### 5. Multi-Language Audio Dubbing & Subtitles
- Integrate ElevenLabs or Google Cloud TTS to auto-generate dubbed Hindi $\leftrightarrow$ English audio tracks and `.srt` subtitle files.

---

## 6. Developer Reference: CLI Tools & Key API Endpoints

### Command Line Shortcuts
```bash
# 1. Refresh all credentials in vault (YouTube & Meta)
python scripts/refresh_credentials.py

# 2. Start the web application
python run.py
# or Windows shortcut:
start.bat

# 3. Check failure reconciliation manually in Python
python -c "from app.failure_tracker import reconcile_failed_jobs; print(reconcile_failed_jobs())"

# 4. Check active database path
python -c "from app.config import DB_PATH; print('Active DB:', DB_PATH)"

# 5. Run automated unit tests
python -m unittest discover -s tests -v
```

### Core API Endpoints
- `GET /api/health` — Full system status, FFmpeg check, DB integrity.
- `GET /api/jobs/failed` — Comprehensive failure diagnostics and recovery states.
- `POST /api/jobs/retry-all-resolved` — Batch retry for all resolved jobs.
- `POST /api/jobs/{id}/diagnose` — On-demand failure diagnostics for a specific job.
- `POST /api/backup` — Hot non-blocking SQLite database backup stream.
- `POST /api/restore` — Validated live database restore.
- `GET /features-roadmap` (and `/FEATURES_ROADMAP.md`) — Interactive web view and markdown of this feature matrix and roadmap (Restricted to Admin/Creator only; hidden and forbidden for all other accounts).
