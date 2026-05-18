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


# ─────────────────────────────────────────────
# Video preparation
# ─────────────────────────────────────────────

def download_video(url: str, output: Path):
    log(f"⬇️  Mendownload video dari: {url}")
    response = requests.get(
        url, stream=True, timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    response.raise_for_status()
    with open(output, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    size_mb = output.stat().st_size / (1024 * 1024)
    log(f"✅ Video downloaded ({size_mb:.1f} MB)")


def prepare_video(source: str):
    if source.startswith("http://") or source.startswith("https://"):
        download_video(source, VIDEO_FILE)
    elif source.startswith("file://"):
        path = Path(source.replace("file://", ""))
        if not path.exists():
            raise FileNotFoundError(f"File tidak ditemukan: {path}")
        shutil.copy2(path, VIDEO_FILE)
        log(f"✅ Local video copied ({VIDEO_FILE.stat().st_size / 1048576:.1f} MB)")
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"File tidak ditemukan: {path}")
        if path.resolve() != VIDEO_FILE.resolve():
            shutil.copy2(path, VIDEO_FILE)
        log(f"✅ Video ready ({VIDEO_FILE.stat().st_size / 1048576:.1f} MB)")


# ─────────────────────────────────────────────
# Cookie parsing
# ─────────────────────────────────────────────

def parse_cookies(cookies_path: str) -> list:
    with open(cookies_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content.startswith("["):
        raise ValueError("❌ Gunakan format cookies JSON array")

    raw = json.loads(content)
    cookies = []

    for c in raw:
        if "name" not in c or "value" not in c:
            continue

        # Domain: strip titik Netscape, fallback tiktok.com
        domain = c.get("domain", "").lstrip(".")
        if not domain:
            domain = "tiktok.com"

        # expires: ambil expirationDate (format Chrome extension) atau expires
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

        # expires hanya jika positif
        if expiry > 0:
            cookie["expires"] = expiry

        # sameSite hanya jika ada dan valid — jangan set jika tidak ada
        raw_ss = str(c.get("sameSite", "")).strip()
        if raw_ss in VALID_SAME_SITE:
            cookie["sameSite"] = raw_ss
        elif raw_ss:
            mapped = SAME_SITE_MAP.get(raw_ss.lower())
            if mapped:
                cookie["sameSite"] = mapped
        # Jika tidak ada / tidak dikenali → tidak di-set (Playwright pakai default)

        cookies.append(cookie)

    log(f"🍪 {len(cookies)} cookies dimuat")
    return cookies


# ─────────────────────────────────────────────
# Browser helpers
# ─────────────────────────────────────────────

def goto_with_retry(page, url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            log(f"🌐 Membuka halaman (percobaan {attempt}/{retries})...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            return
        except PlaywrightTimeout:
            log(f"⚠️  Timeout percobaan {attempt}")
            time.sleep(3)
    raise RuntimeError("❌ Gagal membuka halaman setelah beberapa percobaan")


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
                log(f"✅ Modal ditutup: {sel}")
                time.sleep(1)
                return True
        except Exception:
            continue
    return False


def handle_content_check_popup(page):
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
        return False

    cancel_selectors = [
        "button:has-text('Cancel')",
        "button:has-text('Skip')",
        "button:has-text('Not now')",
    ]
    for sel in cancel_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click(force=True)
                log(f"✅ Popup content check ditutup: {sel}")
                time.sleep(2)
                return True
        except Exception:
            continue

    page.keyboard.press("Escape")
    time.sleep(2)
    return True


def find_upload_input(page):
    log("🔍 Mencari input upload...")
    try:
        file_input = page.locator("input[type='file']").first
        file_input.wait_for(state="attached", timeout=15000)
        log("✅ File input ditemukan")
        return file_input
    except Exception as e:
        raise RuntimeError(f"❌ Input upload tidak ditemukan: {e}")


def wait_for_upload_complete(page, timeout=180):
    log("⏳ Menunggu upload selesai...")
    start = time.time()
    progress_selectors = [
        "[class*='progress']",
        "[class*='uploading']",
        "[class*='Progress']",
    ]
    while time.time() - start < timeout:
        uploading = any(
            page.locator(sel).count() > 0
            for sel in progress_selectors
        )
        if not uploading:
            log("✅ Upload selesai")
            break
        time.sleep(2)
    time.sleep(5)


def fill_caption(page, text: str):
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
            for word in text.split():
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
            log(f"✅ Caption diisi: {box.inner_text()[:60]}...")
            return True
        except Exception as e:
            log(f"⚠️  Caption selector gagal ({selector}): {e}")
            continue
    log("⚠️  Tidak bisa mengisi caption otomatis")
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
            if btn.get_attribute("disabled") is not None:
                log("⏳ Tombol Draft masih disabled, tunggu sebentar...")
                time.sleep(5)
            btn.scroll_into_view_if_needed()
            time.sleep(1)
            try:
                btn.click(timeout=5000)
            except Exception:
                btn.click(force=True)
            log(f"✅ Draft diklik via: {sel}")
            return True
        except Exception:
            continue
    return False


# ─────────────────────────────────────────────
# Main upload flow
# ─────────────────────────────────────────────

def upload_to_tiktok(video_path: Path, cookies_path: str, description: str = "", headless: bool = True):
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
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Load cookies satu per satu — skip yang error
        cookies = parse_cookies(cookies_path)
        ok, skip = 0, 0
        for cookie in cookies:
            try:
                context.add_cookies([cookie])
                ok += 1
            except Exception as e:
                skip += 1
                if cookie.get("name") in ("sessionid", "sid_tt", "sid_guard"):
                    log(f"⚠️  Cookie login penting gagal '{cookie['name']}': {e}")
        log(f"🍪 Cookies: {ok} OK, {skip} di-skip")

        page = context.new_page()

        # Buka halaman upload
        goto_with_retry(page, TIKTOK_UPLOAD_URL)

        # Cek login via URL — jika redirect ke /login berarti cookies invalid
        current_url = page.url.lower()
        if "login" in current_url:
            log("❌ Redirect ke halaman login — cookies tidak valid atau sudah expired!")
            log("   Ekspor ulang cookies dari browser saat sudah login di TikTok.")
            browser.close()
            sys.exit(1)

        log("✅ Login berhasil, halaman upload terbuka")

        # Tutup modal awal jika ada
        close_modal(page)

        # Upload file
        file_input = find_upload_input(page)
        log(f"📤 Mengupload: {video_path.resolve()}")
        file_input.set_input_files(str(video_path.resolve()))
        time.sleep(5)

        # Tunggu proses upload
        wait_for_upload_complete(page)

        # Tutup popup setelah upload
        close_modal(page)
        handle_content_check_popup(page)

        # Isi caption
        if description:
            fill_caption(page, description)
            time.sleep(2)

        # Klik Draft
        time.sleep(3)
        drafted = click_draft_button(page)

        if drafted:
            log("⏳ Menunggu proses save draft...")
            time.sleep(10)
            log("✅ VIDEO BERHASIL DISIMPAN KE DRAFT TIKTOK!")
        else:
            log("❌ Tombol Draft tidak ditemukan — cek halaman TikTok secara manual")

        browser.close()


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Upload video ke TikTok Draft")
    parser.add_argument("--url", required=True,
                        help="URL video atau path file (http://, https://, file://, atau path biasa)")
    parser.add_argument("--cookies", default="cookies.json",
                        help="Path file cookies JSON")
    parser.add_argument("--description", default="Video keren 🚀 #fyp #viral",
                        help="Caption/deskripsi video")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Jalankan dalam mode headless (default: True)")
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
        args.headless,
    )


if __name__ == "__main__":
    main()
