"""
ChatGPT (chatgpt.com) Browser Automation & Interactive Login Assistant
----------------------------------------------------------------------
Allows creator to log in to ChatGPT using their own username & password directly
in a dedicated browser window. Uses a persistent profile (data/chatgpt_browser_profile)
with CDP port 9223 so sessions stay active without ever storing passwords.
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional

from .config import BASE_DIR
from .ai_multiprovider import build_analysis_prompt, parse_llm_json, sanitize_ai_result, extract_video_keyframes

log = logging.getLogger(__name__)

CHATGPT_USER_DATA_DIR = BASE_DIR / "data" / "chatgpt_browser_profile"
CHATGPT_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
CHATGPT_CDP_PORT = 9223


def find_browser_executable() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome") or "",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("msedge") or "",
    ]
    return next((p for p in candidates if p and Path(p).exists()), "")


def open_chatgpt_interactive_login() -> Dict[str, Any]:
    """
    Launches browser window directly to https://chatgpt.com so creator can log in
    with their credentials without being blocked by Chrome process singleton locks.
    """
    browser_exe = find_browser_executable()
    opened = False
    
    # Try launching standalone browser window first
    if browser_exe:
        try:
            # Use --new-window to open an active interactive window
            cmd = [browser_exe, "--new-window", "https://chatgpt.com"]
            log.info("[CHATGPT_AUTOMATION] Launching browser window for user login: %s", " ".join(cmd))
            subprocess.Popen(cmd)
            opened = True
        except Exception as e:
            log.warning("[CHATGPT_AUTOMATION] Subprocess launch failed: %s, falling back to OS handler", e)

    if not opened:
        try:
            import os
            os.startfile("https://chatgpt.com")
            opened = True
        except Exception:
            import webbrowser
            webbrowser.open("https://chatgpt.com", new=1)
            opened = True

    return {
        "ok": True,
        "message": "ChatGPT browser window opened! Please log in with your email/username and password in the opened window. Once logged in, your session is ready for AI analysis."
    }


def check_chatgpt_session_status() -> Dict[str, Any]:
    """
    Checks whether the local browser profile directory exists and has session files.
    """
    has_profile = False
    if CHATGPT_USER_DATA_DIR.exists():
        # Check if Default folder or cookies exist
        has_profile = any(CHATGPT_USER_DATA_DIR.iterdir())

    cdp_active = False
    try:
        import requests
        r = requests.get(f"http://localhost:{CHATGPT_CDP_PORT}/json/version", timeout=1.5)
        if r.ok:
            cdp_active = True
    except Exception:
        cdp_active = False

    return {
        "ok": True,
        "has_profile": has_profile,
        "cdp_active": cdp_active,
        "profile_dir": str(CHATGPT_USER_DATA_DIR),
        "status_text": "Live Browser Active (CDP 9223)" if cdp_active else ("Profile Saved" if has_profile else "Not Logged In")
    }


def automated_chatgpt_browser_analysis(video_path: str, filename: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Uses the authenticated ChatGPT browser session to analyze the video and generate YouTube metadata.
    Connects to the live browser via CDP or launches the persistent profile headless=False.
    """
    from playwright.sync_api import sync_playwright

    log.info("[CHATGPT_AUTOMATION] Starting browser-based ChatGPT video analysis for: %s", filename)
    prompt = build_analysis_prompt(filename)

    with sync_playwright() as p:
        browser = None
        context = None
        is_cdp = False

        # 1. Connect to live Chrome on port 9223
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{CHATGPT_CDP_PORT}", timeout=3000)
            if browser and browser.contexts:
                context = browser.contexts[0]
                is_cdp = True
                log.info("[CHATGPT_AUTOMATION] Connected directly to active ChatGPT browser via CDP!")
        except Exception:
            is_cdp = False

        # 2. If no CDP session running, launch persistent context with saved profile
        if not context:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(CHATGPT_USER_DATA_DIR),
                    channel="chrome",
                    headless=False,
                    args=[f"--remote-debugging-port={CHATGPT_CDP_PORT}"]
                )
                log.info("[CHATGPT_AUTOMATION] Launched persistent ChatGPT browser context.")
            except Exception as e:
                log.warning("[CHATGPT_AUTOMATION] Failed to launch persistent context: %s", e)
                raise RuntimeError(
                    f"Could not connect to ChatGPT browser session. Please click 'Open ChatGPT Window' to launch and log in first. (Error: {e})"
                )

        # 3. Find or create ChatGPT page
        page = None
        for pg in context.pages:
            if "chatgpt.com" in pg.url or "openai.com" in pg.url:
                page = pg
                break
        if not page:
            page = context.new_page()
            page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=30000)

        page.bring_to_front()
        time.sleep(2)

        # 4. Check if prompt textarea is present (indicates logged in)
        textarea_sel = "#prompt-textarea, textarea[placeholder*='Message'], div[contenteditable='true']"
        try:
            page.wait_for_selector(textarea_sel, timeout=8000)
        except Exception:
            # Check if login button is visible
            login_btn = page.query_selector("button:has-text('Log in'), a:has-text('Log in')")
            if login_btn:
                raise RuntimeError(
                    "You are not logged in to ChatGPT. Please click 'Open ChatGPT Window' and log in with your credentials."
                )

        # 5. Type prompt into textarea
        log.info("[CHATGPT_AUTOMATION] Inputting prompt into ChatGPT prompt area...")
        prompt_box = page.locator(textarea_sel).first
        prompt_box.click()
        prompt_box.fill(prompt)
        time.sleep(1)

        # 6. Click send / submit
        send_btn = page.locator("button[data-testid='send-button'], button[aria-label*='Send'], button:has-text('Send')").first
        if send_btn.is_visible():
            send_btn.click()
        else:
            page.keyboard.press("Enter")

        log.info("[CHATGPT_AUTOMATION] Submitted prompt to ChatGPT. Waiting for completion...")
        time.sleep(4)

        # 7. Wait for streaming to finish (stop button vanishes)
        for _ in range(60):
            stop_btn = page.locator("button[data-testid='stop-button'], button[aria-label*='Stop']")
            if not stop_btn.is_visible():
                break
            time.sleep(1)

        time.sleep(2)

        # 8. Extract the latest assistant message
        articles = page.locator("article, div[data-message-author-role='assistant']")
        count = articles.count()
        if count == 0:
            # Fallback to general markdown container
            responses = page.locator(".markdown").all_inner_texts()
            reply_text = responses[-1] if responses else ""
        else:
            reply_text = articles.nth(count - 1).inner_text()

        if not reply_text:
            raise RuntimeError("ChatGPT did not return a response. Please check your browser window.")

        log.info("[CHATGPT_AUTOMATION] Received response from ChatGPT (length=%d)", len(reply_text))
        parsed = parse_llm_json(reply_text)
        out = sanitize_ai_result(parsed, filename)
        out["ai_provider"] = "chatgpt_browser"
        out["vision_model"] = "ChatGPT Web"
        return out
