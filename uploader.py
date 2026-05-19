import os
import sys
import json
import time
import shutil
import argparse
import requests

from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeout
)

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

VIDEO_FILE = Path("video.mp4")


# ─────────────────────────────
# Logger
# ─────────────────────────────
def log(msg: str):
    print(f"[TikTokUploader] {msg}", flush=True)


# ─────────────────────────────
# Download Video
# ─────────────────────────────
def download_video(url: str, output: Path):

    log(f"⬇️ Downloading video: {url}")

    response = requests.get(
        url,
        stream=True,
        timeout=120,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )

    response.raise_for_status()

    with open(output, "wb") as f:
        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):
            if chunk:
                f.write(chunk)

    size_mb = output.stat().st_size / (1024 * 1024)

    log(f"✅ Video downloaded ({size_mb:.1f} MB)")


# ─────────────────────────────
# Prepare Video
# ─────────────────────────────
def prepare_video(source):

    if source.startswith("http://") or source.startswith("https://"):

        download_video(source, VIDEO_FILE)

    elif source.startswith("file://"):

        path = Path(
            source.replace("file://", "")
        )

        if not path.exists():
            raise Exception(f"❌ File not found: {path}")

        shutil.copy2(path, VIDEO_FILE)

    else:

        path = Path(source)

        if not path.exists():
            raise Exception(f"❌ File not found: {path}")

        if path.resolve() != VIDEO_FILE.resolve():
            shutil.copy2(path, VIDEO_FILE)

    size_mb = VIDEO_FILE.stat().st_size / (1024 * 1024)

    log(f"✅ Video ready ({size_mb:.1f} MB)")


# ─────────────────────────────
# Parse Cookies
# ─────────────────────────────
def parse_cookies(cookies_path: str):

    with open(cookies_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]

    cookies = []

    for c in raw:

        expiry = (
            c.get("expirationDate")
            or c.get("expires")
            or -1
        )

        cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "expires": int(expiry),
            "httpOnly": c.get("httpOnly", False),
        })

    log(f"🍪 Cookies loaded ({len(cookies)})")

    return cookies


# ─────────────────────────────
# Open Page Retry
# ─────────────────────────────
def goto_with_retry(page, url: str, retries: int = 3):

    for attempt in range(1, retries + 1):

        try:

            log(f"🌐 Opening page ({attempt}/{retries})")

            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=120000
            )

            page.wait_for_timeout(15000)

            log(f"✅ Page opened: {page.url}")

            if response:
                log(f"🌐 Status: {response.status}")

            return

        except Exception as error:

            log(f"⚠️ Open failed: {error}")

            page.screenshot(
                path=f"goto_error_{attempt}.png"
            )

            time.sleep(5)

    raise Exception("❌ Failed open page")


# ─────────────────────────────
# Close Modal
# ─────────────────────────────
def close_modal(page):

    selectors = [
        "[data-e2e='modal-close-inner-button']",
        "button[aria-label='Close']",
        "button:has-text('Close')",
        "button:has-text('Cancel')",
        "button:has-text('OK')",
    ]

    for sel in selectors:

        try:

            btn = page.locator(sel).first

            if btn.is_visible(timeout=2000):

                btn.click(force=True)

                log(f"✅ Modal closed: {sel}")

                time.sleep(2)

                return True

        except:
            pass

    return False


# ─────────────────────────────
# Find Upload Input
# ─────────────────────────────
def find_upload_input(page):

    log("🔍 Finding upload input...")

    selectors = [
        "input[type='file']",
        "input[accept*='video']",
    ]

    for sel in selectors:

        try:

            file_input = page.locator(sel).first

            file_input.wait_for(
                state="attached",
                timeout=30000
            )

            log(f"✅ Upload input found: {sel}")

            return file_input

        except:
            pass

    raise Exception("❌ Upload input not found")


# ─────────────────────────────
# Wait Upload Complete
# ─────────────────────────────
def wait_for_upload_complete(page):

    log("⏳ Waiting upload process...")

    selectors = [
        "[data-e2e='post-video-button']",
        "button:has-text('Post')",
        "button:has-text('Publish')",
    ]

    start = time.time()

    while time.time() - start < 300:

        for sel in selectors:

            try:

                btn = page.locator(sel).first

                if btn.is_visible():

                    if btn.is_enabled():

                        log("✅ Upload completed")

                        time.sleep(10)

                        return True

            except:
                pass

        log("⏳ Still processing...")

        time.sleep(5)

    raise Exception("❌ Upload timeout")


# ─────────────────────────────
# Fill Caption
# ─────────────────────────────
def fill_caption(page, text):

    selectors = [
        "div.public-DraftEditor-content",
        "[data-e2e='caption-input']",
        "div[contenteditable='true']",
    ]

    for selector in selectors:

        try:

            box = page.locator(selector).first

            if not box.is_visible(timeout=5000):
                continue

            box.click(force=True)

            time.sleep(1)

            page.keyboard.press("Control+a")

            time.sleep(0.5)

            page.keyboard.press("Backspace")

            time.sleep(1)

            box.press_sequentially(
                text,
                delay=60
            )

            log("✅ Caption filled")

            time.sleep(2)

            return True

        except Exception as e:

            log(f"⚠️ Caption failed: {e}")

    return False


# ─────────────────────────────
# Click Post Button
# ─────────────────────────────
def click_post_button(page):

    log("🚀 Finding Post button...")

    selectors = [
        "[data-e2e='post-video-button']",
        "button:has-text('Post')",
        "button:has-text('Publish')",
    ]

    for sel in selectors:

        try:

            btn = page.locator(sel).first

            if not btn.is_visible(timeout=5000):
                continue

            if not btn.is_enabled():
                continue

            btn.scroll_into_view_if_needed()

            time.sleep(2)

            page.screenshot(
                path="before_post_click.png"
            )

            btn.click(force=True)

            log(f"✅ Post clicked: {sel}")

            return True

        except Exception as e:

            log(f"⚠️ Post failed: {e}")

    return False


# ─────────────────────────────
# Upload Function
# ─────────────────────────────
def upload_to_tiktok(
    video_path,
    cookies_path,
    description=""
):

    with sync_playwright() as p:

        log("🌐 Launching Chromium...")

        browser = p.chromium.launch(
            headless=False,
            channel="chromium",
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1400,900",
                "--start-maximized",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
                "--disable-web-security",
                "--disable-features=AutomationControlled",
                "--disable-features=Translate",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--metrics-recording-only",
                "--mute-audio",
            ]
        )

        context = browser.new_context(

            viewport={
                "width": 1400,
                "height": 900
            },

            locale="en-US",

            timezone_id="Asia/Jakarta",

            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # Anti Detection
        context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        window.chrome = {
            runtime: {}
        };

        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });

        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3]
        });
        """)

        # Load Cookies
        log("🍪 Loading cookies...")

        cookies = parse_cookies(cookies_path)

        context.add_cookies(cookies)

        page = context.new_page()

        page.set_default_timeout(120000)

        # Open Upload Page
        goto_with_retry(
            page,
            TIKTOK_UPLOAD_URL
        )

        if "login" in page.url.lower():
            raise Exception(
                "❌ Login failed. Invalid cookies"
            )

        log("✅ Login success")

        close_modal(page)

        # Upload File
        file_input = find_upload_input(page)

        log(f"📤 Uploading video: {video_path}")

        file_input.set_input_files(
            str(video_path.resolve())
        )

        time.sleep(10)

        # Wait Upload Complete
        wait_for_upload_complete(page)

        close_modal(page)

        # Fill Caption
        if description:
            fill_caption(page, description)

        time.sleep(5)

        # Click POST
        posted = click_post_button(page)

        page.screenshot(
            path="after_post_click.png"
        )

        html = page.content()

        with open(
            "debug_after_post.html",
            "w",
            encoding="utf-8"
        ) as f:
            f.write(html)

        if posted:

            log("⏳ Waiting publish process...")

            try:

                page.wait_for_url(
                    "**/creator-center/**",
                    timeout=120000
                )

                log("✅ Redirect detected")

            except:

                log("⚠️ No redirect detected")

            time.sleep(60)

            current_url = page.url

            log(f"📍 Current URL: {current_url}")

            page.screenshot(
                path="publish_result.png"
            )

            if "upload" not in current_url:

                log("✅ VIDEO LIKELY POSTED")

            else:

                log("❌ STILL ON UPLOAD PAGE")

        else:

            log("❌ Post button not found")

        time.sleep(10)

        browser.close()


# ─────────────────────────────
# Main
# ─────────────────────────────
def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--url",
        required=True
    )

    parser.add_argument(
        "--cookies",
        default="cookies.json"
    )

    parser.add_argument(
        "--description",
        default="Video keren 🚀 #fyp #viral"
    )

    args = parser.parse_args()

    if not Path(args.cookies).exists():

        log("❌ Cookies file not found")

        sys.exit(1)

    try:

        prepare_video(args.url)

    except Exception as e:

        log(f"❌ Prepare video failed: {e}")

        sys.exit(1)

    upload_to_tiktok(
        VIDEO_FILE,
        args.cookies,
        args.description
    )


if __name__ == "__main__":
    main()
