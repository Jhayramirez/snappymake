#!/usr/bin/env python3
"""One profile at a time. Start ONCE, keep open until login done. No focus. No open/close retry spam."""
from __future__ import annotations

import json
import random
import re
import string
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

BASE = "http://local.adspower.net:50325"
GROUP_NAME = "SnappyMail-Test"
# Cross-platform paths (Windows/macOS): anchor to the project root, not /Users or /tmp.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BATCH_FILE = ROOT / "data" / "snappymail_batch.tsv"
STATUS_FILE = ROOT / "data" / "snappymail_batch_status.json"
SKIP_EMAILS = {"pashtrsafre@gmail.com", "hhdnghahh@gmail.com"}
SKIP_IF_PROFILE_EXISTS = True  # user: skip mails already in AdsPower group

NETLOX_HOST = "proxy.netloxproxies.com"
NETLOX_PORT = "8080"
NETLOX_USER_PREFIX = "SL5PYGQLDA"
NETLOX_PASS = "kgywfgsjnm"
NETLOX_LIFE = "10080"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


def log(*a):
    print(*a, flush=True)


def api_get(path: str, params: dict | None = None, timeout: float = 30) -> dict:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def api_post(path: str, body: dict, timeout: float = 90) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_accounts():
    rows = []
    for line in BATCH_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) < 2:
            continue
        email, password = parts[0], parts[1]
        raw_codes = parts[2:]
        codes = []
        for c in raw_codes:
            digits = "".join(ch for ch in c if ch.isdigit())
            if not digits:
                continue
            # pad short codes to 8 if clearly truncated paste
            if len(digits) < 8:
                digits = digits.zfill(8)
            codes.append(digits[:8] if len(digits) >= 8 else digits)
        rows.append({"email": email, "password": password, "codes": codes})
    return rows


def group_id() -> str:
    # Prefer known SnappyMail-Test id; fall back to search
    known = "10675936"
    try:
        d = api_get("/api/v1/group/list", {"page_size": 200})
        for g in (d.get("data") or {}).get("list") or []:
            if g.get("group_name") == GROUP_NAME:
                return str(g.get("group_id"))
    except Exception as exc:
        log("  group list warn", exc)
    return known


def list_profiles(gid: str):
    d = api_get("/api/v1/user/list", {"group_id": gid, "page_size": 100})
    return list((d.get("data") or {}).get("list") or [])


def find_profile(email: str, profiles):
    matches = [x for x in profiles if (x.get("name") or "") == email]
    if not matches:
        return None

    def score(x):
        rem = (x.get("remark") or "").lower()
        s = 0
        if "login_ok" in rem:
            s += 100
        if "proxy:" in rem and "pending" not in rem:
            s += 50
        if "error" in rem:
            s -= 50
        if "captcha" in rem:
            s -= 20
        return s

    return sorted(matches, key=score, reverse=True)[0]


def update_user(pid: str, **fields):
    api_post("/api/v1/user/update", {"user_id": pid, **fields})


def remark(email, password, backup, extra=""):
    base = f"Gmail · {email} · Pass: {password} · Backup: {backup}"
    return f"{base} · {extra}" if extra else base


def fmt_backup(code: str) -> str:
    code = code.replace(" ", "")
    return " ".join(code[i : i + 4] for i in range(0, len(code), 4))


def create_profile(email, gid, password, backup) -> str:
    body = {
        "name": email,
        "group_id": gid,
        "tabs": [
            "https://accounts.google.com/signin/v2/identifier?hl=en&service=mail&continue=https://mail.google.com/mail/u/0/"
        ],
        "user_proxy_config": {"proxy_soft": "no_proxy"},
        "fingerprint_config": {
            "automatic_timezone": "1",
            "language": ["en-US", "en"],
            "page_language": "en-US",
            "ua": UA,
        },
        "remark": remark(email, password, backup, "proxy pending"),
    }
    d = api_post("/api/v2/browser-profile/create", body)
    if str(d.get("code")) != "0":
        raise RuntimeError(f"create: {d}")
    data = d.get("data") or {}
    pid = data.get("profile_id") or data.get("id") or ""
    if isinstance(pid, list):
        pid = pid[0] if pid else ""
    if not pid:
        raise RuntimeError(f"create no id: {d}")
    return str(pid)


def stop_browser(pid: str):
    try:
        api_get("/api/v1/browser/stop", {"user_id": pid}, timeout=30)
    except Exception:
        pass


def stop_all_browsers():
    """Hard rule: never more than one AdsPower browser. Close everything first."""
    for round_i in range(3):
        try:
            active = api_get("/api/v1/browser/local-active")
        except Exception:
            break
        lst = (active.get("data") or {}).get("list") or []
        if not lst:
            return
        for x in lst:
            pid = x.get("user_id")
            if pid:
                log("  stop leftover", pid)
                stop_browser(pid)
        time.sleep(2)
    try:
        lst = (api_get("/api/v1/browser/local-active").get("data") or {}).get("list") or []
        if lst:
            log("  warn stale active (continuing):", [x.get("user_id") for x in lst])
    except Exception:
        pass


def start_once(pid: str) -> str:
    """Start ONE browser. Require debug port stable for several seconds (crash detection)."""
    stop_all_browsers()
    time.sleep(2.0)

    log("  starting browser (only this one)...")
    d = api_get(
        "/api/v1/browser/start",
        {"user_id": pid, "open_tabs": "1", "ip_tab": "0", "headless": "0"},
        timeout=120,
    )
    if str(d.get("code")) != "0":
        raise RuntimeError(f"start failed: {d}")
    ws = ((d.get("data") or {}).get("ws") or {}).get("puppeteer") or ""
    if not ws:
        raise RuntimeError(f"no ws: {d}")
    log("  ws", ws)

    # Wait until port is up, then require STABLE_OK consecutive seconds
    STABLE_NEED = 4
    stable = 0
    saw_up = False
    for i in range(25):
        if tcp_ok(ws):
            saw_up = True
            stable += 1
            log(f"  port up ({stable}/{STABLE_NEED}) t={i}s")
            if stable >= STABLE_NEED:
                time.sleep(1.0)
                if not tcp_ok(ws):
                    stop_browser(pid)
                    raise RuntimeError("browser died during settle (unstable profile)")
                return ws
        else:
            if saw_up:
                # was up then died — classic crash profile
                stop_browser(pid)
                raise RuntimeError("browser crashed ~2s after open (unstable profile)")
            stable = 0
            if i % 3 == 0:
                log(f"  waiting port t={i}s")
        time.sleep(1)
    stop_browser(pid)
    raise RuntimeError(f"port never stable: {ws}")


def delete_profile(pid: str) -> None:
    try:
        api_post("/api/v2/browser-profile/delete", {"profile_id": [pid]})
        log("  deleted broken profile", pid)
    except Exception as exc:
        log("  delete warn", exc)


def start_with_retries(pid: str, attempts: int = 1) -> str:
    """Single open. No open/close spam — caller recreates if this fails."""
    log("  opening browser once...")
    return start_once(pid)


def is_crash_error(exc_or_result) -> bool:
    s = str(exc_or_result).lower()
    return any(
        x in s
        for x in (
            "has been closed",
            "target closed",
            "econnrefused",
            "port never",
            "browser has been closed",
            "ws disconnected",
            "err_aborted",
            "frame was detached",
            "unstable profile",
            "crashed ~2s",
            "died during settle",
        )
    )


def tcp_ok(ws: str) -> bool:
    import socket

    try:
        host = ws.split("://")[1].split("/")[0]
        h, p = host.split(":")
        s = socket.socket()
        s.settimeout(1.5)
        s.connect((h, int(p)))
        s.close()
        return True
    except Exception:
        return False


def inject_proxy(pid: str) -> str:
    states = json.load(
        urllib.request.urlopen("https://netloxproxies.com/api/locations/countries/US/states")
    )["states"]
    st = random.choice(states)
    cities = json.load(
        urllib.request.urlopen(
            f"https://netloxproxies.com/api/locations/countries/US/states/{st['code']}/cities"
        )
    )["cities"]
    city = random.choice(cities)
    sess = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    user = f"{NETLOX_USER_PREFIX}_US-{st['code']}-{city['code']}_{NETLOX_LIFE}_{sess}"
    label = f"{st.get('name') or st['code']}/{city.get('name') or city['code']}"
    cfg = {
        "proxy_soft": "other",
        "proxy_type": "http",
        "proxy_host": NETLOX_HOST,
        "proxy_port": NETLOX_PORT,
        "proxy_user": user,
        "proxy_password": NETLOX_PASS,
    }
    update_user(pid, user_proxy_config=cfg)
    return label


def human_type(page, locator, text: str):
    locator.click(timeout=20000)
    page.wait_for_timeout(random.randint(120, 280))
    try:
        locator.fill("")
    except Exception:
        pass
    try:
        for ch in text:
            locator.type(ch, delay=random.randint(45, 120))
    except Exception:
        # fallback if locator goes stale mid-type
        try:
            locator.fill(text)
        except Exception:
            raise
    page.wait_for_timeout(random.randint(150, 320))


def find_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    for pg in reversed(pages):
        if really_gmail_inbox(pg.url or ""):
            return pg
    for pg in reversed(pages):
        u = (pg.url or "").lower()
        if "accounts.google.com" in u or "gds.google.com" in u:
            return pg
    return pages[-1] if pages else None


def click_named(page, *names):
    extra = ("다음", "계속", "확인", "继续", "下一步", "确定", "確定", "繼續", "次へ", "続行")
    for name in (*names, *extra):
        loc = page.get_by_role("button", name=name)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=8000)
                page.wait_for_timeout(random.randint(1200, 2000))
                return name
        except Exception:
            pass
    for sel in ("#identifierNext", "#passwordNext", "#totpNext", "#backupCodeNext"):
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=8000)
                page.wait_for_timeout(1500)
                return sel
        except Exception:
            pass
    return None


def visible_input(page, selectors, skip_email=False, skip_password=False):
    for sel in selectors:
        loc = page.locator(sel)
        try:
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                if not (el.is_visible() and el.is_editable()):
                    continue
                tp = (el.get_attribute("type") or "").lower()
                nm = (el.get_attribute("name") or "").lower()
                if skip_email and (tp == "email" or "identifier" in nm):
                    continue
                if skip_password and (tp == "password" or "passwd" in nm):
                    continue
                return el
        except Exception:
            pass
    return None


def body_text(page) -> str:
    try:
        return " ".join((page.inner_text("body") or "").split())
    except Exception:
        return ""


def really_gmail_inbox(url: str) -> bool:
    """True only on the real Gmail app inbox — not marketing, not Google sign-in."""
    u = (url or "").lower()
    if "accounts.google" in u or "workspace.google" in u:
        return False
    return "mail.google.com/mail" in u


PWD_FAIL = (
    "wrong password",
    "incorrect password",
    "password is incorrect",
    "couldn't sign you in",
    "could not sign you in",
    "wrong password. try again",
    "enter a valid password",
    "too many failed attempts",
    "your password was changed",
    "mali ang password",
    "hindi tama ang password",
    "hindi tama ang iyong password",
)


def password_rejected(page) -> bool:
    url = (page.url or "").lower()
    if "challenge/pwd" not in url and "/pwd" not in url:
        return False
    blob = body_text(page).lower()
    if any(s in blob for s in PWD_FAIL):
        return True
    try:
        err = page.evaluate(
            """() => {
              const n = document.querySelector(
                '[aria-live="assertive"], [jsname="B34EJ"], span[id*="passwordError"], div[jsname="B34EJ"]'
              );
              return ((n && n.innerText) || '').trim();
            }"""
        )
    except Exception:
        err = ""
    low = (err or "").lower()
    return bool(low) and any(
        s in low
        for s in (
            "wrong",
            "incorrect",
            "invalid",
            "failed attempts",
            "password was changed",
        )
    )


def strong_backup_error(page) -> bool:
    if "challenge/bc" not in (page.url or ""):
        return False
    blob = body_text(page).lower()
    return any(
        s in blob
        for s in (
            "wrong",
            "incorrect",
            "invalid",
            "didn't work",
            "didnt work",
            "try again",
            "mali ang",
            "hindi tama",
            "code you entered",
            "hindi wastong",
        )
    )


def pick_option(page, needles):
    return page.evaluate(
        """(needles) => {
          const nodes=Array.from(document.querySelectorAll(
            '[role="link"],[role="option"],li,div[role="button"],button,a,span,label,div'
          ));
          const hits=[];
          for (const n of nodes) {
            const raw=((n.innerText||'')+' '+(n.getAttribute('aria-label')||'')).trim();
            const t=raw.toLowerCase().replace(/\\s+/g,' ');
            if (!t || t.length>70) continue;
            if (t.includes('susunod') || t==='next' || t.includes('skip to')) continue;
            if (!needles.some(x=>t.includes(x))) continue;
            hits.push({n, t, len:t.length});
          }
          hits.sort((a,b)=>a.len-b.len);
          if (!hits.length) return null;
          const best=hits[0];
          (best.n.closest('[role="link"],[role="option"],[role="button"],button,a,li')||best.n).click();
          return best.t;
        }""",
        needles,
    )


BACKUP_NEEDLES = [
    "backup code",
    "backup codes",
    "8-digit",
    "8 digit",
    "8-digit backup",
    "enter one of your",
    "enter a backup code",
    "use your backup",
    "ilagay ang isa",
]
TRY_ANOTHER_NEEDLES = ["try another way", "sumubok ng iba"]
TOTP_NEEDLES = [
    "google authenticator",
    "authenticator app",
    "get a verification code from the google authenticator",
    "verification code from the google authenticator",
    "enter the code from your authenticator",
    "authenticator",
]


def on_2sv_chooser(page) -> bool:
    url = (page.url or "").lower()
    if any(
        x in url
        for x in (
            "challenge/selection",
            "challenge/sk",
            "challenge/dp",
            "challenge/ipp",
            "challenge/kpe",
            "challenge/totp",
        )
    ):
        return True
    blob = body_text(page).lower()
    return any(
        x in blob
        for x in (
            "2-step verification",
            "choose how you want to sign in",
            "try another way",
            "tap yes on your phone",
            "confirm your recovery email",
        )
    )


def pick_backup_path(page) -> str | None:
    """From 2-step / method list, open the 8-digit backup-code form."""
    hit = pick_option(page, BACKUP_NEEDLES)
    if hit:
        return hit
    try:
        loc = page.get_by_text(re.compile(r"8-?digit|backup code", re.I)).first
        if loc.count() and loc.is_visible(timeout=800):
            loc.click(timeout=4000)
            return "text:backup"
    except Exception:
        pass
    for name in ("Try another way", "More ways to verify"):
        try:
            loc = page.get_by_role("button", name=re.compile(rf"^{name}$", re.I)).first
            if loc.count() and loc.is_visible(timeout=700):
                loc.click(timeout=5000)
                return f"role:{name}"
        except Exception:
            pass
        try:
            loc = page.get_by_text(re.compile(name, re.I)).first
            if loc.count() and loc.is_visible(timeout=600):
                loc.click(timeout=5000)
                return f"text:{name}"
        except Exception:
            pass
    hit = pick_option(page, TRY_ANOTHER_NEEDLES)
    if hit:
        return hit
    return None


def click_authenticator(page) -> str | None:
    """Open Google Authenticator / 2fa.cn-style 6-digit TOTP form."""
    for pat in (
        r"Get a verification code from the Google Authenticator",
        r"Google Authenticator",
        r"authenticator app",
        r"Enter the 6-digit code",
    ):
        try:
            loc = page.get_by_text(re.compile(pat, re.I)).first
            if loc.count() and loc.is_visible(timeout=700):
                loc.click(timeout=5000)
                return f"text:{pat}"
        except Exception:
            pass
    hit = pick_option(page, TOTP_NEEDLES)
    return hit or None


def click_recovery_email(page) -> str | None:
    """Open Google 'Confirm your recovery email' (not phone, not Send)."""
    for pat in (
        r"Confirm your recovery email",
        r"Enter the email address you added as a recovery",
        r"recovery email",
        r"email you added",
    ):
        try:
            loc = page.get_by_text(re.compile(pat, re.I)).first
            if loc.count() and loc.is_visible(timeout=700):
                loc.click(timeout=5000)
                return f"text:{pat}"
        except Exception:
            pass
    hit = pick_option(
        page,
        [
            "confirm your recovery email",
            "recovery email",
            "enter the email address you added",
        ],
    )
    if hit and "phone" in (hit or "").lower():
        return None
    return hit or None


def on_recovery_form(page) -> bool:
    url = (page.url or "").lower()
    if "challenge/kpe" in url:
        return True
    blob = body_text(page).lower()
    if "recovery email" in blob and ("enter" in blob or "confirm" in blob or "type" in blob):
        return True
    try:
        if page.locator('input[name="knowledgePreregisteredEmailResponse"]').first.is_visible(timeout=300):
            return True
    except Exception:
        pass
    return False


def type_recovery_email(page, recovery: str) -> bool:
    rec = (recovery or "").strip()
    if not rec:
        return False
    field = None
    for _ in range(20):
        field = visible_input(
            page,
            [
                'input[name="knowledgePreregisteredEmailResponse"]',
                'input[type="email"]',
                'input[type="text"]',
            ],
            skip_password=True,
        )
        if field:
            break
        page.wait_for_timeout(250)
    if not field:
        return False
    log("  try recovery email", rec)
    try:
        human_type(page, field, rec)
    except Exception:
        try:
            field.fill(rec)
        except Exception:
            return False
    click_named(page, "Next", "Susunod", "Continue")
    for _ in range(24):
        page.wait_for_timeout(400)
        u = (page.url or "").lower()
        if "challenge/kpe" not in u and not on_recovery_form(page):
            log("  RECOVERY_OK", rec)
            return True
        if strong_backup_error(page):
            log("  RECOVERY_BAD", rec)
            return False
    return "challenge/kpe" not in (page.url or "").lower()


def on_totp_form(page) -> bool:
    url = (page.url or "").lower()
    if "challenge/totp" in url:
        return True
    try:
        if page.locator('input[name="totpPin"]').first.is_visible(timeout=400):
            return True
    except Exception:
        pass
    blob = body_text(page).lower()
    return "enter the 6-digit" in blob and "authenticator" in blob


def type_totp(page, secret: str) -> bool:
    from app.services.totp_2fa import next_code

    sels = ['input[name="totpPin"]']
    if "challenge/totp" in (page.url or "").lower():
        sels.extend(['input[name="Pin"]', 'input[type="tel"]', 'input[type="text"]'])
    field = None
    for _ in range(20):
        field = visible_input(page, sels, skip_email=True, skip_password=True)
        if field:
            break
        page.wait_for_timeout(250)
    if not field:
        return False
    code = next_code(secret)
    log("  try totp", code)
    try:
        human_type(page, field, code)
    except Exception:
        try:
            field.fill(code)
        except Exception:
            return False
    click_named(page, "Next", "Susunod", "Continue")
    for _ in range(24):
        page.wait_for_timeout(400)
        u = (page.url or "").lower()
        if "challenge/totp" not in u and not on_totp_form(page):
            log("  TOTP_OK", code)
            return True
        if strong_backup_error(page):
            log("  TOTP_BAD", code)
            return False
    return "challenge/totp" not in (page.url or "").lower()


def force_english(page) -> None:
    """Keep Google UI in English so we don't chase Filipino labels."""
    try:
        page.context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
    except Exception:
        pass
    try:
        page.evaluate(
            """() => {
              try { document.documentElement.lang = 'en'; } catch (e) {}
              try {
                document.cookie = 'GOOGLE_ABSINTH=en;path=/;domain=.google.com';
                document.cookie = 'OGPC=;path=/;domain=.google.com';
              } catch (e) {}
            }"""
        )
    except Exception:
        pass


def with_hl_en(url: str) -> str:
    if "hl=" in url:
        return url
    join = "&" if "?" in url else "?"
    return f"{url}{join}hl=en"


def click_english(page, *names) -> str | None:
    """Click by exact English button/link name first."""
    for name in names:
        for loc in (
            page.get_by_role("button", name=name),
            page.get_by_role("link", name=name),
            page.locator(f'button:has-text("{name}")'),
            page.locator(f'[role="button"]:has-text("{name}")'),
            page.locator(f'a:has-text("{name}")'),
        ):
            try:
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    return name
            except Exception:
                pass
    return None


def dismiss_selfie(page, browser) -> bool:
    """On selfie challenge: Not now → continue to inbox. Returns True if left selfie."""
    log("  selfie: trying Not now (English)")
    force_english(page)
    # Prefer rewriting URL to hl=en
    try:
        u = page.url or ""
        if "selfie" in u and "hl=en" not in u:
            page.goto(with_hl_en(u), wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            force_english(page)
    except Exception as exc:
        log("  selfie hl=en warn", str(exc).splitlines()[0][:80])

    for attempt in range(5):
        page = find_page(browser) or page
        url = (page.url or "").lower()
        if "selfie" not in url and "mail.google.com" in url:
            return True
        if "selfie" not in url and "accounts.google.com" not in url and "myaccount.google" not in url:
            if "mail.google.com" in url or "gds.google.com" in url:
                return True

        hit = click_english(
            page,
            "Not now",
            "Skip",
            "Skip for now",
            "Do this later",
            "Cancel",
            "No thanks",
            "Remind me later",
        )
        log(f"  selfie click[{attempt}]", hit)
        page.wait_for_timeout(1500)

        # Also try text match (case-insensitive) without Filipino
        if not hit:
            hit = page.evaluate(
                """() => {
                  const prefer=['not now','skip for now','do this later','skip','cancel','no thanks','remind me later'];
                  for (const key of prefer) {
                    for (const n of document.querySelectorAll('button,a,div[role="button"],span')) {
                      const t=(n.innerText||n.getAttribute('aria-label')||'').trim().toLowerCase();
                      if (!t || t.length>36) continue;
                      if (t===key || t.startsWith(key+' ')) {
                        (n.closest('button,a,[role="button"]')||n).click();
                        return t;
                      }
                    }
                  }
                  return null;
                }"""
            )
            log(f"  selfie text[{attempt}]", hit)
            page.wait_for_timeout(1500)

        try:
            page.goto(
                with_hl_en("https://mail.google.com/mail/u/0/#inbox"),
                wait_until="domcontentloaded",
                timeout=90000,
            )
            page.wait_for_timeout(2500)
        except Exception as exc:
            log("  selfie→inbox", str(exc).splitlines()[0][:80])

        page = find_page(browser) or page
        if "mail.google.com" in (page.url or "") and "selfie" not in (page.url or ""):
            return True
        if "selfie" not in (page.url or "").lower():
            return True
    return "selfie" not in (page.url or "").lower()


def skip_to_inbox(page, browser) -> bool:
    for step in range(16):
        page = find_page(browser) or page
        url = page.url or ""
        host = urlparse(url).netloc.lower()
        log(f"  [{step}] {host}{urlparse(url).path}")
        if really_gmail_inbox(url):
            return True
        if password_rejected(page):
            log("  WRONG_PASSWORD on skip")
            return False
        if "challenge/pwd" in url:
            log("  still on password — not skipping to Snap/inbox")
            return False
        if (
            "challenge/bc" in url
            or "signin/rejected" in url
            or on_2sv_chooser(page)
        ):
            log("  still on 2-step — not skipping to inbox")
            return False

        if "selfie" in url.lower() or "verification/selfie" in url.lower():
            if dismiss_selfie(page, browser):
                page = find_page(browser) or page
                if really_gmail_inbox(page.url or ""):
                    return True
                # left selfie — keep skipping other cards
                continue
            log("  HIT_SELFIE_VERIFICATION (Not now did not clear)")
            raise RuntimeError("HIT_SELFIE")

        force_english(page)
        if "gds.google.com" in host and step >= 3:
            log("  gds card — jump to inbox")
            try:
                page.goto(
                    with_hl_en("https://mail.google.com/mail/u/0/#inbox"),
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
                page.wait_for_timeout(2500)
                continue
            except Exception as exc:
                log("  gds→inbox", str(exc).splitlines()[0][:80])
        hit = click_english(
            page,
            "Skip",
            "Not now",
            "Skip for now",
            "Cancel",
            "No thanks",
            "Later",
            "Remind me later",
            "Done",
            "Got it",
            "Next",
            "Continue",
        )
        if not hit:
            hit = page.evaluate(
                """() => {
                  const prefer=['skip','not now','cancel','no thanks','later','remind me later','done','got it','skip for now','do this later'];
                  for (const key of prefer) {
                    for (const n of document.querySelectorAll('button,a,div[role="button"],span')) {
                      const t=(n.innerText||n.getAttribute('aria-label')||'').trim().toLowerCase();
                      if (!t || t.length>40 || t.includes('skip to content')) continue;
                      if (t===key || t.startsWith(key+' ')) {
                        (n.closest('button,a,[role="button"]')||n).click(); return t;
                      }
                    }
                  }
                  return null;
                }"""
            )
        log(f"  click {hit}")
        if hit:
            page.wait_for_timeout(1700)
            continue
        try:
            page.goto(
                with_hl_en("https://mail.google.com/mail/u/0/#inbox"),
                wait_until="domcontentloaded",
                timeout=90000,
            )
            page.wait_for_timeout(2500)
        except Exception as exc:
            log("  goto", exc)
            break
    page = find_page(browser) or page
    return really_gmail_inbox(page.url or "")


def login_gmail(
    ws: str,
    email: str,
    password: str,
    codes: list[str],
    totp_secret: str = "",
    recovery_email: str = "",
) -> str:
    try:
        return _login_gmail_inner(
            ws,
            email,
            password,
            codes,
            totp_secret=totp_secret,
            recovery_email=recovery_email,
        )
    except RuntimeError as exc:
        if "HIT_SELFIE" in str(exc):
            return "FAIL:HIT_SELFIE"
        raise


def _login_gmail_inner(
    ws: str,
    email: str,
    password: str,
    codes: list[str],
    totp_secret: str = "",
    recovery_email: str = "",
) -> str:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws, timeout=30000)
        # Wait for AdsPower to finish spawning its startup tab(s)
        page = None
        for i in range(40):
            pages = [pg for ctx in browser.contexts for pg in ctx.pages]
            if pages:
                # Prefer google/mail tabs; else last page
                for pg in reversed(pages):
                    u = (pg.url or "").lower()
                    if any(
                        x in u
                        for x in (
                            "accounts.google",
                            "mail.google",
                            "gmail.com",
                            "gds.google",
                        )
                    ):
                        page = pg
                        break
                if not page:
                    # skip adspower splash if another tab exists
                    for pg in reversed(pages):
                        u = (pg.url or "").lower()
                        if "start.adspower" not in u and u not in ("about:blank", "chrome://newtab/"):
                            page = pg
                            break
                    page = page or pages[-1]
                break
            time.sleep(0.5)
        if not page:
            if not browser.contexts:
                return "FAIL:no_context"
            page = browser.contexts[0].new_page()

        log("  page", page.url)
        # NO bring_to_front
        force_english(page)

        if really_gmail_inbox(page.url or ""):
            log("  already inbox")
            return "LOGIN_OK"

        def click_verify_you() -> bool:
            """Google 'Verify it's you' has no email box — only Next."""
            try:
                u = (page.url or "").lower()
                t = (page.inner_text("body") or "").lower()
            except Exception:
                return False
            if "confirmidentifier" not in u and "verify it" not in t:
                return False
            log("  verify-it's-you — clicking Next")
            hit = click_named(page, "Next", "Susunod", "Continue")
            log("  verify Next", hit, page.url)
            try:
                page.wait_for_timeout(2000)
            except Exception:
                time.sleep(2)
            return True

        click_verify_you()

        already_google = "accounts.google.com" in (page.url or "").lower()
        # Don't bounce a live 2-step page onto workspace.google.com marketing.
        if not already_google:
            try:
                page.goto(
                    with_hl_en("https://mail.google.com/mail/u/0/#inbox"),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(2500)
                page = find_page(browser) or page
                force_english(page)
                log("  inbox probe", page.url)
                if really_gmail_inbox(page.url or ""):
                    log("  already inbox")
                    return "LOGIN_OK"
            except Exception as exc:
                log("  inbox probe fail", str(exc).splitlines()[0][:100])

        def safe_goto(url: str) -> None:
            nonlocal page
            last = None
            url = with_hl_en(url)
            for attempt in range(4):
                try:
                    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
                    if pages:
                        page = pages[-1]
                    force_english(page)
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(1500)
                    force_english(page)
                    return
                except Exception as exc:
                    last = exc
                    log(f"  goto retry {attempt}: {str(exc).splitlines()[0][:100]}")
                    time.sleep(1.5)
                    try:
                        if browser.contexts:
                            page = browser.contexts[0].new_page()
                    except Exception:
                        pass
            raise last  # type: ignore

        if "accounts.google" not in (page.url or ""):
            safe_goto(
                "https://accounts.google.com/signin/v2/identifier?hl=en&service=mail"
                "&continue=https://mail.google.com/mail/u/0/"
            )
            page = find_page(browser) or page
            force_english(page)
            log("  after goto", page.url)

        field = None
        for _ in range(35):
            if "recaptcha" in (page.url or ""):
                return "HIT_RECAPTCHA"
            field = visible_input(
                page, ['input[type="email"]', "#identifierId", 'input[name="identifier"]']
            )
            if field:
                break
            if visible_input(page, ['input[type="password"]', 'input[name="Passwd"]']):
                break
            click_verify_you()
            page.wait_for_timeout(400)
        if field:
            human_type(page, field, email)
            log("  email→", click_named(page, "Next", "Susunod", "Continue"), page.url)
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        field = None
        for _ in range(45):
            if "recaptcha" in (page.url or ""):
                return "HIT_RECAPTCHA"
            field = visible_input(
                page, ['input[type="password"]', 'input[name="Passwd"]', "#password input"]
            )
            if field:
                break
            if "challenge/" in (page.url or "") and "challenge/pwd" not in (page.url or ""):
                break
            page.wait_for_timeout(400)
        if field:
            # re-resolve password field fresh to avoid stale locator timeout
            fresh = visible_input(
                page, ['input[name="Passwd"]', 'input[type="password"]', "#password input"]
            )
            target = fresh or field
            try:
                human_type(page, target, password)
            except Exception as exc:
                log("  pass type warn", str(exc).splitlines()[0][:100])
                try:
                    target.fill(password)
                except Exception:
                    return f"FAIL:password:{exc}"
            log("  pass→", click_named(page, "Next", "Susunod", "Continue"), page.url)
        page.wait_for_timeout(2200)
        if password_rejected(page):
            log("  WRONG_PASSWORD")
            return "WRONG_PASSWORD"
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        saw_try_another = False
        totp_secret = (totp_secret or "").strip()
        recovery_email = (recovery_email or "").strip()
        recovery_tries = 0
        totp_fails = 0
        for round_i in range(12):
            page = find_page(browser) or page
            url = (page.url or "").lower()
            if "signin/rejected" in url or "/rejected" in urlparse(url).path:
                log("  2sv rejected by Google")
                return "FAIL:2sv_rejected"
            if "challenge/bc" in url or really_gmail_inbox(url):
                break
            if "recaptcha" in url:
                return "HIT_RECAPTCHA"
            if recovery_email and recovery_tries < 5:
                if not on_recovery_form(page):
                    hit = click_recovery_email(page)
                    if hit:
                        log(f"  2sv[{round_i}] recovery option", hit)
                        for _ in range(10):
                            page.wait_for_timeout(300)
                            if on_recovery_form(page):
                                break
                if on_recovery_form(page):
                    if type_recovery_email(page, recovery_email):
                        break
                    log(f"  2sv[{round_i}] recovery rejected")
                    return "FAIL:recovery_rejected"
                if on_2sv_chooser(page) or "challenge/" in url:
                    recovery_tries += 1
                    hit = pick_option(
                        page,
                        [
                            "confirm your recovery email",
                            "recovery email",
                            "try another way",
                        ],
                    )
                    saw_try_another = True
                    log(f"  2sv[{round_i}] try another (for recovery)", hit, urlparse(page.url or "").path)
                    page.wait_for_timeout(1800)
                    continue
            if totp_secret:
                if not on_totp_form(page):
                    hit = click_authenticator(page)
                    if hit:
                        log(f"  2sv[{round_i}] authenticator", hit)
                        for _ in range(10):
                            page.wait_for_timeout(300)
                            if on_totp_form(page):
                                break
                if on_totp_form(page):
                    if type_totp(page, totp_secret):
                        break
                    totp_fails += 1
                    log(f"  2sv[{round_i}] totp rejected ({totp_fails}/1) — skip immediately")
                    if totp_fails >= 1:
                        log("  OTP failed — 2FA key rejected")
                        return "FAIL:totp_rejected"
                if on_2sv_chooser(page) or "challenge/" in url:
                    hit = pick_backup_path(page)
                    saw_try_another = True
                    log(f"  2sv[{round_i}] try another (for authenticator)", hit, urlparse(page.url or "").path)
                    page.wait_for_timeout(1800)
                    continue
            if not on_2sv_chooser(page) and "challenge/" not in url:
                break
            clicked_backup = False
            try:
                loc = page.get_by_text(
                    re.compile(r"Enter one of your 8-?digit backup codes", re.I)
                ).first
                if loc.count() and loc.is_visible(timeout=800):
                    loc.click(timeout=5000)
                    clicked_backup = True
                    log(f"  2sv[{round_i}] clicked backup row")
            except Exception as exc:
                log("  backup row warn", exc)
            if not clicked_backup:
                try:
                    loc = page.get_by_text(re.compile(r"8-?digit|backup code", re.I)).first
                    if loc.count() and loc.is_visible(timeout=600):
                        loc.click(timeout=4000)
                        clicked_backup = True
                        log(f"  2sv[{round_i}] clicked backup text")
                except Exception:
                    pass
            if clicked_backup:
                for _ in range(12):
                    page.wait_for_timeout(350)
                    if "challenge/bc" in (page.url or "").lower():
                        break
                if "challenge/bc" in (page.url or "").lower():
                    log("  backup form open")
                    break
                log("  backup click did not open form", urlparse(page.url or "").path)
                continue
            # Phone / prompt screens hide backup until Try another way.
            # Keep clicking it and re-scan; do not fail after a single click.
            if on_2sv_chooser(page) or "challenge/" in url:
                hit = pick_backup_path(page)
                saw_try_another = True
                log(f"  2sv[{round_i}] try another", hit, urlparse(page.url or "").path)
                page.wait_for_timeout(2500)
                # Wait until Google actually leaves the phone-collect screen.
                for _ in range(8):
                    u2 = (page.url or "").lower()
                    blob = body_text(page).lower()
                    if "challenge/selection" in u2 or "challenge/bc" in u2 or "8-digit" in blob or "backup code" in blob:
                        break
                    page.wait_for_timeout(400)
                continue
            if saw_try_another:
                log("  2sv has no backup-code option")
                return "FAIL:2sv_no_backup"
            break

        page = find_page(browser) or page
        if totp_secret and on_totp_form(page):
            log("  TOTP never accepted — 2FA key rejected")
            return "FAIL:totp_rejected"

        need_code = "challenge/bc" in (page.url or "")
        if need_code:
            ok = False
            for code in codes:
                log(f"  try backup {code}")
                field = None
                for _ in range(25):
                    field = visible_input(
                        page,
                        [
                            'input[type="tel"]',
                            'input[name="totpPin"]',
                            'input[name="Pin"]',
                            'input[type="text"]',
                        ],
                        skip_email=True,
                        skip_password=True,
                    )
                    if field:
                        break
                    page.wait_for_timeout(300)
                if not field:
                    return "FAIL:no_backup_field"
                human_type(page, field, code)
                log(
                    "  typed",
                    field.input_value(),
                    "→",
                    click_named(page, "Next", "Susunod", "Continue"),
                )
                accepted = False
                for _ in range(32):
                    page.wait_for_timeout(500)
                    if "challenge/bc" not in (page.url or ""):
                        accepted = True
                        break
                    if strong_backup_error(page):
                        break
                log("  after", page.url)
                if accepted:
                    log("  CODE_OK", code)
                    ok = True
                    break
                log("  CODE_BAD", code)
                try:
                    field.fill("")
                except Exception:
                    pass
            if not ok and "challenge/bc" in (page.url or ""):
                return "FAIL:all_codes_bad"

        if password_rejected(page):
            log("  WRONG_PASSWORD")
            return "WRONG_PASSWORD"
        if totp_secret and on_totp_form(page):
            log("  TOTP never accepted — 2FA key rejected")
            return "FAIL:totp_rejected"
        if not skip_to_inbox(page, browser):
            page = find_page(browser) or page
            if password_rejected(page):
                return "WRONG_PASSWORD"
            if totp_secret and on_totp_form(page):
                log("  TOTP never accepted — 2FA key rejected")
                return "FAIL:totp_rejected"
            if not really_gmail_inbox(page.url or ""):
                return f"FAIL:not_inbox:{page.url}"
        page = find_page(browser) or page
        if not really_gmail_inbox(page.url or ""):
            if password_rejected(page):
                return "WRONG_PASSWORD"
            if totp_secret and ("challenge/totp" in (page.url or "").lower() or on_totp_form(page)):
                log("  TOTP never accepted — 2FA key rejected")
                return "FAIL:totp_rejected"
            return f"FAIL:not_inbox:{page.url}"
        log("  INBOX", page.url)
        return "LOGIN_OK"


def save_status(status):
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def process_one(acc, gid, profiles, status):
    email = acc["email"]
    password = acc["password"]
    codes = acc["codes"]
    backup = fmt_backup(codes[0]) if codes else ""

    log(f"\n========== {email} ==========")
    if email in SKIP_EMAILS:
        log("  SKIP captcha list")
        existing = find_profile(email, profiles)
        status[email] = {
            "result": "SKIPPED_CAPTCHA",
            "profile_id": (existing or {}).get("user_id"),
        }
        save_status(status)
        return

    existing = find_profile(email, profiles)
    # Only skip finished successes / known captcha. Crashes, errors, selfie, pending → reopen & retry.
    pid = None
    if existing:
        rem = existing.get("remark") or ""
        rem_l = rem.lower()
        if "login_ok" in rem_l:
            log(f"  SKIP done ({existing['user_id']}) [LOGIN_OK]")
            status[email] = {
                "result": "SKIPPED_LOGIN_OK",
                "profile_id": existing["user_id"],
                "remark": rem[:160],
            }
            save_status(status)
            return
        if "captcha" in rem_l and "upon login" in rem_l:
            log(f"  SKIP captcha ({existing['user_id']})")
            status[email] = {
                "result": "SKIPPED_CAPTCHA",
                "profile_id": existing["user_id"],
                "remark": rem[:160],
            }
            save_status(status)
            return
        pid = existing["user_id"]
        why = (
            "selfie"
            if "selfie" in rem_l
            else "crash/error"
            if ("error" in rem_l or "fail" in rem_l or "closed" in rem_l)
            else "incomplete"
        )
        log(f"  reuse {pid} — retry ({why})")

    if not pid:
        pid = create_profile(email, gid, password, backup)
        log("  created", pid)
        time.sleep(2)

    try:
        result = None
        last_exc = None
        # At most: open once → if unstable, recreate once → open once. Never spam.
        for attempt in range(1, 3):
            try:
                ws = start_with_retries(pid)
                log(f"  logging in (no focus)...")
                result = login_gmail(ws, email, password, codes)
                log("  RESULT", result)
                if is_crash_error(result):
                    raise RuntimeError(str(result))
                break
            except Exception as exc:
                last_exc = exc
                log(f"  fail: {str(exc).splitlines()[0][:140]}")
                stop_browser(pid)
                stop_all_browsers()
                if is_crash_error(exc) and attempt < 2:
                    log("  unstable — recreate once (no reopen spam)")
                    try:
                        delete_profile(pid)
                    except Exception:
                        pass
                    time.sleep(2)
                    pid = create_profile(email, gid, password, backup)
                    log("  recreated", pid)
                    time.sleep(5)
                    continue
                raise

        if result is None:
            raise last_exc or RuntimeError("no result")

        if result == "HIT_RECAPTCHA":
            update_user(pid, remark=remark(email, password, backup, "CAPTCHA UPON LOGIN"))
            time.sleep(2)
            stop_browser(pid)
            stop_all_browsers()
            status[email] = {"result": "HIT_RECAPTCHA", "profile_id": pid}
            save_status(status)
            return

        if "SELFIE" in result or result == "FAIL:HIT_SELFIE":
            update_user(
                pid,
                remark=remark(email, password, backup, "SELFIE VERIFICATION REQUIRED"),
            )
            stop_browser(pid)
            stop_all_browsers()
            status[email] = {"result": "HIT_SELFIE", "profile_id": pid}
            save_status(status)
            return

        if result != "LOGIN_OK":
            update_user(pid, remark=remark(email, password, backup, f"FAIL {result}"))
            stop_browser(pid)
            stop_all_browsers()
            status[email] = {"result": result, "profile_id": pid}
            save_status(status)
            return

        # success — close this one, then inject proxy (browser closed)
        stop_browser(pid)
        stop_all_browsers()
        time.sleep(2)
        label = inject_proxy(pid)
        log("  proxy", label)
        update_user(
            pid, remark=remark(email, password, backup, f"Proxy: {label} · LOGIN_OK")
        )
        status[email] = {"result": "LOGIN_OK", "profile_id": pid, "proxy": label}
        save_status(status)
    except Exception as exc:
        log("  ERROR", exc)
        try:
            stop_browser(pid)
            stop_all_browsers()
            update_user(pid, remark=remark(email, password, backup, f"ERROR {exc}"))
        except Exception:
            pass
        status[email] = {"result": f"ERROR:{exc}", "profile_id": pid}
        save_status(status)


def main():
    only = {a.lower() for a in sys.argv[1:]} if len(sys.argv) > 1 else set()
    gid = group_id()
    accounts = load_accounts()
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    status.setdefault(
        "zainamurillo981@gmail.com",
        {"result": "LOGIN_OK", "profile_id": "k1gtykla", "note": "prior"},
    )
    status.setdefault(
        "maetafoterway@gmail.com",
        {"result": "LOGIN_OK", "profile_id": "k1guwqdg", "note": "prior"},
    )

    for acc in accounts:
        if only and acc["email"].lower() not in only:
            continue
        stop_all_browsers()
        profiles = list_profiles(gid)
        process_one(acc, gid, profiles, status)
        stop_all_browsers()
        time.sleep(4)

    log("\n======== STATUS ========")
    for k, v in status.items():
        log(f"{k}\t{v.get('result')}\t{v.get('profile_id')}\t{v.get('proxy', '')}")


if __name__ == "__main__":
    main()
