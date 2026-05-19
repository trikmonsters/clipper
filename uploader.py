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
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
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
                wait_until="networkidle",
                timeout=120000
            )

            time.sleep(8)

            return

        except PlaywrightTimeout:

            log("⚠️ Timeout retrying...")
            time.sleep(5)

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

            if button.is_visible(timeout=2000):

                button.click(force=True)

                log(f"✅ Closed popup: {selector}")

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

    for selector in selectors:

        try:

            file_input = page.locator(selector).first

            file_input.wait_for(
                state="attached",
                timeout=30000
            )

            log(f"✅ Upload input found: {selector}")

            return file_input

        except:
            pass

    raise Exception("❌ Upload input not found")


# ─────────────────────────────
# Wait Upload Complete
# ─────────────────────────────
def wait_for_upload_complete(page):

    log("⏳ Waiting TikTok upload processing...")

    upload_done_selectors = [
        "button:has-text('Post')",
        "button:has-text('Publish')",
        "[data-e2e='post-video-button']",
    ]

    start = time.time()

    while time.time() - start < 300:

        for selector in upload_done_selectors:

            try:

                button = page.locator(selector).first

                if button.is_visible():

                    if button.is_enabled():

                        log("✅ Upload processing finished")

                        time.sleep(10)

                        return True

            except:
                pass

        log("⏳ Still processing...")
        time.sleep(5)

    raise Exception("❌ Upload processing timeout")


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
                delay=50
            )

            log("✅ Caption filled")

            time.sleep(2)

            return True

        except Exception as error:

            log(f"⚠️ Caption failed: {error}")

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

    for selector in selectors:

        try:

            button = page.locator(selector).first

            if not button.is_visible(timeout=5000):
                continue

            if not button.is_enabled():
                continue

            button.scroll_into_view_if_needed()

            time.sleep(2)

            page.screenshot(path="before_post_click.png")

            button.click(force=True)

            log(f"✅ Post clicked: {selector}")

            return True

        except Exception as error:

            log(f"⚠️ Post failed: {error}")

    return False


# ─────────────────────────────
# Click Draft Button
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

            if not button.is_enabled():
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
# Upload Function
# ─────────────────────────────
def upload_to_tiktok(
    video_path,
    cookies_path,
    description="",
    post=False
):

    with sync_playwright() as playwright:

        log("🌐 Launching browser...")

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
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
                "--disable-web-security",
                "--disable-features=AutomationControlled",
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
            raise Exception("❌ Login failed. Invalid cookies")

        log("✅ Login success")

        close_modal(page)

        # Upload File
        file_input = find_upload_input(page)

        log(f"📤 Uploading video: {video_path}")

        file_input.set_input_files(
            str(video_path.resolve())
        )

        time.sleep(10)

        # Wait upload complete
        wait_for_upload_complete(page)

        close_modal(page)

        # Caption
        if description:
            fill_caption(page, description)

        time.sleep(5)

        # POST
        if post:

            posted = click_post_button(page)

            page.screenshot(path="after_post_click.png")

            html = page.content()

            with open(
                "debug_after_post.html",
                "w",
                encoding="utf-8"
            ) as f:
                f.write(html)

            if posted:

                log("⏳ Waiting publish response...")

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

        # DRAFT
        else:

            drafted = click_draft_button(page)

            if drafted:

                log("⏳ Waiting save draft...")

                time.sleep(20)

                log("✅ VIDEO SAVED TO DRAFT")

            else:

                log("❌ Draft button not found")

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
