#!/usr/bin/env python3
"""Refresh Gmail inbox until a Snapchat Team confirmation mail is visible."""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"
GMAIL_PROMOTIONS = "https://mail.google.com/mail/u/0/#inbox/p2"
GMAIL_SPAM = "https://mail.google.com/mail/u/0/#spam"
HIT = (
    "snapchat",
    "team snapchat",
    "snapchat team",
    "no-reply@snapchat",
    "noreply@snapchat",
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


def inbox_rows(page) -> list[str]:
    try:
        return page.evaluate(
            """() => {
              const rows = Array.from(document.querySelectorAll('tr.zA, div[role="row"]'));
              const out = [];
              for (const r of rows) {
                const t = (r.innerText || '').replace(/\\s+/g, ' ').trim();
                if (t && t.length > 8 && t.length < 400) out.push(t);
              }
              return out.slice(0, 25);
            }"""
        ) or []
    except Exception:
        return []


def body_has_snap(page) -> bool:
    try:
        t = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(k in t for k in HIT)


def row_is_hit(text: str) -> bool:
    t = (text or "").lower()
    if "snapchat" not in t:
        return False
    # Ignore Gmail chrome / ads / our own snapchat.com tabs leaking.
    if "accounts.snapchat" in t:
        return False
    return True


def find_gmail(ctx):
    gmail = None
    for pg in list(ctx.pages):
        try:
            host = urlparse(pg.url or "").netloc.lower()
        except Exception:
            continue
        if "mail.google.com" in host:
            gmail = pg
            break
    if gmail is None:
        gmail = ctx.new_page()
    try:
        gmail.bring_to_front()
    except Exception:
        pass
    return gmail


def click_hit_row(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => {
                  const rows = Array.from(document.querySelectorAll('tr.zA, div[role="row"]'));
                  for (const r of rows) {
                    const t = (r.innerText || '').toLowerCase();
                    if (t.includes('snapchat') && !t.includes('accounts.snapchat')) {
                      (r.querySelector('span.bog, span.bqe, td, div') || r).click();
                      r.click();
                      return true;
                    }
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


def click_refresh(page) -> None:
    try:
        page.evaluate(
            """() => {
              const n = document.querySelector('div[aria-label="Refresh"], div[data-tooltip="Refresh"]');
              if (n) n.click();
            }"""
        )
    except Exception:
        pass


def main() -> int:
    timeout_s = 240
    poll_s = 8
    ws = ws_url()
    deadline = time.time() + timeout_s
    urls = [GMAIL_INBOX, GMAIL_INBOX, GMAIL_PROMOTIONS, GMAIL_SPAM]
    attempt = 0

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        gmail = find_gmail(ctx)

        while time.time() < deadline:
            attempt += 1
            left = int(deadline - time.time())
            target = urls[(attempt - 1) % len(urls)]
            folder = (
                "spam"
                if "#spam" in target
                else ("promotions" if "/p2" in target else "inbox")
            )
            log(f"[{attempt}] refresh {folder} · {left}s left")
            try:
                gmail.goto(target, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                log("  goto err", type(exc).__name__, str(exc)[:120])
                gmail = find_gmail(ctx)
                continue
            gmail.wait_for_timeout(2500)
            click_refresh(gmail)
            gmail.wait_for_timeout(1800)
            try:
                title = (gmail.title() or "")[:90]
            except Exception:
                title = ""
            rows = inbox_rows(gmail)
            hits = [r for r in rows if row_is_hit(r)]
            log(" ", title)
            for r in rows[:8]:
                log("   ·", r[:140])
            if hits or body_has_snap(gmail):
                if click_hit_row(gmail):
                    gmail.wait_for_timeout(1500)
                try:
                    gmail.bring_to_front()
                except Exception:
                    pass
                log("FOUND", (hits[0] if hits else "snapchat in body")[:180])
                log("url", gmail.url)
                return 0
            time.sleep(poll_s)

        log("TIMEOUT no Snapchat confirmation in inbox/promotions/spam")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
