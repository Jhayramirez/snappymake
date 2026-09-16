#!/usr/bin/env python3
"""One profile at a time. Start ONCE, keep open until login done. No focus. No open/close retry spam."""
from __future__ import annotations

import json
import random
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
        u = (pg.url or "").lower()
        if any(x in u for x in ("accounts.google.com", "mail.google.com", "gds.google.com")):
            return pg
    return pages[-1] if pages else None


def click_named(page, *names):
    for name in names:
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
          const nodes=Array.from(document.querySelectorAll('[role="link"],li,div[role="button"],button,a,div,span'));
          for (const n of nodes) {
            const t=(n.innerText||'').trim().toLowerCase();
            if (!t || t.length>90) continue;
            if (t.includes('susunod') || t==='next') continue;
            if (needles.some(x=>t.includes(x))) {
              (n.closest('[role="link"],[role="button"],button,a,li')||n).click();
              return t.slice(0,90);
            }
          }
          return null;
        }""",
        needles,
    )


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
        if host == "mail.google.com":
            return True

        if "selfie" in url.lower() or "verification/selfie" in url.lower():
            if dismiss_selfie(page, browser):
                page = find_page(browser) or page
                if "mail.google.com" in (page.url or ""):
                    return True
                # left selfie — keep skipping other cards
                continue
            log("  HIT_SELFIE_VERIFICATION (Not now did not clear)")
            raise RuntimeError("HIT_SELFIE")

        force_english(page)
        hit = click_english(
            page,
            "Not now",
            "Skip",
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
    return "mail.google.com" in (page.url or "")


def login_gmail(ws: str, email: str, password: str, codes: list[str]) -> str:
    try:
        return _login_gmail_inner(ws, email, password, codes)
    except RuntimeError as exc:
        if "HIT_SELFIE" in str(exc):
            return "FAIL:HIT_SELFIE"
        raise


def _login_gmail_inner(ws: str, email: str, password: str, codes: list[str]) -> str:
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

        if "mail.google.com" in (page.url or "") and "accounts.google" not in (page.url or ""):
            log("  already inbox")
            return "LOGIN_OK"

        # Try inbox first — session may already exist (e.g. nodmock)
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
            if "mail.google.com" in (page.url or "") and "accounts.google" not in (page.url or ""):
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
        page.wait_for_timeout(1200)
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        for needles in (
            ["try another way", "another way", "sumubok ng iba"],
            ["backup code", "8-digit", "8 digit", "ilagay ang isa"],
        ):
            hit = pick_option(page, needles)
            log("  pick", needles[0], "→", hit)
            page.wait_for_timeout(1300)

        need_code = "challenge/bc" in (page.url or "") or visible_input(
            page,
            ['input[type="tel"]', 'input[name="totpPin"]', 'input[name="Pin"]'],
            skip_email=True,
            skip_password=True,
        )
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

        if not skip_to_inbox(page, browser):
            page = find_page(browser) or page
            if "mail.google.com" not in (page.url or ""):
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
