"""Gmail Login pool — accounts we log INTO Gmail with (password + 2FA).

This is deliberately separate from `app.mail` / the IMAP `gmail_pool`, which is
the app-password pool used only to *receive* Snapchat OTP. Here we track the
accounts that get warmed in an AdsPower profile (login with password + Google
Authenticator TOTP / 8-digit backup codes) and later used for Snapchat-via-Google
signup.

TSV import format (one account per line):

    email <TAB> password <TAB> code1 <TAB> code2 ...
    email <TAB> password <TAB> TOTP_SECRET <TAB> code1 ...

TOTP secrets are the Base32 keys you paste into 2fa.cn (or an otpauth:// URI).
A recovery email in column 3 (another @gmail) is used on Google's
"Confirm your recovery email" 2SV screen. Spaced 8×4-char Base32 tails are
joined into a TOTP secret — they are never treated as 8-digit backup codes.
Codes may be pasted as "9538 5125" (4+4 with a space) or "95385125"; both mean
one backup code. Whitespace/tab layout is normalized defensively.
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
from app.services.totp_2fa import looks_like_totp_secret, normalize_secret

OFFICIAL_GROUP_NAME = "SnappyMake Official"
READY_GROUP_NAME = "SnappyOfficial - Ready to Use (Gmail Method)"
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
        totp_secret = ""
        recovery_email = ""
        rest: list[str] = []
        for tok in fields[2:]:
            if "@" in tok and tok.strip().lower() != email:
                recovery_email = tok.strip().lower()
                continue
            rest.append(tok)
        joined = re.sub(r"[\s\-]", "", "".join(rest))
        if looks_like_totp_secret(joined):
            totp_secret = normalize_secret(joined)
            codes: list[str] = []
        else:
            for tok in rest:
                if looks_like_totp_secret(tok):
                    totp_secret = normalize_secret(tok)
            leftover = [t for t in rest if not looks_like_totp_secret(t)]
            codes = _split_codes(leftover) if not totp_secret else []
            if totp_secret:
                codes = []
        parsed.append(
            {
                "email": email,
                "password": password,
                "codes": codes,
                "totp_secret": totp_secret,
                "recovery_email": recovery_email,
            }
        )
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
        result = upsert_gmail_login(
            email,
            acc["password"],
            acc["codes"],
            totp_secret=acc.get("totp_secret") or None,
            recovery_email=acc.get("recovery_email") or None,
        )
        (added if result == "added" else updated).append(email)
    return {
        "added": added,
        "updated": updated,
        "excluded": excluded,
        "invalid": invalid,
        "pool": snapshot(),
    }


_TOTP_HEADERS = (
    "2fa",
    "totp",
    "secret",
    "otp secret",
    "2fa secret",
    "authenticator",
    "双重密钥",
    "密钥",
)
_RECOVERY_HEADERS = ("recovery email", "recovery mail", "recovery", "backup email")
_EMAIL_HEADERS = ("email", "gmail", "mail", "账号", "邮箱")
_PASS_HEADERS = ("password", "pass", "pwd", "密码")


def _header_key(name: object) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def import_xlsx(path: str, *, batch_name: str = "", exclude_emails: set[str] | None = None) -> dict[str, Any]:
    """Import Gmail rows from Excel. Detects 2fa.cn secret columns by header name."""
    from openpyxl import load_workbook

    exclude = {e.lower() for e in (exclude_emails or set())}
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return {"added": [], "updated": [], "excluded": [], "invalid": [], "pool": snapshot()}

    header = [_header_key(c) for c in rows[0]]
    has_header = any(
        h in _EMAIL_HEADERS or h in _PASS_HEADERS or h in _TOTP_HEADERS or h in _RECOVERY_HEADERS
        for h in header
    )

    def col(*names: str) -> int | None:
        for i, h in enumerate(header):
            if h in names or any(n in h for n in names):
                return i
        return None

    i_email = col(*_EMAIL_HEADERS) if has_header else 0
    i_pass = col(*_PASS_HEADERS) if has_header else 1
    i_totp = col(*_TOTP_HEADERS) if has_header else None
    i_rec = col(*_RECOVERY_HEADERS) if has_header else None
    body = rows[1:] if has_header else rows

    lines: list[str] = []
    for row in body:
        cells = list(row or ())
        if not cells:
            continue
        email = str(cells[i_email] if i_email is not None and i_email < len(cells) else "").strip()
        password = str(cells[i_pass] if i_pass is not None and i_pass < len(cells) else "").strip()
        if "@" not in email or not password:
            continue
        extra: list[str] = []
        skip = {i_email, i_pass, i_totp, i_rec}
        if i_totp is not None and i_totp < len(cells) and cells[i_totp]:
            extra.append(str(cells[i_totp]).strip())
        if i_rec is not None and i_rec < len(cells) and cells[i_rec]:
            extra.append(str(cells[i_rec]).strip())
        for j, cell in enumerate(cells):
            if j in skip or cell is None:
                continue
            extra.append(str(cell).strip())
        lines.append("\t".join([email, password, *extra]))

    result = import_accounts("\n".join(lines), exclude_emails=exclude)
    if batch_name.strip():
        from app.db import set_gmail_login_fields as _set

        for email in (result.get("added") or []) + (result.get("updated") or []):
            _set(email, batch_name=batch_name.strip())
        result["pool"] = snapshot()
    return result


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
        "wrong_password": sum(1 for r in rows if r["login_status"] == "wrong_password"),
        "signed_up": signed_up,
        "codes_total": codes_total,
        "codes_used": codes_used,
        "codes_left": codes_total - codes_used,
        "totp_ready": sum(1 for r in rows if (r.get("totp_secret") or "").strip()),
        "recovery_ready": sum(1 for r in rows if (r.get("recovery_email") or "").strip()),
        "accounts": rows,
    }


TOTP_REJECTED_ERR = "2FA key rejected"


def normalize_last_error(err: str | None) -> str | None:
    """Keep TOTP failures as a stable DB label, not a Google URL dump."""
    if err is None:
        return None
    text = str(err).strip()
    if not text:
        return text
    low = text.lower()
    if (
        "2fa key rejected" in low
        or "totp_rejected" in low
        or "challenge/totp" in low
        or ("totp never accepted" in low)
    ):
        return TOTP_REJECTED_ERR
    return text


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
    if "last_error" in extra:
        extra["last_error"] = normalize_last_error(extra.get("last_error"))
    set_gmail_login_fields(
        email, login_status=login_status, snap_status=snap_status, **extra
    )
    return snapshot()


def assign_batch_if_empty(batch_name: str) -> int:
    """Tag every untagged mailbox with this batch. Does not overwrite an existing batch."""
    name = (batch_name or "").strip()
    if not name:
        return 0
    n = 0
    for row in gmail_login_rows():
        if (row.get("batch_name") or "").strip():
            continue
        set_gmail_login_fields(row["email"], batch_name=name)
        n += 1
    return n


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


_PROXY_RE = re.compile(r"Proxy:\s*([^·]+)", re.I)


def status_from_remark(remark: str) -> dict[str, str]:
    """Map an AdsPower remark to gmail_login fields."""
    r = (remark or "").lower()
    login = "pending"
    snap = "none"
    err = (remark or "").strip()[-240:]
    proxy = ""
    m = _PROXY_RE.search(remark or "")
    if m:
        proxy = m.group(1).strip()
    if "email verified" in r or "snap_ok" in r or "snap_welcome" in r:
        login = "login_ok"
        snap = "signed_up"
    elif "already associated" in r:
        login = "login_ok"
        snap = "failed"
        err = "google email already associated with Snapchat"
    elif "wrong_password" in r or "wrong password" in r:
        login = "wrong_password"
        snap = "failed"
    elif "captcha" in r:
        login = "captcha"
        snap = "failed"
    elif "selfie" in r:
        login = "selfie"
        snap = "failed"
    elif "2sv" in r or "all_codes_bad" in r or "fail:not_in" in r:
        login = "error"
        snap = "failed"
        err = err or "FAIL:2sv_no_backup"
    elif "login_ok" in r or "gmail_ok" in r:
        login = "login_ok"
        snap = "none"
    elif "gmail_not_inbox" in r or "gmail_lost" in r:
        login = "error"
        snap = "failed"
    return {"login_status": login, "snap_status": snap, "last_error": err, "proxy_label": proxy}


def ingest_adspower_mails(client: AdsPowerClient) -> dict[str, int]:
    """Copy Official + SnappyMail-Test profiles into gmail_login for one statistic pool.

    Does not overwrite an email that is already signed_up.
    Test leftovers keep group_name=SnappyMail-Test so Official Start will not pick them.
    """
    counts = {"added": 0, "updated": 0, "kept_signed_up": 0}
    existing = {row["email"].lower(): row for row in gmail_login_rows()}
    jobs = [
        (OFFICIAL_GROUP_NAME, group_id_by_name(client, OFFICIAL_GROUP_NAME)),
        (EXCLUDE_GROUP_NAME, group_id_by_name(client, EXCLUDE_GROUP_NAME)),
    ]
    # Best status wins when the same Gmail exists twice (Test duplicates).
    rank = {
        ("login_ok", "signed_up"): 0,
        ("login_ok", "failed"): 1,
        ("login_ok", "none"): 2,
        ("wrong_password", "failed"): 3,
        ("captcha", "failed"): 3,
        ("selfie", "failed"): 3,
        ("error", "failed"): 4,
        ("pending", "none"): 9,
    }
    best: dict[str, dict[str, Any]] = {}
    for group_name, gid in jobs:
        if not gid:
            continue
        for p in client.list_profiles(group_id=gid):
            name = str(p.get("name") or "")
            remark = str(p.get("remark") or "")
            email = name.strip().lower() if "@" in name else ""
            if not email:
                found = _EMAIL_RE.findall(remark)
                email = found[0].lower() if found else ""
            if not email:
                continue
            password = ""
            m_pass = re.search(r"Pass:\s*([^·]+)", remark)
            if m_pass:
                password = m_pass.group(1).strip()
            raw_bak = ""
            m_bak = re.search(r"Backup:\s*([0-9 ]+)", remark)
            if m_bak:
                raw_bak = m_bak.group(1)
            digits = "".join(ch for ch in raw_bak if ch.isdigit())
            codes = [digits[i : i + 8] for i in range(0, len(digits), 8) if len(digits[i : i + 8]) >= 7]
            st = status_from_remark(remark)
            rec = {
                "email": email,
                "password": password,
                "codes": codes,
                "profile_id": str(p.get("profile_id") or p.get("user_id") or ""),
                "group_name": group_name,
                **st,
            }
            prev = best.get(email)
            if prev is None or rank.get((rec["login_status"], rec["snap_status"]), 8) < rank.get(
                (prev["login_status"], prev["snap_status"]), 8
            ):
                best[email] = rec
            elif prev is not None and group_name == OFFICIAL_GROUP_NAME:
                rec["group_name"] = OFFICIAL_GROUP_NAME
                if rec["snap_status"] == "none":
                    rec["snap_status"] = prev["snap_status"]
                    rec["login_status"] = prev["login_status"]
                best[email] = rec
    for email, rec in best.items():
        old = existing.get(email)
        if old and old.get("snap_status") == "signed_up":
            set_gmail_login_fields(
                email,
                profile_id=rec["profile_id"] or old.get("profile_id"),
                group_name=OFFICIAL_GROUP_NAME if rec["group_name"] == OFFICIAL_GROUP_NAME else (old.get("group_name") or rec["group_name"]),
                proxy_label=rec["proxy_label"] or old.get("proxy_label") or "",
                batch_name=old.get("batch_name") or None,
            )
            counts["kept_signed_up"] += 1
            continue
        result = upsert_gmail_login(email, rec["password"], rec["codes"])
        set_gmail_login_fields(
            email,
            login_status=rec["login_status"],
            snap_status=rec["snap_status"],
            profile_id=rec["profile_id"],
            proxy_label=rec["proxy_label"],
            group_name=rec["group_name"],
            last_error=rec["last_error"],
            batch_name=(old.get("batch_name") if old else "") or None,
        )
        counts["added" if result == "added" else "updated"] += 1
    return counts


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


def ensure_ready_group(client: AdsPowerClient) -> dict[str, Any]:
    """Return the Ready to Use (Gmail Method) group id, creating it if missing."""
    gid = group_id_by_name(client, READY_GROUP_NAME)
    created = False
    if not gid:
        try:
            data = client.create_group(READY_GROUP_NAME, remark="SnappyOfficial Ready to Use")
        except AdsPowerError:
            gid = group_id_by_name(client, READY_GROUP_NAME)
            if not gid:
                raise
        else:
            gid = str(data.get("group_id") or "") or group_id_by_name(
                client, READY_GROUP_NAME
            )
            created = True
    return {"group_id": gid, "name": READY_GROUP_NAME, "created": created}
