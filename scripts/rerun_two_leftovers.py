#!/usr/bin/env python3
"""Re-run caretoy807 (keep Gmail profile) then recreate billingdepet348."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["SNAPPY_NO_FOCUS"] = "1"
os.environ.setdefault("SNAPPY_BATCH", "Second Batch")

from playwright.sync_api import sync_playwright

import official_full_run as R
from gmail_batch_login_v3 import fmt_backup, login_gmail, really_gmail_inbox

from app.ads import AdsPowerError
from app.db import init_db
from app.services import gmail_login as G
from app.services.profiles import close_browser, create_one_profile, open_browser

_orig_close = close_browser


def _close_ok(c, pid: str) -> None:
    try:
        _orig_close(c, pid)
    except AdsPowerError as exc:
        R.log("  close warn", exc)
    except Exception as exc:
        R.log("  close warn", exc)


R.close_browser = _close_ok

CARETOY = "caretoy807@gmail.com"
BILLING = "billingdepet348@gmail.com"


def creds(email: str):
    row = next(r for r in G.snapshot()["accounts"] if r["email"].lower() == email.lower())
    codes = [x["code"] for x in row.get("codes") or [] if not x.get("used")] or [
        x["code"] for x in row.get("codes") or []
    ]
    return row, row["password"], row.get("totp_secret") or "", row.get("recovery_email") or "", codes


def keep_drop(c, pid: str, why: str = "") -> None:
    R.log("  keep AdsPower profile", pid, why)
    _close_ok(c, pid)
    R.stop_everything(c)


def rerun_caretoy(c) -> int:
    row, password, totp, rec, codes = creds(CARETOY)
    pid = (row.get("profile_id") or "").strip()
    if not pid:
        R.log("caretoy has no profile — skip")
        return 1
    R.log("=== RERUN CARETOY keep Gmail", pid, "===")
    G.set_status(
        CARETOY,
        login_status="login_ok",
        snap_status="none",
        last_error="",
        profile_id=pid,
        batch_name="Second Batch",
        group_name=G.OFFICIAL_GROUP_NAME,
    )
    R.SKIP_USED.discard(CARETOY.lower())
    R._inject_proxy = True
    orig_drop = R.drop_profile

    def _drop(cc, pp, why=""):
        if pp == pid:
            return keep_drop(cc, pp, why)
        return orig_drop(cc, pp, why)

    R.drop_profile = _drop
    try:
        return R.finish_after_gmail(
            c, pid, CARETOY, password, codes, totp_secret=totp, recovery_email=rec
        )
    finally:
        R.drop_profile = orig_drop


def recreate_billing(c) -> int:
    row, password, totp, rec, codes = creds(BILLING)
    backup = fmt_backup(codes[0]) if codes else ""
    gid = str(G.ensure_official_group(c).get("group_id") or "")
    R.log("=== RECREATE BILLING", BILLING, "===")
    R.stop_everything(c)
    old = (row.get("profile_id") or "").strip()
    if old:
        try:
            c.delete_profiles([old])
            R.log("  deleted old", old)
        except Exception as exc:
            R.log("  old delete warn", exc)
    G.set_status(
        BILLING,
        login_status="pending",
        snap_status="none",
        last_error="",
        batch_name="Second Batch",
        group_name=G.OFFICIAL_GROUP_NAME,
    )
    created = create_one_profile(
        c,
        {
            "name": BILLING,
            "auto_name": False,
            "auto_username": False,
            "auto_password": False,
            "bitmoji_gender": "female",
            "platform": "google.com",
            "tabs": R.GMAIL_TAB,
            "proxy_mode": "none",
            "fingerprint_mode": "random",
            "remark": f"Gmail · {BILLING} · Pass: {password} · 2FA recreate · proxy pending",
        },
        index=0,
        serial=1,
        group_id=gid,
    )
    pid = created["profile_id"]
    R.log("created", pid)
    G.set_status(BILLING, profile_id=pid, group_name=G.OFFICIAL_GROUP_NAME, batch_name="Second Batch")
    time.sleep(2)
    from gmail_batch_login_v3 import inject_proxy

    label = inject_proxy(pid)
    R.log("proxy injected immediately", label)
    G.set_status(BILLING, profile_id=pid, proxy_label=label)
    R.log("  waiting 15s for proxy to settle")
    time.sleep(15)
    session = open_browser(
        c,
        pid,
        headless=False,
        timeout=R.PROXY_REOPEN_S,
        attempt_timeout=R.PROXY_REOPEN_ATTEMPT_S,
        tabs=[R.GMAIL_TAB],
    )
    ws = R.ws_of(session)
    R.log("ws", ws)
    result = login_gmail(ws, BILLING, password, codes, totp_secret=totp, recovery_email=rec)
    R.log("gmail result", result)
    inbox_ok = False
    if result == "LOGIN_OK":
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws)
            inbox = next(
                (pg for pg in browser.contexts[0].pages if really_gmail_inbox(pg.url or "")),
                None,
            )
            inbox_ok = inbox is not None
            R.log("inbox", inbox.url if inbox else "MISSING")
    if not inbox_ok:
        if "WRONG_PASSWORD" in str(result):
            status, err = "wrong_password", "wrong password"
        elif "CAPTCHA" in str(result):
            status, err = "captcha", str(result)[:300]
        elif "SELFIE" in str(result):
            status, err = "selfie", str(result)[:300]
        elif "totp_rejected" in str(result).lower() or "challenge/totp" in str(result).lower():
            status, err = "error", "2FA key rejected"
        else:
            status, err = "error", str(result)[:300]
        G.set_status(
            BILLING,
            login_status=status,
            profile_id=pid,
            last_error=err,
            batch_name="Second Batch",
        )
        R.log("gmail failed", status, result)
        R.push_sheet(BILLING)
        if status in ("captcha",):
            R.log("leave browser open for captcha")
            return 2
        R.drop_profile(c, pid, status)
        return 1
    G.set_status(
        BILLING,
        login_status="login_ok",
        profile_id=pid,
        last_error="",
        batch_name="Second Batch",
        proxy_label=label,
    )
    R._inject_proxy = False
    rc = R.finish_after_gmail(
        c, pid, BILLING, password, codes, totp_secret=totp, recovery_email=rec
    )
    R.push_sheet(BILLING)
    row2 = next(r for r in G.snapshot()["accounts"] if r["email"] == BILLING)
    R.log("DONE BILLING", row2.get("snap_status"), (row2.get("last_error") or "")[:120], "rc", rc)
    return 0 if row2.get("snap_status") == "signed_up" else 1


def main() -> int:
    init_db()
    R._stop_after_signed_up = True
    c = R.client()
    R.log("stop leftover browsers first")
    R.stop_everything(c)

    rc1 = rerun_caretoy(c)
    row1 = next(r for r in G.snapshot()["accounts"] if r["email"] == CARETOY)
    R.log("CARETOY RESULT", row1.get("snap_status"), (row1.get("last_error") or "")[:160], "rc", rc1)
    R.push_sheet(CARETOY)

    rc2 = recreate_billing(c)
    R.log("ALL DONE caretoy", rc1, "billing", rc2)
    return 0 if rc1 == 0 and rc2 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
