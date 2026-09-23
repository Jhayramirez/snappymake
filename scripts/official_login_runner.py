#!/usr/bin/env python3
"""Log the Gmail Login pool into the "SnappyMake Official" AdsPower group.

Reuses the proven login machinery from gmail_batch_login_v3 (create/start/login/
proxy) but sources accounts from the app DB (gmail_login table) and writes
results back there. By default it STOPS at the first clean LOGIN_OK so we can
proceed step-by-step to Snapchat.

Usage:
  python3 scripts/official_login_runner.py               # stop at first LOGIN_OK
  python3 scripts/official_login_runner.py --all         # run the whole pending list
  python3 scripts/official_login_runner.py --no-proxy    # skip Netlox proxy inject
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gmail_batch_login_v3 as B  # noqa: E402
from gmail_batch_login_v3 import (  # noqa: E402
    create_profile,
    delete_profile,
    fmt_backup,
    inject_proxy,
    is_crash_error,
)

from app.db import init_db  # noqa: E402
from app.services import gmail_login as G  # noqa: E402

OFFICIAL_GROUP_NAME = "SnappyMake Official"


def log(*a):
    print(*a, flush=True)


def official_group_id() -> str:
    d = B.api_get("/api/v1/group/list", {"page": 1, "page_size": 500})
    for g in (d.get("data") or {}).get("list") or []:
        if str(g.get("group_name") or "").strip() == OFFICIAL_GROUP_NAME:
            return str(g.get("group_id"))
    # create if missing
    d = B.api_post(
        "/api/v1/group/create",
        {"group_name": OFFICIAL_GROUP_NAME, "remark": OFFICIAL_GROUP_NAME},
    )
    return str((d.get("data") or {}).get("group_id") or "")


def pending_accounts():
    """Accounts still needing a login: pending or previous error. Skip
    login_ok / wrong_password / captcha / selfie (known terminal states).
    Skip SnappyMail-Test leftovers that only live in the stats DB."""
    rows = G.snapshot()["accounts"]
    out = []
    for r in rows:
        if r["login_status"] in ("pending", "error"):
            gname = (r.get("group_name") or "").strip()
            if gname == G.EXCLUDE_GROUP_NAME:
                continue
            codes = [c["code"] for c in r["codes"]]
            out.append(
                {
                    "email": r["email"],
                    "password": r["password"],
                    "codes": codes,
                    "totp_secret": r.get("totp_secret") or "",
                    "recovery_email": r.get("recovery_email") or "",
                }
            )
    return out


def run_one(acc, gid, inject: bool) -> str:
    email = acc["email"]
    password = acc["password"]
    codes = acc["codes"]
    totp_secret = acc.get("totp_secret") or ""
    recovery_email = acc.get("recovery_email") or ""
    backup = fmt_backup(codes[0]) if codes else ""

    log(f"\n========== {email} ==========")
    B.stop_all_browsers()

    pid = create_profile(email, gid, password, backup)
    log("  created", pid)
    time.sleep(2)

    result = None
    last_exc = None
    for attempt in range(1, 3):
        try:
            ws = B.start_with_retries(pid)
            log("  logging in (no focus)...")
            result = B.login_gmail(
                ws, email, password, codes, totp_secret=totp_secret, recovery_email=recovery_email
            )
            log("  RESULT", result)
            if is_crash_error(result):
                raise RuntimeError(str(result))
            break
        except Exception as exc:
            last_exc = exc
            log(f"  fail: {str(exc).splitlines()[0][:140]}")
            B.stop_browser(pid)
            B.stop_all_browsers()
            if is_crash_error(exc) and attempt < 2:
                log("  unstable — recreate once")
                try:
                    delete_profile(pid)
                except Exception:
                    pass
                time.sleep(2)
                pid = create_profile(email, gid, password, backup)
                log("  recreated", pid)
                time.sleep(5)
                continue
            result = f"ERROR:{exc}"
            break

    if result is None:
        result = f"ERROR:{last_exc or 'no result'}"

    # Interpret + persist to the app DB.
    if result == "HIT_RECAPTCHA":
        B.update_user(pid, remark=B.remark(email, password, backup, "CAPTCHA UPON LOGIN"))
        B.stop_browser(pid); B.stop_all_browsers()
        G.set_status(email, login_status="captcha", profile_id=pid)
        return "captcha"

    if "WRONG_PASSWORD" in str(result):
        B.update_user(pid, remark=B.remark(email, password, backup, "WRONG PASSWORD"))
        B.stop_browser(pid); B.stop_all_browsers()
        G.set_status(
            email,
            login_status="wrong_password",
            profile_id=pid,
            last_error="wrong password",
        )
        return "wrong_password"

    if "SELFIE" in result:
        B.update_user(pid, remark=B.remark(email, password, backup, "SELFIE VERIFICATION REQUIRED"))
        B.stop_browser(pid); B.stop_all_browsers()
        G.set_status(email, login_status="selfie", profile_id=pid)
        return "selfie"

    if "totp_rejected" in str(result).lower() or "challenge/totp" in str(result).lower():
        B.update_user(pid, remark=B.remark(email, password, backup, "2FA KEY REJECTED"))
        B.stop_browser(pid); B.stop_all_browsers()
        G.set_status(
            email,
            login_status="error",
            profile_id=pid,
            last_error="2FA key rejected",
        )
        return "error"

    if result != "LOGIN_OK":
        B.update_user(pid, remark=B.remark(email, password, backup, f"FAIL {result}"))
        B.stop_browser(pid); B.stop_all_browsers()
        G.set_status(email, login_status="error", profile_id=pid, last_error=str(result)[:300])
        return "error"

    # success — close, then optionally inject proxy
    B.stop_browser(pid); B.stop_all_browsers()
    time.sleep(2)
    label = ""
    if inject:
        try:
            label = inject_proxy(pid)
            log("  proxy", label)
        except Exception as exc:
            log("  proxy inject warn:", exc)
    B.update_user(pid, remark=B.remark(email, password, backup, f"Proxy: {label or 'none'} · LOGIN_OK"))
    G.set_status(email, login_status="login_ok", profile_id=pid, proxy_label=label)
    return "login_ok"


def main() -> int:
    init_db()
    run_all = "--all" in sys.argv[1:]
    inject = "--no-proxy" not in sys.argv[1:]

    gid = official_group_id()
    if not gid:
        log("ERROR: could not resolve SnappyMake Official group")
        return 2
    log(f"Official group id: {gid} · inject_proxy={inject} · run_all={run_all}")

    accts = pending_accounts()
    log(f"Pending accounts: {len(accts)}")

    tally = {"login_ok": 0, "wrong_password": 0, "captcha": 0, "selfie": 0, "error": 0}
    first_good = None
    for acc in accts:
        try:
            outcome = run_one(acc, gid, inject)
        except Exception as exc:
            log("  hard error:", exc)
            outcome = "error"
            try:
                G.set_status(acc["email"], login_status="error", last_error=str(exc)[:300])
            except Exception:
                pass
        tally[outcome] = tally.get(outcome, 0) + 1
        B.stop_all_browsers()
        time.sleep(3)
        if outcome == "login_ok" and first_good is None:
            first_good = acc["email"]
            if not run_all:
                snap = G.snapshot()
                log(f"\nFIRST_GOOD_LOGIN {acc['email']}")
                log(f"  pool: {snap['logged_in']} logged in / {snap['total']} total")
                log("STOPPING (first clean login). Re-run with --all to continue.")
                return 0

    log("\n======== DONE ========")
    for k, v in tally.items():
        log(f"  {k}: {v}")
    if first_good is None:
        log("NO_SUCCESS — no account logged in cleanly")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
