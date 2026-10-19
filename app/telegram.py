"""Telegram notification helper.

Sends messages to a Telegram chat via the Bot API.
Config (bot_token + chat_id) is stored in the app_settings DB table.
All functions are fire-and-forget — failures are logged but never raise.
"""
import logging
import requests
import time

log = logging.getLogger(__name__)

_last_token_warning: dict[str, float] = {}

def _should_notify(account_name: str, days_remaining: float) -> bool:
    """Return True if a warning should be sent.

    * **Only** send a warning when the token expires in **2 days or less**.
    * Throttle to at most one notification per account per 24 h.
    """
    # If more than 2 days remain, stay silent.
    if days_remaining > 2:
        return False
    now = time.time()
    last = _last_token_warning.get(account_name, 0)
    # Ensure we do not spam the same account within a day.
    if now - last < 24 * 3600:
        return False
    _last_token_warning[account_name] = now
    return True
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _get_config():
    from .database import get_app_setting
    token = get_app_setting("telegram_bot_token") or ""
    chat_id = get_app_setting("telegram_chat_id") or ""
    enabled = get_app_setting("telegram_enabled") or "0"
    return token.strip(), chat_id.strip(), enabled.strip() == "1"


def get_app_url(path: str = "") -> str:
    """Retrieve normalized application base URL with optional path."""
    from .database import get_app_setting
    from .config import PUBLIC_BASE_URL, APP_PORT
    raw = (get_app_setting("app_url") or get_app_setting("telegram_app_url") or PUBLIC_BASE_URL or "").strip()
    if not raw:
        raw = f"http://localhost:{APP_PORT}"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"http://{raw}"
    base = raw.rstrip("/")
    if path:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}"
    return base


def send(message: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message. Returns True on success."""
    token, chat_id, enabled = _get_config()
    if not enabled or not token or not chat_id:
        return False
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": message, "parse_mode": parse_mode},
            timeout=10,
        )
        data = r.json()
        if not r.ok or not data.get("ok"):
            log.warning("Telegram send failed: %s", data.get("description", r.text[:200]))
            return False
        return True
    except Exception as exc:
        log.warning("Telegram send error: %s", exc)
        return False


def test_connection(token: str, chat_id: str, app_url: str = "") -> dict:
    """Test a bot token + chat_id pair. Raises RuntimeError on failure."""
    token, chat_id = token.strip(), chat_id.strip()
    if not token or not chat_id:
        raise RuntimeError("Bot token and Chat ID are both required.")
    url = (app_url.strip() if app_url else "") or get_app_url()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"http://{url}"
    url = url.rstrip("/")
    msg = (
        "✅ <b>OmniCast Multi-Platform Publisher</b>\n"
        "Telegram notifications connected successfully!\n\n"
        f"🌐 <b>App Dashboard:</b> {url}"
    )
    r = requests.post(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )
    data = r.json()
    if not r.ok or not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram API error {r.status_code}")
    return {"ok": True, "message_id": data.get("result", {}).get("message_id")}


# ── Notification helpers ──────────────────────────────────────────────────────

def notify_youtube_upload_success(channel_name: str, title: str, youtube_video_id: str, filename: str):
    app_link = get_app_url("/library")
    send(
        f"✅ <b>YouTube Upload Complete</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"🎬 Title: {title}\n"
        f"📁 File: {filename}\n"
        f"🔗 YouTube: https://youtu.be/{youtube_video_id}\n"
        f"🌐 App Library: {app_link}"
    )

def notify_youtube_upload_failed(channel_name: str, filename: str, error: str):
    app_link = get_app_url("/jobs")
    send(
        f"❌ <b>YouTube Upload Failed</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"📁 File: {filename}\n"
        f"⚠️ Error: {error[:300]}\n"
        f"🌐 Queue: {app_link}"
    )

def notify_meta_publish_success(destination: str, page_name: str, title: str, filename: str, meta_id: str):
    icons = {"facebook_reel": "📘 Facebook Reel", "facebook_video": "📘 Facebook Video", "instagram_reel": "📸 Instagram Reel"}
    label = icons.get(destination, destination)
    app_link = get_app_url("/library")
    send(
        f"✅ <b>{label} Published</b>\n"
        f"📄 Page: {page_name}\n"
        f"🎬 Title: {title}\n"
        f"📁 File: {filename}\n"
        f"🆔 Meta ID: {meta_id}\n"
        f"🌐 App Library: {app_link}"
    )

def notify_meta_publish_failed(destination: str, page_name: str, filename: str, error: str):
    icons = {"facebook_reel": "📘 Facebook Reel", "facebook_video": "📘 Facebook Video", "instagram_reel": "📸 Instagram Reel"}
    label = icons.get(destination, destination)
    app_link = get_app_url("/jobs")
    send(
        f"❌ <b>{label} Failed</b>\n"
        f"📄 Page: {page_name}\n"
        f"📁 File: {filename}\n"
        f"⚠️ Error: {error[:300]}\n"
        f"🌐 Queue: {app_link}"
    )

def notify_scheduler_upload_success(channel_name: str, title: str, youtube_video_id: str, filename: str):
    app_link = get_app_url("/library")
    send(
        f"🤖 <b>Scheduled Upload Complete</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"🎬 Title: {title}\n"
        f"📁 File: {filename}\n"
        f"🔗 YouTube: https://youtu.be/{youtube_video_id}\n"
        f"🌐 App Library: {app_link}"
    )

def notify_scheduler_upload_failed(channel_name: str, filename: str, error: str):
    app_link = get_app_url("/jobs")
    send(
        f"🤖❌ <b>Scheduled Upload Failed</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"📁 File: {filename}\n"
        f"⚠️ Error: {error[:300]}\n"
        f"🌐 Queue: {app_link}"
    )

def notify_token_expiring(account_name: str, days_remaining: float, service: str = "YouTube"):
    if not _should_notify(account_name, days_remaining):
        return
    days = max(0, int(days_remaining))
    app_link = get_app_url("/connections")
    send(
        f"⚠️ <b>{service} Token Expiring Soon</b>\n"
        f"👤 Account: {account_name}\n"
        f"⏳ Expires in ~{days} day{'s' if days != 1 else ''}\n"
        f"🔑 Reconnect in App: {app_link}"
    )

def notify_token_expired(account_name: str):
    app_link = get_app_url("/connections")
    send(
        f"🔴 <b>YouTube Token Expired</b>\n"
        f"👤 Account: {account_name}\n"
        f"🔑 Reconnect Immediately: {app_link}"
    )

def notify_meta_token_expiring(page_name: str):
    app_link = get_app_url("/connections")
    send(
        f"⚠️ <b>Meta Token Expiring</b>\n"
        f"📄 Page: {page_name}\n"
        f"🔑 Reconnect in App: {app_link}"
    )

def notify_video_optimized(
    channel_name: str,
    old_title: str,
    new_title: str,
    youtube_video_id: str,
    views_prior: int,
    days_live: float,
    cycle: int = 1,
    thumbnail_variant: int = 1,
    pushed_to_youtube: bool = True
):
    """Notify when an underperforming video has been automatically audited and optimized with fresh metadata/thumbnail."""
    app_link = get_app_url("/library")
    yt_url = f"https://youtu.be/{youtube_video_id}" if youtube_video_id else ""
    push_badge = "✅ Pushed to YouTube" if pushed_to_youtube else "⚠️ Saved locally"
    send(
        f"⚡ <b>Video Optimizer Pivot Applied</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"🎬 <b>Old Title:</b> {old_title}\n"
        f"🚀 <b>New Title:</b> {new_title}\n"
        f"📊 Views at Audit: {views_prior:,} (Live {days_live:.1f} days)\n"
        f"🔄 A/B Cycle: #{cycle} · Thumbnail: Variant #{thumbnail_variant}\n"
        f"📡 Sync: {push_badge}\n"
        + (f"🔗 YouTube: {yt_url}\n" if yt_url else "")
        + f"🌐 App Library: {app_link}"
    )


def notify_bulk_optimization_completed(
    channel_name: str,
    total_scanned: int,
    total_optimized: int,
    sample_titles: list = None
):
    """Notify when bulk old video optimization and boilerplate cleanup has finished."""
    app_link = get_app_url("/analytics")
    lines = [
        "🧹 <b>Old Video Optimization & Cleanup Completed</b>",
        f"📺 <b>Scope:</b> {channel_name or 'All Channels'}",
        f"🔍 <b>Scanned:</b> {total_scanned} videos",
        f"✨ <b>Optimized:</b> {total_optimized} videos updated with clean hook titles",
    ]
    if sample_titles:
        lines.append("\n<b>Sample Title Improvements:</b>")
        for s in sample_titles[:3]:
            old_t = (s.get('old_title') or '')[:50]
            new_t = (s.get('new_title') or '')[:50]
            lines.append(f"• <s>{old_t}</s> ➡️ <b>{new_t}</b>")
    lines.append(f"\n🌐 View in Analytics: {app_link}")
    send("\n".join(lines))


def notify_community_post_ready(
    title: str,
    channel_name: str,
    channel_id: str,
    youtube_video_id: str,
    headline: str,
    body: str,
    poll_question: str,
    poll_options: list = None
) -> bool:
    """
    Notify when a community post and interactive poll is ready for YouTube.
    Provides direct link to YouTube Studio Community tab for 1-click posting.
    """
    studio_url = f"https://studio.youtube.com/channel/{channel_id}/community" if channel_id else "https://studio.youtube.com/"
    yt_url = f"https://youtu.be/{youtube_video_id}" if youtube_video_id else ""
    app_link = get_app_url("/library")

    opts = poll_options or []
    opts_lines = "\n".join(f"  [{i+1}] {opt}" for i, opt in enumerate(opts))

    msg = (
        "📢 <b>Community Post Ready for YouTube!</b>\n"
        f"📺 <b>Channel:</b> {channel_name}\n"
        f"🎬 <b>Video:</b> {title}\n\n"
        f"<b>📋 Post Announcement Copy:</b>\n"
        f"{headline}\n\n"
        f"{body}\n\n"
        f"<b>📊 Interactive Poll:</b>\n"
        f"❓ {poll_question}\n"
        f"{opts_lines}\n\n"
        f"👉 <b>Open Studio Community Tab:</b>\n"
        f"{studio_url}\n"
        + (f"▶️ <b>Watch Video:</b> {yt_url}\n" if yt_url else "")
        + f"🌐 <b>Library Dashboard:</b> {app_link}"
    )
    return send(msg)


send_telegram_sync = send

