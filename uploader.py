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


def log(message):
    print(f"[TikTokUploader] {message}", flush=True)


# ─────────────────────────────
# Download Video
# ─────────────────────────────
def download_video(url, output_path):

    log(f"⬇️ Downloading video: {url}")

    response = requests.get(
        url,
        stream=True,
        timeout=120,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    with open(output_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file.write(chunk)

    size_mb = output_path.stat().st_size / (1024 * 1024)

    log(f"✅ Downloaded ({size_mb:.2f} MB)")


# ─────────────────────────────
# Prepare Video
# ─────────────────────────────
def prepare_video(source):

    if source.startswith("http://") or source.startswith("https://"):

        download_video(source, VIDEO_FILE)

    elif source.startswith("file://"):

        local_path = Path(
            source.replace("file://", "")
        )

        if not local_path.exists():
            raise Exception(f"❌ File not found: {local_path}")

        shutil.copy2(local_path, VIDEO_FILE)

    else:

        local_path = Path(source)

        if not local_path.exists():
            raise Exception(f"❌ File not found: {local_path}")

        if local_path.resolve() != VIDEO_FILE.resolve():
            shutil.copy2(local_path, VIDEO_FILE)

    log(f"✅ Video ready: {VIDEO_FILE}")


# ─────────────────────────────
# Parse Cookies
# ─────────────────────────────
def parse_cookies(cookies_path):

    with open(cookies_path, "r", encoding="utf-8") as file:
        raw = json.load(file)

    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]

    cookies = []

    for cookie in raw:

        expires = (
            cookie.get("expirationDate")
            or cookie.get("expires")
            or -1
        )

        cookies.append({
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie["domain"],
            "path": cookie.get("path", "/"),
            "secure": cookie.get("secure", False),
            "httpOnly": cookie.get("httpOnly", False),
            "expires": int(expires),
        })

    log(f"🍪 Loaded cookies: {len(cookies)}")

    return cookies


# ─────────────────────────────
# Open Page Retry
# ─────────────────────────────
def goto_with_retry(page, url, retries=3):

    for attempt in range(1, retries + 1):

        try:

            log(f"🌐 Opening page ({attempt}/{retries})")

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            time.sleep(5)

            return

        except PlaywrightTimeout:

            log("⚠️ Timeout retrying...")
            time.sleep(3)

    raise Exception("❌ Failed opening page")


# ─────────────────────────────
# Close Popup
# ─────────────────────────────
def close_modal(page):

    selectors = [
        "[data-e2e='modal-close-inner-button']",
        "button[aria-label='Close']",
        "button:has-text('Close')",
        "button:has-text('Cancel')",
        "button:has-text('OK')",
    ]

    for selector in selectors:

        try:

            button = page.locator(selector).first

            if button.is_visible(timeout=1500):

                button.click(force=True)

                log(f"✅ Closed popup: {selector}")

                time.sleep(1)

                return True

        except:
            pass

    return False


# ─────────────────────────────
# Find Upload Input
# ─────────────────────────────
def find_upload_input(page):

    log("🔍 Finding upload input...")

    file_input = page.locator(
        "input[type='file']"
    ).first

    file_input.wait_for(
        state="attached",
        timeout=20000
    )

    log("✅ Upload input found")

    return file_input


# ─────────────────────────────
# Wait Upload Complete
# ─────────────────────────────
def wait_for_upload_complete(page, timeout=180):

    log("⏳ Waiting upload process...")

    start = time.time()

    while time.time() - start < timeout:

        uploading = False

        selectors = [
            "[class*='progress']",
            "[class*='uploading']",
            "[class*='Progress']",
        ]

        for selector in selectors:

            try:

                if page.locator(selector).count() > 0:
                    uploading = True
                    break

            except:
                pass

        if not uploading:

            log("✅ Upload completed")
            break

        time.sleep(2)

    time.sleep(5)


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

            page.keyboard.press("Backspace")

            time.sleep(1)

            box.press_sequentially(
                text,
                delay=60
            )

            log("✅ Caption filled")

            time.sleep(2)

            return True

        except Exception as error:

            log(f"⚠️ Caption failed: {error}")

    return False


# ─────────────────────────────
# Draft Button
# ─────────────────────────────
def click_draft_button(page):

    log("📂 Finding Draft button...")

    selectors = [
        "[data-e2e='save-draft-button']",
        "button:has-text('Draft')",
        "button:has-text('Save draft')",
    ]

    for selector in selectors:

        try:

            button = page.locator(selector).first

            if not button.is_visible(timeout=3000):
                continue

            disabled = button.get_attribute("disabled")

            if disabled is not None:
                continue

            button.scroll_into_view_if_needed()

            time.sleep(1)

            button.click(force=True)

            log(f"✅ Draft clicked: {selector}")

            return True

        except:
            pass

    return False


# ─────────────────────────────
# Post Button
# ─────────────────────────────
def click_post_button(page):

    log("🚀 Finding Post button...")

    selectors = [
        "[data-e2e='post-video-button']",
        "button:has-text('Post')",
        "button:has-text('Publish')",
    ]

    for selector in selectors:

        try:

            button = page.locator(selector).first

            if not button.is_visible(timeout=3000):
                continue

            disabled = button.get_attribute("disabled")

            if disabled is not None:
                continue

            button.scroll_into_view_if_needed()

            time.sleep(1)

            button.click(force=True)

            log(f"✅ Post clicked: {selector}")

            return True

        except:
            pass

    return False


# ─────────────────────────────
# Upload Function
# ─────────────────────────────
def upload_to_tiktok(
    video_path,
    cookies_path,
    description="",
    post=False
):

    with sync_playwright() as playwright:

        log("🌐 Launching Chromium XVFB mode...")

        browser = playwright.chromium.launch(
            headless=False,
            channel="chromium",
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1400,900",
                "--start-maximized",
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

        # Create Page
        page = context.new_page()

        page.set_default_timeout(60000)

        # Open Upload Page
        goto_with_retry(
            page,
            TIKTOK_UPLOAD_URL
        )

        if "login" in page.url.lower():
            raise Exception("❌ Login failed. Invalid cookies")

        log("✅ Login success")

        close_modal(page)

        # Upload File
        file_input = find_upload_input(page)

        log(f"📤 Uploading video: {video_path}")

        file_input.set_input_files(
            str(video_path.resolve())
        )

        time.sleep(8)

        wait_for_upload_complete(page)

        close_modal(page)

        # Caption
        if description:
            fill_caption(page, description)

        time.sleep(3)

        # Draft / Post
        if post:

            posted = click_post_button(page)
            page.screenshot(path="after_post.png")

            if posted:

                log("⏳ Waiting publish process...")

                time.sleep(20)

                log("✅ VIDEO SUCCESSFULLY POSTED")

            else:

                log("❌ Post button not found")

        else:

            drafted = click_draft_button(page)

            if drafted:

                log("⏳ Waiting save draft...")

                time.sleep(15)

                log("✅ VIDEO SAVED TO DRAFT")

            else:

                log("❌ Draft button not found")

        time.sleep(5)

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

    parser.add_argument(
        "--post",
        default="false"
    )

    args = parser.parse_args()

    if not Path(args.cookies).exists():

        log("❌ Cookies file not found")
        sys.exit(1)

    try:

        prepare_video(args.url)

    except Exception as error:

        log(f"❌ Prepare video failed: {error}")
        sys.exit(1)

    upload_to_tiktok(
        VIDEO_FILE,
        args.cookies,
        args.description,
        args.post.lower() == "true"
    )


if __name__ == "__main__":
    main()
