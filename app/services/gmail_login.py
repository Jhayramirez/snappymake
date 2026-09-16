"""Gmail Login pool — accounts we log INTO Gmail with (password + backup codes).

This is deliberately separate from `app.mail` / the IMAP `gmail_pool`, which is
the app-password pool used only to *receive* Snapchat OTP. Here we track the
accounts that get warmed in an AdsPower profile (login with password + one of
the 8-digit 2FA backup codes) and later used for Snapchat-via-Google signup.

TSV import format (one account per line):

    email <TAB> password <TAB> code1 <TAB> code2 ...

Codes may be pasted as "9538 5125" (4+4 with a space) or "95385125"; both mean
one code. Whitespace/tab layout is normalized defensively.
"""

from __future__ import annotations

import re
from typing import Any

from app.ads import AdsPowerError
from app.ads.client import AdsPowerClient
from app.db import (
    GMAIL_LOGIN_STATES,
    GMAIL_SNAP_STATES,
    gmail_login_rows,
    remove_gmail_login,
    set_backup_code_used,
    set_gmail_login_fields,
    upsert_gmail_login,
)

OFFICIAL_GROUP_NAME = "SnappyMake Official"
EXCLUDE_GROUP_NAME = "SnappyMail-Test"

_DIGITS = re.compile(r"\d+")


def _split_codes(tail_tokens: list[str]) -> list[str]:
    """Turn the trailing code tokens into a list of digit-string codes.

    Handles the mixed layouts seen in real lists:
      - "9538 5125" -> "95385125"  (two 4-digit halves = one code)
      - "98806525"  -> "98806525"  (single 8-digit code)
      - "3001348"   -> "3001348"   (7 digits, leading zero stripped on paste)
    """
    groups: list[str] = []
    for tok in tail_tokens:
        for m in _DIGITS.findall(tok):
            groups.append(m)

    codes: list[str] = []
    buf = ""
    for g in groups:
        if len(g) >= 7:
            # A standalone, complete code. Flush any half-code buffer first.
            if buf:
                codes.append(buf)
                buf = ""
            codes.append(g)
        else:
            buf += g
            if len(buf) >= 8:
                codes.append(buf)
                buf = ""
    if buf:
        codes.append(buf)
    return codes


def parse_login_lines(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse pasted TSV lines into accounts. Returns (parsed, invalid_lines)."""
    parsed: list[dict[str, Any]] = []
    invalid: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Prefer tab layout; fall back to any-whitespace when no tabs present.
        fields = [p.strip() for p in line.split("\t")] if "\t" in line else line.split()
        fields = [f for f in fields if f != ""]
        if len(fields) < 2 or "@" not in fields[0]:
            invalid.append(line)
            continue
        email = fields[0].lower()
        password = fields[1]
        codes = _split_codes(fields[2:])
        parsed.append({"email": email, "password": password, "codes": codes})
    return parsed, invalid


def import_accounts(text: str, exclude_emails: set[str] | None = None) -> dict[str, Any]:
    exclude = {e.lower() for e in (exclude_emails or set())}
    parsed, invalid = parse_login_lines(text)
    added: list[str] = []
    updated: list[str] = []
    excluded: list[str] = []
    seen: set[str] = set()
    for acc in parsed:
        email = acc["email"]
        if email in seen:
            continue
        seen.add(email)
        if email in exclude:
            excluded.append(email)
            continue
        result = upsert_gmail_login(email, acc["password"], acc["codes"])
        (added if result == "added" else updated).append(email)
    return {
        "added": added,
        "updated": updated,
        "excluded": excluded,
        "invalid": invalid,
        "pool": snapshot(),
    }


def snapshot() -> dict[str, Any]:
    rows = gmail_login_rows()
    codes_total = sum(len(r["codes"]) for r in rows)
    codes_used = sum(1 for r in rows for c in r["codes"] if c["used"])
    logged_in = sum(1 for r in rows if r["login_status"] == "login_ok")
    signed_up = sum(1 for r in rows if r["snap_status"] == "signed_up")
    return {
        "total": len(rows),
        "logged_in": logged_in,
        "pending": sum(1 for r in rows if r["login_status"] == "pending"),
        "signed_up": signed_up,
        "codes_total": codes_total,
        "codes_used": codes_used,
        "codes_left": codes_total - codes_used,
        "accounts": rows,
    }


def set_status(
    email: str,
    login_status: str | None = None,
    snap_status: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if login_status is not None and login_status not in GMAIL_LOGIN_STATES:
        raise ValueError(f"login_status must be one of {sorted(GMAIL_LOGIN_STATES)}")
    if snap_status is not None and snap_status not in GMAIL_SNAP_STATES:
        raise ValueError(f"snap_status must be one of {sorted(GMAIL_SNAP_STATES)}")
    set_gmail_login_fields(
        email, login_status=login_status, snap_status=snap_status, **extra
    )
    return snapshot()


def toggle_code(email: str, ordinal: int, used: bool) -> dict[str, Any]:
    set_backup_code_used(email, ordinal, used)
    return snapshot()


def remove(email: str) -> dict[str, Any]:
    ok = remove_gmail_login(email)
    return {"ok": ok, "pool": snapshot()}


# --------------------------------------------------------------------------- #
# AdsPower group helpers (exclusion + official group creation)
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _emails_from_profile(prof: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    name = str(prof.get("name") or "").strip().lower()
    if "@" in name:
        found.add(name)
    remark = str(prof.get("remark") or "")
    for m in _EMAIL_RE.findall(remark):
        found.add(m.lower())
    return found


def group_id_by_name(client: AdsPowerClient, name: str) -> str:
    for g in client.list_groups():
        if str(g.get("group_name") or "").strip() == name:
            return str(g.get("group_id") or "")
    return ""


def emails_in_group(client: AdsPowerClient, group_name: str) -> set[str]:
    """All Gmail addresses attached to profiles in a named AdsPower group."""
    gid = group_id_by_name(client, group_name)
    if not gid:
        return set()
    emails: set[str] = set()
    for prof in client.list_profiles(group_id=gid):
        emails |= _emails_from_profile(prof)
    return emails


def excluded_emails(client: AdsPowerClient) -> set[str]:
    """Emails to exclude on import — everything already in SnappyMail-Test."""
    return emails_in_group(client, EXCLUDE_GROUP_NAME)


def ensure_official_group(client: AdsPowerClient) -> dict[str, Any]:
    """Return the SnappyMake Official group id, creating it if missing."""
    gid = group_id_by_name(client, OFFICIAL_GROUP_NAME)
    created = False
    if not gid:
        try:
            data = client.create_group(OFFICIAL_GROUP_NAME, remark="SnappyMake Official")
        except AdsPowerError:
            # A racing create may have already made it; re-read.
            gid = group_id_by_name(client, OFFICIAL_GROUP_NAME)
            if not gid:
                raise
        else:
            gid = str(data.get("group_id") or "") or group_id_by_name(
                client, OFFICIAL_GROUP_NAME
            )
            created = True
    return {"group_id": gid, "name": OFFICIAL_GROUP_NAME, "created": created}
