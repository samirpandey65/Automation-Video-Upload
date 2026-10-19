"""
Google Flow (flow.google.com) Browser Automation Assistant
-----------------------------------------------------------
Uses Playwright with a persistent user data profile (data/flow_browser_profile)
so the creator logs into Google Flow once, and subsequent prompt generation
and image retrieval can run automatically without asking for credentials again.
"""

import logging
import os
import time
from pathlib import Path
from .config import BASE_DIR

log = logging.getLogger(__name__)

FLOW_USER_DATA_DIR = BASE_DIR / "data" / "flow_browser_profile"
FLOW_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


def open_flow_interactive_login() -> dict:
    """
    Launches Chrome with remote debugging on port 9222 and persistent profile (data/flow_browser_profile).
    This allows the creator to stay logged in and enables Playwright to automate Flow directly without profile locks.
    """
    import subprocess
    import shutil

    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome") or "",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    browser_exe = next((p for p in chrome_candidates if p and Path(p).exists()), "")

    if browser_exe:
        cmd = [
            browser_exe,
            "--remote-debugging-port=9222",
            f"--user-data-dir={str(FLOW_USER_DATA_DIR.resolve())}",
            "https://flow.google.com"
        ]
        log.info("[FLOW_AUTOMATION] Launching browser with persistent profile & CDP port 9222: %s", " ".join(cmd))
        subprocess.Popen(cmd)
        return {
            "ok": True,
            "message": "Chrome launched! Log in once to Google on flow.google.com. You can keep this window open—Auto-Generate can connect directly to it!"
        }
    else:
        import webbrowser
        webbrowser.open("https://flow.google.com")
        return {
            "ok": True,
            "message": "Opened flow.google.com in default browser."
        }


def automated_generate_flow_image(
    prompt_text: str,
    output_path: str,
    aspect_ratio: str = "9:16",
    count: int = 1,
    reference_image_path: str = None
) -> dict:
    """
    Automated zero-click generator using the saved persistent session or live CDP session.
    Navigates to flow.google.com, configures aspect ratio & image count, enters prompt,
    triggers generation, detects policy violations instantly, and downloads the resulting high-res image.
    """
    from playwright.sync_api import sync_playwright

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    log.info("[FLOW_AUTOMATION] Initiating automated flow generation (ratio=%s count=%d) for prompt: %s",
             aspect_ratio, count, prompt_text[:80])
    
    with sync_playwright() as p:
        browser = None
        context = None
        is_cdp = False

        # 1. First attempt: connect to existing live Chrome session over CDP (port 9222)
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222", timeout=3000)
            if browser and browser.contexts:
                context = browser.contexts[0]
                is_cdp = True
                log.info("[FLOW_AUTOMATION] Connected directly to active Chrome browser via CDP!")
        except Exception:
            is_cdp = False

        # 2. If no CDP session running, launch persistent context using installed Chrome
        if not context:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(FLOW_USER_DATA_DIR),
                    channel="chrome",
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
            except Exception as launch_err:
                log.warning("[FLOW_AUTOMATION] Could not launch persistent context with channel='chrome': %s", launch_err)
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(FLOW_USER_DATA_DIR),
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                    )
                except Exception as fb_err:
                    log.warning("[FLOW_AUTOMATION] Fallback launch failed: %s", fb_err)
                    return {
                        "ok": False,
                        "reason": "profile_locked",
                        "message": "Chrome is currently using the Flow profile. Please either close any background Chrome windows or click 'Connect Flow Session'."
                    }

        try:
            # Look for existing flow tab or open a new one
            page = None
            for existing_page in context.pages:
                if "flow.google.com" in existing_page.url:
                    page = existing_page
                    break
            
            if not page:
                page = context.new_page()
                page.goto("https://flow.google.com", timeout=35000, wait_until="domcontentloaded")
            else:
                page.bring_to_front()

            page.wait_for_timeout(2000)

            # Handle landing / get started or sign in if on /about
            if "/about" in page.url or "about" in page.url:
                get_started = page.locator("a:has-text('Get started'), button:has-text('Get started'), a:has-text('Sign in')").first
                if get_started.is_visible(timeout=2000):
                    get_started.click()
                    page.wait_for_timeout(3000)

            # If on home screen, click "New project" button
            new_proj = page.locator('button:has-text("New project"), div:has-text("New project"), [aria-label*="New project" i]').first
            if new_proj.is_visible(timeout=3000):
                log.info("[FLOW_AUTOMATION] Clicking '+ New project' on Google Flow home...")
                new_proj.click()
                page.wait_for_timeout(3500)

            # Dismiss any announcement / onboarding modal (e.g. "Nano Banana 2.1" with "Get started")
            for btn_text in ["Get started", "Got it", "Done", "Continue", "Next"]:
                m_btn = page.locator(f'button:has-text("{btn_text}")').first
                if m_btn.is_visible(timeout=1200):
                    log.info("[FLOW_AUTOMATION] Dismissing intro modal via '%s' button", btn_text)
                    m_btn.click()
                    page.wait_for_timeout(1000)
                    break
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            # 3. Configure Aspect Ratio & Image Count via settings popup
            try:
                settings_overlay = page.locator("flow-prompt-box-settings")
                if not settings_overlay.is_visible():
                    trigger = page.locator("button[aria-label='Settings trigger'], button:has-text('Nano Banana')").first
                    if trigger.is_visible(timeout=2500):
                        log.info("[FLOW_AUTOMATION] Opening prompt settings popup...")
                        trigger.click()
                        page.wait_for_timeout(600)

                if settings_overlay.is_visible(timeout=2000):
                    # Ensure Image mode is active
                    img_toggle = page.locator("flow-prompt-box-settings mat-button-toggle:has-text('Image') button").first
                    if img_toggle.is_visible(timeout=1000):
                        img_toggle.click()
                        page.wait_for_timeout(200)

                    # Select Aspect Ratio (9:16 vertical or 16:9 horizontal)
                    target_ratio = "9:16" if "9:16" in str(aspect_ratio) or "vert" in str(aspect_ratio).lower() else "16:9"
                    btn_ratio = page.locator(f"flow-prompt-box-settings mat-button-toggle:has-text('{target_ratio}') button").first
                    if btn_ratio.is_visible(timeout=1000):
                        log.info("[FLOW_AUTOMATION] Selecting aspect ratio %s in Flow settings", target_ratio)
                        btn_ratio.click()
                        page.wait_for_timeout(250)

                    # Select Image Count (x1, x2, x4)
                    target_count = f"x{count}" if count in [1, 2, 3, 4] else "x1"
                    btn_cnt = page.locator(f"flow-prompt-box-settings mat-button-toggle:has-text('{target_count}') button").first
                    if btn_cnt.is_visible(timeout=1000):
                        log.info("[FLOW_AUTOMATION] Selecting image count %s in Flow settings", target_count)
                        btn_cnt.click()
                        page.wait_for_timeout(250)

                    # Close settings popup
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
            except Exception as set_err:
                log.warning("[FLOW_AUTOMATION] Non-critical: could not adjust settings popup: %s", set_err)
                page.keyboard.press("Escape")

            # Count existing error tiles before submitting new generation
            initial_error_count = len(page.locator("flow-error-tile").all())

            # Find visible prompt input box (DIV contenteditable or textarea)
            input_box = page.locator('[contenteditable="true"]:visible, [placeholder*="What do you want to create" i]:visible, textarea:visible').first
            if not input_box.is_visible(timeout=8000):
                debug_shot = out_file.parent / "flow_debug.png"
                try:
                    page.screenshot(path=str(debug_shot))
                except Exception:
                    pass
                return {
                    "ok": False,
                    "reason": "flow_login_needed",
                    "message": "Google Flow window is open! Please log into Google on that window. Once logged in, click Auto-Generate again."
                }

            input_box.click()
            page.wait_for_timeout(300)
            # Clear previous prompt text
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(200)

            # Optional: Upload character keyframe reference image as ingredient to Flow
            if reference_image_path and Path(reference_image_path).exists():
                try:
                    ref_resolved = str(Path(reference_image_path).resolve())
                    log.info("[FLOW_AUTOMATION] Providing character reference image to Flow: %s", ref_resolved)
                    add_btn = page.locator('button[aria-label*="Add ingredients" i]').first
                    if add_btn.is_visible(timeout=1500):
                        add_btn.click()
                        page.wait_for_timeout(600)
                        up_btn = page.locator('button:has-text("Upload media"), [role="menuitem"]:has-text("Upload media")').first
                        if up_btn.is_visible(timeout=1500):
                            with page.expect_file_chooser(timeout=4000) as fc_info:
                                up_btn.click()
                            file_chooser = fc_info.value
                            file_chooser.set_files(ref_resolved)
                            log.info("[FLOW_AUTOMATION] Character reference image uploaded to Flow ingredients!")
                            page.wait_for_timeout(2500)
                            # Close ingredient popup if still open
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(300)
                except Exception as up_err:
                    log.warning("[FLOW_AUTOMATION] Non-critical: could not attach reference image: %s", up_err)
                    page.keyboard.press("Escape")

            # Type prompt cleanly into contenteditable element
            page.keyboard.type(prompt_text, delay=6)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            log.info("[FLOW_AUTOMATION] Submitted prompt to Google Flow: %s", prompt_text[:60])
            
            # Wait for generation to start and image to render (poll up to 45 seconds)
            start_wait = time.time()
            img_url = ""
            img_el = None

            while time.time() - start_wait < 45:
                page.wait_for_timeout(2000)

                # 1. Check for NEW Policy Violation error
                current_errors = page.locator("flow-error-tile").all()
                if len(current_errors) > initial_error_count:
                    # New error tile appeared!
                    new_err = current_errors[0]
                    err_txt = new_err.inner_text().strip().replace("\n", " ")
                    log.warning("[FLOW_AUTOMATION] Google Flow policy violation detected: %s", err_txt)
                    return {
                        "ok": False,
                        "reason": "policy_violation",
                        "message": (
                            "Google Flow safety filter rejected this prompt: "
                            f"\"{err_txt[:120]}\". "
                            "This typically happens when trademarked character/celebrity names "
                            "(e.g., Spider-Man, Miles Morales) or sensitive phrases are in the prompt. "
                            "Please edit or sanitize the prompt in Thumbnail Studio and click Auto-Generate again."
                        )
                    }

                # 2. Check for newly completed image
                tile = page.locator("flow-image-tile:not(:has(flow-error-tile))").first
                if tile.is_visible(timeout=600):
                    candidate_img = tile.locator('img[src*="flow-content.google"], img[src*="googleusercontent"], img').first
                    if candidate_img.is_visible(timeout=600):
                        src = candidate_img.get_attribute("src") or ""
                        if src and "data:image/svg" not in src:
                            img_el = candidate_img
                            img_url = src
                            break

            if img_el and img_el.is_visible():
                page.wait_for_timeout(1000)
                # Download full-resolution image bytes if direct URL available
                downloaded = False
                if img_url and ("http://" in img_url or "https://" in img_url):
                    try:
                        resp = page.request.get(img_url, timeout=10000)
                        if resp.status == 200 and len(resp.body()) > 5000:
                            with open(out_file, "wb") as f:
                                f.write(resp.body())
                            downloaded = True
                            log.info("[FLOW_AUTOMATION] Downloaded full-res Flow image (%d bytes) to %s", len(resp.body()), out_file)
                    except Exception as dl_err:
                        log.warning("[FLOW_AUTOMATION] Direct download failed, falling back to screenshot: %s", dl_err)

                if not downloaded:
                    img_el.screenshot(path=str(out_file))
                    log.info("[FLOW_AUTOMATION] Captured generated image screenshot to %s", out_file)

                if not is_cdp:
                    context.close()
                return {
                    "ok": True,
                    "path": str(out_file),
                    "message": f"High-res {aspect_ratio} thumbnail successfully generated & captured from Google Flow!"
                }
            
            if not is_cdp:
                context.close()
            return {
                "ok": False,
                "reason": "image_timeout",
                "message": "Prompt submitted to Flow, but image render took longer than expected. You can edit the prompt or drag and drop the image directly into Thumbnail Studio."
            }
        except Exception as exc:
            if not is_cdp and context:
                try:
                    context.close()
                except Exception:
                    pass
            log.warning("[FLOW_AUTOMATION] Execution error: %s", exc)
            return {
                "ok": False,
                "reason": "error",
                "message": str(exc)
            }
