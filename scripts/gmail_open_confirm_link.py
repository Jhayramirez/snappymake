#!/usr/bin/env python3
"""Open the Snapchat CONFIRM EMAIL href (not the notMyAccount link)."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

CONFIRM = (
    "https://accounts.snapchat.com/accounts/confirm_email"
    "?n=bM4PbSW5TPC87GtZZUBFIg&source=welcome"
)


def log(*a):
    print(*a, flush=True)


def ws_url() -> str:
    d = json.loads(
        urllib.request.urlopen(
            "http://local.adspower.net:50325/api/v1/browser/local-active", timeout=10
        ).read()
    )
    lst = (d.get("data") or {}).get("list") or []
    if not lst:
        raise RuntimeError("no AdsPower browser open")
    return (lst[0].get("ws") or {}).get("puppeteer") or ""


def main() -> int:
    ws = ws_url()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        log("goto", CONFIRM)
        page.goto(CONFIRM, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            body = " ".join((page.inner_text("body") or "").split())[:500]
        except Exception:
            body = ""
        log("url", page.url)
        log("title", (page.title() or "")[:120])
        log("body", body)
        t = body.lower()
        if any(
            n in t
            for n in (
                "email confirmed",
                "email has been confirmed",
                "email verified",
                "verified your email",
                "thanks for confirming",
                "successfully confirmed",
                "your email is confirmed",
                "email address confirmed",
                "you're all set",
                "you are all set",
                "already confirmed",
                "already verified",
            )
        ):
            log("CONFIRMED")
            return 0
        log("OPENED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
