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

VALID_SAME_SITE = {"Strict", "Lax", "None"}
SAME_SITE_MAP = {
    "unspecified": "None",
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
    "": "None",
}


def log(msg: str):
    print(f"[TikTok Uploader] {msg}", flush=True)


def download_video(url: str, output: Path):
    log(f"⬇️ Mendownload video dari: {url}")
    response = requests.get(
        url, stream=True, timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    response.raise_for_status()
    with open(output, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    size_mb = output.stat().st_size / (1024 * 1024)
    log(f"✅ Video berhasil didownload ({size_mb:.1f} MB)")


def prepare_video(source):
    if source.startswith("http://") or source.startswith("https://"):
        download_video(source, VIDEO_FILE)
    elif source.startswith("file://"):
        path = Path(source.replace("file://", ""))
        if not path.exists():
            raise Exception(f"❌ File tidak ditemukan: {path}")
        shutil.copy2(path, VIDEO_FILE)
        size_mb = VIDEO_FILE.stat().st_size / (1024 * 1024)
        log(f"✅ Local video loaded ({size_mb:.1f} MB)")
    else:
        path = Path(source)
        if not path.exists():
            raise Exception(f"❌ File tidak ditemukan: {path}")
        if path.resolve() != VIDEO_FILE.resolve():
            shutil.copy2(path, VIDEO_FILE)
            size_mb = VIDEO_FILE.stat().st_size / (1024 * 1024)
            log(f"✅ Video loaded from: {path} ({size_mb:.1f} MB)")
        else:
            size_mb = VIDEO_FILE.stat().st_size / (1024 * 1024)
            log(f"✅ Video already ready ({size_mb:.1f} MB)")


def parse_cookies(cookies_path: str) -> list:
    with open(cookies_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content.startswith("["):
        raise Exception("❌ Gunakan format cookies JSON array")

    raw = json.loads(content)
    cookies = []

    for c in raw:
        if "name" not in c or "value" not in c:
            continue

        domain = c.get("domain", "").lstrip(".")
        if not domain:
            domain = "tiktok.com"

        expiry = c.get("expirationDate") or c.get("expires") or -1
        try:
            expiry = int(float(expiry))
        except Exception:
            expiry = -1

        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": domain,
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }

        if expiry > 0:
            cookie["expires"] = expiry

        raw_ss = str(c.get("sameSite", "")).strip()
        if raw_ss in VALID_SAME_SITE:
            cookie["sameSite"] = raw_ss
        elif raw_ss:
            mapped = SAME_SITE_MAP.get(raw_ss.lower())
            if mapped:
                cookie["sameSite"] = mapped

        cookies.append(cookie)

    log(f"🍪 JSON cookies dimuat ({len(cookies)})")
    return cookies


def goto_with_retry(page, url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            log(f"🌐 Membuka halaman ({attempt}/{retries})")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            return
        except PlaywrightTimeout:
            log(f"⚠️ Timeout percobaan {attempt}")
            time.sleep(3)
    raise Exception("❌ Gagal membuka halaman")


def close_modal(page):
    close_selectors = [
        "[data-e2e='modal-close-inner-button']",
        "button[aria-label='Close']",
        "button:has-text('Close')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Cancel')",
    ]
    for sel in close_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(force=True)
                log(f"✅ Modal ditutup via: {sel}")
                time.sleep(1)
                return True
        except Exception:
            continue
    return False


def handle_content_check_popup(page):
    try:
        log("🔍 Memeriksa popup content check...")
        popup_selectors = [
            "text=Turn on automatic content checks",
            "text=Music copyright check",
            "text=Content check lite",
        ]
        popup_found = False
        for sel in popup_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=3000):
                    popup_found = True
                    break
            except Exception:
                continue
        if not popup_found:
            log("✅ Tidak ada popup content check")
            return False
        for sel in ["button:has-text('Cancel')", "button:has-text('Skip')", "button:has-text('Not now')"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3000):
                    btn.click(force=True)
                    log(f"✅ Popup ditutup via: {sel}")
                    time.sleep(2)
                    return True
            except Exception:
                continue
        page.keyboard.press("Escape")
        log("✅ Popup ditutup via Escape")
        time.sleep(2)
        return True
    except Exception as e:
        log(f"⚠️ Error popup: {e}")
        return False


def find_upload_input(page):
    """
    Cari input[type=file] di:
    1. Halaman utama
    2. Semua iframe via frame_locator (cara resmi Playwright)
    3. Semua frame object langsung
    """
    log("🔍 Mencari input upload...")

    # 1. Halaman utama
    try:
        el = page.locator("input[type='file']").first
        el.wait_for(state="attached", timeout=8000)
        log("✅ File input ditemukan di halaman utama")
        return el
    except Exception:
        pass

    # 2. Cari via frame_locator — cara resmi Playwright untuk iframe
    # TikTok Studio embed editor dalam iframe dengan src berisi 'tiktok'
    iframe_selectors = [
        "iframe",
        "iframe[src*='tiktok']",
        "iframe[src*='upload']",
        "iframe[src*='studio']",
    ]
    for iframe_sel in iframe_selectors:
        try:
            fl = page.frame_locator(iframe_sel)
            el = fl.locator("input[type='file']").first
            el.wait_for(state="attached", timeout=5000)
            log(f"✅ File input ditemukan via frame_locator({iframe_sel})")
            return el
        except Exception:
            continue

    # 3. Fallback: cari lewat semua frame object
    log("🔍 Mencari di semua frame object...")
    for i, frame in enumerate(page.frames):
        try:
            frame_url = frame.url
            log(f"   Frame {i}: {frame_url[:80]}")
            el = frame.locator("input[type='file']").first
            el.wait_for(state="attached", timeout=4000)
            log(f"✅ File input ditemukan di frame {i}: {frame_url[:60]}")
            return el
        except Exception:
            continue

    raise Exception("❌ Input upload tidak ditemukan di halaman maupun iframe")


def wait_for_upload_complete(page, timeout=180):
    log("⏳ Menunggu upload selesai...")
    start = time.time()
    progress_selectors = [
        "[class*='progress']",
        "[class*='uploading']",
        "[class*='Progress']",
    ]
    while time.time() - start < timeout:
        uploading = False
        for sel in progress_selectors:
            try:
                if page.locator(sel).count() > 0:
                    uploading = True
                    break
            except Exception:
                pass
        if not uploading:
            log("✅ Upload selesai")
            break
        time.sleep(2)
    time.sleep(5)


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
            words = text.split()
            for word in words:
                if word.startswith("#"):
                    page.keyboard.press("Space")
                    box.press_sequentially(word, delay=120)
                    time.sleep(2)
                    page.keyboard.press("Enter")
                    time.sleep(0.5)
                else:
                    box.press_sequentially(word + " ", delay=80)
                    time.sleep(0.2)
            time.sleep(2)
            current_text = box.inner_text()
            log(f"📝 Caption result: {current_text}")
            log("✅ Caption filled")
            return True
        except Exception as e:
            log(f"⚠️ Caption failed: {e}")
            continue
    return False


def click_draft_button(page):
    log("📂 Mencari tombol Draft...")
    selectors = [
        "[data-e2e='save-draft-button']",
        "button:has-text('Draft')",
        "button:has-text('Save draft')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if not btn.is_visible(timeout=3000):
                continue
            disabled = btn.get_attribute("disabled")
            if disabled is not None:
                log("⏳ Tombol Draft masih disabled")
                continue
            btn.scroll_into_view_if_needed()
            time.sleep(1)
            try:
                btn.click(timeout=5000)
            except Exception:
                btn.click(force=True)
            log(f"✅ Tombol Draft diklik via: {sel}")
            return True
        except Exception:
            continue
    return False


def upload_to_tiktok(video_path, cookies_path, description="", headless=True):
    with sync_playwright() as p:
        log("🌐 Membuka browser...")
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        log(f"🍪 Memuat cookies: {cookies_path}")
        cookies = parse_cookies(cookies_path)

        ok, skip = 0, 0
        for cookie in cookies:
            try:
                context.add_cookies([cookie])
                ok += 1
            except Exception as e:
                skip += 1
                if cookie.get("name") in ("sessionid", "sid_tt", "sid_guard"):
                    log(f"⚠️ Cookie login penting gagal '{cookie['name']}': {e}")
        log(f"🍪 Cookies: {ok} OK, {skip} di-skip")

        page = context.new_page()
        goto_with_retry(page, TIKTOK_UPLOAD_URL)

        if "login" in page.url.lower():
            raise Exception("❌ Login gagal, cookies invalid")

        log("✅ Login berhasil")
        close_modal(page)

        file_input = find_upload_input(page)
        log(f"📤 Uploading: {video_path}")
        file_input.set_input_files(str(video_path.resolve()))
        time.sleep(5)

        wait_for_upload_complete(page)
        close_modal(page)
        handle_content_check_popup(page)

        if description:
            fill_caption(page, description)
            time.sleep(2)

        time.sleep(3)
        drafted = click_draft_button(page)

        if drafted:
            log("⏳ Menunggu save draft...")
            time.sleep(10)
            log("✅ VIDEO BERHASIL DISIMPAN KE DRAFT")
        else:
            log("❌ Tombol Draft gagal ditemukan")

        browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Upload video ke TikTok dari URL atau file lokal"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--cookies", default="cookies.json")
    parser.add_argument("--description", default="Video keren 🚀 #fyp #viral")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    if not Path(args.cookies).exists():
        log(f"❌ Cookies tidak ditemukan: {args.cookies}")
        sys.exit(1)

    try:
        prepare_video(args.url)
    except Exception as e:
        log(f"❌ Gagal menyiapkan video: {e}")
        sys.exit(1)

    upload_to_tiktok(
        VIDEO_FILE,
        args.cookies,
        args.description,
        args.headless
    )


if __name__ == "__main__":
    main()
