#!/usr/bin/env python3
"""Push a client-friendly Official report to the Google Sheet.

Does not touch the live AdsPower run. Safe while salvage / Start is going.
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ads.client import AdsPowerClient
from app.db import get_all_profile_life, init_db
from app.services import gmail_login as G
from app.services import load_runtime_settings
from app.services.sheets import (
    _client,
    _fmt_ts,
    _warmup_stage_label,
    _write_tab,
    sheets_id,
)

PROFILES_TAB = "Official Profiles"
MAIL_TAB = "Mail Status"
TOTALS_TAB = "Totals"

KEEP_TABS = {PROFILES_TAB, MAIL_TAB, TOTALS_TAB, "Official Profiles SMS Method"}

PROFILE_HEADERS = [
    "Profile #",
    "Profile ID",
    "Gmail",
    "Created",
    "Age",
    "Warm up",
    "Life",
    "Batch",
    "Browser",
    "Gmail status",
    "Snapchat status",
    "What happened",
    "Snapchat name",
    "Snapchat username",
    "Proxy",
    "Last open",
]
MAIL_HEADERS = [
    "Batch",
    "Gmail",
    "Password",
    "Gmail status",
    "Snapchat status",
    "What happened",
    "Login issue",
    "2FA",
    "Snapchat name",
    "Snapchat username",
    "Where it is",
    "Updated",
]
TOTALS_HEADERS = ["Section", "What this means", "Count", "Plain-English note"]

PROFILE_WIDTHS = {
    "Profile #": 90,
    "Profile ID": 130,
    "Gmail": 260,
    "Created": 170,
    "Age": 140,
    "Warm up": 140,
    "Life": 80,
    "Batch": 130,
    "Browser": 80,
    "Gmail status": 220,
    "Snapchat status": 220,
    "What happened": 420,
    "Snapchat name": 130,
    "Snapchat username": 160,
    "Proxy": 180,
    "Last open": 160,
}
MAIL_WIDTHS = {
    "Batch": 130,
    "Gmail": 260,
    "Password": 140,
    "Gmail status": 240,
    "Snapchat status": 220,
    "What happened": 440,
    "Login issue": 220,
    "2FA": 180,
    "Snapchat name": 130,
    "Snapchat username": 160,
    "Where it is": 180,
    "Updated": 160,
}
TOTALS_WIDTHS = {"Section": 200, "What this means": 340, "Count": 80, "Plain-English note": 720}

CAPTCHA_NOTE = (
    "Once Google shows a robot check, it then loops asking for a phone number. "
    "Pressing “Try another way” and entering a backup code still sends you back "
    "to phone-number verification. These Gmails cannot be recovered this way."
)

SNAP_OK_RE = re.compile(r"SNAP_OK\s*·\s*([^/·]+)/\s*([A-Za-z0-9._]+)", re.I)
SNAP_OLD_RE = re.compile(
    r"snap via google as [^/\n]+/\s*([^/]+)/\s*([A-Za-z0-9._]+)",
    re.I,
)


def _unix_seconds(value) -> int:
    if value in (None, "", 0, "0"):
        return 0
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    if n >= 100_000_000_000:
        n = n // 1000
    return n


def _age_label(created) -> str:
    """Simple age for a client: less than 1 hour, 3 hours, 1 day, 2 days…"""
    n = _unix_seconds(created)
    if not n:
        return ""
    sec = max(0, int(time.time()) - n)
    if sec < 3600:
        return "less than 1 hour"
    hours = sec // 3600
    if hours < 24:
        return "1 hour" if hours == 1 else f"{hours} hours"
    days = hours // 24
    return "1 day" if days == 1 else f"{days} days"


def _four_days_up(created) -> bool:
    n = _unix_seconds(created)
    if not n:
        return False
    return (time.time() - n) >= 96 * 3600


def _snap_ident(err: str) -> tuple[str, str]:
    t = err or ""
    m = SNAP_OK_RE.search(t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = SNAP_OLD_RE.search(t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


def _batch(row: dict) -> str:
    return (row.get("batch_name") or "").strip() or "Unbatched"


def _2fa_label(row: dict) -> str:
    rec = (row.get("recovery_email") or "").strip()
    if rec:
        return f"Recovery mail ({rec})"
    if (row.get("totp_secret") or "").strip():
        return "Authenticator key"
    if row.get("codes"):
        return "Backup codes"
    return "—"


def _login_issue(row: dict) -> str:
    err = (row.get("last_error") or "").strip()
    login = (row.get("login_status") or "").lower()
    if login == "wrong_password":
        return "wrong password"
    if login == "captcha":
        return "captcha"
    if "google wants a phone" in (err or "").lower() or "unusual activity" in (err or "").lower():
        return "Google wants a phone"
    if login == "selfie":
        return "selfie"
    return err


def _where(row: dict) -> str:
    g = (row.get("group_name") or "").strip()
    if g == G.OFFICIAL_GROUP_NAME:
        return "Official group"
    if g == getattr(G, "READY_GROUP_NAME", ""):
        return "Ready to Use (Gmail Method)"
    if g == G.EXCLUDE_GROUP_NAME:
        return "Test group (in this report)"
    return "Mail list"


def explain(row: dict) -> dict[str, str]:
    """Turn DB flags into language a client can read. One bucket per mail."""
    login = (row.get("login_status") or "pending").lower()
    snap = (row.get("snap_status") or "none").lower()
    err = (row.get("last_error") or "").strip()
    low = err.lower()
    first, user = _snap_ident(err)

    if snap == "signed_up" or "snap_ok" in low or "snap_welcome" in low or "email verified" in low:
        what = "Gmail worked and a Snapchat account was created."
        if "email verified" in low:
            what = "Gmail worked, Snapchat was created, and the email was confirmed."
        return {
            "gmail": "Logged in",
            "snap": "Snapchat created",
            "happened": what,
            "bucket": "snap_created",
            "first": first,
            "user": user,
        }

    if login == "wrong_password" or "wrong password" in low or "password was changed" in low:
        return {
            "gmail": "Wrong password",
            "snap": "Not created — Gmail blocked",
            "happened": "The Gmail password is wrong, or Google says it was changed.",
            "bucket": "wrong_password",
            "first": first,
            "user": user,
        }

    if (
        "google wants a phone" in low
        or "unusual activity" in low
        or "challenge/iap" in low
    ):
        return {
            "gmail": "Google wants a phone",
            "snap": "Not created — Gmail blocked",
            "happened": (
                "Google asked for a phone number after login (unusual activity). "
                "Try another way still stays on phone. These Gmails cannot be recovered this way."
            ),
            "bucket": "wants_phone",
            "first": first,
            "user": user,
        }

    if login == "captcha" or "captcha" in low or "recaptcha" in low:
        return {
            "gmail": "Google robot check",
            "snap": "Not created — Gmail blocked",
            "happened": (
                "Google showed a robot check. After that it keeps asking for a phone number, "
                "even if you press “Try another way” and enter a backup code."
            ),
            "bucket": "captcha",
            "first": first,
            "user": user,
        }

    if login == "selfie" or "selfie" in low:
        return {
            "gmail": "Google asked for a selfie",
            "snap": "Not created — Gmail blocked",
            "happened": "Google asked for a face / extra ID check. We do not complete that.",
            "bucket": "selfie",
            "first": first,
            "user": user,
        }

    if "2fa key rejected" in low or "totp_rejected" in low or "challenge/totp" in low:
        return {
            "gmail": "2FA key rejected",
            "snap": "Not created — Gmail blocked",
            "happened": "Google rejected the authenticator / 2fa.cn key for this mailbox.",
            "bucket": "bad_2fa_key",
            "first": first,
            "user": user,
        }

    if (
        "2sv_no_backup" in low
        or "all_codes_bad" in low
        or "2sv" in low
        or "backup code" in low
    ):
        return {
            "gmail": "2-step verification — no working backup code",
            "snap": "Not created — Gmail blocked",
            "happened": "Google asked for 2-step verification. There was no backup-code option, or the saved backup code did not work.",
            "bucket": "no_backup_2fa",
            "first": first,
            "user": user,
        }

    if (
        "connect error" in low
        or "ws://" in low
        or "browsertype" in low
        or "open failed" in low
        or "browser" in low and "fail" in low
    ):
        return {
            "gmail": "Couldn’t open the browser",
            "snap": "Not created — never reached Gmail",
            "happened": "The browser profile failed to open, so Gmail login never started.",
            "bucket": "browser_failed",
            "first": first,
            "user": user,
        }

    if "already associated" in low or "already has snap" in low:
        return {
            "gmail": "Logged in",
            "snap": "Not created — already on Snapchat",
            "happened": "This Gmail already has a Snapchat account, so we cannot sign it up again.",
            "bucket": "already_on_snap",
            "first": first,
            "user": user,
        }

    if login == "login_ok" and snap == "failed":
        return {
            "gmail": "Logged in",
            "snap": "Not created — signup didn’t finish",
            "happened": "Gmail worked, but Snapchat signup did not finish.",
            "bucket": "snap_didnt_finish",
            "first": first,
            "user": user,
        }

    if login == "login_ok":
        return {
            "gmail": "Logged in",
            "snap": "Not created yet",
            "happened": "Gmail is logged in. Snapchat signup is not finished yet.",
            "bucket": "gmail_ok_waiting",
            "first": first,
            "user": user,
        }

    # leftover remark that is only credentials = never attempted
    if login == "pending" and (
        not err
        or low.startswith("gmail ·")
        or "proxy pending" in low
    ):
        return {
            "gmail": "Not tried yet",
            "snap": "Not created yet",
            "happened": "This mailbox has not been logged in yet.",
            "bucket": "not_tried",
            "first": first,
            "user": user,
        }

    if login == "pending":
        return {
            "gmail": "Not tried yet",
            "snap": "Not created yet",
            "happened": "This mailbox has not been logged in yet.",
            "bucket": "not_tried",
            "first": first,
            "user": user,
        }

    return {
        "gmail": "Gmail login failed",
        "snap": "Not created — Gmail blocked",
        "happened": "Gmail login did not finish.",
        "bucket": "other_gmail",
        "first": first,
        "user": user,
    }


def _by_email(accounts: list[dict]) -> dict[str, dict]:
    return {str(r.get("email") or "").lower(): r for r in accounts}


def _drop_other_tabs(spreadsheet) -> list[str]:
    removed = []
    extras = [ws for ws in list(spreadsheet.worksheets()) if ws.title not in KEEP_TABS]
    for ws in extras:
        if len(spreadsheet.worksheets()) <= 1:
            break
        try:
            spreadsheet.del_worksheet(ws)
            removed.append(ws.title)
        except Exception:
            pass
    return removed


def _blocked_count(b: Counter) -> int:
    return (
        b["wrong_password"]
        + b["captcha"]
        + b["wants_phone"]
        + b["selfie"]
        + b["no_backup_2fa"]
        + b["bad_2fa_key"]
        + b["browser_failed"]
        + b["other_gmail"]
    )


def _batch_total_rows(section: str, explained: list[dict]) -> list[list[object]]:
    b = Counter(x["bucket"] for x in explained)
    blocked = _blocked_count(b)
    return [
        [section, "Gmails in this batch", len(explained), "Report-only label — Official AdsPower group can mix batches"],
        [section, "Snapchat created", b["snap_created"], "Signup finished"],
        [section, "Could not use (Gmail problem)", blocked, "Never got a working Gmail login"],
        [section, "Wrong password", b["wrong_password"], "Password is wrong, or Google says it was changed"],
        [section, "2FA key rejected", b["bad_2fa_key"], "Authenticator / 2fa.cn key did not work"],
        [section, "Google robot check", b["captcha"], CAPTCHA_NOTE],
        [section, "Google wants a phone", b["wants_phone"], "Google asked for a phone number (unusual activity). Not recoverable this way."],
        [section, "2-step verification — no working backup code", b["no_backup_2fa"], "No backup-code option, or the saved code failed"],
        [section, "Google asked for a selfie", b["selfie"], "Extra ID / face check"],
        [section, "This Gmail already has Snapchat", b["already_on_snap"], "Cannot sign up again"],
        [section, "Gmail login failed", b["other_gmail"] + b["browser_failed"], "Never reached inbox"],
        [section, "Not tried yet", b["not_tried"], ""],
    ]


def main() -> int:
    init_db()
    sid = sheets_id()
    if not sid:
        print("no sheet id", flush=True)
        return 2

    conf = load_runtime_settings()
    c = AdsPowerClient(conf["api_base"], conf.get("api_key") or "")
    snap = G.snapshot()
    accounts = snap["accounts"]
    explained = [explain(r) | {"row": r} for r in accounts]
    buckets = Counter(x["bucket"] for x in explained)
    mail_map = _by_email(accounts)

    info = G.ensure_official_group(c)
    gid = str(info.get("group_id") or "")
    ready = G.ensure_ready_group(c)
    ready_gid = str(ready.get("group_id") or "")
    seen: set[str] = set()
    official = []
    for src_gid in (gid, ready_gid):
        if not src_gid:
            continue
        for p in c.list_profiles(group_id=src_gid) or []:
            if "@" not in str(p.get("name") or ""):
                continue
            pid = str(p.get("profile_id") or p.get("user_id") or "")
            if pid in seen:
                continue
            seen.add(pid)
            official.append(p)
    try:
        active = set(c.local_active() or [])
        if active and isinstance(next(iter(active)), str):
            open_ids = set(active)
        else:
            open_ids = {str(x.get("user_id") or x) for x in (active or [])}
    except Exception:
        open_ids = set()

    official.sort(key=lambda p: int(p.get("profile_no") or 0) if str(p.get("profile_no") or "").isdigit() else 0)
    lives = get_all_profile_life()

    profile_rows = []
    for p in official:
        pid = str(p.get("profile_id") or p.get("user_id") or "")
        name = str(p.get("name") or "")
        email = name.strip().lower() if "@" in name else ""
        row = mail_map.get(email) or {}
        info_row = explain(row) if row else {
            "gmail": "",
            "snap": "",
            "happened": "",
            "first": "",
            "user": "",
        }
        life = str(lives.get(pid) or "").strip().lower()
        if life == "logout":
            life = "dead"
        remark = str(p.get("remark") or "")
        created = p.get("created_time")
        if life == "dead":
            recovered = (
                "gmail recovered by the owner" in remark.lower()
                or "gmail recovered by the owner" in str(row.get("last_error") or "").lower()
            )
            info_row = {
                **info_row,
                "snap": "Gmail Recovered by the owner" if recovered else "Dead",
                "happened": (
                    "Gmail recovered by the owner."
                    if recovered
                    else "Marked dead. Snapchat login kick / account not found."
                ),
            }
        elif _four_days_up(created):
            info_row = {
                **info_row,
                "snap": "Ready to use",
                "happened": "4 days+. Account is ready to use.",
            }
        elif (
            _warmup_stage_label(remark) in {"Stage 1 Done", "Done"}
            or "Still inside" in remark
        ):
            info_row = {
                **info_row,
                "snap": "Still inside",
                "happened": "Still logged in. Landed on Snapchat for Web after Chat.",
            }
        profile_rows.append(
            [
                str(p.get("profile_no") or ""),
                pid,
                email,
                _fmt_ts(created),
                _age_label(created),
                _warmup_stage_label(remark),
                "dead" if life == "dead" else (life or "live"),
                _batch(row),
                "open" if pid in open_ids else "closed",
                info_row["gmail"],
                info_row["snap"],
                info_row["happened"],
                info_row["first"],
                info_row["user"],
                row.get("proxy_label") or "",
                _fmt_ts(p.get("last_open_time")),
            ]
        )

    mail_rows = []
    for x in explained:
        r = x["row"]
        mail_rows.append(
            [
                _batch(r),
                r.get("email") or "",
                r.get("password") or "",
                x["gmail"],
                x["snap"],
                x["happened"],
                _login_issue(r),
                _2fa_label(r),
                x["first"],
                x["user"],
                _where(r),
                _fmt_ts(r.get("updated_at")),
            ]
        )

    gmail_blocked = (
        buckets["wrong_password"]
        + buckets["captcha"]
        + buckets["wants_phone"]
        + buckets["selfie"]
        + buckets["no_backup_2fa"]
        + buckets["bad_2fa_key"]
        + buckets["browser_failed"]
        + buckets["other_gmail"]
    )
    snap_not = (
        buckets["already_on_snap"]
        + buckets["snap_didnt_finish"]
        + buckets["gmail_ok_waiting"]
        + gmail_blocked
        + buckets["not_tried"]
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    totals_rows: list[list[object]] = [
        ["At a glance", "Gmail accounts in this report", len(accounts), "Every mailbox we used — Official + test group"],
        ["At a glance", "Snapchat accounts created", buckets["snap_created"], "Signup finished"],
        ["At a glance", "Could not create Snapchat (Gmail problem)", gmail_blocked, "Never got a working Gmail login"],
        ["At a glance", "Gmail worked, Snapchat not created yet", buckets["gmail_ok_waiting"] + buckets["already_on_snap"] + buckets["snap_didnt_finish"], "Logged into Gmail, but no new Snapchat"],
        ["At a glance", "Not tried yet", buckets["not_tried"], "Still waiting / not logged in yet"],
        ["At a glance", "Report updated", 1, now],
        ["Snapchat", "Created successfully", buckets["snap_created"], "These Gmails now have a Snapchat account"],
        ["Snapchat", "Already had Snapchat", buckets["already_on_snap"], "Gmail worked, but this email is already tied to Snapchat"],
        ["Snapchat", "Signup didn’t finish", buckets["snap_didnt_finish"], "Gmail worked, Snapchat form / Google step did not complete"],
        ["Snapchat", "Waiting (Gmail is in)", buckets["gmail_ok_waiting"], "Gmail is logged in; Snapchat signup still open"],
        ["Why Gmail failed", "Wrong password", buckets["wrong_password"], "Password is wrong, or Google says it was changed"],
        ["Why Gmail failed", "2FA key rejected", buckets["bad_2fa_key"], "Google rejected the authenticator / 2fa.cn key"],
        ["Why Gmail failed", "Google robot check", buckets["captcha"], CAPTCHA_NOTE],
        ["Why Gmail failed", "Google wants a phone", buckets["wants_phone"], "Google asked for a phone number after login (unusual activity). Not recoverable this way."],
        [
            "Note for client",
            "Google robot check / captcha",
            buckets["captcha"],
            "Once Gmail hits captcha, Google will repeatedly ask for phone-number verification. "
            "After you press “Try another way” you can enter a backup code, but it still loops "
            "back to asking for a phone number. These accounts are not usable.",
        ],
        ["Why Gmail failed", "2-step verification — no working backup code", buckets["no_backup_2fa"], "Google asked for 2-step. No backup-code option, or the saved code failed"],
        ["Why Gmail failed", "Google asked for a selfie", buckets["selfie"], "Extra ID / face check — we skip these"],
        ["Why Gmail failed", "Couldn’t open the browser", buckets["browser_failed"], "The browser profile crashed or never opened, so login never started"],
        ["Why Gmail failed", "Other Gmail problem", buckets["other_gmail"], "Login did not finish for another reason"],
        ["Why Gmail failed", "Not tried yet", buckets["not_tried"], "We have not attempted this mailbox yet"],
        ["Where they live", "Browsers in Official (AdsPower)", len(official), "Open profiles in the Official group right now"],
        ["Where they live", "Browsers open now", len(open_ids), ""],
        ["Check", "Snapchat created + not created should equal all Gmails", buckets["snap_created"] + snap_not, f"Created {buckets['snap_created']} + not created {snap_not} = {len(accounts)}"],
    ]
    by_batch: dict[str, list] = {}
    for x in explained:
        by_batch.setdefault(_batch(x["row"]), []).append(x)
    for name in sorted(by_batch):
        totals_rows.extend(_batch_total_rows(name, by_batch[name]))

    gc = _client()
    spreadsheet = gc.open_by_key(sid)
    n_prof = _write_tab(
        spreadsheet,
        PROFILES_TAB,
        PROFILE_HEADERS,
        profile_rows,
        PROFILE_WIDTHS,
        highlight_alive=True,
    )
    n_mail = _write_tab(spreadsheet, MAIL_TAB, MAIL_HEADERS, mail_rows, MAIL_WIDTHS)
    n_tot = _write_tab(spreadsheet, TOTALS_TAB, TOTALS_HEADERS, totals_rows, TOTALS_WIDTHS)
    removed = _drop_other_tabs(spreadsheet)

    url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    print("ok", spreadsheet.title, flush=True)
    print("buckets", dict(buckets), flush=True)
    print("profiles", n_prof, "mails", n_mail, "totals", n_tot, flush=True)
    print("removed", removed, flush=True)
    print(url, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
