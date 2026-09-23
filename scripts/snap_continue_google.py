#!/usr/bin/env python3
"""Connect to the already-open AdsPower profile and click Snapchat → Continue with Google.

Does NOT close the AdsPower browser.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SNAP_SIGNUP = "https://accounts.snapchat.com/v2/signup"
SNAP_WELCOME = "https://accounts.snapchat.com/v2/welcome"
OUT = Path("/tmp/snappy_google")
OUT.mkdir(parents=True, exist_ok=True)


def log(*a):
    print(*a, flush=True)


def shot(page, name: str):
    path = OUT / f"{int(time.time())}_{name}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        log("  shot", path)
    except Exception as exc:
        log("  shot warn", exc)


def dump(page):
    try:
        log("  url", page.url)
        log("  title", page.title())
        blob = page.evaluate(
            """() => {
              const texts = [...document.querySelectorAll('button, a, [role=button], span')]
                .slice(0, 80)
                .map(el => (el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ').slice(0,80))
                .filter(Boolean);
              return texts.slice(0, 40);
            }"""
        )
        for t in blob:
            log("   ·", t)
    except Exception as exc:
        log("  dump warn", exc)


def click_label(page, labels: list[str], timeout=4000) -> bool:
    lower = [x.lower() for x in labels]
    # buttons / links / role=button
    locators = [
        page.get_by_role("button", name=lab, exact=False)
        for lab in labels
    ] + [
        page.get_by_role("link", name=lab, exact=False)
        for lab in labels
    ]
    for loc in locators:
        try:
            el = loc.first
            if el.count() and el.is_visible(timeout=800):
                el.click(timeout=timeout)
                return True
        except Exception:
            continue
    # fallback: any visible element whose text matches
    try:
        clicked = page.evaluate(
            """(needles) => {
              const n = needles.map(s => s.toLowerCase());
              const els = [...document.querySelectorAll('button, a, [role=button], div, span')];
              for (const el of els) {
                const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ');
                if (!t || t.length > 80) continue;
                const low = t.toLowerCase();
                if (n.some(x => low === x || low.includes(x))) {
                  const r = el.getBoundingClientRect();
                  if (r.width > 8 && r.height > 8) { el.click(); return t; }
                }
              }
              return '';
            }""",
            lower,
        )
        if clicked:
            log("  clicked via text:", clicked)
            return True
    except Exception:
        pass
    return False


def main() -> int:
    ws = sys.argv[1] if len(sys.argv) > 1 else ""
    if not ws:
        log("usage: snap_continue_google.py <ws://...>")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("connected pages", len(ctx.pages), "url", page.url)
        shot(page, "00_before")

        # Go to welcome first — that's where "Continue with Google" usually lives.
        # Signup form is the email/password path.
        for url in (SNAP_WELCOME, SNAP_SIGNUP):
            log("goto", url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                log("  goto warn", exc)
            page.wait_for_timeout(2500)
            dump(page)
            shot(page, "01_landed")
            if click_label(
                page,
                [
                    "Continue with Google",
                    "Sign up with Google",
                    "Sign in with Google",
                    "Google",
                    "Continue with google",
                ],
            ):
                log("clicked Google on", page.url)
                break
        else:
            # still try Google on current page
            log("no google click yet, scanning once more")
            dump(page)

        page.wait_for_timeout(4000)
        # Google OAuth may open a popup
        pages = ctx.pages
        log("pages after click", len(pages), [pg.url for pg in pages])
        target = pages[-1] if pages else page
        dump(target)
        shot(target, "02_after_google")

        # Account chooser: click the Gmail we just logged in, or the first account.
        for lab in (
            "a67784598@gmail.com",
            "@gmail.com",
            "Continue",
            "Allow",
            "Confirm",
            "Next",
        ):
            if click_label(target, [lab]):
                log("clicked", lab)
                target.wait_for_timeout(2500)
                dump(target)
                shot(target, "03_chooser")

        log("FINAL url", target.url)
        shot(target, "99_final")
        # disconnect only — do not browser.close()
    log("disconnected (browser left open)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
