import os
import sys
import json
import time
import random
import requests
import argparse
import shutil

from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeout
)

try:
    import pytz
    TZ = pytz.timezone("Asia/Jakarta")
except ImportError:
    TZ = None

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
VIDEO_FILE = Path("video.mp4")


def log(msg: str):
    print(f"[TikTok Uploader] {msg}", flush=True)


def human_delay(min_sec=1.5, max_sec=4.0):
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)


def human_mouse_move(page):
    try:
        for _ in range(random.randint(2, 4)):
            x = random.randint(200, 1200)
            y = random.randint(100, 800)
            page.mouse.move(x, y, steps=random.randint(5, 15))
            time.sleep(random.uniform(0.1, 0.4))
    except:
        pass


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
            human_delay(4, 7)
            return
        except PlaywrightTimeout:
            log(f"⚠️ Timeout attempt {attempt}")
            human_delay(3, 5)
    raise Exception("❌ Failed open page")


def close_modal(page):
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
                human_mouse_move(page)
                btn.click(force=True)
                log(f"✅ Modal closed: {sel}")
                human_delay(1, 2)
                closed_any = True
        except:
            pass
    return closed_any


def close_content_popup(page):
    log("🔍 Checking content popups...")
    popup_selectors = [
        "button:has-text('Continue')",
        "button:has-text('I agree')",
        "button:has-text('Confirm')",
        "button:has-text('Understood')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Done')",
        "[data-e2e='content-check-confirm']",
        "[data-e2e='copyright-confirm']",
        "[data-e2e='guideline-confirm']",
        "[data-e2e='alert-confirm-button']",
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
                human_mouse_move(page)
                btn.click(force=True)
                log(f"✅ Content popup closed: '{btn_text}' | {sel}")
                human_delay(1.5, 3)
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


def wait_for_upload_complete(page, timeout=240):
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
    log("⏳ Waiting for TikTok to fully process video...")
    human_delay(8, 12)


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
            human_mouse_move(page)
            box.click(force=True)
            human_delay(1, 2)
            page.keyboard.press("Control+a")
            human_delay(0.3, 0.7)
            page.keyboard.press("Backspace")
            human_delay(0.8, 1.5)
            for char in text:
                box.type(char, delay=random.randint(40, 120))
            human_delay(1.5, 3)
            log("✅ Caption filled")
            return True
        except Exception as e:
            log(f"⚠️ Caption failed with selector {selector}: {e}")
    log("❌ All caption selectors failed")
    return False


def set_schedule_post(page, schedule_minutes_from_now=20):
    log("📅 Setting up scheduled post...")

    if TZ:
        schedule_time = datetime.now(TZ) + timedelta(minutes=schedule_minutes_from_now)
    else:
        schedule_time = datetime.now() + timedelta(minutes=schedule_minutes_from_now)

    log(f"📅 Target schedule: {schedule_time.strftime('%Y-%m-%d %H:%M')}")

    # ── Klik toggle Schedule ──
    schedule_toggle_selectors = [
        "[data-e2e='schedule-switch']",
        "input[type='checkbox'][class*='schedule']",
        "label:has-text('Schedule')",
        "div:has-text('Schedule') input[type='checkbox']",
        "[class*='schedule'] input[type='checkbox']",
        "[class*='Schedule'] input[type='checkbox']",
        "button:has-text('Schedule')",
    ]

    toggled = False
    for sel in schedule_toggle_selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                human_mouse_move(page)
                el.click(force=True)
                log(f"✅ Schedule toggle clicked: {sel}")
                human_delay(2, 3)
                toggled = True
                break
        except:
            pass

    if not toggled:
        log("❌ Schedule toggle not found")
        page.screenshot(path="schedule_toggle_failed.png")
        return False

    # ── Set tanggal ──
    date_str = schedule_time.strftime("%Y-%m-%d")
    time_str = schedule_time.strftime("%H:%M")

    date_selectors = [
        "[data-e2e='schedule-date-input']",
        "input[type='date']",
        "input[placeholder*='date']",
        "input[placeholder*='Date']",
        "[class*='date'] input",
        "[class*='Date'] input",
    ]

    for sel in date_selectors:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=3000):
                inp.click(force=True)
                human_delay(0.5, 1)
                inp.fill(date_str)
                page.keyboard.press("Tab")
                log(f"✅ Date set: {date_str}")
                human_delay(1, 2)
                break
        except:
            pass

    # ── Set waktu ──
    time_selectors = [
        "[data-e2e='schedule-time-input']",
        "input[type='time']",
        "input[placeholder*='time']",
        "input[placeholder*='Time']",
        "[class*='time'] input",
        "[class*='Time'] input",
    ]

    for sel in time_selectors:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=3000):
                inp.click(force=True)
                human_delay(0.5, 1)
                inp.fill(time_str)
                page.keyboard.press("Tab")
                log(f"✅ Time set: {time_str}")
                human_delay(1, 2)
                break
        except:
            pass

    page.screenshot(path="schedule_set.png")
    log("📸 schedule_set.png saved")

    return True, schedule_time


def click_schedule_button(page):
    log("📅 Finding Schedule button...")
    selectors = [
        "[data-e2e='schedule-button']",
        "button:has-text('Schedule')",
        "button:has-text('Schedule post')",
        "button[class*='schedule']",
    ]
    for sel in selectors:
        try:
            buttons = page.locator(sel).all()
            for btn in buttons:
                if not btn.is_visible(timeout=2000):
                    continue
                disabled = btn.get_attribute("disabled")
                if disabled is not None:
                    log(f"⚠️ Schedule button disabled: {sel}")
                    continue
                btn_text = btn.inner_text().strip().lower()
                log(f"🔍 Button found: '{btn_text}'")
                if "schedule" not in btn_text:
                    continue
                btn.scroll_into_view_if_needed()
                human_delay(1.5, 2.5)
                human_mouse_move(page)
                page.screenshot(path="confirm_schedule_button.png")
                btn.click(force=True)
                log(f"✅ Schedule button clicked: '{btn_text}'")
                return True
        except Exception as e:
            log(f"⚠️ Error: {e}")
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
                human_delay(1.5, 3)
                human_mouse_move(page)
                page.screenshot(path="confirm_post_button.png")
                btn.click(force=True)
                log(f"✅ Post clicked: '{btn_text}' | {sel}")
                return True
        except Exception as e:
            log(f"⚠️ Error on selector {sel}: {e}")
    return False


def wait_for_post_success(page, timeout=45):
    log("🔍 Validating post/schedule success...")
    start = time.time()
    success_selectors = [
        "[data-e2e='post-success']",
        "div:has-text('Your video is being uploaded')",
        "div:has-text('successfully')",
        "div:has-text('posted')",
        "div:has-text('scheduled')",
        "div:has-text('Scheduled')",
    ]
    while time.time() - start < timeout:
        current_url = page.url
        log(f"🔗 Current URL: {current_url}")
        if "upload" not in current_url.lower():
            log("✅ Redirected — success!")
            return True
        for sel in success_selectors:
            try:
                if page.locator(sel).count() > 0:
                    log(f"✅ Success indicator found: {sel}")
                    return True
            except:
                pass
        time.sleep(2)
    log("⚠️ Could not confirm success — check TikTok manually")
    return False


def upload_to_tiktok(video_path, cookies_path, description="", use_schedule=False, schedule_minutes=20):

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
                "--disable-extensions",
                "--disable-plugins-discovery",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
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
            get: () => undefined,
            configurable: true
        });
        delete navigator.__proto__.webdriver;
        window.chrome = {
            runtime: {
                connect: () => {},
                sendMessage: () => {},
                onMessage: { addListener: () => {} }
            },
            loadTimes: () => {},
            csi: () => {},
        };
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en', 'id']
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ];
                arr.__proto__ = PluginArray.prototype;
                return arr;
            }
        });
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            const context = this.getContext('2d');
            if (context) {
                const imageData = context.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < imageData.data.length; i += 100) {
                    imageData.data[i] = imageData.data[i] ^ (Math.random() * 2 | 0);
                }
                context.putImageData(imageData, 0, 0);
            }
            return originalToDataURL.apply(this, arguments);
        };
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(screen, 'width', { get: () => 1920 });
        Object.defineProperty(screen, 'height', { get: () => 1080 });
        Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
        Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
        Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
        Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
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
        human_mouse_move(page)
        human_delay(2, 4)

        # ── Tutup modal awal ──
        close_modal(page)
        human_delay(2, 3)

        # ── Upload file video ──
        file_input = find_upload_input(page)
        log(f"📤 Uploading video: {video_path}")
        human_mouse_move(page)
        human_delay(1, 2)
        file_input.set_input_files(str(video_path.resolve()))
        human_delay(8, 12)

        # ── Tunggu upload selesai ──
        wait_for_upload_complete(page)

        # ── Tutup modal setelah upload ──
        close_modal(page)
        human_delay(2, 4)

        # ── Tutup popup konten ──
        close_content_popup(page)
        human_delay(2, 3)

        # ── Tutup modal sekali lagi ──
        close_modal(page)
        human_delay(2, 3)

        # ── Screenshot sebelum caption ──
        page.screenshot(path="before_caption.png")
        log("📸 Screenshot saved: before_caption.png")

        # ── Isi caption ──
        if description:
            fill_caption(page, description)
            human_delay(2, 4)

        # ── Tutup popup setelah caption ──
        close_content_popup(page)
        human_delay(1, 2)
        close_modal(page)
        human_delay(2, 3)

        # ── Scroll seperti manusia ──
        page.mouse.wheel(0, random.randint(100, 300))
        human_delay(1, 2)
        page.mouse.wheel(0, random.randint(-100, -50))
        human_delay(1, 2)

        # ── Screenshot sebelum aksi akhir ──
        page.screenshot(path="before_post.png")
        log("📸 Screenshot saved: before_post.png")

        # ── Schedule atau Post langsung ──
        if use_schedule:
            log(f"📅 Using Schedule Post (T+{schedule_minutes} menit)...")
            result = set_schedule_post(page, schedule_minutes_from_now=schedule_minutes)

            if result:
                _, sched_time = result
                human_delay(2, 3)
                scheduled = click_schedule_button(page)

                if scheduled:
                    log("⏳ Waiting schedule confirmation...")
                    human_delay(10, 15)
                    success = wait_for_post_success(page)
                    page.screenshot(path="after_schedule.png")
                    log("📸 Screenshot saved: after_schedule.png")

                    if success:
                        log(f"✅ VIDEO SCHEDULED: {sched_time.strftime('%Y-%m-%d %H:%M')} WIB")
                    else:
                        log("⚠️ Schedule clicked but unconfirmed — check TikTok!")

                else:
                    log("❌ Schedule button not found — fallback to Post")
                    posted = click_post_button(page)
                    if posted:
                        human_delay(10, 15)
                        wait_for_post_success(page)
                        page.screenshot(path="after_post_fallback.png")
                        log("✅ VIDEO POSTED (fallback)")
                    else:
                        log("❌ Post fallback also failed")
                        page.screenshot(path="post_failed.png")

            else:
                log("❌ Schedule setup failed — fallback to Post")
                posted = click_post_button(page)
                if posted:
                    human_delay(10, 15)
                    wait_for_post_success(page)
                    page.screenshot(path="after_post_fallback.png")
                    log("✅ VIDEO POSTED (fallback)")
                else:
                    log("❌ Post fallback also failed")
                    page.screenshot(path="post_failed.png")

        else:
            # ── Post langsung ──
            posted = click_post_button(page)

            if posted:
                log("⏳ Waiting post to publish...")
                human_delay(10, 15)
                success = wait_for_post_success(page)
                page.screenshot(path="after_post.png")
                log("📸 Screenshot saved: after_post.png")

                if success:
                    log("✅ VIDEO POSTED SUCCESSFULLY")
                else:
                    log("⚠️ Post clicked but unconfirmed — check TikTok!")

            else:
                log("❌ Post button not found")
                page.screenshot(path="post_failed.png")
                log("📸 Screenshot saved: post_failed.png")

        human_delay(5, 8)
        browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cookies", default="cookies.json")
    parser.add_argument("--description", default="Video keren 🚀 #fyp #viral")
    parser.add_argument("--schedule", action="store_true", help="Gunakan schedule post")
    parser.add_argument("--schedule-minutes", type=int, default=20, help="Menit dari sekarang (min 15)")
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
        args.description,
        use_schedule=args.schedule,
        schedule_minutes=args.schedule_minutes
    )


if __name__ == "__main__":
    main()
