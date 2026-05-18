import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

VALID_SAME_SITE = {"Strict", "Lax", "None"}

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

        # --- sameSite ---
        raw_ss = str(nc.get("sameSite", "")).strip()
        if raw_ss in VALID_SAME_SITE:
            nc["sameSite"] = raw_ss
        else:
            nc["sameSite"] = SAME_SITE_MAP.get(raw_ss.lower(), "None")

        # --- domain: strip titik di depan (Netscape format) ---
        domain = nc.get("domain", "")
        domain = domain.lstrip(".")
        # Fallback jika domain kosong
        if not domain:
            domain = "tiktok.com"
        nc["domain"] = domain

        # Hapus "url" — Playwright tidak butuh ini jika domain sudah ada
        nc.pop("url", None)

        # --- path ---
        nc.setdefault("path", "/")

        # --- expires: harus int positif ---
        if "expires" in nc:
            try:
                val = int(float(nc["expires"]))
                if val > 0:
                    nc["expires"] = val
                else:
                    nc.pop("expires")
            except Exception:
                nc.pop("expires", None)

        # --- hanya field yang dikenal Playwright ---
        allowed = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
        nc = {k: v for k, v in nc.items() if k in allowed}

        normalized.append(nc)

    return normalized


def find_upload_input(page):
    """Cari input[type=file] di halaman utama atau iframe."""
    try:
        el = page.query_selector('input[type="file"]')
        if el:
            return page, el
    except Exception:
        pass

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


def set_description(page, text):
    candidates = [
        'div[data-e2e="caption-content-editable"]',
        'div[class*="caption"] div[contenteditable="true"]',
        'div[contenteditable="true"]',
        'textarea[placeholder]',
        'textarea',
    ]

    targets = [page] + (list(page.frames) if hasattr(page, "frames") else [])

    for target in targets:
        for sel in candidates:
            try:
                el = target.query_selector(sel)
                if el:
                    tag = el.evaluate("e => e.tagName").lower()
                    if tag == "div":
                        el.click()
                        time.sleep(0.3)
                        el.evaluate("e => { e.innerText = ''; }")
                        el.type(text)
                    else:
                        el.fill(text)
                    return True
            except Exception:
                continue

    return False


def click_post_button(page):
    button_texts = ["Post", "Publish", "Save as draft", "Draft"]
    targets = [page] + (list(page.frames) if hasattr(page, "frames") else [])

    for target in targets:
        for text in button_texts:
            try:
                btn = target.query_selector(f'button:has-text("{text}")')
                if btn:
                    btn.click()
                    print(f"✅ Clicked '{text}'")
                    return True
            except Exception:
                continue

    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--cookies", required=True)
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    file_arg = args.url
    file_path = Path(file_arg[len("file://"):]) if file_arg.startswith("file://") else Path(file_arg)

    if not file_path.exists():
        print(f"❌ Video tidak ditemukan: {file_path}")
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

        # Tambahkan cookies satu per satu — skip yang error, jangan gagal semua
        ok_count = 0
        skip_count = 0
        for cookie in cookies:
            try:
                context.add_cookies([cookie])
                ok_count += 1
            except Exception as e:
                skip_count += 1
                if cookie.get("name") in ("sessionid", "sid_tt", "sid_guard"):
                    print(f"⚠️ Cookie login penting gagal '{cookie['name']}': {e}")
        print(f"✅ Cookies: {ok_count} OK, {skip_count} di-skip")

        page = context.new_page()

        try:
            print("Navigating to TikTok upload page...")
            page.goto("https://www.tiktok.com/upload", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            frame_or_page, file_input = find_upload_input(page)

            if not file_input:
                print("❌ Tidak bisa menemukan input upload. Kemungkinan belum login.")
                print("   Cek: apakah cookies.json mengandung 'sessionid' atau 'sid_tt'?")
                if args.headful:
                    print("   Mode headful aktif — login manual, lalu Ctrl+C.")
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        pass
                sys.exit(1)

            print(f"✅ Input upload ditemukan. Mengupload: {file_path}")
            try:
                frame_or_page.set_input_files('input[type="file"]', str(file_path))
            except Exception as e:
                print(f"❌ Gagal set file: {e}")
                sys.exit(1)

            print("Menunggu upload selesai...")
            try:
                page.wait_for_selector('text="Upload complete"', timeout=60000)
                print("✅ Upload selesai!")
            except PlaywrightTimeoutError:
                print("⚠️ Timeout 'Upload complete', melanjutkan...")

            time.sleep(2)

            if args.description:
                if set_description(page, args.description):
                    print("✅ Caption berhasil di-set")
                else:
                    print("⚠️ Tidak bisa set caption otomatis")

            time.sleep(1)

            if not click_post_button(page):
                print("⚠️ Tidak bisa klik tombol Post otomatis")

            print("✅ Done. Cek TikTok untuk konfirmasi.")

        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
