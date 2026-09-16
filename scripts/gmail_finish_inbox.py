#!/usr/bin/env python3
from __future__ import annotations

import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def find_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    for pg in reversed(pages):
        host = urlparse(pg.url or "").netloc.lower()
        if any(
            h in host
            for h in (
                "accounts.google",
                "mail.google",
                "gds.google",
                "workspace.google",
                "myaccount.google",
            )
        ):
            return pg
    return pages[-1] if pages else None


def main() -> int:
    ws = sys.argv[1]
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        for step in range(15):
            pages = [pg for ctx in browser.contexts for pg in ctx.pages]
            print(
                "tabs:",
                [
                    (urlparse(pg.url).netloc + urlparse(pg.url).path)[:90]
                    for pg in pages
                ],
            )
            page = find_page(browser)
            if not page:
                break
            try:
                page.bring_to_front()
            except Exception:
                pass
            host = urlparse(page.url).netloc.lower()
            path = urlparse(page.url).path
            print(f"[{step}] {host}{path}")
            try:
                print(" ", " ".join((page.inner_text("body") or "").split())[:300])
            except Exception:
                pass
            if host == "mail.google.com":
                print("INBOX", page.title())
                print("LOGIN_OK")
                return 0

            hit = page.evaluate(
                """()=>{
              const prefer=['skip','not now','cancel','no thanks','later','remind me later','laktawan','sa ibang pagkakataon','skip for now'];
              const nodes=Array.from(document.querySelectorAll('button,a,div[role="button"],span'));
              for (const key of prefer){
                for (const n of nodes){
                  const t=(n.innerText||n.getAttribute('aria-label')||'').trim().toLowerCase();
                  if(!t||t.length>40) continue;
                  if(t.includes('skip to')) continue;
                  if(t===key || t.startsWith(key+' ')){
                    (n.closest('button,a,[role="button"]')||n).click();
                    return t;
                  }
                }
              }
              return null;
            }"""
            )
            print(" click", hit)
            if hit:
                page.wait_for_timeout(2000)
                continue
            try:
                page.goto(
                    "https://mail.google.com/mail/u/0/#inbox",
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
                page.wait_for_timeout(2500)
            except Exception as exc:
                print("goto", exc)
                break

        page = find_page(browser)
        print("FINAL", page.url if page else None)
        if page and "mail.google.com" in page.url:
            print("LOGIN_OK")
            return 0
        print("NOT_INBOX")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
