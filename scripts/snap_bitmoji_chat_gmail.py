#!/usr/bin/env python3
"""On an already-signed-up AdsPower profile: Bitmoji → Snapchat Web Chat → Gmail inbox.

Uses the original injector helpers. Does not close the AdsPower browser.
Scans all tabs (never assumes page 0).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from app.inject.snapchat import (
    _on_welcome_page,
    _snapchat_bitmoji,
    _snapchat_web_onboard,
    _welcome_tab,
)

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"


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


def dump_tabs(ctx, tag: str) -> None:
    log(f"--- tabs {tag} ({len(ctx.pages)}) ---")
    for i, pg in enumerate(list(ctx.pages)):
        try:
            u = pg.url
        except Exception:
            u = "?"
        log(f"  [{i}] {u[:110]}")


def main() -> int:
    ws = ws_url()
    notes: list[str] = []

    def on_step(msg: str) -> None:
        log("STEP", msg)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        dump_tabs(ctx, "start")
        page = ctx.pages[0]
        page = _welcome_tab(ctx, page, "bellapinky8nr7")
        if not _on_welcome_page(page, "bellapinky8nr7"):
            for pg in ctx.pages:
                if _on_welcome_page(pg, "bellapinky8nr7") or _on_welcome_page(pg):
                    page = pg
                    break
        try:
            page.bring_to_front()
        except Exception:
            pass
        log("WELCOME TAB", page.url)

        log("=== BITMOJI ===")
        ok = _snapchat_bitmoji(page, ctx, notes, on_step, gender="female")
        log("bitmoji_ok", ok, "notes", notes[-12:])
        dump_tabs(ctx, "after_bitmoji")

        log("=== SNAPCHAT WEB CHAT ===")
        page = _welcome_tab(ctx, page, "bellapinky8nr7")
        web = _snapchat_web_onboard(page, ctx, notes, on_step, add_friends=True)
        log("web tab", getattr(web, "url", None), "notes", notes[-16:])
        dump_tabs(ctx, "after_chat")

        log("=== GMAIL INBOX ===")
        gmail = None
        for pg in list(ctx.pages):
            try:
                if "mail.google.com" in (pg.url or ""):
                    gmail = pg
                    break
            except Exception:
                continue
        if gmail is None:
            gmail = ctx.new_page()
        gmail.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=45000)
        gmail.wait_for_timeout(2500)
        try:
            gmail.bring_to_front()
        except Exception:
            pass
        log("gmail", gmail.url, (gmail.title() or "")[:80])
        dump_tabs(ctx, "end")

    log("done — browser left open")
    log("ALL NOTES:")
    for n in notes:
        log(" ", n)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
