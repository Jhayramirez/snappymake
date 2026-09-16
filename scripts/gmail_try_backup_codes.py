#!/usr/bin/env python3
"""Try backup codes one-by-one. Do NOT redirect until a code is accepted."""
from __future__ import annotations

import random
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

# nodmockquorcite@gmail.com backups (spaces stripped)
CODES = [
    "80077232",
    "77518770",
    "09304080",
    "02405539",
    "08058998",
    "65537071",
    "94282926",
    "74934579",
    "88576804",
    "66656201",
]

ERROR_HINTS = (
    "wrong",
    "incorrect",
    "invalid",
    "try again",
    "didn't work",
    "didnt work",
    "mali",
    "hindi",
    "subukan",
    "enter a code",
    "maglagay",
    "backup code",
)


def human_type(page, locator, text: str) -> None:
    locator.click(timeout=15000)
    page.wait_for_timeout(random.randint(120, 280))
    try:
        locator.fill("")
    except Exception:
        pass
    page.wait_for_timeout(80)
    for ch in text:
        locator.type(ch, delay=random.randint(45, 120))
    page.wait_for_timeout(200)


def find_accounts_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    for pg in reversed(pages):
        if "accounts.google.com" in (pg.url or ""):
            return pg
    return pages[-1] if pages else None


def visible_input(page, selectors):
    for sel in selectors:
        loc = page.locator(sel)
        try:
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                if el.is_visible() and el.is_editable():
                    tp = (el.get_attribute("type") or "").lower()
                    nm = (el.get_attribute("name") or "").lower()
                    if tp == "email" or "identifier" in nm or "password" in nm or tp == "password":
                        continue
                    return el
        except Exception:
            pass
    return None


def click_next(page) -> str | None:
    for name in ("Next", "Susunod", "Continue"):
        loc = page.get_by_role("button", name=name)
        try:
            if loc.count() and loc.first.is_visible():
                # Prefer the primary Next near the form — first visible role button
                loc.first.click(timeout=8000)
                return name
        except Exception:
            pass
    for sel in ("#backupCodeNext", "#totpNext", 'button:has-text("Next")', 'button:has-text("Susunod")'):
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=8000)
                return sel
        except Exception:
            pass
    return None


def body_text(page) -> str:
    try:
        return " ".join((page.inner_text("body") or "").split())
    except Exception:
        return ""


def strong_backup_error(page) -> bool:
    """True only when Google shows an explicit wrong-code message."""
    if "challenge/bc" not in (page.url or ""):
        return False
    blob = body_text(page).lower()
    strong = (
        "wrong",
        "incorrect",
        "invalid",
        "didn't work",
        "didnt work",
        "try again",
        "mali ang",
        "hindi tama",
        "code you entered",
        "enter a valid",
        "hindi wastong",
    )
    return any(s in blob for s in strong)


def code_accepted(page) -> bool:
    """Accepted = left the backup-code challenge page."""
    url = page.url or ""
    return "challenge/bc" not in url


def main() -> int:
    ws = sys.argv[1]
    start_at = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    codes = CODES[start_at:]

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        page = find_accounts_page(browser)
        if not page:
            print("NO_PAGE")
            return 1
        try:
            page.bring_to_front()
        except Exception:
            pass
        print("start", page.url)
        print("body", body_text(page)[:400])

        # If not on backup page, stop — caller should get us here first
        if "challenge/bc" not in (page.url or ""):
            # Try to click backup option only (never Next)
            hit = page.evaluate(
                """() => {
              const needles = ['8-digit', '8 digit', 'backup code', 'ilagay ang isa sa iyong 8'];
              const nodes = Array.from(document.querySelectorAll('[role="link"],li,div[role="button"],a,div,span'));
              for (const n of nodes) {
                const t = (n.innerText || '').trim().toLowerCase();
                if (!t || t.length > 90) continue;
                if (t.includes('susunod') || t === 'next' || t.includes('sumubok')) continue;
                if (needles.some(x => t.includes(x))) {
                  (n.closest('[role="link"],[role="button"],a,li') || n).click();
                  return t.slice(0, 90);
                }
              }
              return null;
            }"""
            )
            print("clicked_option", hit)
            page.wait_for_timeout(2000)
            print("after_option", page.url)

        if "challenge/bc" not in (page.url or ""):
            print("NOT_ON_BACKUP_PAGE", page.url)
            return 2

        for i, code in enumerate(codes):
            print(f"TRY[{i}] code={code}")
            field = visible_input(
                page,
                [
                    'input[type="tel"]',
                    'input[name="Pin"]',
                    'input[name="totpPin"]',
                    'input[type="text"]',
                ],
            )
            if not field:
                print("NO_INPUT")
                return 3
            human_type(page, field, code)
            typed = field.input_value()
            print("typed_value", typed)
            if typed.replace(" ", "") != code:
                print("TYPE_MISMATCH")
            btn = click_next(page)
            print("clicked", btn)
            # Wait for Google to accept or reject — do NOT redirect ourselves
            accepted = False
            rejected = False
            for tick in range(30):  # up to ~15s
                page.wait_for_timeout(500)
                if code_accepted(page):
                    accepted = True
                    break
                if strong_backup_error(page):
                    rejected = True
                    break
            # Still on bc with no strong error after timeout → treat as bad and try next
            if not accepted and not rejected and "challenge/bc" in (page.url or ""):
                rejected = True
                print("timeout_still_on_bc")
            print("url_after", page.url)
            print("body_after", body_text(page)[:350])
            if accepted:
                print("CODE_OK", code)
                print("STOPPED_HERE_NO_REDIRECT")
                return 0
            print("CODE_BAD", code, "rejected" if rejected else "unknown")
            # clear for next attempt
            field = visible_input(
                page,
                [
                    'input[type="tel"]',
                    'input[name="Pin"]',
                    'input[name="totpPin"]',
                    'input[type="text"]',
                ],
            )
            if field:
                try:
                    field.fill("")
                except Exception:
                    pass
            page.wait_for_timeout(400)

        print("ALL_CODES_FAILED")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
