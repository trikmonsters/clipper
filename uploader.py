import os
import sys
import json
import time
import requests
import argparse
import shutil

from pathlib import Path
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeout
)

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
VIDEO_FILE = Path("video.mp4")


def log(msg: str):
    print(f"[TikTok Uploader] {msg}", flush=True)


def download_video(url: str, output: Path):
    log(f"⬇️ Downloading video: {url}")

    response = requests.get(
        url,
        stream=True,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    response.raise_for_status()

    with open(output, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    size_mb = output.stat().st_size / (1024 * 1024)
    log(f"✅ Video downloaded ({size_mb:.1f} MB)")


def prepare_video(source):
    if source.startswith("http://") or source.startswith("https://"):
        download_video(source, VIDEO_FILE)

    elif source.startswith("file://"):
        path = Path(source.replace("file://", ""))
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


def parse_cookies(cookies_path: str) -> list:
    with open(cookies_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content.startswith("["):
        raise Exception("❌ Invalid cookies format")

    raw = json.loads(content)
    cookies = []

    for c in raw:
        expiry = c.get("expirationDate") or c.get("expires") or -1
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


def goto_with_retry(page, url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            log(f"🌐 Opening page ({attempt}/{retries})")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            return

        except PlaywrightTimeout:
            log(f"⚠️ Timeout attempt {attempt}")
            time.sleep(3)

    raise Exception("❌ Failed open page")


def close_modal(page):
    """Tutup semua modal/popup yang mungkin muncul"""
    selectors = [
        "[data-e2e='modal-close-inner-button']",
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "button:has-text('Close')",
        "button:has-text('Cancel')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        "button:has-text('I understand')",
        "button:has-text('Dismiss')",
        "button:has-text('Skip')",
        "[data-e2e='close-button']",
        ".modal-close",
        "[class*='closeButton']",
        "[class*='close-btn']",
    ]

    closed_any = False

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(force=True)
                log(f"✅ Modal closed: {sel}")
                time.sleep(1)
                closed_any = True
        except:
            pass

    return closed_any


def close_content_popup(page):
    """Tutup popup konten / copyright / community guidelines sebelum caption"""
    log("🔍 Checking content popups...")

    popup_selectors = [
        # Copyright popup
        "button:has-text('Continue')",
        "button:has-text('I agree')",
        "button:has-text('Confirm')",
        "button:has-text('Understood')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Done')",
        # TikTok specific
        "[data-e2e='content-check-confirm']",
        "[data-e2e='copyright-confirm']",
        "[data-e2e='guideline-confirm']",
        "[data-e2e='alert-confirm-button']",
        # Generic dialog confirm
        "div[role='dialog'] button:has-text('Confirm')",
        "div[role='dialog'] button:has-text('Continue')",
        "div[role='dialog'] button:has-text('OK')",
        "div[role='dialog'] button:has-text('Done')",
        "div[role='alertdialog'] button",
    ]

    closed_any = False

    for sel in popup_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn_text = btn.inner_text().strip()
                btn.click(force=True)
                log(f"✅ Content popup closed: '{btn_text}' | {sel}")
                time.sleep(1.5)
                closed_any = True
        except:
            pass

    if not closed_any:
        log("ℹ️ No content popup found")

    return closed_any


def find_upload_input(page):
    log("🔍 Finding upload input...")
    file_input = page.locator("input[type='file']").first
    file_input.wait_for(state="attached", timeout=20000)
    log("✅ Upload input found")
    return file_input


def wait_for_upload_complete(page, timeout=180):
    log("⏳ Waiting upload process...")
    start = time.time()

    while time.time() - start < timeout:
        progress = False

        selectors = [
            "[class*='progress']",
            "[class*='uploading']",
            "[class*='Progress']",
            "[class*='loading']",
        ]

        for sel in selectors:
            try:
                if page.locator(sel).count() > 0:
                    progress = True
                    break
            except:
                pass

        if not progress:
            log("✅ Upload completed")
            break

        time.sleep(2)

    # Tunggu ekstra agar TikTok proses video sepenuhnya
    log("⏳ Waiting for TikTok to process video...")
    time.sleep(8)


def fill_caption(page, text):
    log("✏️ Filling caption...")

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

            box.press_sequentially(text, delay=60)
            time.sleep(2)

            log("✅ Caption filled")
            return True

        except Exception as e:
            log(f"⚠️ Caption failed with selector {selector}: {e}")

    log("❌ All caption selectors failed")
    return False


def click_post_button(page):
    log("🚀 Finding Post button...")

    selectors = [
        "[data-e2e='post-button']",
        "button[class*='btn-post']",
        "button[class*='submit']:not([class*='draft'])",
        "button:has-text('Post'):not(:has-text('Draft'))",
        "button:has-text('Publish')",
    ]

    for sel in selectors:
        try:
            buttons = page.locator(sel).all()

            for btn in buttons:
                if not btn.is_visible(timeout=2000):
                    continue

                disabled = btn.get_attribute("disabled")
                if disabled is not None:
                    log(f"⚠️ Skipping disabled button: {sel}")
                    continue

                btn_text = btn.inner_text().strip().lower()
                log(f"🔍 Button found: '{btn_text}' | selector: {sel}")

                if btn_text not in ["post", "publish"]:
                    log(f"⚠️ Skipping button with text: '{btn_text}'")
                    continue

                btn.scroll_into_view_if_needed()
                time.sleep(2)

                page.screenshot(path="confirm_post_button.png")
                log("📸 confirm_post_button.png saved")

                btn.click(force=True)
                log(f"✅ Post clicked: '{btn_text}' | {sel}")
                return True

        except Exception as e:
            log(f"⚠️ Error on selector {sel}: {e}")

    return False


def wait_for_post_success(page, timeout=30):
    log("🔍 Validating post success...")
    start = time.time()

    success_selectors = [
        "[data-e2e='post-success']",
        "div:has-text('Your video is being uploaded')",
        "div:has-text('successfully')",
        "div:has-text('posted')",
    ]

    while time.time() - start < timeout:
        current_url = page.url
        log(f"🔗 Current URL: {current_url}")

        if "upload" not in current_url.lower():
            log("✅ Redirected away from upload page — post successful!")
            return True

        for sel in success_selectors:
            try:
                if page.locator(sel).count() > 0:
                    log(f"✅ Success indicator found: {sel}")
                    return True
            except:
                pass

        time.sleep(2)

    log("⚠️ Could not confirm post success — check TikTok manually")
    return False


def upload_to_tiktok(video_path, cookies_path, description=""):

    with sync_playwright() as p:

        log("🌐 Launching Chromium XVFB mode...")

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
            ]
        )

        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="en-US",
            timezone_id="Asia/Jakarta",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3]
        });
        """)

        log("🍪 Loading cookies...")
        cookies = parse_cookies(cookies_path)
        context.add_cookies(cookies)

        page = context.new_page()
        page.set_default_timeout(60000)

        # ── Buka halaman upload ──
        goto_with_retry(page, TIKTOK_UPLOAD_URL)

        if "login" in page.url.lower():
            raise Exception("❌ Login failed. Invalid cookies")

        log("✅ Login success")

        # ── Tutup modal awal ──
        close_modal(page)
        time.sleep(2)

        # ── Upload file video ──
        file_input = find_upload_input(page)
        log(f"📤 Uploading video: {video_path}")
        file_input.set_input_files(str(video_path.resolve()))

        time.sleep(8)

        # ── Tunggu upload selesai ──
        wait_for_upload_complete(page)

        # ── Tutup modal setelah upload ──
        close_modal(page)
        time.sleep(2)

        # ── Tutup popup konten (copyright, guidelines, dll) ──
        close_content_popup(page)
        time.sleep(2)

        # ── Tutup modal sekali lagi untuk pastikan bersih ──
        close_modal(page)
        time.sleep(2)

        # ── Screenshot sebelum isi caption ──
        page.screenshot(path="before_caption.png")
        log("📸 Screenshot saved: before_caption.png")

        # ── Isi caption ──
        if description:
            fill_caption(page, description)
            time.sleep(2)

        # ── Tutup popup yang mungkin muncul setelah caption ──
        close_content_popup(page)
        time.sleep(1)
        close_modal(page)
        time.sleep(2)

        # ── Screenshot sebelum post ──
        page.screenshot(path="before_post.png")
        log("📸 Screenshot saved: before_post.png")

        # ── Klik tombol Post ──
        posted = click_post_button(page)

        if posted:
            log("⏳ Waiting post to publish...")
            time.sleep(10)

            success = wait_for_post_success(page)

            page.screenshot(path="after_post.png")
            log("📸 Screenshot saved: after_post.png")

            if success:
                log("✅ VIDEO POSTED SUCCESSFULLY")
            else:
                log("⚠️ Post clicked but success unconfirmed — check TikTok!")

        else:
            log("❌ Post button not found")
            page.screenshot(path="post_failed.png")
            log("📸 Screenshot saved: post_failed.png")

        time.sleep(5)
        browser.close()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--url", required=True)
    parser.add_argument("--cookies", default="cookies.json")
    parser.add_argument("--description", default="Video keren 🚀 #fyp #viral")

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
