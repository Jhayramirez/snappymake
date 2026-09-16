#!/usr/bin/env python3
"""Open an existing AdsPower profile and confirm it still has a live Gmail session.

Usage: python3 scripts/verify_gmail_session.py <profile_id> [--keep-open]

Reuses the proven start/stop machinery from gmail_batch_login_v3 so we honor the
"only one browser open" rule and crash detection.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gmail_batch_login_v3 import start_once, stop_all_browsers, stop_browser  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_gmail_session.py <profile_id> [--keep-open]")
        return 2
    pid = sys.argv[1]
    keep_open = "--keep-open" in sys.argv[2:]

    print(f"[verify] starting profile {pid} ...", flush=True)
    ws = start_once(pid)
    print(f"[verify] ws {ws}", flush=True)

    logged_in = False
    detail = ""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        try:
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(
                "https://mail.google.com/mail/u/0/?hl=en#inbox",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            page.wait_for_timeout(4000)
            url = page.url
            detail = url
            # Logged in => we stay on mail.google.com/mail. Signed out => bounced
            # to accounts.google.com / ServiceLogin / signin.
            if "mail.google.com/mail" in url and "signin" not in url and "ServiceLogin" not in url:
                # Extra signal: the compose button / search box exists when in inbox.
                try:
                    page.wait_for_selector(
                        "div[gh='cm'], input[aria-label*='Search'], input[name='q']",
                        timeout=8000,
                    )
                    logged_in = True
                except Exception:
                    logged_in = "mail.google.com/mail" in url
            else:
                logged_in = False
        finally:
            # Do NOT call browser.close() — over CDP that kills the AdsPower
            # browser. Exiting the sync_playwright context just disconnects.
            pass

    print(f"[verify] final url: {detail}", flush=True)
    print(f"[verify] RESULT: {'LOGGED_IN' if logged_in else 'NOT_LOGGED_IN'}", flush=True)

    if not keep_open:
        stop_browser(pid)
        stop_all_browsers()
        print("[verify] closed browser", flush=True)
    else:
        print("[verify] left browser OPEN for next step", flush=True)

    return 0 if logged_in else 1


if __name__ == "__main__":
    raise SystemExit(main())
