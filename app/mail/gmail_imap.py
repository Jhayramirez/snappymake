from __future__ import annotations

import email as email_lib
import imaplib
import re
import time
from datetime import timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable

from app.config import DATA_DIR, settings
from app.db import (
    gmail_pool_rows,
    gmail_used_set,
    mark_gmail_unused,
    mark_gmail_used,
    rename_gmail_email,
    set_gmail_error,
)

ACCOUNTS_PATH = DATA_DIR / "gmail_accounts.txt"
CODE_RE = re.compile(r"\b(\d{6})\b")
FOLDERS = ("INBOX", "[Gmail]/All Mail", "[Gmail]/Spam")
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
LINK_KEYWORDS = (
    "verify",
    "verification",
    "confirm",
    "activate",
    "sign in",
    "signin",
    "log in",
    "login",
    "magic",
    "reset",
    "continue",
    "accounts.snapchat",
    "snapchat.com",
)
LINK_SKIP = (
    "unsubscribe",
    "privacy",
    "policy",
    "terms",
    "help",
    "support",
    "apps.apple.com",
    "play.google.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
    "youtube.com",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".css",
)


class GmailImapError(RuntimeError):
    pass


def _normalize_pw(pw: str) -> str:
    return (pw or "").replace(" ", "").strip()


def parse_account_lines(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sep = "|" if "|" in line else ":"
        parts = [p.strip() for p in line.split(sep)]
        if len(parts) < 2 or "@" not in parts[0]:
            continue
        out.append(
            {
                "email": parts[0].strip(),
                "app_password": _normalize_pw(parts[1]),
            }
        )
    return out


def load_accounts() -> list[dict[str, str]]:
    if not ACCOUNTS_PATH.exists():
        return []
    try:
        rows = parse_account_lines(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except OSError:
        return []
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        email_addr = row["email"].strip()
        if "@" not in email_addr or not row["app_password"]:
            continue
        seen[email_addr.lower()] = {
            "email": email_addr,
            "app_password": row["app_password"],
        }
    return list(seen.values())


def add_accounts(text: str) -> dict[str, list[str]]:
    existing = {a["email"].lower() for a in load_accounts()}
    added, skipped, invalid, lines = [], [], [], []
    for row in parse_account_lines(text):
        email_addr = row["email"]
        pw = row["app_password"]
        if "@" not in email_addr or not pw:
            invalid.append(email_addr or "(blank)")
            continue
        if email_addr.lower() in existing:
            skipped.append(email_addr)
            continue
        lines.append(f"{email_addr}|{pw}")
        added.append(email_addr)
        existing.add(email_addr.lower())
    if lines:
        ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if ACCOUNTS_PATH.exists():
            current = ACCOUNTS_PATH.read_text(encoding="utf-8")
            if current and not current.endswith("\n"):
                prefix = "\n"
        with ACCOUNTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(prefix + "\n".join(lines) + "\n")
    return {"added": added, "skipped": skipped, "invalid": invalid}


def remove_account(email_addr: str) -> bool:
    key = (email_addr or "").strip().lower()
    if not key or not ACCOUNTS_PATH.exists():
        return False
    try:
        lines = ACCOUNTS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False
    kept, removed = [], False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        sep = "|" if "|" in stripped else ":"
        current = stripped.split(sep)[0].strip().lower()
        if current == key:
            removed = True
            continue
        kept.append(line)
    if removed:
        ACCOUNTS_PATH.write_text("".join(kept), encoding="utf-8")
        mark_gmail_unused(key)
    return removed


def pool_snapshot(*, include_secrets: bool = False) -> dict[str, Any]:
    accounts = load_accounts()
    meta = {row["email"].lower(): row for row in gmail_pool_rows()}
    emails = []
    used_n = 0
    for acct in accounts:
        info = meta.get(acct["email"].lower()) or {}
        used = bool(info.get("used"))
        if used:
            used_n += 1
        row = {
            "email": acct["email"],
            "used": used,
            "status": "used" if used else "available",
            "profile_id": info.get("profile_id") or "",
            "used_at": int(info.get("used_at") or 0),
            "last_error": info.get("last_error") or "",
        }
        if include_secrets:
            row["app_password"] = acct["app_password"]
        emails.append(row)
    return {
        "total": len(accounts),
        "unused": len(accounts) - used_n,
        "used": used_n,
        "emails": emails,
    }


def _account_line_email(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    sep = "|" if "|" in stripped else ":"
    return stripped.split(sep)[0].strip().lower()


def update_account(current_email: str, *, email: str | None = None, app_password: str | None = None) -> dict[str, str]:
    key = (current_email or "").strip().lower()
    if not key:
        raise GmailImapError("Missing current email.")
    found = _find_account(key)
    if found is None:
        raise GmailImapError("Unknown account (not in your Gmail pool).")
    new_email = (email if email is not None else found["email"]).strip()
    new_pw = _normalize_pw(app_password if app_password is not None else found["app_password"])
    if "@" not in new_email or not new_pw:
        raise GmailImapError("Need a valid email and app password.")
    if new_email.lower() != key:
        clash = _find_account(new_email)
        if clash is not None:
            raise GmailImapError(f"{new_email} is already in the pool.")
    if not ACCOUNTS_PATH.exists():
        raise GmailImapError("Gmail list file is missing.")
    try:
        lines = ACCOUNTS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        raise GmailImapError(f"Could not read Gmail list: {exc}") from exc
    replaced = False
    out: list[str] = []
    for line in lines:
        if _account_line_email(line) == key:
            ending = "\n" if line.endswith("\n") else ""
            out.append(f"{new_email}|{new_pw}{ending}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        ending = "" if (not out or out[-1].endswith("\n")) else "\n"
        out.append(f"{ending}{new_email}|{new_pw}\n")
    ACCOUNTS_PATH.write_text("".join(out), encoding="utf-8")
    if new_email.lower() != key:
        rename_gmail_email(key, new_email)
    return {"email": new_email, "app_password": new_pw}


def claim_unused_mailbox(profile_id: str = "") -> dict[str, str] | None:
    """Pick the next unused Gmail. Caller must mark it used after OTP succeeds."""
    used = gmail_used_set()
    for acct in load_accounts():
        if acct["email"].lower() in used:
            continue
        return acct
    return None


def release_mailbox(email: str) -> None:
    if email:
        mark_gmail_unused(email)


def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _decode_part(part) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        try:
            return str(part.get_payload())
        except Exception:
            return ""


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_parts(msg) -> tuple[str, str]:
    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain" and not text_body:
                text_body = _decode_part(part)
            elif ctype == "text/html" and not html_body:
                html_body = _decode_part(part)
    else:
        payload = _decode_part(msg)
        if msg.get_content_type() == "text/html":
            html_body = payload
        else:
            text_body = payload
    return text_body, html_body


def _message_text(msg) -> str:
    text_body, html_body = _extract_parts(msg)
    if (text_body or "").strip():
        return re.sub(r"\s+", " ", text_body).strip()
    return _strip_html(html_body)


def _looks_like_snapchat(from_addr: str, subject: str, body: str) -> bool:
    blob = f"{from_addr} {subject} {body}".lower()
    return "snapchat" in blob


def extract_otp(subject: str, body: str, from_addr: str = "") -> str | None:
    if not _looks_like_snapchat(from_addr, subject, body):
        return None
    for blob in (subject, body):
        match = CODE_RE.search(blob or "")
        if match:
            code = match.group(1)
            if code != "000000":
                return code
    return None


def _clean_url(url: str) -> str:
    return (url or "").rstrip(".,);]'\"")


def extract_verification_link(subject: str, body: str, html: str = "") -> str | None:
    """Best http(s) link from an email, preferring verify/confirm/sign-in URLs."""
    seen: list[str] = []
    for blob in (html, body, subject):
        for match in URL_RE.finditer(blob or ""):
            url = _clean_url(match.group(0))
            if url and url not in seen:
                seen.append(url)
    if not seen:
        return None
    best = None
    best_score = -1
    for url in seen:
        low = url.lower()
        if any(skip in low for skip in LINK_SKIP):
            continue
        score = 0
        for i, kw in enumerate(LINK_KEYWORDS):
            if kw in low:
                score += len(LINK_KEYWORDS) - i
        if "snapchat" in low:
            score += 5
        if score > best_score:
            best_score = score
            best = url
    if best is not None and best_score > 0:
        return best
    # nothing keyword-matched: fall back to the first non-skipped link
    for url in seen:
        if not any(skip in url.lower() for skip in LINK_SKIP):
            return url
    return None


def fetch_latest_signals(acct: dict[str, str], *, since: float = 0.0, scan: int = 12) -> dict[str, Any]:
    """Newest verification link and OTP for an account, scanned across folders."""
    conn = _connect(acct)
    best_link: dict[str, Any] | None = None
    best_otp: dict[str, Any] | None = None
    try:
        for folder in FOLDERS:
            try:
                conn.select(_imap_quote(folder), readonly=True)
            except Exception:
                continue
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()[-scan:]
            for uid in reversed(uids):
                uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
                typ, msg_data = conn.uid("FETCH", uid_s, "(RFC822)")
                raw = _fetch_bytes(msg_data)
                if typ != "OK" or not raw:
                    continue
                msg = email_lib.message_from_bytes(raw)
                if since and not _message_after(msg, since):
                    continue
                try:
                    dt = parsedate_to_datetime(msg.get("Date"))
                    ts = int(dt.timestamp()) if dt else 0
                except Exception:
                    ts = 0
                from_name, from_addr = parseaddr(_decode_header(msg.get("From")))
                subject = _decode_header(msg.get("Subject")) or "(no subject)"
                text_body, html_body = _extract_parts(msg)
                plain = (text_body or "").strip() or _strip_html(html_body)
                meta = {
                    "subject": subject,
                    "from": from_addr or from_name,
                    "ts": ts,
                    "folder": folder,
                    "uid": uid_s,
                }
                link = extract_verification_link(subject, plain, html_body)
                if link and (best_link is None or ts >= best_link["ts"]):
                    best_link = {**meta, "link": link}
                code = extract_otp(subject, plain or html_body, from_addr or from_name)
                if code and (best_otp is None or ts >= best_otp["ts"]):
                    best_otp = {**meta, "otp": code}
        newest = max(
            [x for x in (best_link, best_otp) if x],
            key=lambda x: x["ts"],
            default=None,
        )
        return {
            "email": acct["email"],
            "link": (best_link or {}).get("link"),
            "otp": (best_otp or {}).get("otp"),
            "subject": (newest or {}).get("subject"),
            "from": (newest or {}).get("from"),
            "ts": (newest or {}).get("ts") or 0,
            "folder": (newest or {}).get("folder"),
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def latest_signals_for(email_addr: str, *, since: float = 0.0) -> dict[str, Any]:
    acct = _find_account(email_addr)
    if acct is None:
        raise GmailImapError("Unknown account (not in your Gmail pool).")
    return fetch_latest_signals(acct, since=since)


def _connect(acct: dict[str, str]) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(settings.gmail_imap_host, settings.gmail_imap_port)
    except Exception as exc:
        raise GmailImapError(f"Could not reach Gmail IMAP: {exc}") from exc
    try:
        conn.login(acct["email"], acct["app_password"])
    except imaplib.IMAP4.error as exc:
        try:
            conn.logout()
        except Exception:
            pass
        raise GmailImapError(
            f"IMAP login failed for {acct['email']}. Check 2-Step, IMAP enabled, and the app password."
        ) from exc
    return conn


def _message_after(msg, since: float) -> bool:
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt is None:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() >= since - 30
    except Exception:
        return True


def fetch_snapchat_otp(acct: dict[str, str], *, since: float) -> str | None:
    conn = _connect(acct)
    try:
        for folder in FOLDERS:
            try:
                quoted = "INBOX" if folder.upper() == "INBOX" else f'"{folder}"'
                typ, _ = conn.select(quoted, readonly=True)
            except Exception:
                continue
            if typ != "OK":
                continue
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()[-12:]
            for uid in reversed(uids):
                uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
                typ, msg_data = conn.uid("FETCH", uid_s, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = None
                for item in msg_data:
                    if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                        raw = item[1]
                        break
                if not raw:
                    continue
                msg = email_lib.message_from_bytes(raw)
                if not _message_after(msg, since):
                    continue
                from_name, from_addr = parseaddr(_decode_header(msg.get("From")))
                subject = _decode_header(msg.get("Subject"))
                body = _message_text(msg)
                code = extract_otp(subject, body, from_addr or from_name)
                if code:
                    return code
        return None
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def wait_for_snapchat_otp(
    acct: dict[str, str],
    *,
    since: float,
    timeout: float = 90,
    poll: float = 4,
    on_wait: Callable[[str], None] | None = None,
) -> str:
    deadline = time.time() + timeout
    last_error = ""
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        left = int(deadline - time.time())
        if on_wait:
            on_wait(f"IMAP poll {attempt} for {acct['email']} · {left}s left")
        try:
            code = fetch_snapchat_otp(acct, since=since)
            if code:
                return code
        except GmailImapError as exc:
            last_error = str(exc)
            set_gmail_error(acct["email"], last_error)
            if on_wait:
                on_wait(last_error)
        time.sleep(poll)
    detail = last_error or "no Snapchat code in INBOX / All Mail / Spam"
    raise GmailImapError(f"Timed out waiting for OTP in {acct['email']}: {detail}")


def _find_account(email_addr: str) -> dict[str, str] | None:
    key = (email_addr or "").strip().lower()
    for acct in load_accounts():
        if acct["email"].lower() == key:
            return acct
    return None


def _imap_quote(name: str) -> str:
    if (name or "").upper() == "INBOX":
        return "INBOX"
    raw = name or "INBOX"
    return '"' + raw.replace('"', '\\"') + '"'


def _fetch_bytes(msg_data) -> bytes | None:
    for item in msg_data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return item[1]
    return None


def list_folders(email_addr: str) -> list[str]:
    acct = _find_account(email_addr)
    if acct is None:
        raise GmailImapError("Unknown account (not in your Gmail pool).")
    conn = _connect(acct)
    try:
        typ, data = conn.list()
        names: list[str] = []
        if typ == "OK" and data:
            for line in data:
                if not line:
                    continue
                raw = line if isinstance(line, bytes) else bytes(line)
                decoded = raw.decode(errors="replace")
                if "\\Noselect" in decoded or "\\NonExistent" in decoded:
                    continue
                name = decoded.split(' "')[-1].strip().strip('"')
                if name:
                    names.append(name)
        names = sorted(set(names), key=lambda n: (n.lower() != "inbox", n.lower()))
        return names or ["INBOX"]
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_inbox(email_addr: str, folder: str = "INBOX", limit: int = 30) -> tuple[list[dict[str, Any]], int]:
    acct = _find_account(email_addr)
    if acct is None:
        raise GmailImapError("Unknown account (not in your Gmail pool).")
    conn = _connect(acct)
    try:
        typ, _ = conn.select(_imap_quote(folder), readonly=True)
        if typ != "OK":
            raise GmailImapError(f"Could not open folder '{folder}'.")
        typ, search_data = conn.uid("SEARCH", None, "ALL")
        if typ != "OK" or not search_data or not search_data[0]:
            return [], 0
        uids = search_data[0].split()
        total = len(uids)
        messages: list[dict[str, Any]] = []
        for uid in reversed(uids[-limit:]):
            uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
            typ, msg_data = conn.uid(
                "FETCH",
                uid_s,
                "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])",
            )
            raw = _fetch_bytes(msg_data)
            if typ != "OK" or not raw:
                continue
            msg = email_lib.message_from_bytes(raw)
            from_name, from_addr = parseaddr(_decode_header(msg.get("From")))
            subject = _decode_header(msg.get("Subject")) or "(no subject)"
            date_raw = _decode_header(msg.get("Date"))
            ts = 0
            try:
                dt = parsedate_to_datetime(msg.get("Date"))
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = int(dt.timestamp())
            except Exception:
                ts = 0
            blob = f"{from_addr} {from_name} {subject}".lower()
            messages.append(
                {
                    "uid": uid_s,
                    "from_name": from_name,
                    "from_addr": from_addr,
                    "subject": subject,
                    "date": date_raw,
                    "ts": ts,
                    "snapchat": "snapchat" in blob,
                }
            )
        return messages, total
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_message_body(email_addr: str, folder: str, uid: str) -> dict[str, Any]:
    acct = _find_account(email_addr)
    if acct is None:
        raise GmailImapError("Unknown account (not in your Gmail pool).")
    conn = _connect(acct)
    try:
        typ, _ = conn.select(_imap_quote(folder), readonly=True)
        if typ != "OK":
            raise GmailImapError(f"Could not open folder '{folder}'.")
        typ, msg_data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = _fetch_bytes(msg_data)
        if typ != "OK" or not raw:
            raise GmailImapError("Could not load that message.")
        msg = email_lib.message_from_bytes(raw)
        from_name, from_addr = parseaddr(_decode_header(msg.get("From")))
        subject = _decode_header(msg.get("Subject")) or "(no subject)"
        text_body, html_body = _extract_parts(msg)
        plain = (text_body or "").strip() or _strip_html(html_body)
        html = (html_body or "").strip()
        return {
            "uid": str(uid),
            "from_name": from_name,
            "from_addr": from_addr,
            "subject": subject,
            "date": _decode_header(msg.get("Date")),
            "body": plain[:20000],
            "html": html[:400000],
            "body_is_html": bool(html),
            "otp": extract_otp(subject, plain or html, from_addr or from_name),
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass
