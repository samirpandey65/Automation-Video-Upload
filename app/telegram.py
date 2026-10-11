"""Telegram notification helper.

Sends messages to a Telegram chat via the Bot API.
Config (bot_token + chat_id) is stored in the app_settings DB table.
All functions are fire-and-forget — failures are logged but never raise.
"""
import logging
import requests

log = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _get_config():
    from .database import get_app_setting
    token = get_app_setting("telegram_bot_token") or ""
    chat_id = get_app_setting("telegram_chat_id") or ""
    enabled = get_app_setting("telegram_enabled") or "0"
    return token.strip(), chat_id.strip(), enabled.strip() == "1"


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


def test_connection(token: str, chat_id: str) -> dict:
    """Test a bot token + chat_id pair. Raises RuntimeError on failure."""
    token, chat_id = token.strip(), chat_id.strip()
    if not token or not chat_id:
        raise RuntimeError("Bot token and Chat ID are both required.")
    r = requests.post(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat_id, "text": "✅ <b>YouTube Local Auto Uploader</b>\nTelegram notifications connected successfully!", "parse_mode": "HTML"},
        timeout=10,
    )
    data = r.json()
    if not r.ok or not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram API error {r.status_code}")
    return {"ok": True, "message_id": data.get("result", {}).get("message_id")}


# ── Notification helpers ──────────────────────────────────────────────────────

def notify_youtube_upload_success(channel_name: str, title: str, youtube_video_id: str, filename: str):
    send(
        f"✅ <b>YouTube Upload Complete</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"🎬 Title: {title}\n"
        f"📁 File: {filename}\n"
        f"🔗 https://youtu.be/{youtube_video_id}"
    )

def notify_youtube_upload_failed(channel_name: str, filename: str, error: str):
    send(
        f"❌ <b>YouTube Upload Failed</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"📁 File: {filename}\n"
        f"⚠️ Error: {error[:300]}"
    )

def notify_meta_publish_success(destination: str, page_name: str, title: str, filename: str, meta_id: str):
    icons = {"facebook_reel": "📘 Facebook Reel", "facebook_video": "📘 Facebook Video", "instagram_reel": "📸 Instagram Reel"}
    label = icons.get(destination, destination)
    send(
        f"✅ <b>{label} Published</b>\n"
        f"📄 Page: {page_name}\n"
        f"🎬 Title: {title}\n"
        f"📁 File: {filename}\n"
        f"🆔 Meta ID: {meta_id}"
    )

def notify_meta_publish_failed(destination: str, page_name: str, filename: str, error: str):
    icons = {"facebook_reel": "📘 Facebook Reel", "facebook_video": "📘 Facebook Video", "instagram_reel": "📸 Instagram Reel"}
    label = icons.get(destination, destination)
    send(
        f"❌ <b>{label} Failed</b>\n"
        f"📄 Page: {page_name}\n"
        f"📁 File: {filename}\n"
        f"⚠️ Error: {error[:300]}"
    )

def notify_scheduler_upload_success(channel_name: str, title: str, youtube_video_id: str, filename: str):
    send(
        f"🤖 <b>Scheduled Upload Complete</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"🎬 Title: {title}\n"
        f"📁 File: {filename}\n"
        f"🔗 https://youtu.be/{youtube_video_id}"
    )

def notify_scheduler_upload_failed(channel_name: str, filename: str, error: str):
    send(
        f"🤖❌ <b>Scheduled Upload Failed</b>\n"
        f"📺 Channel: {channel_name}\n"
        f"📁 File: {filename}\n"
        f"⚠️ Error: {error[:300]}"
    )

def notify_token_expiring(account_name: str, days_remaining: float):
    days = int(days_remaining)
    send(
        f"⚠️ <b>YouTube Token Expiring Soon</b>\n"
        f"👤 Account: {account_name}\n"
        f"⏳ Expires in ~{days} day{'s' if days != 1 else ''}\n"
        f"🔑 Open the app → Connections to reconnect."
    )

def notify_token_expired(account_name: str):
    send(
        f"🔴 <b>YouTube Token Expired</b>\n"
        f"👤 Account: {account_name}\n"
        f"🔑 Open the app → Connections to reconnect immediately."
    )

def notify_meta_token_expiring(page_name: str):
    send(
        f"⚠️ <b>Meta Token Expiring</b>\n"
        f"📄 Page: {page_name}\n"
        f"🔑 Open the app → Connections to reconnect."
    )
