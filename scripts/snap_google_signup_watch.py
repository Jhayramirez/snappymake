#!/usr/bin/env python3
"""Drive Snapchat Google signup across ALL AdsPower tabs.

- Never assume page 0 is the live one
- If username is taken, retry a unique handle on that same tab
- Watch every tab in a loop for Skip / Not now / Maybe later popups
"""
from __future__ import annotations

import json
import random
import re
import string
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from app.inject.identity import girly_username

SKIP_EXACT = {
    "skip",
    "not now",
    "skip for now",
    "maybe later",
    "no thanks",
    "laktawan",
    "hindi ngayon",
}
# Do NOT match "Skip to content" / Gmail help chrome
SKIP_BLOCK = ("skip to", "skip to main", "skip to content")


def log(*a):
    print(*a, flush=True)


def active_ws() -> str:
    d = json.loads(
        urllib.request.urlopen(
            "http://local.adspower.net:50325/api/v1/browser/local-active", timeout=10
        ).read()
    )
    lst = (d.get("data") or {}).get("list") or []
    if not lst:
        raise RuntimeError("no AdsPower browser open")
    return (lst[0].get("ws") or {}).get("puppeteer") or ""


def safe_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def safe_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def dump_tabs(ctx, tag: str) -> None:
    log(f"\n--- tabs {tag} ({len(ctx.pages)}) ---")
    for i, pg in enumerate(list(ctx.pages)):
        t = safe_text(pg).replace("\n", " | ")[:180]
        log(f"  [{i}] {safe_url(pg)[:90]} :: {t}")


def snap_pages(ctx):
    out = []
    for pg in list(ctx.pages):
        u = safe_url(pg).lower()
        if "snapchat.com" in u:
            out.append(pg)
    return out


def live_signup_tab(ctx):
    """Prefer the Snapchat tab that actually has the Google-connected form / errors."""
    scored = []
    for pg in snap_pages(ctx):
        t = safe_text(pg).lower()
        score = 0
        if "connected with google" in t:
            score += 50
        if "already taken" in t:
            score += 40
        if "agree and continue" in t:
            score += 20
        if "step 1 of 2" in t:
            score += 15
        if "#firstname" and True:
            try:
                if pg.locator("#firstname").count():
                    score += 25
            except Exception:
                pass
        scored.append((score, pg))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return snap_pages(ctx)[0] if snap_pages(ctx) else None


def unique_username(first: str) -> str:
    base = girly_username(first or "bella", 2004)
    extra = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return (base + extra)[:15]


def click_exact_skip(page) -> str:
    """Click Skip / Not now only when the label is exactly that — not 'Skip to content'.

    Prefer buttons inside Snapchat modals (contact-sync overlay covers the form).
    """
    try:
        hit = page.evaluate(
            """(needles, blocked) => {
              const n = new Set(needles);
              const roots = [
                ...document.querySelectorAll('[data-testid="modal-body"], [data-testid="modal-backdrop"], [class*="Modal"]'),
                document.body,
              ];
              const seen = new Set();
              const els = [];
              for (const root of roots) {
                if (!root) continue;
                for (const el of root.querySelectorAll('button, a, [role=button]')) {
                  if (seen.has(el)) continue;
                  seen.add(el);
                  els.push(el);
                }
              }
              for (const el of els) {
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
                const low = t.toLowerCase();
                if (!low || t.length > 28) continue;
                if (blocked.some(b => low.includes(b))) continue;
                if (!n.has(low)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                el.click();
                return t;
              }
              return '';
            }""",
            list(SKIP_EXACT),
            list(SKIP_BLOCK),
        )
        return hit or ""
    except Exception:
        return ""


def fill_unique_user(page) -> str:
    first = "Bella"
    try:
        first = page.locator("#firstname").input_value() or first
    except Exception:
        pass
    user = unique_username(first)
    loc = page.locator("#username")
    loc.click(timeout=5000, force=True)
    loc.fill("")
    loc.type(user, delay=25)
    pw = page.locator("#password")
    try:
        if not (pw.input_value() or ""):
            pw.click()
            pw.type("PCGpp00##", delay=20)
    except Exception:
        pass
    return user


def submit(page) -> None:
    page.get_by_role("button", name=re.compile("Agree and Continue", re.I)).first.click(
        timeout=8000
    )


def main() -> int:
    ws = active_ws()
    log("ws", ws)
    deadline = time.time() + 90
    last_user = ""

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        dump_tabs(ctx, "start")

        page = live_signup_tab(ctx)
        if page is None:
            log("no snapchat tab")
            return 1
        log("LIVE TAB", safe_url(page), safe_text(page).replace("\n", " | ")[:200])

        blob = safe_text(page).lower()
        skipped = click_exact_skip(page)
        if skipped:
            log("POPUP on live tab first:", skipped)
            time.sleep(1.0)
            blob = safe_text(page).lower()
        if "already taken" in blob or "connected with google" in blob:
            last_user = fill_unique_user(page)
            log("typed username", last_user)
            submit(page)
            log("submitted on live tab (did not switch away)")
            time.sleep(2.5)

        # Realtime watcher: every 0.6s scan ALL tabs
        while time.time() < deadline:
            dump = False
            for pg in list(ctx.pages):
                t = safe_text(pg).lower()
                u = safe_url(pg)

                skip = click_exact_skip(pg)
                if skip:
                    log(f"POPUP {skip!r} on {u[:80]}")
                    dump = True
                    time.sleep(1.2)
                    continue

                if "snapchat.com" in u and "already taken" in t:
                    last_user = fill_unique_user(pg)
                    log("username taken → retry", last_user, "on", u[:80])
                    try:
                        submit(pg)
                    except Exception as exc:
                        log("submit warn", exc)
                    time.sleep(2.0)
                    dump = True
                    continue

                if any(
                    x in t
                    for x in (
                        "welcome to snapchat",
                        "chat now",
                        "add your best friends",
                        "web/",
                    )
                ) and "agree and continue" not in t:
                    log("LANDED", u[:100], t.replace("\n", " | ")[:180])
                    dump_tabs(ctx, "landed")
                    log("username", last_user)
                    return 0

            if dump:
                dump_tabs(ctx, "watch")
            time.sleep(0.6)

        dump_tabs(ctx, "timeout")
        log("watch timeout — browser left open")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
