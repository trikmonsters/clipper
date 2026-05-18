import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Nilai sameSite yang valid di Playwright
VALID_SAME_SITE = {"Strict", "Lax", "None"}

# Map nilai tidak standar dari browser export ke nilai Playwright
SAME_SITE_MAP = {
    "unspecified": "None",
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
    "": "None",
}


def load_cookies(path):
    if not Path(path).exists():
        raise FileNotFoundError(f"Cookies file not found: {path}")

    raw = Path(path).read_text(encoding="utf-8").strip()

    cookies = json.loads(raw)

    if isinstance(cookies, dict) and "cookies" in cookies:
        cookies = cookies["cookies"]

    if not isinstance(cookies, list):
        raise ValueError("Cookies JSON must be a list of cookie objects")

    normalized = []
    for c in cookies:
        nc = dict(c)

        if "name" not in nc or "value" not in nc:
            continue

        # Normalisasi sameSite — ini penyebab utama error Playwright
        raw_ss = str(nc.get("sameSite", "")).strip()
        if raw_ss in VALID_SAME_SITE:
            nc["sameSite"] = raw_ss
        else:
            nc["sameSite"] = SAME_SITE_MAP.get(raw_ss.lower(), "None")

        # Strip titik di depan domain (format Netscape)
        if "domain" in nc:
            nc["domain"] = nc["domain"].lstrip(".")

        # Tambahkan url dari domain jika belum ada
        if "url" not in nc and "domain" in nc:
            nc["url"] = f"https://{nc['domain']}"

        # Pastikan path ada
        nc.setdefault("path", "/")

        # Normalisasi expires ke int
        if "expires" in nc:
            try:
                nc["expires"] = int(float(nc["expires"]))
                if nc["expires"] < 0:
                    nc.pop("expires", None)
            except Exception:
                nc.pop("expires", None)

        # Hapus field yang tidak dikenal Playwright
        allowed_fields = {
            "name", "value", "domain", "path", "expires",
            "httpOnly", "secure", "sameSite", "url"
        }
        nc = {k: v for k, v in nc.items() if k in allowed_fields}

        normalized.append(nc)

    return normalized


def find_upload_input(page):
    """Cari input file di halaman utama atau di dalam iframe."""
    # Coba di halaman utama dulu
    try:
        el = page.query_selector('input[type="file"]')
        if el:
            return page, el
    except Exception:
        pass

    # TikTok upload menggunakan iframe — cari di sana
    try:
        for frame in page.frames:
            try:
                el = frame.query_selector('input[type="file"]')
                if el:
                    return frame, el
            except Exception:
                continue
    except Exception:
        pass

    return None, None


def set_description(page_or_frame, text):
    candidates = [
        'div[data-e2e="caption-content-editable"]',
        'div[class*="caption"] div[contenteditable="true"]',
        'div[contenteditable="true"]',
        'textarea[placeholder]',
        'textarea',
    ]

    for sel in candidates:
        try:
            el = page_or_frame.query_selector(sel)
            if el:
                tag = el.evaluate("e => e.tagName")
                if tag and tag.lower() == "div":
                    el.click()
                    time.sleep(0.3)
                    el.evaluate("e => { e.innerText = ''; }")
                    el.type(text)
                else:
                    el.fill(text)
                return True
        except Exception:
            continue

    # Coba juga di setiap frame
    try:
        for frame in page_or_frame.frames if hasattr(page_or_frame, "frames") else []:
            for sel in candidates:
                try:
                    el = frame.query_selector(sel)
                    if el:
                        tag = el.evaluate("e => e.tagName")
                        if tag and tag.lower() == "div":
                            el.click()
                            time.sleep(0.3)
                            el.evaluate("e => { e.innerText = ''; }")
                            el.type(text)
                        else:
                            el.fill(text)
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    return False


def click_post_button(page):
    """Klik tombol Post/Publish di halaman utama atau iframe."""
    button_texts = ["Post", "Publish", "Save as draft", "Draft"]

    # Coba halaman utama
    for text in button_texts:
        try:
            btn = page.query_selector(f'button:has-text("{text}")')
            if btn:
                btn.click()
                print(f"✅ Clicked button '{text}'")
                return True
        except Exception:
            continue

    # Coba di setiap frame
    try:
        for frame in page.frames:
            for text in button_texts:
                try:
                    btn = frame.query_selector(f'button:has-text("{text}")')
                    if btn:
                        btn.click()
                        print(f"✅ Clicked button '{text}' (in frame)")
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    return False


def main():
    parser = argparse.ArgumentParser(description="Upload video to TikTok using Playwright")
    parser.add_argument("--url", required=True, help="file:// URL atau path absolut ke video")
    parser.add_argument("--description", default="", help="Caption video")
    parser.add_argument("--cookies", required=True, help="Path ke cookies.json")
    parser.add_argument("--headful", action="store_true", help="Jalankan browser dengan UI")

    args = parser.parse_args()

    # Normalisasi path file
    file_arg = args.url
    if file_arg.startswith("file://"):
        file_path = Path(file_arg[len("file://"):])
    else:
        file_path = Path(file_arg)

    if not file_path.exists():
        print(f"❌ Video file tidak ditemukan: {file_path}")
        sys.exit(1)

    try:
        cookies = load_cookies(args.cookies)
        print(f"✅ Loaded {len(cookies)} cookies dari {args.cookies}")
    except Exception as e:
        print(f"❌ Gagal load cookies: {e}")
        sys.exit(1)

    print("Starting Playwright browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        if cookies:
            try:
                context.add_cookies(cookies)
                print(f"✅ Cookies berhasil ditambahkan ke browser context")
            except Exception as e:
                print(f"❌ Gagal menambahkan cookies: {e}")
                print("   Pastikan cookies.json valid dan berasal dari sesi login TikTok yang aktif.")
                browser.close()
                sys.exit(1)

        page = context.new_page()

        try:
            print("Navigating to TikTok upload page...")
            page.goto("https://www.tiktok.com/upload", wait_until="domcontentloaded", timeout=30000)

            # Tunggu halaman load penuh
            time.sleep(4)

            # Cek apakah sudah login (cari elemen yang hanya muncul saat login)
            frame_or_page, file_input = find_upload_input(page)

            if not file_input:
                print("❌ Tidak bisa menemukan input upload. Kemungkinan belum login.")
                print("   Periksa:")
                print("   1. Cookies sudah benar dan belum expired")
                print("   2. Cookie domain adalah 'tiktok.com' (bukan '.tiktok.com')")
                print("   3. Ada cookie 'sessionid' atau 'sid_tt' di cookies.json")
                if args.headful:
                    print("   Mode headful: login manual lalu Ctrl+C untuk stop.")
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        pass
                else:
                    sys.exit(1)

            print(f"✅ Input upload ditemukan. Mengupload: {file_path}")

            try:
                frame_or_page.set_input_files('input[type="file"]', str(file_path))
            except Exception as e:
                print(f"❌ Gagal set file input: {e}")
                sys.exit(1)

            print("File dipilih, menunggu upload selesai...")
            try:
                page.wait_for_selector('text="Upload complete"', timeout=60000)
                print("✅ Upload selesai!")
            except PlaywrightTimeoutError:
                print("⚠️ Timeout menunggu 'Upload complete', melanjutkan...")

            # Tunggu UI setelah upload
            time.sleep(2)

            if args.description:
                ok = set_description(page, args.description)
                if ok:
                    print("✅ Description/caption berhasil di-set")
                else:
                    print("⚠️ Tidak bisa set description otomatis.")

            time.sleep(1)

            if not click_post_button(page):
                print("⚠️ Tidak bisa klik tombol Post otomatis.")

            print("✅ Done. Cek TikTok untuk konfirmasi draft/post.")

        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
