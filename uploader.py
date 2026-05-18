import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def load_cookies(path):
    if not Path(path).exists():
        raise FileNotFoundError(f"Cookies file not found: {path}")

    raw = Path(path).read_text(encoding="utf-8").strip()

    # If the secret was stored as a JSON string literal (e.g. '[]' or '{"k": "v"}'), json.loads will work.
    # If it's a single-line export from a browser extension, it should also be valid JSON.
    cookies = json.loads(raw)

    # Playwright expects a list of cookie dicts. If the stored value is a dict with a "cookies" key,
    # support that format as well.
    if isinstance(cookies, dict) and "cookies" in cookies:
        cookies = cookies["cookies"]

    if not isinstance(cookies, list):
        raise ValueError("Cookies JSON must be a list of cookie objects")

    # Ensure required fields and normalize
    normalized = []
    for c in cookies:
        nc = dict(c)
        # Playwright requires name and value
        if "name" not in nc or "value" not in nc:
            continue
        # Ensure path
        nc.setdefault("path", "/")
        # Ensure expires is int if present
        if "expires" in nc:
            try:
                nc["expires"] = int(nc["expires"])
            except Exception:
                nc.pop("expires", None)
        normalized.append(nc)
    return normalized


def set_description(page, text):
    # Try a few known selectors for TikTok's caption input.
    candidates = [
        'textarea[data-e2e="caption-textarea"]',
        'textarea[placeholder] ',
        'textarea',
        'div[role="textbox"]',
        'div[contenteditable="true"]',
    ]

    for sel in candidates:
        try:
            el = page.query_selector(sel)
            if el:
                # Some editors are contenteditable divs
                tag = el.evaluate("e => e.tagName")
                if tag and tag.lower() == "div":
                    el.fill("")
                    el.type(text)
                else:
                    el.fill(text)
                return True
        except Exception:
            continue
    return False


def main():
    parser = argparse.ArgumentParser(description="Upload video to TikTok (best-effort) using Playwright")
    parser.add_argument("--url", required=True, help="file:// URL to local video or absolute path")
    parser.add_argument("--description", default="", help="Video caption/description")
    parser.add_argument("--cookies", required=True, help="Path to cookies.json file (Playwright-style list of cookies)")
    parser.add_argument("--headful", action="store_true", help="Run browser with UI visible (useful for debugging)")

    args = parser.parse_args()

    # Normalize file path
    file_arg = args.url
    if file_arg.startswith("file://"):
        file_path = Path(file_arg[len("file://"):])
    else:
        file_path = Path(file_arg)

    if not file_path.exists():
        print(f"❌ Video file not found: {file_path}")
        sys.exit(1)

    try:
        cookies = load_cookies(args.cookies)
    except Exception as e:
        print(f"❌ Failed to load cookies: {e}")
        sys.exit(1)

    print("Starting Playwright browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        context = browser.new_context()

        if cookies:
            try:
                # Try to add cookies directly. Playwright requires domain or url for each cookie.
                # If cookie entries lack a url, try to add a url from the domain.
                for c in cookies:
                    if "url" not in c and "domain" in c:
                        # build https://<domain> as url
                        domain = c["domain"].lstrip(".")
                        c.setdefault("url", f"https://{domain}")
                context.add_cookies(cookies)
                print(f"✅ Loaded {len(cookies)} cookies into browser context")
            except Exception as e:
                print(f"⚠️ Could not add cookies directly: {e}")

        page = context.new_page()

        try:
            print("Navigating to TikTok upload page...")
            page.goto("https://www.tiktok.com/upload", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            # Check login state by searching for the file input
            file_input = None
            try:
                file_input = page.query_selector('input[type="file"]')
            except Exception:
                file_input = None

            if not file_input:
                print("⚠️ Could not find the upload file input. You may not be logged in. Check your cookies.")
                # Still give a chance: pause and let user inspect if headful
                if args.headful:
                    print("Running in headful mode; please login manually and press Ctrl+C to stop when done.")
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        pass
                else:
                    sys.exit(1)

            print(f"Uploading file: {file_path}")
            # Set the file to the file input
            try:
                page.set_input_files('input[type="file"]', str(file_path))
            except Exception as e:
                print(f"❌ Failed to set input files: {e}")
                sys.exit(1)

            print("File selected, waiting for upload to finish (this may take some time)...")
            # Wait some time for upload UI to appear/update
            try:
                # Wait for a common progress or title element; this is best-effort
                page.wait_for_selector('text="Upload complete"', timeout=45000)
            except PlaywrightTimeoutError:
                # Not fatal; continue to try to set description and post
                pass

            if args.description:
                ok = set_description(page, args.description)
                if ok:
                    print("✅ Description set")
                else:
                    print("⚠️ Could not set description automatically. You may need to run with --headful to fill it manually.")

            # Try to click Post/Publish button
            clicked = False
            try:
                # Try common button texts
                for text in ("Post", "Publish", "Save as draft", "Draft"):
                    try:
                        btn = page.query_selector(f'button:has-text("{text}")')
                        if btn:
                            btn.click()
                            print(f"Clicked button '{text}'")
                            clicked = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            if not clicked:
                print("⚠️ Could not automatically click the publish/post button. Run with --headful to complete the upload manually.")

            print("Done. If no errors were shown, check TikTok to confirm the draft/post.")

        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
