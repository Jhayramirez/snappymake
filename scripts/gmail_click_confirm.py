#!/usr/bin/env python3
"""Click Snapchat 'Confirm your email' in the open Gmail message."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

HIT_TEXT = (
    "confirm your email",
    "confirm email",
    "verify your email",
    "verify email",
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


def dump_tabs(ctx, tag: str) -> None:
    log(f"--- tabs {tag} ({len(ctx.pages)}) ---")
    for i, pg in enumerate(list(ctx.pages)):
        try:
            u = pg.url
        except Exception:
            u = "?"
        log(f"  [{i}] {u[:140]}")


def find_gmail(ctx):
    for pg in list(ctx.pages):
        try:
            if "mail.google.com" in (pg.url or ""):
                return pg
        except Exception:
            continue
    return None


def collect_links(page) -> list[dict]:
    try:
        return page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              const push = (href, text, inIframe) => {
                href = (href || '').trim();
                if (!href || href.startsWith('javascript:') || href.startsWith('mailto:')) return;
                const key = href + '|' + (text || '');
                if (seen.has(key)) return;
                seen.add(key);
                out.push({href, text: (text || '').replace(/\\s+/g, ' ').trim().slice(0, 80), inIframe});
              };
              const scan = (root, inIframe) => {
                for (const a of root.querySelectorAll('a[href]')) {
                  push(a.href, a.innerText || a.getAttribute('aria-label') || '', inIframe);
                }
              };
              scan(document, false);
              for (const f of document.querySelectorAll('iframe')) {
                try {
                  const doc = f.contentDocument || f.contentWindow.document;
                  if (doc) scan(doc, true);
                } catch (e) {}
              }
              return out;
            }"""
        ) or []
    except Exception as exc:
        log("collect_links err", type(exc).__name__, str(exc)[:120])
        return []


def pick_confirm(links: list[dict]) -> dict | None:
    ranked = []
    for link in links:
        href = (link.get("href") or "").lower()
        text = (link.get("text") or "").lower()
        if "unsubscribe" in href or "unsubscribe" in text:
            continue
        score = 0
        if any(t in text for t in HIT_TEXT):
            score += 50
        if "confirm" in text and "email" in text:
            score += 20
        if "snapchat.com" in href:
            score += 30
        if any(k in href for k in ("confirm", "verify", "activate", "email")):
            score += 10
        if "accounts.snapchat" in href:
            score += 15
        if score:
            ranked.append((score, link))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if ranked:
        log("pick score", ranked[0][0], ranked[0][1])
        return ranked[0][1]
    return None


def click_confirm_in_page(page) -> str | None:
    try:
        return page.evaluate(
            """() => {
              const keys = ['confirm your email', 'confirm email', 'verify your email', 'verify email'];
              const nodes = [];
              const add = (root) => {
                nodes.push(...root.querySelectorAll('a, button, span, div[role="button"]'));
              };
              add(document);
              for (const f of document.querySelectorAll('iframe')) {
                try {
                  const doc = f.contentDocument || f.contentWindow.document;
                  if (doc) add(doc);
                } catch (e) {}
              }
              for (const n of nodes) {
                const t = ((n.innerText || n.getAttribute('aria-label') || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
                if (!t || t.length > 60) continue;
                if (keys.some(k => t === k || t.includes(k))) {
                  const a = n.closest('a') || n;
                  const href = a.href || '';
                  a.click();
                  return href || t;
                }
              }
              return null;
            }"""
        )
    except Exception as exc:
        log("click err", type(exc).__name__, str(exc)[:120])
        return None


def looks_confirmed(page) -> bool:
    try:
        u = (page.url or "").lower()
        t = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    if "snapchat.com" not in u and "snapchat" not in t:
        return False
    needles = (
        "email confirmed",
        "email has been confirmed",
        "email verified",
        "verified your email",
        "thanks for confirming",
        "you're all set",
        "you are all set",
        "successfully confirmed",
        "confirmation successful",
        "your email is confirmed",
        "email address confirmed",
    )
    return any(n in t for n in needles)


def main() -> int:
    ws = ws_url()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        dump_tabs(ctx, "start")
        gmail = find_gmail(ctx)
        if not gmail:
            log("no gmail tab")
            return 1
        try:
            gmail.bring_to_front()
        except Exception:
            pass
        gmail.wait_for_timeout(800)
        try:
            log("gmail", gmail.url, (gmail.title() or "")[:90])
        except Exception:
            pass

        links = collect_links(gmail)
        snap_links = [x for x in links if "snapchat" in (x.get("href") or "").lower() or "snapchat" in (x.get("text") or "").lower() or "confirm" in (x.get("text") or "").lower()]
        log("links total", len(links), "interesting", len(snap_links))
        for x in snap_links[:12]:
            log(" ", x)

        before = [pg.url for pg in ctx.pages]
        chosen = pick_confirm(links)
        hit = click_confirm_in_page(gmail)
        log("clicked_text_or_href", hit)

        if not hit and chosen:
            href = chosen.get("href") or ""
            log("fallback goto", href[:160])
            gmail.goto(href, wait_until="domcontentloaded", timeout=45000)
        else:
            gmail.wait_for_timeout(2500)

        dump_tabs(ctx, "after_click")
        after = list(ctx.pages)
        new_pages = [pg for pg in after if (pg.url or "") not in before]
        target = new_pages[-1] if new_pages else None
        if target is None:
            for pg in after:
                try:
                    u = pg.url or ""
                except Exception:
                    continue
                if "snapchat.com" in u and "mail.google" not in u:
                    target = pg
        if target is None:
            target = gmail

        try:
            target.bring_to_front()
        except Exception:
            pass
        target.wait_for_timeout(2500)
        try:
            body = " ".join((target.inner_text("body") or "").split())[:400]
        except Exception:
            body = ""
        log("result url", target.url)
        log("result title", (target.title() or "")[:90])
        log("result body", body)
        if looks_confirmed(target):
            log("CONFIRMED")
            return 0
        if "snapchat.com" in (target.url or ""):
            log("OPENED_SNAPCHAT")
            return 0
        if hit or chosen:
            log("CLICKED")
            return 0
        log("NO_CONFIRM_CONTROL")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
