#!/usr/bin/env python3
"""Continue an already-open Official Gmail (user solved captcha). Same proxy."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SNAPPY_NO_FOCUS", "1")

from playwright.sync_api import sync_playwright

import official_full_run as R
import gmail_batch_login_v3 as B
from gmail_batch_login_v3 import (
    click_named,
    find_page,
    human_type,
    login_gmail,
    on_totp_form,
    password_rejected,
    really_gmail_inbox,
    skip_to_inbox,
    type_totp,
    visible_input,
)

from app.db import init_db
from app.services import gmail_login as G

EMAIL = "billingdepet348@gmail.com"


def continue_from_open(ws: str, password: str, totp_secret: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws, timeout=30000)
        page = find_page(browser)
        if page is None:
            return "FAIL:no_page"
        url = (page.url or "").lower()
        R.log("  continue from", url[:140])
        if really_gmail_inbox(url):
            return "LOGIN_OK"
        if "recaptcha" in url:
            return "HIT_RECAPTCHA"
        if "challenge/pwd" in url or visible_input(
            page, ['input[type="password"]', 'input[name="Passwd"]']
        ):
            target = visible_input(
                page, ['input[name="Passwd"]', 'input[type="password"]']
            )
            if not target:
                return "FAIL:no_password_field"
            try:
                human_type(page, target, password)
            except Exception:
                target.fill(password)
            R.log("  pass→", click_named(page, "Next", "Susunod", "Continue"), page.url)
            page.wait_for_timeout(2200)
            if password_rejected(page):
                return "WRONG_PASSWORD"
        totp_secret = (totp_secret or "").strip()
        totp_fails = 0
        for round_i in range(8):
            page = find_page(browser) or page
            url = (page.url or "").lower()
            if "signin/rejected" in url or "/rejected" in urlparse(url).path:
                return "FAIL:2sv_rejected"
            if really_gmail_inbox(url):
                break
            if "recaptcha" in url:
                return "HIT_RECAPTCHA"
            if totp_secret and (on_totp_form(page) or "challenge/totp" in url):
                if type_totp(page, totp_secret):
                    break
                totp_fails += 1
                R.log("  totp rejected — skip immediately")
                return "FAIL:totp_rejected"
            if not on_totp_form(page) and "challenge/" not in url:
                break
            page.wait_for_timeout(800)
        if not skip_to_inbox(page, browser):
            page = find_page(browser) or page
            if not really_gmail_inbox(page.url or ""):
                if totp_secret and on_totp_form(page):
                    return "FAIL:totp_rejected"
                return f"FAIL:not_inbox:{page.url}"
        page = find_page(browser) or page
        if not really_gmail_inbox(page.url or ""):
            return f"FAIL:not_inbox:{page.url}"
        R.log("  INBOX", page.url)
        return "LOGIN_OK"


def main() -> int:
    init_db()
    R._stop_after_signed_up = True
    R._inject_proxy = False
    c = R.client()
    row = next(r for r in G.snapshot()["accounts"] if r["email"] == EMAIL)
    pid = (row.get("profile_id") or "").strip()
    password = row["password"]
    totp_secret = row.get("totp_secret") or ""
    recovery_email = row.get("recovery_email") or ""
    codes = [x["code"] for x in row.get("codes") or [] if not x.get("used")] or [
        x["code"] for x in row.get("codes") or []
    ]
    active = c.local_sessions()
    sess = next((s for s in active if s.get("profile_id") == pid), None)
    if not sess:
        sess = active[0] if active else None
    if not sess:
        R.log("no open AdsPower browser")
        return 1
    pid = str(sess.get("profile_id") or pid)
    packed = c.wait_profile_session(pid) if hasattr(c, "wait_profile_session") else None
    ws = ""
    if packed:
        ws = packed.get("puppeteer") or ""
    if not ws:
        ws = ((sess.get("ws") or {}) if isinstance(sess.get("ws"), dict) else {}).get("puppeteer") or ""
    if not ws:
        # local-active list often has ws
        import json, urllib.request

        d = json.loads(
            urllib.request.urlopen(
                "http://127.0.0.1:50325/api/v1/browser/local-active", timeout=10
            ).read()
        )
        lst = (d.get("data") or {}).get("list") or []
        hit = next((x for x in lst if str(x.get("user_id") or "") == pid), lst[0] if lst else {})
        ws = (hit.get("ws") or {}).get("puppeteer") or ""
        pid = str(hit.get("user_id") or pid)
    R.log("=== CONTINUE", EMAIL, pid, "===")
    result = continue_from_open(ws, password, totp_secret)
    R.log("gmail result", result)
    if result != "LOGIN_OK":
        err = "2FA key rejected" if "totp" in result.lower() else str(result)[:300]
        status = (
            "wrong_password"
            if "WRONG_PASSWORD" in result
            else "captcha"
            if "CAPTCHA" in result
            else "error"
        )
        G.set_status(EMAIL, login_status=status, profile_id=pid, last_error=err)
        R.push_sheet(EMAIL)
        R.log("leave browser open for you")
        return 1
    G.set_status(EMAIL, login_status="login_ok", profile_id=pid, last_error="")
    rc = R.finish_after_gmail(
        c,
        pid,
        EMAIL,
        password,
        codes,
        totp_secret=totp_secret,
        recovery_email=recovery_email,
    )
    R.push_sheet(EMAIL)
    row2 = next(r for r in G.snapshot()["accounts"] if r["email"] == EMAIL)
    R.log("DONE", row2.get("snap_status"), (row2.get("last_error") or "")[:120], "rc", rc)
    return 0 if row2.get("snap_status") == "signed_up" else 1


if __name__ == "__main__":
    raise SystemExit(main())
