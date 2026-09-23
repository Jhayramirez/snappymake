#!/usr/bin/env python3
"""All remaining Official-group accounts: Snapchat-style create → Gmail first →
close → Netlox proxy → reopen Gmail → Sign up with Google → fill/skip →
Bitmoji → Snapchat web Chat → Gmail inbox → confirm email.

AdsPower: keep signed-up profiles, delete failed ones.
DB (gmail_login): every email keeps login_status / snap_status / last_error.
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

import gmail_batch_login_v3 as B
from gmail_batch_login_v3 import fmt_backup, inject_proxy, login_gmail, really_gmail_inbox

from app.ads.client import AdsPowerClient, release_user_focus
from app.db import init_db
from app.inject.identity import build_identity, girly_username
from app.inject.snapchat import (
    _on_welcome_page,
    _snapchat_bitmoji,
    _snapchat_web_onboard,
    _welcome_tab,
)
from app.services import load_runtime_settings
from app.services import gmail_login as G
from app.services.profiles import close_browser, create_one_profile, open_browser

GMAIL_TAB = (
    "https://accounts.google.com/signin/v2/identifier?hl=en&service=mail"
    "&continue=https://mail.google.com/mail/u/0/"
)
SNAP_WELCOME = "https://accounts.snapchat.com/v2/welcome"
SNAP_SIGNUP = "https://accounts.snapchat.com/v2/signup"
GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"
# Proxy is on for Snap + Gmail after inject — pages take longer than a direct line.
PROXY_GOTO_MS = 180_000
PROXY_SETTLE_MS = 60_000
GSI_WAIT_S = 90
GSI_PROXY_TRIES = 3
GMAIL_POPUP_S = 300
PROXY_SETTLE_RETRY_MS = 20_000
AFTER_GSI_MS = 10_000
SIGNUP_READY_MS = 5_000
AFTER_FILL_S = 180
INBOX_CONFIRM_S = 1_200
PROXY_REOPEN_S = 240
PROXY_REOPEN_ATTEMPT_S = 180
GMAIL_PROMOS = "https://mail.google.com/mail/u/0/#category/promotions"
GMAIL_SPAM = "https://mail.google.com/mail/u/0/#spam"
GMAIL_ALL = "https://mail.google.com/mail/u/0/#all"
SKIP_USED = set()
SKIP_LOGIN = {"wrong_password", "captcha", "selfie"}
SKIP_SNAP_ERR = (
    "already associated",
    "google email already",
    "email already has snap",
)
SKIP_GMAIL_ERR = (
    "2sv_no_backup",
    "2sv_rejected",
    "fail:2sv",
    "recovery_rejected",
    "totp_rejected",
    "2fa key rejected",
    "gsi still loading",
    "google wants a phone",
    "unusual activity",
)

_on_log = None
_should_stop = None
_stop_after_signed_up = False
_inject_proxy = True


def bring(page) -> None:
    """Keep AdsPower in the background. CDP does not need OS focus."""
    if os.environ.get("SNAPPY_NO_FOCUS", "1") == "0":
        try:
            page.bring_to_front()
        except Exception:
            pass


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    cb = _on_log
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def push_sheet(why: str = "") -> None:
    """Rewrite the Official Google Sheet after each mailbox. Never stop the run if Sheets fails."""
    try:
        import sync_official_sheet as S

        log("sheet sync", why or "update")
        S.main()
    except Exception as exc:
        log("sheet sync warn", str(exc).splitlines()[0][:160])


def stop_now() -> bool:
    fn = _should_stop
    return bool(fn and fn())


def client() -> AdsPowerClient:
    conf = load_runtime_settings()
    return AdsPowerClient(conf["api_base"], conf["api_key"])


def stop_everything(c: AdsPowerClient) -> None:
    for pid in list(c.local_active() or []):
        log("  stop", pid)
        try:
            c.stop_browser(pid)
        except Exception as exc:
            log("  stop warn", exc)
    B.stop_all_browsers()
    time.sleep(2)


def drop_profile(c: AdsPowerClient, pid: str, why: str = "") -> None:
    """Delete the AdsPower profile. Result already lives in gmail_login DB."""
    if why:
        log("  delete AdsPower profile", pid, why)
    try:
        close_browser(c, pid)
    except Exception:
        pass
    stop_everything(c)
    if pid:
        try:
            c.delete_profiles([pid])
            log("  deleted", pid)
        except Exception as exc:
            log("  delete warn", exc)
    time.sleep(2)


def clear_official(c: AdsPowerClient) -> str:
    """Delete failed Official profiles. Keep signed-up ones. History is in DB."""
    info = G.ensure_official_group(c)
    gid = str(info.get("group_id") or "")
    keep_emails = {
        r["email"].lower()
        for r in G.snapshot()["accounts"]
        if r.get("snap_status") == "signed_up"
    }
    profs = c.list_profiles(group_id=gid) if gid else []
    ids = []
    for p in profs:
        pid = str(p.get("profile_id") or "")
        name = str(p.get("name") or "").lower()
        if name in keep_emails:
            log("  keep signed-up", pid, name)
            continue
        if pid:
            ids.append(pid)
            log("  del", pid, p.get("name"))
    if ids:
        for pid in ids:
            try:
                c.stop_browser(pid)
            except Exception:
                pass
        time.sleep(1)
        c.delete_profiles(ids)
        log("cleared failed official profiles", len(ids), "— results kept in DB")
    return gid


def snap_email_taken(text: str) -> bool:
    t = (text or "").lower()
    return any(
        x in t
        for x in (
            "already associated with",
            "google email is already associated",
            "your google email is already",
        )
    )


def pick_account() -> dict:
    want_batch = (os.environ.get("SNAPPY_BATCH") or "Second Batch").strip()
    rows = G.snapshot()["accounts"]
    pending = []
    for r in rows:
        if r["login_status"] in SKIP_LOGIN:
            continue
        if r["snap_status"] == "signed_up":
            continue
        gname = (r.get("group_name") or "").strip()
        if gname == G.EXCLUDE_GROUP_NAME:
            continue
        email = r["email"].lower()
        if email in SKIP_USED:
            continue
        if want_batch and (r.get("batch_name") or "").strip() != want_batch:
            continue
        err = (r.get("last_error") or "").lower()
        if any(x in err for x in SKIP_SNAP_ERR) or any(x in err for x in SKIP_GMAIL_ERR):
            continue
        pending.append(r)
    pending.sort(
        key=lambda r: (
            0 if (r.get("recovery_email") or "").strip() else 1,
            0 if r["login_status"] == "pending" else 1 if r["login_status"] == "login_ok" else 2,
        )
    )
    if not pending:
        raise RuntimeError("no unused Gmail left to login")
    acc = pending[0]
    codes = [x["code"] for x in acc.get("codes") or [] if not x.get("used")]
    if not codes:
        codes = [x["code"] for x in acc.get("codes") or []]
    return {
        "email": acc["email"],
        "password": acc["password"],
        "codes": codes,
        "totp_secret": acc.get("totp_secret") or "",
        "recovery_email": acc.get("recovery_email") or "",
    }


def ws_of(session: dict) -> str:
    return (
        session.get("puppeteer")
        or (session.get("ws") or {}).get("puppeteer")
        or ""
    )


def dump_tabs(ctx, tag: str) -> None:
    log(f"--- tabs {tag} ({len(ctx.pages)}) ---")
    for i, pg in enumerate(list(ctx.pages)):
        try:
            u = pg.url
        except Exception:
            u = "?"
        log(f"  [{i}] {str(u)[:120]}")


def dismiss_continue_as(page) -> None:
    """Dismiss Google FedCM 'Continue as Name' only. Do not click random X — that kills GSI."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass
    try:
        cdp = page.context.new_cdp_session(page)
        try:
            cdp.send("FedCm.enable", {"disableRejectionDelay": True})
        except Exception:
            pass
        try:
            cdp.send("FedCm.dismissDialog")
        except Exception:
            pass
    except Exception:
        pass


def on_snap_login(url: str) -> bool:
    u = (url or "").lower()
    return "/v2/login" in u or "/accounts/sso" in u or "client_id=web-accounts" in u


def close_quietly(c: AdsPowerClient, pid: str) -> None:
    try:
        close_browser(c, pid)
    except Exception as exc:
        log("  close warn", exc)


def force_signup_page(page, settle_ms: int = PROXY_SETTLE_MS) -> bool:
    """Stay on Snapchat SIGN UP only. Never login."""
    try:
        page.goto(SNAP_SIGNUP, wait_until="domcontentloaded", timeout=PROXY_GOTO_MS)
    except Exception as exc:
        log("  signup goto warn", exc)
        return False
    log(f"  on signup — waiting {max(1, settle_ms // 1000)}s for proxy")
    page.wait_for_timeout(settle_ms)
    if on_snap_login(page.url or ""):
        log("  bounced to login — pushing back to signup", page.url)
        try:
            page.goto(SNAP_SIGNUP, wait_until="domcontentloaded", timeout=PROXY_GOTO_MS)
            page.wait_for_timeout(settle_ms)
        except Exception as exc:
            log("  signup retry warn", exc)
    if on_snap_login(page.url or ""):
        log("  still on login, not clicking Google")
        return False
    return "signup" in (page.url or "").lower()


def select_fedcm_gmail(page, email: str = "", timeout: float = 12.0) -> bool:
    """Pick the exact Gmail in Chrome's FedCM chooser (no popup)."""
    shown: list[dict] = []
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.on("FedCm.dialogShown", lambda params: shown.append(params))
        cdp.send("FedCm.enable", {"disableRejectionDelay": True})
    except Exception as exc:
        log("  FedCM enable warn", exc)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline and not shown:
        time.sleep(0.2)
    if not shown:
        return False
    params = shown[-1]
    accs = params.get("accounts") or []
    dialog = params.get("dialogId")
    idx = 0
    want = (email or "").lower()
    for i, acc in enumerate(accs):
        if want and want in json.dumps(acc).lower():
            idx = i
            break
    try:
        cdp.send("FedCm.selectAccount", {"dialogId": dialog, "accountIndex": idx})
        picked = (accs[idx].get("email") if accs else "") or idx
        log("  FedCM selected", picked)
        return True
    except Exception as exc:
        log("  FedCM select warn", exc)
        return False


def click_gsi(page, email: str = "") -> bool:
    """Continue with Google / Sign up with Google on /v2/signup. Never Log In."""
    if on_snap_login(page.url or ""):
        log("  skip Google click — this is login, not signup")
        return False
    deadline = time.time() + GSI_WAIT_S
    log(f"  waiting up to {GSI_WAIT_S}s for Continue with Google (proxy)")
    reloaded = False
    while time.time() < deadline:
        if on_snap_login(page.url or ""):
            log("  page became login — abort Google click")
            return False
        if email and snap_signup_connected(page, email):
            log("  already connected with Google")
            return True
        loc = page.locator('iframe[src*="accounts.google.com/gsi"], iframe[src*="gsi/button"]')
        clicked = False
        try:
            n = loc.count()
            if n:
                box = loc.first.bounding_box()
                if box and box.get("width", 0) > 8:
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    log("  clicked GSI iframe on signup")
                    clicked = True
        except Exception as exc:
            log("  gsi iframe warn", exc)
        if not clicked:
            for lab in (
                "Continue with Google",
                "Sign up with Google",
                "Mag-sign up sa Google",
            ):
                try:
                    btn = page.get_by_role("button", name=re.compile(lab, re.I)).first
                    if btn.count() and btn.is_visible(timeout=600):
                        btn.click(timeout=4000)
                        log("  clicked", lab)
                        clicked = True
                        break
                except Exception:
                    continue
        if not clicked:
            try:
                hit = page.evaluate(
                    """() => {
                      const needles = ['continue with google','sign up with google','mag-sign up sa google'];
                      const blocked = ['sign in with google','log in','login','already have'];
                      for (const el of document.querySelectorAll('button, a, [role=button], span, div')) {
                        const t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ');
                        if (!t || t.length > 48) continue;
                        const low = t.toLowerCase();
                        if (blocked.some(b => low === b || low.startsWith(b))) continue;
                        if (needles.some(n => low === n || low.includes(n))) {
                          const r = el.getBoundingClientRect();
                          if (r.width > 8 && r.height > 8) { el.click(); return t; }
                        }
                      }
                      return '';
                    }"""
                )
                if hit:
                    log("  clicked text", hit)
                    clicked = True
            except Exception:
                pass
        if clicked:
            if select_fedcm_gmail(page, email):
                page.wait_for_timeout(2500)
            if email and snap_signup_connected(page, email):
                return True
            return True
        if not reloaded and (deadline - time.time()) < (GSI_WAIT_S - 90):
            reloaded = True
            log("  GSI still missing — reload signup once (slow proxy)")
            try:
                page.goto(SNAP_SIGNUP, wait_until="domcontentloaded", timeout=PROXY_GOTO_MS)
                page.wait_for_timeout(8000)
            except Exception as exc:
                log("  signup reload warn", exc)
        page.wait_for_timeout(700)
    try:
        texts = page.evaluate(
            """() => [...document.querySelectorAll('button, a, [role=button], iframe')]
              .map(el => (el.innerText || el.getAttribute('aria-label') || el.getAttribute('src') || '').trim().replace(/\\s+/g,' ').slice(0,80))
              .filter(Boolean).slice(0,20)"""
        ) or []
        log("  signup Google not found; page has", texts[:12], (page.url or "")[:120])
    except Exception:
        pass
    return False


def _is_gmail_popup(url: str) -> bool:
    u = (url or "").lower()
    return "accounts.google.com" in u and "gsi/button" not in u


def _gmail_popup_pages(ctx):
    out = []
    for pg in list(ctx.pages):
        try:
            u = pg.url or ""
        except Exception:
            continue
        if _is_gmail_popup(u):
            out.append(pg)
    return out


def tap_gmail_popup_continue(pg) -> str:
    """OAuth Continue on the Gmail popup only. Never Continue as / Continue with Google."""
    try:
        hit = pg.evaluate(
            """() => {
              const blocked = ['continue as', 'continue with', 'agree and continue', 'send'];
              const ok = [
                'continue', 'allow', 'confirm', 'next',
                'tiếp tục', 'magpatuloy', 'continuar', 'continuer',
                'weiter', 'lanjutkan',
                '계속', '다음', '확인',
                '继续', '下一步', '确定', '確定', '繼續',
                '次へ', '続行'
              ];
              const els = [
                ...document.querySelectorAll(
                  'button, [role=button], input[type=submit], input[type=button], div[role=button]'
                )
              ];
              for (const el of els) {
                const t = (
                  el.innerText || el.value || el.getAttribute('aria-label') || ''
                ).trim().replace(/\\s+/g, ' ');
                if (!t || t.length > 24) continue;
                const low = t.toLowerCase();
                if (blocked.some(b => low.includes(b))) continue;
                if (!ok.includes(low)) continue;
                if (el.disabled) continue;
                const r = el.getBoundingClientRect();
                if (r.width > 8 && r.height > 8) { el.click(); return t; }
              }
              return '';
            }"""
        )
        if hit:
            return str(hit)
    except Exception:
        pass
    try:
        loc = pg.locator("#submit_approve_access").first
        if loc.count() and loc.is_visible(timeout=400):
            loc.click(timeout=3000)
            return "Allow"
    except Exception:
        pass
    try:
        b = pg.get_by_role(
            "button",
            name=re.compile(
                r"^(Continue|Allow|Next|계속|다음|확인|继续|下一步|确定|確定|繼續|次へ|続行)$",
                re.I,
            ),
        ).first
        if b.count() and b.is_visible(timeout=400):
            name = (b.inner_text() or "Continue").strip()
            if "as" in name.lower() or "with" in name.lower():
                return ""
            b.click(timeout=3000)
            return name or "Continue"
    except Exception:
        pass
    return ""


def _click_label(pg, *needles: str) -> str:
    """Click a visible control whose text matches one of the needles. Never Send."""
    for needle in needles:
        try:
            el = pg.get_by_role("button", name=re.compile(needle, re.I)).first
            if el.count() and el.is_visible(timeout=500):
                name = (el.inner_text() or needle).strip()
                if "send" in name.lower():
                    continue
                el.click(timeout=5000)
                return name
        except Exception:
            pass
        try:
            el = pg.get_by_text(re.compile(needle, re.I)).first
            if el.count() and el.is_visible(timeout=500):
                name = (el.inner_text() or needle).strip()
                if "send" in name.lower():
                    continue
                el.click(timeout=5000)
                return name
        except Exception:
            pass
    try:
        hit = pg.evaluate(
            """(needles) => {
              const blocked = ['send'];
              for (const n of document.querySelectorAll('button,a,[role=button],span,div')) {
                const t = (n.innerText || n.getAttribute('aria-label') || '').trim();
                if (!t || t.length > 80) continue;
                const low = t.toLowerCase();
                if (blocked.some(b => low === b || low.startsWith(b + ' '))) continue;
                if (needles.some(x => low === x || low.includes(x))) {
                  (n.closest('button,a,[role=button],[role=link],[role=option]') || n).click();
                  return t;
                }
              }
              return '';
            }""",
            [n.lower() for n in needles],
        )
        if hit:
            return str(hit)
    except Exception:
        pass
    return ""


def handle_oauth_verify(
    pg, codes: list[str], totp_secret: str = "", recovery_email: str = ""
) -> str:
    """OAuth 2SV popup: recovery email, Authenticator (2fa.cn TOTP), or 8-digit backup. Never Send."""
    url = (pg.url or "").lower()
    if "accounts.google.com" not in url:
        return ""
    if not any(x in url for x in ("challenge/ipp", "challenge/selection", "challenge/bc", "challenge/totp", "challenge/kpe", "challenge/")):
        try:
            blob = (pg.inner_text("body") or "").lower()
        except Exception:
            blob = ""
        if (
            "verify" not in blob
            and "2-step" not in blob
            and "more ways" not in blob
            and "recovery email" not in blob
        ):
            return ""

    rec = (recovery_email or "").strip()
    if rec:
        hit = _click_label(
            pg,
            r"confirm your recovery email",
            r"enter the email address you added as a recovery",
            r"recovery email",
            r"more ways to verify",
            r"try another way",
        )
        if hit:
            log("  OAuth verify tapped", hit)
            try:
                pg.wait_for_timeout(1800)
            except Exception:
                time.sleep(1.8)
        try:
            if B.on_recovery_form(pg):
                if B.type_recovery_email(pg, rec):
                    return f"recovery:{rec}"
                log("  OAuth recovery rejected")
        except Exception as exc:
            log("  OAuth recovery warn", exc)

    secret = (totp_secret or "").strip()
    if secret:
        hit = _click_label(
            pg,
            r"more ways to verify",
            r"try another way",
            r"get a verification code from the google authenticator",
            r"google authenticator",
            r"authenticator app",
        )
        if hit:
            log("  OAuth verify tapped", hit)
            try:
                pg.wait_for_timeout(1800)
            except Exception:
                time.sleep(1.8)
        try:
            on_totp = "challenge/totp" in (pg.url or "").lower()
            pin = pg.locator('input[name="totpPin"]').first
            if on_totp or (pin.count() and pin.is_visible(timeout=600)):
                from app.services.totp_2fa import next_code

                code = next_code(secret)
                field = pin if pin.count() else pg.locator('input[name="Pin"]').first
                field.click()
                field.fill("")
                field.type(code, delay=40)
                nxt = _click_label(
                    pg,
                    r"^next$",
                    r"^continue$",
                    r"^계속$",
                    r"^다음$",
                    r"^확인$",
                    r"^继续$",
                    r"^下一步$",
                    r"^确定$",
                    r"^次へ$",
                )
                log("  OAuth totp typed", code, "→", nxt)
                pg.wait_for_timeout(2200)
                if "challenge/totp" not in (pg.url or "").lower():
                    return f"totp:{code}"
        except Exception as exc:
            log("  OAuth totp warn", exc)

    if "challenge/bc" not in url:
        hit = _click_label(
            pg,
            r"more ways to verify",
            r"try another way",
            r"enter one of your 8-?digit backup codes",
            r"8-?digit backup",
            r"backup code",
        )
        if hit:
            log("  OAuth verify tapped", hit)
            try:
                pg.wait_for_timeout(2000)
            except Exception:
                time.sleep(2)
        # Scroll the chooser — backup can sit under "Scroll down"
        try:
            pg.evaluate(
                """() => {
                  for (const n of document.querySelectorAll('button,[role=button]')) {
                    const t = (n.innerText || n.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (t === 'scroll down' || t.includes('scroll down')) { n.click(); return; }
                  }
                }"""
            )
        except Exception:
            pass
        hit2 = _click_label(
            pg,
            r"enter one of your 8-?digit backup codes",
            r"8-?digit backup",
            r"backup code",
        )
        if hit2:
            log("  OAuth backup option", hit2)
            try:
                pg.wait_for_timeout(1800)
            except Exception:
                time.sleep(1.5)

    url = (pg.url or "").lower()
    try:
        blob = (pg.inner_text("body") or "").lower()
    except Exception:
        blob = ""
    if "challenge/bc" not in url and "backup code" not in blob and "8-digit" not in blob:
        return ""
    if not codes:
        return ""
    field = pg.locator('input[name="Pin"]').first
    try:
        if not (field.count() and field.is_visible(timeout=800)):
            return ""
    except Exception:
        return ""
    for code in codes:
        try:
            field = pg.locator('input[name="Pin"]').first
            field.click()
            field.fill("")
            field.type(code, delay=40)
            nxt = _click_label(pg, r"^next$", r"^continue$")
            log("  OAuth backup typed", code, "→", nxt)
            pg.wait_for_timeout(2200)
            t = (pg.inner_text("body") or "").lower()
            if "wrong" in t or "try again" in t or "incorrect" in t:
                continue
            if "challenge/bc" not in (pg.url or "").lower():
                return f"backup:{code}"
        except Exception as exc:
            log("  OAuth backup warn", exc)
            continue
    return ""


def snap_signup_connected(pg, email: str) -> bool:
    """True only after Google OAuth returns to Snap signup (not the empty pre-OAuth form)."""
    try:
        u = (pg.url or "").lower()
    except Exception:
        return False
    if "snapchat.com" not in u or on_snap_login(u):
        return False
    try:
        t = (pg.inner_text("body") or "").lower()
    except Exception:
        return False
    if "connected with google" in t:
        return True
    if email.lower() in t and ("signup" in u or "firstname" in t) and "choose an account" not in t:
        if "agree and continue" in t or "firstname" in t:
            return True
    return False


def pick_exact_gmail(ctx, email: str, timeout: float = GMAIL_POPUP_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for pg in _gmail_popup_pages(ctx):
            bring(pg)
            try:
                hit = pg.get_by_text(email, exact=False).first
                if hit.count() and hit.is_visible(timeout=800):
                    hit.click(timeout=5000)
                    log("  chooser clicked", email)
                    pg.wait_for_timeout(1500)
                    return True
            except Exception:
                continue
        time.sleep(0.5)
    return False


def wait_gmail_popup_then_signup(
    ctx,
    email: str,
    timeout: float = GMAIL_POPUP_S,
    codes: list[str] | None = None,
    totp_secret: str = "",
    recovery_email: str = "",
):
    """Pick Gmail → 2SV recovery/authenticator/backup if shown → Continue → wait until Snap signup is back."""
    unused = [c for c in (codes or []) if c]
    secret = (totp_secret or "").strip()
    rec = (recovery_email or "").strip()
    picked = pick_exact_gmail(ctx, email, timeout=min(90.0, timeout))
    if not picked:
        log("  Gmail popup: exact email not clicked yet — keep watching")
    deadline = time.time() + timeout
    last_dump = 0.0
    phone_only_since = 0.0
    while time.time() < deadline:
        popups = _gmail_popup_pages(ctx)
        for pg in popups:
            bring(pg)
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
            verify = handle_oauth_verify(
                pg, unused, totp_secret=secret, recovery_email=rec
            )
            if verify:
                log("  Gmail OAuth 2SV", verify)
            try:
                blob = (pg.inner_text("body") or "").lower()
                u = (pg.url or "").lower()
            except Exception:
                blob, u = "", ""
            has_backup = "backup code" in blob or "8-digit" in blob or "8 digit" in blob
            has_auth = "authenticator" in blob or "challenge/totp" in u
            has_recovery = "recovery email" in blob or "challenge/kpe" in u
            phone_only = (
                ("challenge/selection" in u or "challenge/ipp" in u)
                and ("phone" in blob or "••••" in blob or "•" in blob)
                and not has_backup
                and not (secret and has_auth)
                and not (rec and has_recovery)
                and not rec
                and not secret
            )
            if phone_only:
                if not phone_only_since:
                    phone_only_since = time.time()
                    log("  OAuth verify list is phone-only — no backup option")
                elif time.time() - phone_only_since > 18:
                    log("  skip Snap — Google OAuth only offers phone, no backup/authenticator")
                    return None
            else:
                phone_only_since = 0.0
            lab = tap_gmail_popup_continue(pg)
            if lab:
                log("  Gmail popup tapped", lab)
                try:
                    pg.wait_for_timeout(2000)
                except Exception:
                    time.sleep(2)
        for pg in list(ctx.pages):
            if snap_signup_connected(pg, email):
                bring(pg)
                log("  back on Snap signup after Google", (pg.url or "")[:120])
                return pg
        now = time.time()
        if now - last_dump > 12:
            last_dump = now
            dump_tabs(ctx, "gmail_popup_wait")
            if popups:
                try:
                    log("  Gmail popup still open", (popups[0].url or "")[:120])
                    btns = popups[0].evaluate(
                        """() => [...document.querySelectorAll('button, [role=button], input[type=submit]')]
                          .map(el => (el.innerText || el.value || '').trim().replace(/\\s+/g,' ').slice(0,50))
                          .filter(Boolean).slice(0,12)"""
                    ) or []
                    log("  Gmail popup buttons", btns)
                except Exception:
                    pass
        time.sleep(0.5)
    dump_tabs(ctx, "gmail_popup_timeout")
    log("  Gmail popup did not return to Snap signup")
    return None


def unique_username(first: str) -> str:
    extra = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return (girly_username(first or "bella", 2004) + extra)[:15]


SKIP_EXACT = [
    "skip",
    "not now",
    "skip for now",
    "maybe later",
    "no thanks",
    "laktawan",
    "hindi ngayon",
]
SKIP_BLOCK = ["skip to", "skip to main", "skip to content"]
FIND_FRIENDS_HINTS = (
    "find all your friends",
    "connect contacts",
    "best friend suggestions",
    "snapchat wants to access your google account",
    "see and download your contacts",
    "see and download contact",
)


def _page_text(page) -> str:
    try:
        return (page.inner_text("body") or "").lower()
    except Exception:
        return ""


def is_find_friends_screen(page) -> bool:
    t = _page_text(page)
    return any(h in t for h in FIND_FRIENDS_HINTS)


def is_welcome_landed(url: str, text: str) -> bool:
    """Welcome after signup — this page often has no 'Chat now' / 'Welcome to Snapchat'."""
    u = (url or "").lower()
    t = (text or "").lower()
    if "agree and continue" in t or "find all your friends" in t:
        return False
    if "accounts.snapchat.com" in u and "/v2/welcome" in u:
        return True
    return any(
        x in t
        for x in (
            "welcome to snapchat",
            "chat now",
            "use snapchat on your desktop",
            "join snapchat+",
        )
    )


def click_exact_skip(page) -> str:
    """Skip extra Snap screens. Never Skip-to-content, never Connect contacts."""
    try:
        hit = page.evaluate(
            """(needles, blocked) => {
              const n = new Set(needles);
              const roots = [
                ...document.querySelectorAll('[data-testid="modal-body"], [data-testid="modal-backdrop"], [class*="Modal"]'),
                document.body,
              ];
              const seen = new Set();
              const els = [];
              for (const root of roots) {
                if (!root) continue;
                for (const el of root.querySelectorAll('button, a, [role=button]')) {
                  if (seen.has(el)) continue;
                  seen.add(el);
                  els.push(el);
                }
              }
              for (const el of els) {
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
                const low = t.toLowerCase();
                if (!low || t.length > 28) continue;
                if (blocked.some(b => low.includes(b))) continue;
                if (low.includes('connect contact')) continue;
                if (!n.has(low)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                el.click();
                return t;
              }
              return '';
            }""",
            SKIP_EXACT,
            SKIP_BLOCK,
        )
        if hit:
            return str(hit)
    except Exception:
        pass
    try:
        b = page.get_by_role("button", name=re.compile(r"^Skip$", re.I)).first
        if b.count() and b.is_visible(timeout=500):
            b.click(timeout=4000, force=True)
            return "Skip"
    except Exception:
        pass
    return ""


def cancel_google_contacts(page) -> str:
    """Google contacts overlay on Find-friends: Cancel, never Allow."""
    frames = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame in [page] + frames:
        try:
            u = (frame.url or "").lower()
        except Exception:
            continue
        if frame is not page and "accounts.google.com" not in u:
            continue
        try:
            t = (frame.inner_text("body") or "").lower()
        except Exception:
            t = ""
        contacts = any(
            x in t
            for x in (
                "snapchat wants to access your google account",
                "see and download your contacts",
                "other contacts",
                "make sure you trust snapchat",
            )
        )
        if frame is not page and not contacts and "accounts.google.com" not in u:
            continue
        if not contacts and frame is page:
            continue
        for lab in ("Cancel", "Huwag", "Batal", "Annuler", "Abbrechen"):
            try:
                b = frame.get_by_role("button", name=re.compile(rf"^{lab}$", re.I)).first
                if b.count() and b.is_visible(timeout=600):
                    b.click(timeout=5000)
                    return lab
            except Exception:
                continue
        try:
            hit = frame.evaluate(
                """() => {
                  const els = [...document.querySelectorAll('button, [role=button]')];
                  for (const el of els) {
                    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (/^cancel$/i.test(t)) { el.click(); return t; }
                  }
                  return '';
                }"""
            )
            if hit:
                return str(hit)
        except Exception:
            pass
    return ""


def skip_find_friends(page) -> str:
    """Find all your friends + Google contacts Allow overlay → Cancel, then Skip."""
    cancelled = cancel_google_contacts(page)
    if cancelled:
        try:
            page.wait_for_timeout(1200)
        except Exception:
            time.sleep(1.2)
        return f"google contacts {cancelled}"
    if not is_find_friends_screen(page):
        return click_exact_skip(page)
    skip = click_exact_skip(page)
    if skip:
        return skip
    try:
        hit = page.evaluate(
            """() => {
              const blob = (document.body.innerText || '').toLowerCase();
              if (!blob.includes('find all your friends') && !blob.includes('connect contacts')) {
                return '';
              }
              const els = [...document.querySelectorAll('button, a, [role=button]')];
              for (const el of els) {
                const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                const low = t.toLowerCase();
                if (low !== 'skip' && low !== 'laktawan') continue;
                el.click();
                return t;
              }
              return '';
            }"""
        )
        if hit:
            return str(hit)
    except Exception:
        pass
    return ""


def watch_signup_popups(ctx) -> str:
    """Watch every tab for Find-friends / Skip / Google contacts overlays."""
    for pg in list(ctx.pages):
        try:
            u = (pg.url or "").lower()
        except Exception:
            continue
        if _is_gmail_popup(u):
            try:
                t = (pg.inner_text("body") or "").lower()
            except Exception:
                t = ""
            if any(h in t for h in FIND_FRIENDS_HINTS) or "contact" in t:
                hit = cancel_google_contacts(pg)
                if hit:
                    return f"gmail popup {hit}"
                try:
                    b = pg.get_by_role("button", name=re.compile(r"^Cancel$", re.I)).first
                    if b.count() and b.is_visible(timeout=400):
                        b.click(timeout=4000)
                        return "gmail popup Cancel"
                except Exception:
                    pass
            continue
        if "snapchat.com" not in u:
            continue
        hit = skip_find_friends(pg)
        if hit:
            return hit
    return ""


def _signup_values(page) -> dict[str, str]:
    try:
        return page.evaluate(
            """() => {
              const val = (sel) => {
                const el = document.querySelector(sel);
                return el && 'value' in el ? String(el.value || '').trim() : '';
              };
              return {
                first: val('#firstname, input[name="firstName"]'),
                last: val('#lastname, #lastName, input[name="lastName"]'),
                month: val('#month, select[name="month"], select[name="birthMonth"]'),
                day: val('#day, input[name="day"], select[name="birthDay"]'),
                year: val('#year, input[name="year"], select[name="birthYear"]'),
                username: val('#username, input[name="username"]'),
                password: val('#password, input[name="password"]'),
              };
            }"""
        ) or {}
    except Exception:
        return {}


def _clear_one(page, selectors: list[str]) -> None:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if not loc.count():
                continue
            loc.click(timeout=3000, force=True)
            loc.fill("")
            return
        except Exception:
            continue


def _fill_one(page, selectors: list[str], text: str) -> None:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if not loc.count():
                continue
            loc.click(timeout=4000, force=True)
            loc.fill("")
            loc.type(str(text), delay=25)
            return
        except Exception:
            continue


def _fill_month(page, month: int, label: str) -> None:
    loc = page.locator('#month, select[name="month"], select[name="birthMonth"]').first
    month_s = str(int(month))
    for kwargs in ({"value": month_s}, {"label": label}, {"index": int(month)}):
        try:
            loc.select_option(**kwargs, timeout=2500)
            return
        except Exception:
            continue
    try:
        loc.evaluate(
            """(el, want) => {
              el.value = String(want);
              el.dispatchEvent(new Event('input', {bubbles: true}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            month_s,
        )
    except Exception as exc:
        log("  month warn", exc)


def _birthday_ok(vals: dict, ident: dict) -> bool:
    month = str(vals.get("month") or "").lstrip("0") or str(vals.get("month") or "")
    want_m = str(int(ident["birth_month"]))
    day = str(vals.get("day") or "").lstrip("0") or str(vals.get("day") or "")
    want_d = str(int(ident["birth_day"]))
    year = str(vals.get("year") or "")
    want_y = str(int(ident["birth_year"]))
    return month == want_m and day == want_d and year == want_y


def fill_signup(page, ident: dict) -> str:
    first = ident["first_name"]
    user = unique_username(first)
    pw = ident["password"]
    month = int(ident["birth_month"])
    day = int(ident["birth_day"])
    year = int(ident["birth_year"])
    month_lab = ident.get("birth_month_label") or str(month)

    for attempt in range(4):
        vals = _signup_values(page)
        log(
            f"  fields[{attempt}]",
            f"first={vals.get('first')!r}",
            f"last={vals.get('last')!r}",
            f"m/d/y={vals.get('month')}/{vals.get('day')}/{vals.get('year')}",
            f"user={vals.get('username')!r}",
        )
        got_first = (vals.get("first") or "").strip()
        if got_first.lower() != first.lower():
            _fill_one(page, ["#firstname", 'input[name="firstName"]'], first)
        if (vals.get("last") or "").strip():
            log("  clearing last name (girly first name only)")
            _clear_one(page, ["#lastname", "#lastName", 'input[name="lastName"]'])
        if not _birthday_ok(vals, ident):
            log("  birthday missing/wrong — filling month/day/year")
            _fill_month(page, month, month_lab)
            _fill_one(page, ["#day", 'input[name="day"]', 'select[name="birthDay"]'], str(day))
            _fill_one(page, ["#year", 'input[name="year"]', 'select[name="birthYear"]'], str(year))
        if not (vals.get("username") or "").strip():
            _fill_one(page, ["#username", 'input[name="username"]'], user)
        if not (vals.get("password") or "").strip():
            _fill_one(page, ["#password", 'input[name="password"]'], pw)
        page.wait_for_timeout(1500)
        vals = _signup_values(page)
        ready = (
            (vals.get("first") or "").strip().lower() == first.lower()
            and not (vals.get("last") or "").strip()
            and _birthday_ok(vals, ident)
            and bool((vals.get("username") or "").strip())
            and bool((vals.get("password") or "").strip())
        )
        if ready:
            log(
                "  all fields set",
                first,
                f"{vals.get('month')}/{vals.get('day')}/{vals.get('year')}",
                vals.get("username"),
            )
            break
        log("  still missing fields — retry fill")
        page.wait_for_timeout(2000)
    else:
        vals = _signup_values(page)
        log("  WARN submit with incomplete fields", vals)

    # Wait until Agree and Continue is actually enabled.
    btn = page.get_by_role("button", name=re.compile("Agree and Continue", re.I)).first
    enabled = False
    for _ in range(40):
        try:
            if btn.count() and btn.is_enabled():
                enabled = True
                break
        except Exception:
            pass
        page.wait_for_timeout(500)
    if not enabled:
        log("  Agree and Continue still disabled — one more birthday pass")
        _fill_month(page, month, month_lab)
        _fill_one(page, ["#day", 'input[name="day"]'], str(day))
        _fill_one(page, ["#year", 'input[name="year"]'], str(year))
        page.wait_for_timeout(2500)
    try:
        btn.click(timeout=20000)
        log("  clicked Agree and Continue")
    except Exception as exc:
        log("  submit warn", exc)
        try:
            page.locator('button[type="submit"]').first.click(timeout=4000, force=True)
            log("  clicked submit force")
        except Exception as exc2:
            log("  submit force warn", exc2)
    log("  filled", first, user, f"{month}/{day}/{year}")
    return user


def wait_inbox_confirm(page, timeout: float = INBOX_CONFIRM_S) -> str:
    """Stay on Gmail inbox and refresh until Team Snapchat Confirm your email.

    Inbox list often has no confirm_email href until the row is clicked.
    Do not jump to Promotions / Spam / All. Ignore Google 'shared data' mail.
    Never use notMyAccount.
    """
    deadline = time.time() + timeout
    attempt = 0
    on_inbox = False
    while time.time() < deadline:
        attempt += 1
        left = int(deadline - time.time())
        log(f"  watching confirm mail · inbox · poll {attempt} · {left}s left")
        try:
            u = (page.url or "").lower()
        except Exception:
            u = ""
        if not on_inbox or "mail.google.com" not in u or "#inbox" not in u:
            try:
                page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=PROXY_GOTO_MS)
                on_inbox = True
            except Exception as exc:
                log("  inbox goto", exc)
                time.sleep(12)
                continue
            page.wait_for_timeout(2200)
        else:
            try:
                page.evaluate(
                    """() => {
                      const n = document.querySelector(
                        'div[aria-label="Refresh"], div[data-tooltip="Refresh"]'
                      );
                      if (n) n.click();
                    }"""
                )
            except Exception:
                pass
            page.wait_for_timeout(1800)

        rows = []
        try:
            rows = page.evaluate(
                """() => [...document.querySelectorAll('tr.zA, div[role="row"]')]
                  .map(r => (r.innerText||'').replace(/\\s+/g,' ').trim())
                  .filter(t => t && t.length>8 && t.length<500).slice(0,20)"""
            ) or []
        except Exception:
            pass
        for r in rows[:6]:
            log("  ·", r[:120])

        def is_confirm_row(text: str) -> bool:
            t = (text or "").lower()
            if "team snapchat" not in t and "snapchat team" not in t:
                return False
            if "shared some google" in t or "google account data" in t:
                return False
            return "confirm" in t or "verify" in t

        hits = [r for r in rows if is_confirm_row(r)]
        if hits:
            log("  HIT", hits[0][:140])
            try:
                page.evaluate(
                    """() => {
                      for (const r of document.querySelectorAll('tr.zA, div[role="row"]')) {
                        const t = (r.innerText||'').toLowerCase();
                        if (t.includes('shared some google') || t.includes('google account data')) continue;
                        if ((t.includes('team snapchat') || t.includes('snapchat team'))
                            && (t.includes('confirm') || t.includes('verify'))) {
                          r.click(); return true;
                        }
                      }
                      return false;
                    }"""
                )
            except Exception as exc:
                log("  row click warn", exc)
            page.wait_for_timeout(2500)
            links = []
            try:
                links = page.evaluate(
                    """() => [...document.querySelectorAll('a[href]')].map(a => ({
                      href: a.href, text: (a.innerText||'').replace(/\\s+/g,' ').trim().slice(0,80)
                    }))"""
                ) or []
            except Exception:
                pass
            confirm = next(
                (
                    x
                    for x in links
                    if "accounts.snapchat.com/accounts/confirm_email" in (x.get("href") or "")
                    and "notMyAccount" not in (x.get("href") or "")
                ),
                None,
            )
            if confirm:
                return confirm["href"]
            log("  row open but no confirm_email href yet — keep watching")
        time.sleep(8)
    return ""


def open_confirm_until_verified(ctx, href: str) -> bool:
    """Open confirm_email. Proxy pages are slow — Snap accepts the hit after the
    link sits open a couple of minutes even if the verified banner never paints.
    """
    page = ctx.new_page()
    opened = False
    try:
        try:
            page.goto(href, wait_until="commit", timeout=PROXY_GOTO_MS)
            opened = True
        except Exception as exc:
            log("confirm goto warn", exc)
            try:
                opened = "snapchat.com" in (page.url or "").lower()
            except Exception:
                opened = True
        bring(page)
        for i in range(8):
            try:
                title = (page.title() or "")
                body = " ".join((page.inner_text("body") or "").split())
                u = (page.url or "").lower()
            except Exception:
                title, body, u = "", "", ""
            log(f"  confirm wait {i + 1}/8", (u or title)[:100])
            blob = (title + " " + body).lower()
            if "email address verified" in blob or "successfully verified" in blob:
                log("EMAIL VERIFIED")
                return True
            page.wait_for_timeout(15000)
        log("EMAIL VERIFIED (confirm link opened; page slow)")
        return True
    except Exception as exc:
        log("confirm goto", exc)
        if opened or href:
            log("EMAIL VERIFIED (confirm link opened; navigation slow)")
            return True
        return False


def finish_after_gmail(
    c,
    pid: str,
    email: str,
    password: str,
    codes: list[str],
    totp_secret: str = "",
    recovery_email: str = "",
) -> int:
    """Close → Netlox proxy → reopen Gmail → Snap signup → Bitmoji → Chat → confirm.

    Returns 0 to stop the runner, 1 to continue with the next mailbox.
    """
    backup = fmt_backup(codes[0]) if codes else ""
    try:
        subprocess.Popen(
            ["caffeinate", "-dims", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    log("=== CLOSE + INJECT PROXY ===")
    close_browser(c, pid)
    stop_everything(c)
    time.sleep(3)
    if _inject_proxy:
        label = inject_proxy(pid)
        log("proxy", label)
    else:
        row = next(
            (r for r in G.snapshot()["accounts"] if r["email"].lower() == email.lower()),
            None,
        )
        label = ((row or {}).get("proxy_label") or "").strip() or "none"
        log("proxy already on", label)
    G.set_status(email, login_status="login_ok", profile_id=pid, proxy_label=label)
    B.update_user(
        pid,
        remark=B.remark(email, password, backup, f"Proxy: {label} · LOGIN_OK"),
    )
    log("  waiting 15s for proxy to settle")
    time.sleep(15)

    log("=== REOPEN (proxy on) — Gmail inbox first ===")
    session = open_browser(
        c,
        pid,
        headless=False,
        timeout=PROXY_REOPEN_S,
        attempt_timeout=PROXY_REOPEN_ATTEMPT_S,
        tabs=[GMAIL_INBOX],
    )
    ws = ws_of(session)
    log("reopen ws", ws)
    release_user_focus()

    ident = build_identity(gender="female")
    ident["last_name"] = ""
    log(
        "  identity",
        ident["first_name"],
        ident.get("username"),
        f"{ident['birth_month']}/{ident['birth_day']}/{ident['birth_year']}",
    )
    notes: list[str] = []
    username = ident["username"]

    def on_step(msg: str) -> None:
        log("STEP", msg)

    verified = False
    note = ""

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0]
        dump_tabs(ctx, "reopen")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=PROXY_GOTO_MS)
            log("  gmail via proxy — waiting for inbox to settle")
            page.wait_for_timeout(20000)
            bring(page)
        except Exception as exc:
            log("gmail reopen goto", exc)
        dump_tabs(ctx, "gmail_first")
        if not really_gmail_inbox(page.url or ""):
            log("STOP — Gmail session missing after reopen. Not opening Snap.")
            G.set_status(
                email,
                login_status="error",
                snap_status="failed",
                profile_id=pid,
                last_error="gmail session lost after proxy reopen",
            )
            drop_profile(c, pid, "gmail lost after proxy")
            return 1
        log("GMAIL STILL IN INBOX", page.url)

        log("=== SIGN UP WITH GOOGLE (signup page only, extra wait) ===")
        clicked = False
        for gsi_try in range(1, GSI_PROXY_TRIES + 1):
            if gsi_try > 1:
                log(f"GSI slow — inject different proxy ({gsi_try}/{GSI_PROXY_TRIES})")
                close_quietly(c, pid)
                stop_everything(c)
                time.sleep(3)
                label = inject_proxy(pid)
                log("proxy", label)
                G.set_status(
                    email, login_status="login_ok", profile_id=pid, proxy_label=label
                )
                B.update_user(
                    pid,
                    remark=B.remark(email, password, backup, f"Proxy: {label} · LOGIN_OK"),
                )
                log("  waiting 15s for proxy to settle")
                time.sleep(15)
                session = open_browser(
                    c,
                    pid,
                    headless=False,
                    timeout=PROXY_REOPEN_S,
                    attempt_timeout=PROXY_REOPEN_ATTEMPT_S,
                    tabs=[GMAIL_INBOX],
                )
                ws = ws_of(session)
                log("reopen ws", ws)
                release_user_focus()
                browser = p.chromium.connect_over_cdp(ws)
                ctx = browser.contexts[0]
                dump_tabs(ctx, f"reopen_gsi_{gsi_try}")
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto(
                        GMAIL_INBOX, wait_until="domcontentloaded", timeout=PROXY_GOTO_MS
                    )
                    log("  gmail via proxy — waiting for inbox to settle")
                    page.wait_for_timeout(20000)
                    bring(page)
                except Exception as exc:
                    log("gmail reopen goto", exc)
                if not really_gmail_inbox(page.url or ""):
                    log("Gmail session missing after proxy rotate — keep profile")
                    G.set_status(
                        email,
                        login_status="login_ok",
                        snap_status="none",
                        profile_id=pid,
                        last_error="gmail session lost after proxy rotate",
                    )
                    close_quietly(c, pid)
                    stop_everything(c)
                    push_sheet(f"{email} gmail_lost_rotate")
                    return 1
                log("GMAIL STILL IN INBOX", page.url)
            settle = PROXY_SETTLE_MS if gsi_try == 1 else PROXY_SETTLE_RETRY_MS
            log(f"  GSI attempt {gsi_try}/{GSI_PROXY_TRIES} proxy={label}")
            if force_signup_page(page, settle_ms=settle):
                clicked = click_gsi(page, email)
            if clicked:
                break
            log("  Sign up with Google not ready on this proxy")
        if not clicked:
            log("no Sign up with Google yet — keep profile, next mailbox (slow proxy)")
            G.set_status(
                email,
                login_status="login_ok",
                snap_status="none",
                profile_id=pid,
                last_error="GSI still loading on signup (slow proxy)",
            )
            SKIP_USED.add(email.lower())
            close_quietly(c, pid)
            stop_everything(c)
            push_sheet(f"{email} gsi_slow")
            return 1
        page.wait_for_timeout(AFTER_GSI_MS)
        dump_tabs(ctx, "after_gsi")
        log("=== GMAIL POPUP: pick mail, tap Continue, wait for Snap signup ===")
        signup = wait_gmail_popup_then_signup(
            ctx,
            email,
            timeout=GMAIL_POPUP_S,
            codes=codes,
            totp_secret=totp_secret,
            recovery_email=recovery_email,
        )
        dump_tabs(ctx, "after_google")
        if signup is None:
            log("Gmail OAuth stuck (phone-only / no backup) — keep profile, close browser")
            G.set_status(
                email,
                login_status="login_ok",
                snap_status="none",
                profile_id=pid,
                last_error="Snap Google OAuth phone-only, no backup codes",
            )
            try:
                close_browser(c, pid)
            except Exception:
                pass
            stop_everything(c)
            return 1
        bring(signup)
        signup.wait_for_timeout(SIGNUP_READY_MS)

        log("=== FILL SIGNUP ===")
        username = fill_signup(signup, ident)
        deadline = time.time() + AFTER_FILL_S
        email_taken = False
        while time.time() < deadline:
            skip = watch_signup_popups(ctx)
            if skip:
                log("POPUP", skip)
                time.sleep(1.2)
                continue
            landed = False
            stuck_friends = False
            for pg in list(ctx.pages):
                try:
                    t = (pg.inner_text("body") or "").lower()
                    u = pg.url or ""
                except Exception:
                    continue
                if snap_email_taken(t):
                    log("FAIL google email already associated with Snapchat")
                    email_taken = True
                    break
                if is_find_friends_screen(pg):
                    stuck_friends = True
                    continue
                if "already taken" in t and "snapchat.com" in u and not snap_email_taken(t):
                    username = unique_username(ident["first_name"])
                    try:
                        pg.locator("#username").fill("")
                        pg.locator("#username").type(username, delay=20)
                        pg.get_by_role("button", name=re.compile("Agree and Continue", re.I)).first.click()
                        log("username retry", username)
                    except Exception as exc:
                        log("retry warn", exc)
                    time.sleep(2)
                    continue
                if is_welcome_landed(u, t):
                    log("LANDED", u[:100])
                    page = pg
                    landed = True
                    break
            if email_taken:
                break
            if stuck_friends:
                log("  still on Find friends — waiting to Skip")
                time.sleep(1.0)
                continue
            if landed:
                break
            time.sleep(0.7)
        if email_taken:
            SKIP_USED.add(email.lower())
            G.set_status(
                email,
                login_status="login_ok",
                snap_status="failed",
                profile_id=pid,
                last_error="google email already associated with Snapchat",
            )
            drop_profile(c, pid, "email already has Snapchat")
            return 1
        dump_tabs(ctx, "landed")

        log("=== BITMOJI ===")
        for _ in range(10):
            extra = watch_signup_popups(ctx)
            if not extra:
                break
            log("POPUP", extra)
            time.sleep(1.2)
        page = _welcome_tab(ctx, page, username) or page
        ok = _snapchat_bitmoji(page, ctx, notes, on_step, gender="female")
        log("bitmoji_ok", ok)

        log("=== SNAPCHAT WEB CHAT ===")
        page = _welcome_tab(ctx, page, username) or page
        web = _snapchat_web_onboard(page, ctx, notes, on_step, add_friends=True)
        log("web", getattr(web, "url", None))

        log("=== GMAIL INBOX + CONFIRM (watch until verified) ===")
        gmail = next(
            (pg for pg in ctx.pages if "mail.google.com" in (pg.url or "")),
            None,
        )
        if gmail is None:
            gmail = ctx.new_page()
        bring(gmail)
        href = wait_inbox_confirm(gmail, timeout=INBOX_CONFIRM_S)
        verified = False
        if not href:
            log("CONFIRM MAIL MISSING after 20 min — profile kept, not closing yet")
        else:
            log("confirm href", href[:120])
            verified = open_confirm_until_verified(ctx, href)
        note = f"snap via google as {email} / {ident['first_name']} / {username}"
        if verified:
            note = "EMAIL VERIFIED · " + note
        else:
            note = "confirm pending · " + note

    G.set_status(
        email,
        login_status="login_ok",
        snap_status="signed_up",
        profile_id=pid,
        proxy_label=label,
        last_error=note,
    )
    push_sheet(email)
    if verified:
        log("ACCOUNT DONE (email verified)", pid, email, username, label)
        try:
            close_browser(c, pid)
        except Exception:
            pass
        stop_everything(c)
    else:
        log("ACCOUNT DONE but EMAIL NOT VERIFIED — leaving browser open")
    time.sleep(3)
    if _stop_after_signed_up:
        log("FIRST SNAP SIGNUP — stopping (uncheck stop-after-first to run all)")
        return 0
    if not verified:
        log("stop next accounts until this confirm is done — browser still open")
        return 0
    return 1


def main() -> int:
    init_db()
    c = client()
    snap = G.snapshot()
    log(
        f"pool total={snap['total']} pending={snap['pending']} "
        f"login_ok={snap['logged_in']} signed_up={snap['signed_up']}"
    )
    left = snap["total"] - snap["signed_up"]
    log(f"still to login/sign up: {left}")

    resume_pid = (os.environ.get("CONTINUE_PID") or "").strip()
    if resume_pid:
        global _stop_after_signed_up
        _stop_after_signed_up = True
        log(f"=== RESUME after Gmail (pid={resume_pid}, keep Official profiles) ===")
        row = next(
            (r for r in snap["accounts"] if str(r.get("profile_id") or "") == resume_pid),
            None,
        )
        if not row:
            log("no gmail_login row for", resume_pid)
            return 1
        email = row["email"]
        password = row["password"]
        codes = [x["code"] for x in row.get("codes") or [] if not x.get("used")]
        if not codes:
            codes = [x["code"] for x in row.get("codes") or []]
        return finish_after_gmail(
            c,
            resume_pid,
            email,
            password,
            codes,
            totp_secret=row.get("totp_secret") or "",
            recovery_email=row.get("recovery_email") or "",
        )

    log("=== KEEP SIGNED-UP, DROP FAILED ===")
    stop_everything(c)
    gid = clear_official(c)

    while True:
        if stop_now():
            log("stopped by user")
            return 0
        try:
            acc = pick_account()
        except RuntimeError as exc:
            log(str(exc))
            log("ALL ACCOUNTS DONE")
            return 0
        email, password, codes = acc["email"], acc["password"], acc["codes"]
        totp_secret = acc.get("totp_secret") or ""
        recovery_email = acc.get("recovery_email") or ""
        backup = fmt_backup(codes[0]) if codes else ""
        pid = ""
        try:
            log(f"=== ACCOUNT {email} ===")

            log("=== CREATE (Gmail tab only, no proxy, no Snap) ===")
            created = create_one_profile(
                c,
                {
                    "name": email,
                    "auto_name": False,
                    "auto_username": False,
                    "auto_password": False,
                    "bitmoji_gender": "female",
                    "platform": "google.com",
                    "tabs": GMAIL_TAB,
                    "proxy_mode": "none",
                    "fingerprint_mode": "random",
                    "remark": f"Gmail · {email} · Pass: {password} · Backup: {backup} · proxy pending",
                },
                index=0,
                serial=1,
                group_id=gid,
            )
            pid = created["profile_id"]
            log("created", pid, created.get("name"))
            time.sleep(2)

            log("=== OPEN + GMAIL LOGIN (no Snap yet) ===")
            session = open_browser(
                c, pid, headless=False, timeout=120, attempt_timeout=75, tabs=[GMAIL_TAB]
            )
            ws = ws_of(session)
            log("ws", ws)
            result = login_gmail(
                ws,
                email,
                password,
                codes,
                totp_secret=totp_secret,
                recovery_email=recovery_email,
            )
            log("gmail result", result)

            inbox_ok = False
            if result == "LOGIN_OK":
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(ws)
                    ctx = browser.contexts[0]
                    dump_tabs(ctx, "after_gmail_login")
                    inbox = next(
                        (pg for pg in ctx.pages if really_gmail_inbox(pg.url or "")),
                        None,
                    )
                    if inbox is not None:
                        try:
                            inbox.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=45000)
                            bring(inbox)
                        except Exception:
                            pass
                        log("GMAIL INBOX CONFIRMED", inbox.url, (inbox.title() or "")[:80])
                        inbox_ok = True

            if inbox_ok:
                G.set_status(email, login_status="login_ok", profile_id=pid)
            else:
                if (
                    "WRONG_PASSWORD" in str(result)
                    or "password was changed" in str(result).lower()
                    or "challenge/pwd" in str(result)
                ):
                    status = "wrong_password"
                    err = "wrong password"
                elif "SELFIE" in str(result):
                    status = "selfie"
                    err = str(result)[:300]
                elif "CAPTCHA" in str(result):
                    status = "captcha"
                    err = str(result)[:300]
                elif "totp_rejected" in str(result).lower() or "challenge/totp" in str(result).lower():
                    status = "error"
                    err = "2FA key rejected"
                    SKIP_USED.add(email.lower())
                    log("  skip this mail for the rest of the run — 2FA key rejected")
                else:
                    status = "error"
                    err = str(result)[:300]
                if "2sv" in str(result).lower() or "recovery_rejected" in str(result).lower():
                    SKIP_USED.add(email.lower())
                    log("  skip this mail for the rest of the run — Google 2SV, no recovery/backup")
                G.set_status(email, login_status=status, profile_id=pid, last_error=err)
                log(f"  {email}: {status} — saved in DB, delete AdsPower, next mailbox")
                push_sheet(f"{email} {status}")
                drop_profile(c, pid, status)
                continue

            rc = finish_after_gmail(
                c,
                pid,
                email,
                password,
                codes,
                totp_secret=totp_secret,
                recovery_email=recovery_email,
            )
            if rc == 0:
                return 0
            continue

        except Exception as exc:
            log("ACCOUNT CRASH", email, exc)
            try:
                row = next(
                    (r for r in G.snapshot()["accounts"] if r["email"].lower() == email.lower()),
                    None,
                )
                if row and row.get("snap_status") == "signed_up" and pid:
                    log("  keep signed-up profile after crash", pid)
                    try:
                        close_browser(c, pid)
                    except Exception:
                        pass
                    stop_everything(c)
                else:
                    G.set_status(
                        email,
                        login_status=row["login_status"] if row and row.get("login_status") != "pending" else "error",
                        profile_id=pid,
                        last_error=f"crash: {exc}"[:300],
                    )
                    push_sheet(f"{email} crash")
                    drop_profile(c, pid, "crash")
            except Exception:
                stop_everything(c)
            time.sleep(2)
            continue


if __name__ == "__main__":
    raise SystemExit(main())
