#!/usr/bin/env python3
"""One-off: salvage SnappyMail-Test into Official.

NOT wired to the dashboard Start button.

Same order as Official, from the top, for each existing Test profile:
  1. Open Gmail only (no proxy, no Snap)
  2. Close → inject Netlox → wait
  3. Reopen Gmail inbox first (proxy on)
  4. Snap SIGN UP only (never Snap login)
  5. Sign up with Google → Continue → FILL the form
  6. Bitmoji / Chat / confirm mail
  7. Remark + move into SnappyMake Official
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

import gmail_batch_login_v3 as B
import official_full_run as R
from app.ads.client import AdsPowerClient
from app.inject.identity import build_identity
from app.inject.snapchat import _snapchat_bitmoji, _snapchat_web_onboard, _welcome_tab
from app.services import gmail_login as G
from app.services import load_runtime_settings
from app.db import init_db
from app.services.profiles import close_browser, open_browser

TEST_GID = "10675936"
OFFICIAL_GID = "10722026"
GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"
SNAP_WELCOME = "https://accounts.snapchat.com/v2/welcome"
SNAP_SIGNUP = "https://accounts.snapchat.com/v2/signup"

RE_PASS = re.compile(r"Pass:\s*([^·]+)")
RE_BACKUP = re.compile(r"Backup:\s*([0-9 ]+)")


def log(*a):
    print(*a, flush=True)


def keep_awake():
    try:
        subprocess.Popen(
            ["caffeinate", "-dims"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("caffeinate on — Mac stays awake")
    except Exception as exc:
        log("caffeinate warn", exc)


def parse_creds(name: str, remark: str) -> tuple[str, str, list[str]]:
    email = name.strip().lower() if "@" in name else ""
    m_pass = RE_PASS.search(remark or "")
    password = (m_pass.group(1) if m_pass else "").strip()
    m_bak = RE_BACKUP.search(remark or "")
    raw = m_bak.group(1) if m_bak else ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    codes = [digits[i : i + 8] for i in range(0, len(digits), 8) if len(digits[i : i + 8]) >= 7]
    return email, password, codes


def gmail_logged_in(page) -> bool:
    try:
        return B.really_gmail_inbox(page.url or "")
    except Exception:
        return False


def snap_registered(page) -> bool:
    try:
        u = (page.url or "").lower()
        t = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    if "accounts.snapchat.com" not in u:
        return False
    if "/v2/login" in u:
        return False
    if "agree and continue" in t or "find all your friends" in t:
        return False
    if "/v2/welcome" in u:
        return True
    if "chat now" in t or "use snapchat on your desktop" in t:
        return True
    return False


def signup_form_ready(page, email: str) -> bool:
    if R.snap_signup_connected(page, email):
        return True
    try:
        u = (page.url or "").lower()
    except Exception:
        return False
    if "snapchat.com" not in u or "/v2/login" in u:
        return False
    try:
        loc = page.locator("#firstname, input[name='firstName']").first
        if loc.count() and loc.is_visible(timeout=800):
            return True
    except Exception:
        pass
    try:
        t = (page.inner_text("body") or "").lower()
    except Exception:
        t = ""
    return "agree and continue" in t and "firstname" in t


def click_leave_login(page) -> bool:
    for lab in (
        re.compile(r"^sign up$", re.I),
        re.compile(r"^mag-sign up$", re.I),
        re.compile(r"don.?t have an account", re.I),
    ):
        for role in ("link", "button"):
            try:
                el = page.get_by_role(role, name=lab).first
                if el.count() and el.is_visible(timeout=600):
                    el.click(timeout=4000)
                    log("  left Snap LOGIN via", role, lab.pattern)
                    page.wait_for_timeout(3500)
                    return True
            except Exception:
                continue
    return False


def land_signup(page) -> bool:
    if R.force_signup_page(page):
        return True
    if click_leave_login(page) and R.force_signup_page(page):
        return True
    log("  still not on SIGN UP", (page.url or "")[:140])
    return "signup" in (page.url or "").lower() and not R.on_snap_login(page.url or "")


def wait_google_then_form(ctx, email: str):
    """Pick Gmail → Continue → return when the Snap SIGN UP form is actually there."""
    picked = R.pick_exact_gmail(ctx, email, timeout=min(90.0, R.GMAIL_POPUP_S))
    if not picked:
        log("  Gmail chooser: exact mail not clicked yet — keep watching")
    deadline = time.time() + R.GMAIL_POPUP_S
    last = 0.0
    while time.time() < deadline:
        for pg in R._gmail_popup_pages(ctx):
            try:
                pg.bring_to_front()
            except Exception:
                pass
            if not picked:
                try:
                    hit = pg.get_by_text(email, exact=False).first
                    if hit.count() and hit.is_visible(timeout=500):
                        hit.click(timeout=5000)
                        log("  chooser clicked", email)
                        picked = True
                        pg.wait_for_timeout(1500)
                except Exception:
                    pass
            lab = R.tap_gmail_popup_continue(pg)
            if lab:
                log("  Gmail popup tapped", lab, "— waiting for Snap SIGN UP form")
                try:
                    pg.wait_for_timeout(2000)
                except Exception:
                    time.sleep(2)
        for pg in list(ctx.pages):
            if signup_form_ready(pg, email):
                try:
                    pg.bring_to_front()
                except Exception:
                    pass
                log("  SIGN UP form is up — filling now", (pg.url or "")[:120])
                return pg
        now = time.time()
        if now - last > 12:
            last = now
            R.dump_tabs(ctx, "waiting_signup_form")
            for pg in list(ctx.pages):
                try:
                    u = pg.url or ""
                    if "snapchat.com" not in u:
                        continue
                    vals = R._signup_values(pg)
                    log("  signup fields so far", vals, u[:100])
                except Exception:
                    pass
        time.sleep(0.5)
    log("  SIGN UP form never appeared after Google")
    return None


def try_login(ws: str, email: str, password: str, codes: list[str]) -> str:
    if not email or not password:
        log("  no creds to login")
        return "NO_CREDS"
    result = B.login_gmail(ws, email, password, codes)
    log("  login_gmail", result)
    return str(result)


def record_db(email: str, password: str = "", codes: list[str] | None = None, **fields) -> None:
    if password:
        try:
            G.upsert_gmail_login(email, password, codes or [])
        except Exception as exc:
            log("  db upsert warn", exc)
    try:
        G.set_status(email, **{k: v for k, v in fields.items() if v is not None})
    except Exception as exc:
        log("  db warn", exc)


def login_fields_from_result(result: str) -> dict:
    s = (result or "").lower()
    if result == "LOGIN_OK":
        return {"login_status": "login_ok", "snap_status": "none"}
    if "WRONG_PASSWORD" in result or "password was changed" in s:
        return {"login_status": "wrong_password", "snap_status": "failed", "last_error": "wrong password"}
    if "SELFIE" in result:
        return {"login_status": "selfie", "snap_status": "failed", "last_error": result[:300]}
    if "CAPTCHA" in result:
        return {"login_status": "captcha", "snap_status": "failed", "last_error": result[:300]}
    if "totp_rejected" in s or "challenge/totp" in s:
        return {"login_status": "error", "snap_status": "failed", "last_error": "2FA key rejected"}
    if "2sv" in s or "all_codes_bad" in s:
        return {"login_status": "error", "snap_status": "failed", "last_error": result[:300]}
    return {"login_status": "error", "snap_status": "failed", "last_error": (result or "gmail failed")[:300]}


def do_signup(ctx, page, email: str) -> tuple[bool, str, str]:
    ident = build_identity(gender="female")
    ident["last_name"] = ""
    username = ident.get("username") or ""
    notes: list[str] = []

    def on_step(msg: str) -> None:
        log("  STEP", msg)

    log("=== 4/6 SNAP SIGN UP (not login) ===")
    if not land_signup(page):
        if snap_registered(page):
            return True, username, "GMAIL_OK · SNAP_WELCOME"
        return False, username, "no signup page (stuck on login)"
    if R.on_snap_login(page.url or ""):
        log("  abort — Snap LOGIN, not clicking Google")
        return False, username, "snap login not signup"
    log("  on SIGN UP — clicking Sign up with Google")
    if not R.click_gsi(page):
        return False, username, "no Sign up with Google"
    page.wait_for_timeout(R.AFTER_GSI_MS)
    signup = wait_google_then_form(ctx, email)
    if signup is None:
        return False, username, "signup form did not appear after Google"
    signup.bring_to_front()
    signup.wait_for_timeout(R.SIGNUP_READY_MS)
    t = ""
    try:
        t = (signup.inner_text("body") or "").lower()
    except Exception:
        pass
    if R.snap_email_taken(t):
        return False, username, "google email already associated with Snapchat"

    log("=== 5/6 FILL SIGNUP (name / birthday / username) ===")
    log(
        "  identity",
        ident["first_name"],
        ident.get("username"),
        f"{ident['birth_month']}/{ident['birth_day']}/{ident['birth_year']}",
    )
    username = R.fill_signup(signup, ident)

    log("=== 6/6 Skip extras + Bitmoji + Chat + confirm mail ===")
    deadline = time.time() + R.AFTER_FILL_S
    email_taken = False
    landed_page = signup
    while time.time() < deadline:
        skip = R.watch_signup_popups(ctx)
        if skip:
            log("  POPUP", skip)
            time.sleep(1.0)
            continue
        for pg in list(ctx.pages):
            try:
                body = (pg.inner_text("body") or "").lower()
                u = pg.url or ""
            except Exception:
                continue
            if R.snap_email_taken(body):
                email_taken = True
                break
            if R.is_welcome_landed(u, body):
                landed_page = pg
                deadline = 0
                break
        if email_taken:
            return False, username, "google email already associated with Snapchat"
        if deadline == 0:
            break
        time.sleep(0.7)
    if email_taken:
        return False, username, "google email already associated with Snapchat"
    try:
        _snapchat_bitmoji(landed_page, ctx, notes, on_step, gender="female")
    except Exception as exc:
        log("  bitmoji warn", exc)
    page = _welcome_tab(ctx, landed_page, username) or landed_page
    try:
        _snapchat_web_onboard(page, ctx, notes, on_step, add_friends=True)
    except Exception as exc:
        log("  web warn", exc)
    gmail = next((pg for pg in ctx.pages if "mail.google.com" in (pg.url or "")), None)
    verified = False
    if gmail is not None:
        try:
            gmail.bring_to_front()
            gmail.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=R.PROXY_GOTO_MS)
        except Exception:
            pass
        href = R.wait_inbox_confirm(gmail, timeout=min(R.INBOX_CONFIRM_S, 600))
        if href:
            verified = R.open_confirm_until_verified(ctx, href)
    note = f"SNAP_OK · {ident['first_name']} / {username}"
    if verified:
        note = "EMAIL VERIFIED · " + note
    return True, username, note


def promote(c: AdsPowerClient, pid: str, extra: str, email: str, password: str, backup: str) -> None:
    text = B.remark(email, password, backup, extra)
    try:
        B.update_user(pid, remark=text)
    except Exception as exc:
        log("  remark warn", exc)
    try:
        c.regroup_profiles([pid], OFFICIAL_GID)
        log("  moved to Official", pid, extra)
    except Exception as exc:
        log("  regroup warn", exc)
    proxy = ""
    m = re.search(r"Proxy:\s*([^·]+)", extra)
    if m:
        proxy = m.group(1).strip()
    record_db(
        email,
        password=password,
        login_status="login_ok",
        snap_status="signed_up",
        profile_id=pid,
        group_name=G.OFFICIAL_GROUP_NAME,
        proxy_label=proxy or None,
        last_error=extra,
    )


def process_one(c: AdsPowerClient, p: dict) -> str:
    pid = str(p.get("profile_id") or p.get("user_id") or "")
    name = str(p.get("name") or "")
    remark = str(p.get("remark") or "")
    email, password, codes = parse_creds(name, remark)
    backup = B.fmt_backup(codes[0]) if codes else ""
    log(f"\n=== TEST {name} {pid} ===")
    if not email:
        log("  skip no email")
        return "skip"
    row = next((r for r in G.snapshot()["accounts"] if r["email"].lower() == email), None)
    if row:
        if row.get("snap_status") == "signed_up":
            log("  skip — already signed_up in Official DB")
            return "skip"
        if row.get("login_status") in {"wrong_password", "captcha", "selfie"}:
            log("  skip — already", row.get("login_status"), "in Official DB")
            return "skip"
        err = (row.get("last_error") or "").lower()
        if any(x in err for x in ("2sv", "all_codes_bad", "already associated")):
            log("  skip —", (row.get("last_error") or "")[:80])
            return "skip"

    verdict = "unusable"
    extra = ""
    try:
        log("=== 1/6 GMAIL FIRST (no proxy, no Snap) ===")
        session = open_browser(c, pid, headless=False, timeout=120, attempt_timeout=75, tabs=[GMAIL_INBOX])
        ws = (
            session.get("puppeteer")
            or (session.get("ws") or {}).get("puppeteer")
            or ""
        )
        log("  ws", ws[:80])
        gmail_ok = False
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(ws)
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(4000)
            except Exception as exc:
                log("  gmail goto warn", exc)
            gmail_ok = gmail_logged_in(page)
            log("  gmail", "INBOX" if gmail_ok else "NOT inbox", (page.url or "")[:120])
        if not gmail_ok:
            log("  typing Gmail login")
            result = try_login(ws, email, password, codes)
            gmail_ok = result == "LOGIN_OK"
            if not gmail_ok:
                fields = login_fields_from_result(result)
                record_db(
                    email,
                    password=password,
                    codes=codes,
                    profile_id=pid,
                    group_name=G.EXCLUDE_GROUP_NAME,
                    **fields,
                )
        if not gmail_ok:
            extra = "GMAIL_NOT_INBOX"
            log("  unusable — Gmail not inbox. No proxy. No Snap.")
            return "unusable"

        log("=== 2/6 CLOSE + NETLOX PROXY ===")
        close_browser(c, pid)
        B.stop_all_browsers()
        time.sleep(3)
        label = B.inject_proxy(pid)
        log("  proxy", label)
        B.update_user(pid, remark=B.remark(email, password, backup, f"Proxy: {label} · LOGIN_OK"))
        log("  waiting 15s for proxy to settle")
        time.sleep(15)

        log("=== 3/6 REOPEN Gmail inbox (proxy ON) ===")
        session = open_browser(
            c,
            pid,
            headless=False,
            timeout=R.PROXY_REOPEN_S,
            attempt_timeout=R.PROXY_REOPEN_ATTEMPT_S,
            tabs=[GMAIL_INBOX],
        )
        ws = (
            session.get("puppeteer")
            or (session.get("ws") or {}).get("puppeteer")
            or ""
        )
        log("  reopen ws", ws[:80])

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(ws)
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=R.PROXY_GOTO_MS)
                log("  gmail via proxy — waiting 20s for inbox")
                page.wait_for_timeout(20000)
                page.bring_to_front()
            except Exception as exc:
                log("  gmail reopen goto", exc)
            if not gmail_logged_in(page):
                extra = "GMAIL_LOST_AFTER_PROXY"
                log("  STOP — Gmail gone after proxy. Not opening Snap.")
                record_db(
                    email,
                    password=password,
                    login_status="error",
                    snap_status="failed",
                    profile_id=pid,
                    group_name=G.EXCLUDE_GROUP_NAME,
                    last_error="gmail session lost after proxy reopen",
                )
                return "unusable"
            log("  GMAIL STILL INBOX", page.url)

            try:
                page.goto(SNAP_WELCOME, wait_until="domcontentloaded", timeout=R.PROXY_GOTO_MS)
                page.wait_for_timeout(6000)
            except Exception as exc:
                log("  welcome goto warn", exc)
            if snap_registered(page):
                extra = f"GMAIL_OK · SNAP_WELCOME · Proxy: {label}"
                log("  already on welcome — no signup needed")
            else:
                ok, username, extra = do_signup(ctx, page, email)
                if not ok:
                    log("  signup failed", extra)
                    snap = "failed"
                    err = extra
                    if "already associated" in (extra or "").lower():
                        err = "google email already associated with Snapchat"
                    record_db(
                        email,
                        password=password,
                        login_status="login_ok",
                        snap_status=snap,
                        profile_id=pid,
                        group_name=G.EXCLUDE_GROUP_NAME,
                        last_error=err,
                    )
                    return "unusable"
                extra = f"{extra} · Proxy: {label}"
            promote(c, pid, extra, email, password, backup)
            verdict = "moved"
    except Exception as exc:
        log("  FAIL", exc)
        extra = f"error {exc}"[:180]
        verdict = "unusable"
        record_db(
            email,
            password=password,
            login_status="error",
            snap_status="failed",
            profile_id=pid,
            group_name=G.EXCLUDE_GROUP_NAME,
            last_error=extra,
        )
    finally:
        try:
            close_browser(c, pid)
        except Exception:
            pass
        try:
            B.stop_all_browsers()
        except Exception:
            pass
        time.sleep(2)
    return verdict


def main() -> int:
    keep_awake()
    init_db()
    conf = load_runtime_settings()
    c = AdsPowerClient(conf["api_base"], conf.get("api_key") or "")
    B.stop_all_browsers()
    counts = G.ingest_adspower_mails(c)
    log("Official DB ingest", counts)
    # This salvage pass already proved these Test mails unusable.
    for em, (st, err) in {
        "hafakonafai@gmail.com": ("wrong_password", "wrong password"),
        "gafasihafay@gmail.com": ("wrong_password", "wrong password"),
        "gajerzohahalo@gmail.com": ("wrong_password", "wrong password"),
        "curdricracgas@gmail.com": ("wrong_password", "wrong password"),
        "niuelsdcoshyls@gmail.com": ("error", "FAIL:all_codes_bad"),
        "markiinekamsron@gmail.com": ("error", "FAIL:all_codes_bad"),
        "flewnacellbout@gmail.com": ("error", "FAIL:all_codes_bad"),
    }.items():
        try:
            G.set_status(
                em,
                login_status=st,
                snap_status="failed",
                last_error=err,
                group_name=G.EXCLUDE_GROUP_NAME,
            )
        except Exception:
            pass
    snap = G.snapshot()
    log(
        "mail stats · total",
        snap["total"],
        "gmail_ok",
        snap["logged_in"],
        "signed_up",
        snap["signed_up"],
        "wrong_password",
        snap["wrong_password"],
    )
    profs = [p for p in c.list_profiles(group_id=TEST_GID) if "@" in str(p.get("name") or "")]

    def rank(p):
        r = (p.get("remark") or "").lower()
        if "login_ok" in r:
            return 0
        if "captcha" in r:
            return 2
        return 1

    profs.sort(key=rank)
    log("SnappyMail-Test to salvage", len(profs))
    log("Order: Gmail → close → Netlox proxy → reopen Gmail → Snap SIGN UP → fill")
    moved = []
    unusable = []
    for p in profs:
        v = process_one(c, p)
        name = p.get("name")
        if v == "moved":
            moved.append(name)
        elif v == "unusable":
            unusable.append(name)
    log("\n=== SALVAGE DONE ===")
    log("moved", len(moved), moved)
    log("unusable", len(unusable), unusable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
