"""
YouTube Community Browser Automation Engine
------------------------------------------
Automates YouTube Community Tab announcements and interactive polls
using Playwright and Chrome with a persistent user profile (data/youtube_browser_profile).
Opens a visible browser window, navigates to the channel's Community tab,
and automatically fills in and publishes the announcement copy and interactive poll.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from .config import BASE_DIR

log = logging.getLogger(__name__)

YOUTUBE_USER_DATA_DIR = BASE_DIR / "data" / "youtube_browser_profile"
YOUTUBE_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_CDP_PORT = 9225


def _get_chrome_executable() -> str:
    """Find installed Google Chrome or Edge executable on the host system."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome") or "",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return str(Path(p).resolve())
    return ""


def open_youtube_community_window(channel_id: str = "") -> Dict[str, Any]:
    """
    Launch Chrome browser window with the persistent profile and remote debugging port.
    Opens directly to the channel's YouTube Community tab.
    """
    browser_exe = _get_chrome_executable()
    target_url = (
        f"https://www.youtube.com/channel/{channel_id}/community"
        if channel_id
        else "https://studio.youtube.com"
    )

    if browser_exe:
        cmd = [
            browser_exe,
            f"--remote-debugging-port={YOUTUBE_CDP_PORT}",
            f"--user-data-dir={str(YOUTUBE_USER_DATA_DIR.resolve())}",
            target_url,
        ]
        log.info("[YOUTUBE_BROWSER] Launching browser window: %s", " ".join(cmd))
        try:
            subprocess.Popen(cmd)
            return {
                "ok": True,
                "browser_launched": True,
                "url": target_url,
                "message": f"Browser window opened to YouTube Community Tab ({target_url})."
            }
        except Exception as e:
            log.warning("[YOUTUBE_BROWSER] Subprocess launch failed: %s", e)

    import webbrowser
    webbrowser.open(target_url)
    return {
        "ok": True,
        "browser_launched": True,
        "url": target_url,
        "message": f"Opened {target_url} in your default browser."
    }


def post_community_via_browser(
    channel_id: str,
    text: str,
    poll_question: Optional[str] = None,
    poll_options: Optional[List[str]] = None,
    headless: bool = False,
    timeout_seconds: int = 60
) -> Dict[str, Any]:
    """
    Automate posting to YouTube Channel Community Tab:
    1. Connects to existing live Chrome on port 9225 or launches persistent Chrome window.
    2. Navigates to https://www.youtube.com/channel/{channel_id}/community.
    3. Types the announcement text.
    4. Clicks the 'Poll' tool button, sets up poll options if provided.
    5. Clicks 'Post' button and confirms publication.
    """
    from playwright.sync_api import sync_playwright

    target_url = (
        f"https://www.youtube.com/channel/{channel_id}/community"
        if channel_id
        else "https://www.youtube.com"
    )

    clean_text = (text or "").strip()
    poll_opts = [str(o).strip() for o in (poll_options or []) if str(o).strip()]

    log.info(
        "[YOUTUBE_BROWSER] Starting automated community post for channel '%s' (options=%d)...",
        channel_id, len(poll_opts)
    )

    with sync_playwright() as p:
        browser = None
        context = None
        is_cdp = False

        # 1. Attempt connection to existing Chrome window over CDP
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{YOUTUBE_CDP_PORT}", timeout=3000)
            if browser and browser.contexts:
                context = browser.contexts[0]
                is_cdp = True
                log.info("[YOUTUBE_BROWSER] Connected directly to active Chrome browser on CDP port %d", YOUTUBE_CDP_PORT)
        except Exception:
            is_cdp = False

        # 2. If no CDP session running, launch persistent context using installed Chrome
        if not context:
            browser_exe = _get_chrome_executable()
            launch_args = [
                f"--remote-debugging-port={YOUTUBE_CDP_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(YOUTUBE_USER_DATA_DIR),
                    executable_path=browser_exe if browser_exe else None,
                    channel="chrome" if not browser_exe else None,
                    headless=headless,
                    args=launch_args,
                    viewport={"width": 1280, "height": 850}
                )
                log.info("[YOUTUBE_BROWSER] Launched persistent Chrome browser context (headless=%s)", headless)
            except Exception as e:
                log.error("[YOUTUBE_BROWSER] Failed to launch Chrome persistent context: %s", e)
                raise RuntimeError(
                    f"Could not launch browser window. Please check Chrome installation or launch manually. Details: {e}"
                )

        page = context.new_page()
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            page.wait_for_timeout(3000)

            # Check if user needs to log into Google
            current_url = page.url.lower()
            if "accounts.google.com" in current_url or "signin" in current_url:
                log.warning("[YOUTUBE_BROWSER] User is not signed in to YouTube in this browser profile.")
                return {
                    "ok": False,
                    "needs_login": True,
                    "message": "YouTube login required: A browser window has opened. Please log into your YouTube account once, then click 'Post to YouTube' again!",
                    "url": target_url
                }

            # Locate Community post box / placeholder
            box_selectors = [
                "#placeholder",
                "#contenteditable-root",
                "div[contenteditable='true']",
                "#create-post",
                "ytd-backstage-post-dialog-renderer [contenteditable='true']",
                "tp-yt-paper-textarea #textarea",
                "[aria-label*='post' i]",
                "[aria-label*='update' i]"
            ]

            post_box = None
            for sel in box_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2500):
                        post_box = el
                        log.info("[YOUTUBE_BROWSER] Found community post box using selector: %s", sel)
                        break
                except Exception:
                    continue

            if not post_box:
                # Try clicking any container with 'post an update'
                try:
                    fallback_click = page.locator("text='Give your audience a glimpse'").or_(
                        page.locator("text='Post an update'")
                    ).or_(
                        page.locator("text='What\'s on your mind'")
                    ).first
                    if fallback_click.is_visible(timeout=2500):
                        fallback_click.click()
                        page.wait_for_timeout(1000)
                        post_box = page.locator("#contenteditable-root, div[contenteditable='true']").first
                except Exception:
                    pass

            if not post_box:
                # Take screenshot for diagnostics
                diag_dir = BASE_DIR / "logs" / "community_browser"
                diag_dir.mkdir(parents=True, exist_ok=True)
                diag_path = diag_dir / f"fail_{int(time.time())}.png"
                try:
                    page.screenshot(path=str(diag_path))
                except Exception:
                    pass

                return {
                    "ok": False,
                    "needs_login": False,
                    "message": f"Community tab opened at {target_url}, but the post creation box could not be activated automatically. Please verify your channel has Community posts enabled or finish posting in the open window.",
                    "url": target_url,
                    "diagnostic_screenshot": str(diag_path)
                }

            # 1. Type the announcement text into the box
            post_box.click()
            page.wait_for_timeout(500)
            full_body_text = clean_text
            if poll_question and poll_question not in full_body_text:
                full_body_text = f"{clean_text}\n\n❓ {poll_question}"

            # Type text
            post_box.fill(full_body_text)
            page.wait_for_timeout(1000)

            # 2. Click the 'Poll' option button if options provided
            if poll_opts and len(poll_opts) >= 2:
                poll_btn_selectors = [
                    "button[aria-label*='poll' i]",
                    "yt-icon-button[aria-label*='poll' i]",
                    "button:has-text('Poll')",
                    "tp-yt-paper-button:has-text('Poll')",
                    "ytd-post-creation-dialog-renderer button:has-text('Poll')",
                    "[aria-label*='Add poll' i]"
                ]
                for psel in poll_btn_selectors:
                    try:
                        pbtn = page.locator(psel).first
                        if pbtn.is_visible(timeout=1500):
                            pbtn.click()
                            log.info("[YOUTUBE_BROWSER] Clicked Poll tool button: %s", psel)
                            page.wait_for_timeout(1200)
                            break
                    except Exception:
                        continue

                # Locate option inputs
                opt_input_selectors = [
                    "input[placeholder*='option' i]",
                    "input[aria-label*='Option' i]",
                    "tp-yt-paper-input input",
                    "input[placeholder*='Add option' i]"
                ]
                
                inputs = page.locator("input[placeholder*='option' i], input[aria-label*='Option' i], tp-yt-paper-input input")
                count = inputs.count()
                log.info("[YOUTUBE_BROWSER] Found %d poll option input fields initially", count)

                for i, opt in enumerate(poll_opts):
                    if i >= count:
                        # Click 'Add another option' button
                        add_btn = page.locator("button:has-text('Add option'), button:has-text('Add another option'), [aria-label*='another option' i]").first
                        if add_btn.is_visible(timeout=1500):
                            add_btn.click()
                            page.wait_for_timeout(800)
                            inputs = page.locator("input[placeholder*='option' i], input[aria-label*='Option' i], tp-yt-paper-input input")
                    
                    try:
                        inp = inputs.nth(i)
                        if inp.is_visible(timeout=1500):
                            inp.fill(opt)
                            page.wait_for_timeout(300)
                    except Exception as e:
                        log.warning("[YOUTUBE_BROWSER] Could not fill poll option %d ('%s'): %s", i+1, opt, e)

            page.wait_for_timeout(1000)

            # 3. Locate and click Post button
            post_submit_selectors = [
                "#post-button button",
                "#post-button",
                "button:has-text('Post')",
                "yt-button-shape:has-text('Post') button",
                "ytd-button-renderer:has-text('Post') button",
                "tp-yt-paper-button:has-text('Post')"
            ]

            post_btn = None
            for psub in post_submit_selectors:
                try:
                    btn = page.locator(psub).first
                    if btn.is_visible(timeout=2000) and btn.is_enabled(timeout=2000):
                        post_btn = btn
                        log.info("[YOUTUBE_BROWSER] Found Post submit button: %s", psub)
                        break
                except Exception:
                    continue

            if post_btn:
                post_btn.click()
                log.info("[YOUTUBE_BROWSER] Clicked Post submit button!")
                page.wait_for_timeout(3500)
                return {
                    "ok": True,
                    "posted": True,
                    "message": "🎉 Successfully posted community announcement & poll to YouTube!",
                    "url": target_url
                }
            else:
                return {
                    "ok": True,
                    "posted": False,
                    "message": "Text and poll options filled in the open browser window! Please review and click 'Post' to publish.",
                    "url": target_url
                }

        finally:
            if is_cdp:
                try:
                    page.close()
                except Exception:
                    pass
            # If not CDP, leave context open for user inspection if headless=False
