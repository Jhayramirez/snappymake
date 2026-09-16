#!/usr/bin/env python3
"""One-shot Gmail login (no proxy) then print LOGIN_OK for the outer injector."""
from __future__ import annotations

import random
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

SESSION = "/tmp/snappy_gmail_session.txt"


def human_type(page, locator, text: str) -> None:
    locator.click(timeout=20000)
    page.wait_for_timeout(random.randint(150, 350))
    try:
        locator.fill("")
    except Exception:
        pass
    for ch in text:
        locator.type(ch, delay=random.randint(45, 130))
        if random.random() < 0.06:
            page.wait_for_timeout(random.randint(70, 220))
    page.wait_for_timeout(random.randint(180, 350))


def find_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    for pg in reversed(pages):
        host = urlparse(pg.url or "").netloc.lower()
        if host in ("accounts.google.com", "mail.google.com", "gds.google.com"):
            return pg
    return pages[-1] if pages else None


def click_named(page, *names):
    for name in names:
        for loc in (
            page.get_by_role("button", name=name),
            page.locator(f'button:has-text("{name}")'),
            page.locator(f'div[role="button"]:has-text("{name}")'),
        ):
            try:
                if loc.count() and loc.first.is_visible():
                    page.wait_for_timeout(random.randint(180, 400))
                    loc.first.click(timeout=10000)
                    page.wait_for_timeout(random.randint(1500, 2400))
                    return name
            except Exception:
                pass
    for sel in ("#identifierNext", "#passwordNext", "#totpNext", "#backupCodeNext"):
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=10000)
                page.wait_for_timeout(1800)
                return sel
        except Exception:
            pass
    return None


def main() -> int:
    lines = open(SESSION).read().strip().splitlines()
    ws, email, password, backup = lines[2], lines[3], lines[4], lines[5]
    print("login", email)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        page = None
        for _ in range(40):
            page = find_page(browser)
            if page and (
                "accounts.google" in page.url
                or "mail.google" in page.url
                or "gmail" in page.url.lower()
            ):
                break
            time.sleep(0.4)
        if not page:
            print("no page")
            return 1
        try:
            page.bring_to_front()
        except Exception:
            pass
        print("start", page.url)

        if "accounts.google" not in (page.url or ""):
            try:
                page.goto(
                    "https://accounts.google.com/signin/v2/identifier?service=mail"
                    "&continue=https://mail.google.com/mail/u/0/",
                    wait_until="commit",
                    timeout=90000,
                )
            except Exception as exc:
                print("goto warn", exc)
            page.wait_for_timeout(2000)
            page = find_page(browser) or page

        field = None
        for _ in range(25):
            for sel in ('input[type="email"]', "#identifierId", 'input[name="identifier"]'):
                loc = page.locator(sel)
                try:
                    if loc.count() and loc.first.is_visible():
                        field = loc.first
                        break
                except Exception:
                    pass
            if field:
                break
            page.wait_for_timeout(350)
        if not field:
            print("no email", page.url)
            return 1
        human_type(page, field, email)
        print("email", click_named(page, "Next", "Susunod", "Continue"), page.url)
        if "recaptcha" in page.url:
            print("HIT_RECAPTCHA", page.url)
            return 2

        field = None
        for _ in range(30):
            if "recaptcha" in page.url:
                print("HIT_RECAPTCHA", page.url)
                return 2
            for sel in ('input[type="password"]', 'input[name="Passwd"]', "#password input"):
                loc = page.locator(sel)
                try:
                    if loc.count() and loc.first.is_visible():
                        field = loc.first
                        break
                except Exception:
                    pass
            if field:
                break
            page.wait_for_timeout(400)
        if not field:
            print("no password", page.url)
            return 1
        human_type(page, field, password)
        print("pass", click_named(page, "Next", "Susunod", "Continue"), page.url)

        page.wait_for_timeout(800)
        for needles in (
            ["try another way", "another way", "sumubok ng iba"],
            ["backup code", "8-digit", "8 digit", "ilagay ang isa"],
        ):
            hit = page.evaluate(
                """(needles) => {
              const nodes=Array.from(document.querySelectorAll('div,span,li,button,a'));
              for (const n of nodes) {
                const t=(n.innerText||'').trim().toLowerCase();
                if (!t || t.length>100) continue;
                if (needles.some(x=>t.includes(x))) {
                  (n.closest('[role="link"],[role="button"],button,a,li')||n).click();
                  return t.slice(0,80);
                }
              }
              return null;
            }""",
                needles,
            )
            print("pick", needles[0], hit)
            page.wait_for_timeout(1200)

        field = None
        for _ in range(25):
            for sel in (
                'input[type="tel"]',
                'input[name="totpPin"]',
                'input[name="Pin"]',
                'input[type="text"]',
                "input",
            ):
                loc = page.locator(sel)
                try:
                    for i in range(min(loc.count(), 5)):
                        el = loc.nth(i)
                        if el.is_visible() and el.is_editable():
                            tp = (el.get_attribute("type") or "").lower()
                            nm = (el.get_attribute("name") or "").lower()
                            if tp == "email" or "identifier" in nm:
                                continue
                            field = el
                            break
                    if field:
                        break
                except Exception:
                    pass
            if field:
                break
            page.wait_for_timeout(350)
        if not field:
            print("no backup field", page.url)
            return 1
        human_type(page, field, backup)
        print("backup", field.input_value(), click_named(page, "Next", "Susunod", "Continue"))
        print("url", page.url)

        for step in range(12):
            page = find_page(browser) or page
            host = urlparse(page.url).netloc.lower()
            print(f"[{step}]", host + urlparse(page.url).path)
            if host == "mail.google.com":
                print("INBOX", page.title())
                break
            hit = page.evaluate(
                """() => {
              const prefer=['skip','not now','cancel','no thanks','later','remind me later','done','got it','laktawan'];
              const nodes=Array.from(document.querySelectorAll('button,a,div[role="button"],span'));
              for (const key of prefer) {
                for (const n of nodes) {
                  const t=(n.innerText||n.getAttribute('aria-label')||'').trim().toLowerCase();
                  if (!t || t.length>40 || t.includes('skip to content')) continue;
                  if (t===key || t.startsWith(key+' ')) {
                    (n.closest('button,a,[role="button"]')||n).click(); return t;
                  }
                }
              }
              return null;
            }"""
            )
            print(" click", hit)
            if hit:
                page.wait_for_timeout(1800)
                continue
            try:
                page.goto(
                    "https://mail.google.com/mail/u/0/#inbox",
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
                page.wait_for_timeout(2000)
            except Exception as exc:
                print("goto", exc)
                break

        page = find_page(browser) or page
        print("FINAL", page.url)
        try:
            print("TITLE", page.title())
        except Exception:
            pass
        if "mail.google.com" not in (page.url or ""):
            print("NOT_INBOX")
            return 1
        print("LOGIN_OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
