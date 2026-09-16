#!/usr/bin/env python3
"""SnappyMake IMAP companion.

A tiny, self-contained HTTP service that the SnappyMake browser extension talks
to. It is INDEPENDENT of the main SnappyMake dashboard/app package — it only
uses the Python standard library.

Why it exists: a browser MV3 extension cannot open raw TCP sockets, so it cannot
speak IMAP. This companion does the IMAP work and exposes two tiny HTTP GET
endpoints (with permissive CORS) that the extension popup calls:

  GET /api/ext/context
      Ask the AdsPower Local API which profiles have an OPEN browser, parse the
      Gmail address (and app password) out of each profile's remark, and return
      { ok, accounts: [{ profile_id, name, email }] }  (password is NOT returned).

  GET /api/ext/latest?account=<email>[&password=<app_password>]
      Resolve the app password (from the profile remark via AdsPower, or from the
      optional &password= param), connect over IMAP SSL, scan INBOX + All Mail +
      Spam, and return the newest verification link + OTP:
      { ok, email, link, otp, subject, from, ts, folder }.

Config (env vars, all optional):
  COMPANION_PORT       listen port                 (default 8799)
  COMPANION_HOST       listen host                 (default 127.0.0.1)
  ADSPOWER_BASE        AdsPower Local API base URL  (default: auto-detect)
  ADSPOWER_API_KEY     AdsPower API key, if your Local API requires one
  IMAP_HOST            IMAP server                 (default imap.gmail.com)
  IMAP_PORT            IMAP SSL port               (default 993)
  IMAP_SCAN            messages scanned per folder (default 12)
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
PORT = int(os.environ.get("COMPANION_PORT", "8799"))
HOST = os.environ.get("COMPANION_HOST", "127.0.0.1")
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_SCAN = int(os.environ.get("IMAP_SCAN", "12"))
ADSPOWER_API_KEY = os.environ.get("ADSPOWER_API_KEY", "").strip()

# AnyMessage (short-term email) — optional alternative provider.
ANYMESSAGE_API = "https://api.anymessage.shop"
# Default token used when env / query param is empty (extension can override).
_DEFAULT_ANYMESSAGE_TOKEN = "7ew8qIG7h1F9GuRahID2N5z5GOSonEhI"
ANYMESSAGE_TOKEN = (os.environ.get("ANYMESSAGE_TOKEN") or _DEFAULT_ANYMESSAGE_TOKEN).strip()
ANYMESSAGE_SITE = os.environ.get("ANYMESSAGE_SITE", "snapchat.com").strip()

ADSPOWER_BASES = []
if os.environ.get("ADSPOWER_BASE", "").strip():
    ADSPOWER_BASES.append(os.environ["ADSPOWER_BASE"].strip().rstrip("/"))
ADSPOWER_BASES += [
    "http://local.adspower.net:50325",
    "http://127.0.0.1:50325",
    "http://local.adspower.com:50325",
    "http://localhost:50325",
]
_working_base: str | None = None

FOLDERS = ("INBOX", "[Gmail]/All Mail", "[Gmail]/Spam")

# --------------------------------------------------------------------------- #
# Link / OTP extraction  (copied from app/mail/gmail_imap.py so this stands
# alone; keep the two in sync if the ranking logic ever changes).
# --------------------------------------------------------------------------- #
CODE_RE = re.compile(r"\b(\d{6})\b")
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
LINK_KEYWORDS = (
    "verify", "verification", "confirm", "activate", "sign in", "signin",
    "log in", "login", "magic", "reset", "continue",
    "accounts.snapchat", "snapchat.com",
)
LINK_SKIP = (
    "unsubscribe", "privacy", "policy", "terms", "help", "support",
    "apps.apple.com", "play.google.com", "facebook.com", "twitter.com",
    "instagram.com", "youtube.com", ".png", ".jpg", ".jpeg", ".gif", ".css",
)


def _clean_url(url: str) -> str:
    return (url or "").rstrip(".,);]'\"")


def extract_verification_link(subject: str, body: str, html: str = "") -> str | None:
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
    for url in seen:
        if not any(skip in url.lower() for skip in LINK_SKIP):
            return url
    return None


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


# --------------------------------------------------------------------------- #
# Email parsing helpers  (copied / trimmed from app/mail/gmail_imap.py)
# --------------------------------------------------------------------------- #
def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_part(part) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return ""


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


def _imap_quote(name: str) -> str:
    if (name or "").upper() == "INBOX":
        return "INBOX"
    return '"' + (name or "INBOX").replace('"', '\\"') + '"'


def _fetch_bytes(msg_data):
    for item in msg_data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return item[1]
    return None


# --------------------------------------------------------------------------- #
# AdsPower Local API
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"Email:\s*([^\s·|]+@[^\s·|]+)", re.I)
PASS_RE = re.compile(r"Pass:\s*([^\s·|]+)", re.I)
AMID_RE = re.compile(r"AMID:\s*([^\s·|]+)", re.I)
SITE_RE = re.compile(r"Site:\s*([^\s·|]+)", re.I)


def _http_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if ADSPOWER_API_KEY:
        headers["Authorization"] = f"Bearer {ADSPOWER_API_KEY}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _adspower(path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    """Call the AdsPower Local API, auto-detecting a reachable base URL."""
    global _working_base
    bases = ([_working_base] if _working_base else []) + [b for b in ADSPOWER_BASES if b != _working_base]
    last_err: Exception | None = None
    for base in bases:
        try:
            payload = _http_json(base + path, method=method, body=body, timeout=timeout)
            _working_base = base
            return payload
        except Exception as exc:  # connection refused / timeout / dns
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise RuntimeError("No AdsPower base URL configured")


def _active_profile_ids() -> list[str]:
    payload = _adspower("/api/v1/browser/local-active", timeout=4.0)
    data = payload.get("data") or {}
    raw = data if isinstance(data, list) else (data.get("list") or data.get("user_ids") or [])
    ids: list[str] = []
    for item in raw:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            pid = str(item.get("user_id") or item.get("profile_id") or "")
            if pid:
                ids.append(pid)
    return ids


def _list_profiles() -> list[dict]:
    """Return profile rows (each ideally carrying a `remark`).

    Tries the v2 endpoint first (same one the dashboard uses, known to include
    `remark`), then falls back to the v1 `user/list` endpoint.
    """
    rows: list[dict] = []
    # v2
    try:
        page = 1
        while True:
            payload = _adspower(
                "/api/v2/browser-profile/list",
                method="POST",
                body={"page": page, "limit": 100, "sort_type": "created_time", "sort_order": "desc"},
                timeout=6.0,
            )
            data = payload.get("data") or {}
            chunk = data.get("list") or []
            rows.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
        if rows:
            return rows
    except Exception:
        rows = []
    # v1 fallback
    try:
        page = 1
        while True:
            q = urllib.parse.urlencode({"page": page, "page_size": 100})
            payload = _adspower(f"/api/v1/user/list?{q}", timeout=6.0)
            data = payload.get("data") or {}
            chunk = data.get("list") or []
            rows.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
    except Exception:
        pass
    return rows


def _row_id(row: dict) -> str:
    return str(row.get("profile_id") or row.get("user_id") or row.get("serial_number") or "")


def _creds_from_remark(remark: str) -> tuple[str | None, str | None]:
    remark = remark or ""
    em = EMAIL_RE.search(remark)
    pw = PASS_RE.search(remark)
    return (em.group(1) if em else None, pw.group(1) if pw else None)


def open_accounts() -> tuple[list[dict], dict[str, str]]:
    """Return (accounts_for_context, email->password map) for OPEN profiles only.

    If no profile browser is currently open, this returns an empty list — it
    must NOT fall back to listing every profile.
    """
    active = set(_active_profile_ids())
    if not active:
        return [], {}
    rows = _list_profiles()
    accounts: list[dict] = []
    pw_map: dict[str, str] = {}
    for row in rows:
        pid = _row_id(row)
        if pid not in active:
            continue
        remark = row.get("remark") or ""
        email_addr, password = _creds_from_remark(remark)
        if not email_addr:
            continue
        am = AMID_RE.search(remark)
        site_m = SITE_RE.search(remark)
        accounts.append({
            "profile_id": pid,
            "name": row.get("name") or row.get("display_name") or "",
            "email": email_addr,
            "provider": "anymessage" if am else "imap",
            "am_id": am.group(1) if am else "",
            "site": site_m.group(1) if site_m else "",
        })
        if password:
            pw_map[email_addr.lower()] = password
    return accounts, pw_map


def password_for(email_addr: str) -> str | None:
    """Look up an app password from any profile remark carrying this email."""
    key = (email_addr or "").strip().lower()
    for row in _list_profiles():
        em, pw = _creds_from_remark(row.get("remark") or "")
        if em and em.lower() == key and pw:
            return pw
    return None


# --------------------------------------------------------------------------- #
# AnyMessage (short-term email) provider
# --------------------------------------------------------------------------- #
def _am_get(path: str, params: dict, timeout: float = 20.0):
    q = urllib.parse.urlencode(params)
    url = f"{ANYMESSAGE_API}{path}?{q}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def anymessage_balance(token: str) -> float:
    data = _am_get("/user/balance", {"token": (token or "").strip()})
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(str(data.get("value") or "balance failed"))
    try:
        return float((data or {}).get("balance") or 0)
    except (TypeError, ValueError):
        return 0.0


def _am_getmessage(token: str, activation_id: str) -> str | None:
    data = _am_get("/email/getmessage", {"token": token, "id": str(activation_id)})
    if isinstance(data, dict):
        if data.get("status") == "error":
            if str(data.get("value")) == "wait message":
                return None
            raise RuntimeError(str(data.get("value") or "no message"))
        msg = data.get("message")
        return msg if isinstance(msg, str) and msg.strip() else None
    return None


def anymessage_latest(token: str, *, email: str = "", site: str = "snapchat.com",
                      activation_id: str = "", reorder: bool = False) -> dict:
    """Return newest link + OTP for an activation.

    reorder=True: ALWAYS call AnyMessage's /email/reorder first (re-open the
    same mailbox to catch the NEXT incoming email), then read. This is what the
    user sees as "reordering" on the AnyMessage side.
    reorder=False: just read the given activation id (no side effects).
    """
    token = (token or "").strip()
    if not token:
        raise RuntimeError("Missing AnyMessage token")
    aid = str(activation_id or "")
    reordered = False
    if reorder:
        if not (email or aid):
            raise RuntimeError("reorder needs an email or id")
        params = {"token": token}
        if aid:
            params["id"] = aid
        else:
            params.update({"email": email, "site": site})
        data = _am_get("/email/reorder", params)
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(str(data.get("value") or "reorder failed"))
        aid = str((data or {}).get("id") or aid)
        email = (data or {}).get("email") or email
        reordered = True
        time.sleep(2)
    html = _am_getmessage(token, aid) if aid else None
    link = extract_verification_link("", _strip_html(html or ""), html or "") if html else None
    otp = None
    if html:
        text = _strip_html(html)
        otp = extract_otp("", text, "snapchat.com")
        if not otp:
            m = CODE_RE.search(text)
            if m and m.group(1) != "000000":
                otp = m.group(1)
    return {"email": email, "id": aid, "site": site or "snapchat.com",
            "link": link, "otp": otp, "has_message": bool(html), "reordered": reordered}


# --------------------------------------------------------------------------- #
# IMAP
# --------------------------------------------------------------------------- #
class ImapError(RuntimeError):
    pass


def fetch_latest_signals(email_addr: str, password: str, *, scan: int = IMAP_SCAN) -> dict:
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    except Exception as exc:
        raise ImapError(f"Could not reach IMAP {IMAP_HOST}:{IMAP_PORT}: {exc}") from exc
    try:
        try:
            conn.login(email_addr, password)
        except imaplib.IMAP4.error as exc:
            raise ImapError(f"IMAP login failed for {email_addr}: {exc}") from exc

        best_link = None
        best_otp = None
        for folder in FOLDERS:
            try:
                typ, _ = conn.select(_imap_quote(folder), readonly=True)
            except Exception:
                continue
            if typ != "OK":
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
                try:
                    dt = parsedate_to_datetime(msg.get("Date"))
                    if dt is not None and dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = int(dt.timestamp()) if dt else 0
                except Exception:
                    ts = 0
                from_name, from_addr = parseaddr(_decode_header(msg.get("From")))
                subject = _decode_header(msg.get("Subject")) or "(no subject)"
                text_body, html_body = _extract_parts(msg)
                plain = (text_body or "").strip() or _strip_html(html_body)
                meta = {"subject": subject, "from": from_addr or from_name,
                        "ts": ts, "folder": folder}
                link = extract_verification_link(subject, plain, html_body)
                if link and (best_link is None or ts >= best_link["ts"]):
                    best_link = {**meta, "link": link}
                code = extract_otp(subject, plain or html_body, from_addr or from_name)
                if code and (best_otp is None or ts >= best_otp["ts"]):
                    best_otp = {**meta, "otp": code}
        newest = max([x for x in (best_link, best_otp) if x], key=lambda x: x["ts"], default=None)
        return {
            "email": email_addr,
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


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "SnappyMakeCompanion/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet-ish
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/health"):
            self._send(200, {"ok": True, "service": "snappymake-imap-companion",
                             "adspower_base": _working_base, "imap_host": IMAP_HOST})
            return

        if path == "/api/ext/context":
            try:
                accounts, _pw = open_accounts()
                self._send(200, {"ok": True, "accounts": accounts, "adspower_base": _working_base})
            except Exception as exc:
                # AdsPower not running / unreachable — clean empty result.
                self._send(200, {"ok": True, "accounts": [],
                                 "warning": f"AdsPower not reachable: {exc}"})
            return

        if path == "/api/ext/latest":
            account = (query.get("account", [""])[0] or "").strip()
            password = (query.get("password", [""])[0] or "").strip()
            if not account:
                self._send(400, {"ok": False, "detail": "Missing account"})
                return
            if not password:
                try:
                    password = password_for(account) or ""
                except Exception:
                    password = ""
            if not password:
                self._send(400, {"ok": False, "detail":
                                 "No app password found in profile remark. "
                                 "Pass &password=<app_password> or add 'Pass: ...' to the remark."})
                return
            try:
                self._send(200, {"ok": True, **fetch_latest_signals(account, password)})
            except ImapError as exc:
                self._send(502, {"ok": False, "detail": str(exc)})
            except Exception as exc:
                self._send(500, {"ok": False, "detail": f"Unexpected error: {exc}"})
            return

        if path == "/api/ext/anymessage/balance":
            token = (query.get("token", [""])[0] or ANYMESSAGE_TOKEN or "").strip()
            if not token:
                self._send(400, {"ok": False, "detail": "Missing AnyMessage token"})
                return
            try:
                self._send(200, {"ok": True, "balance": anymessage_balance(token)})
            except Exception as exc:
                self._send(502, {"ok": False, "detail": str(exc)})
            return

        if path == "/api/ext/anymessage":
            token = (query.get("token", [""])[0] or ANYMESSAGE_TOKEN or "").strip()
            email = (query.get("email", [""])[0] or "").strip()
            site = (query.get("site", [""])[0] or ANYMESSAGE_SITE or "snapchat.com").strip()
            aid = (query.get("id", [""])[0] or "").strip()
            reorder_q = (query.get("reorder", [""])[0] or "").strip()
            # Default: reorder only when we have no activation id to read. When an
            # id is given (polling), do NOT reorder so we keep reading the same
            # mailbox instead of spawning new activations each poll.
            do_reorder = (reorder_q == "1") if reorder_q else (not aid)
            if not token:
                self._send(400, {"ok": False, "detail": "Missing AnyMessage token"})
                return
            if not (email or aid):
                self._send(400, {"ok": False, "detail": "Provide email or id"})
                return
            try:
                self._send(200, {"ok": True, **anymessage_latest(
                    token, email=email, site=site, activation_id=aid, reorder=do_reorder)})
            except Exception as exc:
                self._send(502, {"ok": False, "detail": str(exc)})
            return

        self._send(404, {"ok": False, "detail": "Not found"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"SnappyMake IMAP companion listening on http://{HOST}:{PORT}")
    print(f"  IMAP:     {IMAP_HOST}:{IMAP_PORT}")
    print(f"  AdsPower: {os.environ.get('ADSPOWER_BASE') or 'auto-detect (' + ADSPOWER_BASES[0] + ' ...)'}")
    print("  Endpoints: /api/ext/context, /api/ext/latest?account=<email>[&password=<app_pw>],")
    print("             /api/ext/anymessage?email=<email>&site=<site>[&id=<id>&token=<token>]")
    if ANYMESSAGE_TOKEN:
        print("  AnyMessage token: set via env")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
