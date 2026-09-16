#!/usr/bin/env python3
"""One-at-a-time Gmail login: create/reuse profile → start → WAIT for CDP → login
with multi backup codes (no early redirect) → inbox → stop → inject Netlox.

Usage:
  python scripts/gmail_batch_login_v2.py                 # all remaining
  python scripts/gmail_batch_login_v2.py hhdnghahh@...  # one email
"""
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
BATCH_FILE = Path("/Users/jhayramirez/Projects/snappymake/data/snappymail_batch.tsv")
STATUS_FILE = Path("/tmp/snappymail_batch_status.json")
SKIP_EMAILS = {"pashtrsafre@gmail.com"}

NETLOX_HOST = "proxy.netloxproxies.com"
NETLOX_PORT = "8080"
NETLOX_USER_PREFIX = "SL5PYGQLDA"
NETLOX_PASS = "kgywfgsjnm"
NETLOX_LIFE = "10080"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


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


def load_accounts() -> list[dict]:
    rows = []
    for line in BATCH_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        rows.append(
            {
                "email": parts[0],
                "password": parts[1],
                "codes": [c.replace(" ", "") for c in parts[2:] if c.strip()],
            }
        )
    return rows


def group_id() -> str:
    d = api_get("/api/v1/group/list", {"page_size": 200})
    for g in (d.get("data") or {}).get("list") or []:
        if g.get("group_name") == GROUP_NAME:
            return str(g.get("group_id"))
    raise RuntimeError("group missing")


def list_group_profiles(gid: str) -> list[dict]:
    d = api_get("/api/v1/user/list", {"group_id": gid, "page_size": 100})
    return list((d.get("data") or {}).get("list") or [])


def find_best_profile(email: str, profiles: list[dict]) -> dict | None:
    matches = [x for x in profiles if (x.get("name") or "") == email]
    if not matches:
        return None
    # Prefer ones that look logged-in / not ERROR
    def score(x):
        rem = (x.get("remark") or "").lower()
        s = 0
        if "login_ok" in rem:
            s += 100
        if "proxy:" in rem and "pending" not in rem:
            s += 50
        if "captcha" in rem:
            s -= 20
        if "error" in rem:
            s -= 50
        return s

    return sorted(matches, key=score, reverse=True)[0]


def update_user(pid: str, **fields) -> None:
    body = {"user_id": pid, **fields}
    d = api_post("/api/v1/user/update", body)
    if str(d.get("code")) not in ("0", "0"):
        # try anyway
        print("  update warn", d.get("code"), d.get("msg"))


def remark_line(email: str, password: str, backup: str, extra: str) -> str:
    base = f"Gmail · {email} · Pass: {password} · Backup: {backup}"
    return f"{base} · {extra}" if extra else base


def fmt_backup(code: str) -> str:
    code = code.replace(" ", "")
    return " ".join(code[i : i + 4] for i in range(0, len(code), 4))


def create_profile(email: str, gid: str, password: str, backup: str) -> str:
    body = {
        "name": email,
        "group_id": gid,
        "tabs": ["https://accounts.google.com/signin/v2/identifier?service=mail&continue=https://mail.google.com/mail/u/0/"],
        "user_proxy_config": {"proxy_soft": "no_proxy"},
        "fingerprint_config": {
            "automatic_timezone": "1",
            "language": ["en-US", "en"],
            "ua": UA,
        },
        "remark": remark_line(email, password, backup, "proxy pending"),
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


def stop_browser(pid: str) -> None:
    try:
        api_get("/api/v1/browser/stop", {"user_id": pid}, timeout=30)
    except Exception:
        pass
    time.sleep(1.5)


def start_browser_ready(pid: str, retries: int = 3) -> str:
    """Start and wait until CDP websocket actually accepts connections."""
    last_err = None
    for attempt in range(1, retries + 1):
        stop_browser(pid)
        print(f"  start attempt {attempt}")
        d = api_get(
            "/api/v1/browser/start",
            {
                "user_id": pid,
                "open_tabs": "1",
                "ip_tab": "0",
                "headless": "0",
            },
            timeout=120,
        )
        if str(d.get("code")) != "0":
            last_err = f"start api: {d}"
            print("  ", last_err)
            time.sleep(3)
            continue
        ws = ((d.get("data") or {}).get("ws") or {}).get("puppeteer") or ""
        if not ws:
            last_err = f"no ws: {d}"
            print("  ", last_err)
            time.sleep(3)
            continue
        print(f"  ws {ws}")
        # Wait for browser to settle, then probe CDP
        for wait_i in range(12):
            time.sleep(1.5)
            try:
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(ws, timeout=8000)
                    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
                    print(f"  cdp ok pages={len(pages)} wait={wait_i}")
                    # Do NOT browser.close() — that kills the AdsPower window.
                    # Leaving the sync_playwright context only disconnects CDP.
                return ws
            except Exception as exc:
                last_err = str(exc).split("\n")[0]
                print(f"  cdp wait {wait_i}: {last_err[:120]}")
        stop_browser(pid)
        time.sleep(2)
    raise RuntimeError(f"browser never ready: {last_err}")


def random_netlox() -> tuple[dict, str]:
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
    return cfg, label


def inject_proxy(pid: str) -> str:
    cfg, label = random_netlox()
    update_user(pid, user_proxy_config=cfg)
    return label


def human_type(page, locator, text: str) -> None:
    locator.click(timeout=20000)
    page.wait_for_timeout(random.randint(150, 350))
    try:
        locator.fill("")
    except Exception:
        pass
    for ch in text:
        locator.type(ch, delay=random.randint(50, 130))
    page.wait_for_timeout(random.randint(200, 400))


def find_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    for pg in reversed(pages):
        u = (pg.url or "").lower()
        if "accounts.google.com" in u or "mail.google.com" in u or "gds.google.com" in u:
            return pg
    return pages[-1] if pages else None


def click_named(page, *names) -> str | None:
    for name in names:
        loc = page.get_by_role("button", name=name)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=8000)
                page.wait_for_timeout(random.randint(1400, 2200))
                return name
        except Exception:
            pass
    for sel in ("#identifierNext", "#passwordNext", "#totpNext", "#backupCodeNext"):
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=8000)
                page.wait_for_timeout(1600)
                return sel
        except Exception:
            pass
    return None


def visible_input(page, selectors, *, skip_email=False, skip_password=False):
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


def pick_option(page, needles: list[str]) -> str | None:
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


def skip_to_inbox(page, browser) -> bool:
    for step in range(14):
        page = find_page(browser) or page
        host = urlparse(page.url).netloc.lower()
        print(f"  [{step}] {host}{urlparse(page.url).path}")
        if host == "mail.google.com":
            return True
        hit = page.evaluate(
            """() => {
              const prefer=['skip','not now','cancel','no thanks','later','remind me later','done','got it','laktawan'];
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
        print(f"  click {hit}")
        if hit:
            page.wait_for_timeout(1700)
            continue
        try:
            page.goto(
                "https://mail.google.com/mail/u/0/#inbox",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            page.wait_for_timeout(2500)
        except Exception as exc:
            print("  goto", exc)
            break
    page = find_page(browser) or page
    return "mail.google.com" in (page.url or "")


def login_gmail(ws: str, email: str, password: str, codes: list[str]) -> str:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws, timeout=30000)
        page = None
        for _ in range(40):
            page = find_page(browser)
            if page:
                break
            time.sleep(0.5)
        if not page:
            return "FAIL:no_page"
        # Intentionally do NOT bring_to_front — keep AdsPower in background
        print("  page", page.url)

        if "mail.google.com" in (page.url or "") and "accounts.google" not in page.url:
            print("  already inbox")
            return "LOGIN_OK"

        if "accounts.google" not in (page.url or ""):
            page.goto(
                "https://accounts.google.com/signin/v2/identifier?service=mail"
                "&continue=https://mail.google.com/mail/u/0/",
                wait_until="commit",
                timeout=90000,
            )
            page.wait_for_timeout(2500)
            page = find_page(browser) or page

        # Email
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
            print("  email→", click_named(page, "Next", "Susunod", "Continue"), page.url)
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        # Password
        field = None
        for _ in range(45):
            if "recaptcha" in (page.url or ""):
                return "HIT_RECAPTCHA"
            field = visible_input(
                page, ['input[type="password"]', 'input[name="Passwd"]', "#password input"]
            )
            if field:
                break
            if "challenge/" in (page.url or ""):
                break
            page.wait_for_timeout(400)
        if field:
            human_type(page, field, password)
            print("  pass→", click_named(page, "Next", "Susunod", "Continue"), page.url)
        page.wait_for_timeout(1200)
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        for needles in (
            ["try another way", "another way", "sumubok ng iba"],
            ["backup code", "8-digit", "8 digit", "ilagay ang isa"],
        ):
            hit = pick_option(page, needles)
            print("  pick", needles[0], "→", hit)
            page.wait_for_timeout(1300)

        # Backup codes — verify each; no redirect until accepted
        need_code = "challenge/bc" in (page.url or "") or visible_input(
            page,
            ['input[type="tel"]', 'input[name="totpPin"]', 'input[name="Pin"]'],
            skip_email=True,
            skip_password=True,
        )
        if need_code:
            ok = False
            for code in codes:
                print(f"  try backup {code}")
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
                print("  typed", field.input_value(), "→", click_named(page, "Next", "Susunod", "Continue"))
                accepted = False
                for _ in range(32):
                    page.wait_for_timeout(500)
                    if "challenge/bc" not in (page.url or ""):
                        accepted = True
                        break
                    if strong_backup_error(page):
                        break
                print("  after", page.url)
                if accepted:
                    print("  CODE_OK", code)
                    ok = True
                    break
                print("  CODE_BAD", code)
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
        print("  INBOX", page.url)
        return "LOGIN_OK"


def save_status(status: dict) -> None:
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def process_one(acc: dict, gid: str, profiles: list[dict], status: dict) -> None:
    email = acc["email"]
    password = acc["password"]
    codes = acc["codes"]
    backup = fmt_backup(codes[0]) if codes else ""

    print(f"\n========== {email} ==========")
    if email in SKIP_EMAILS:
        print("  SKIP prior captcha")
        existing = find_best_profile(email, profiles)
        if existing:
            rem = existing.get("remark") or ""
            if "CAPTCHA UPON LOGIN" not in rem:
                update_user(
                    existing["user_id"],
                    remark=remark_line(email, password, backup, "CAPTCHA UPON LOGIN"),
                )
        status[email] = {"result": "SKIPPED_CAPTCHA", "profile_id": (existing or {}).get("user_id")}
        save_status(status)
        return

    existing = find_best_profile(email, profiles)
    rem = ((existing or {}).get("remark") or "").lower()
    if existing and "login_ok" in rem and "proxy:" in rem and "pending" not in rem:
        print("  already done", existing["user_id"])
        status[email] = {"result": "ALREADY_DONE", "profile_id": existing["user_id"]}
        save_status(status)
        return

    # Reuse logged-in nodmock (proxy pending) — just inject
    if existing and email == "nodmockquorcite@gmail.com" and "proxy pending" in rem:
        pid = existing["user_id"]
        print("  reuse logged-in", pid, "→ inject proxy only")
        # confirm session by opening briefly
        try:
            ws = start_browser_ready(pid)
            result = login_gmail(ws, email, password, codes)
            print("  verify", result)
            stop_browser(pid)
            if result == "LOGIN_OK":
                label = inject_proxy(pid)
                update_user(
                    pid,
                    remark=remark_line(email, password, backup, f"Proxy: {label} · LOGIN_OK"),
                )
                status[email] = {"result": "LOGIN_OK", "profile_id": pid, "proxy": label}
            else:
                status[email] = {"result": result, "profile_id": pid}
        except Exception as exc:
            print("  ERROR", exc)
            stop_browser(pid)
            status[email] = {"result": f"ERROR:{exc}", "profile_id": pid}
        save_status(status)
        return

    pid = existing["user_id"] if existing else None
    if not pid:
        pid = create_profile(email, gid, password, backup)
        print("  created", pid)
        time.sleep(2)
    else:
        print("  reuse", pid)
        update_user(pid, remark=remark_line(email, password, backup, "proxy pending"))

    try:
        ws = start_browser_ready(pid)
        print("  logging in (browser stays open)...")
        result = login_gmail(ws, email, password, codes)
        print("  RESULT", result)

        if result == "HIT_RECAPTCHA":
            update_user(
                pid,
                remark=remark_line(email, password, backup, "CAPTCHA UPON LOGIN"),
            )
            # leave browser open a bit so user can see, then stop
            time.sleep(2)
            stop_browser(pid)
            status[email] = {"result": "HIT_RECAPTCHA", "profile_id": pid}
            save_status(status)
            return

        if result != "LOGIN_OK":
            update_user(pid, remark=remark_line(email, password, backup, f"FAIL {result}"))
            stop_browser(pid)
            status[email] = {"result": result, "profile_id": pid}
            save_status(status)
            return

        stop_browser(pid)
        time.sleep(1.5)
        label = inject_proxy(pid)
        print("  proxy injected:", label)
        update_user(
            pid,
            remark=remark_line(email, password, backup, f"Proxy: {label} · LOGIN_OK"),
        )
        status[email] = {"result": "LOGIN_OK", "profile_id": pid, "proxy": label}
        save_status(status)
    except Exception as exc:
        print("  ERROR", exc)
        try:
            stop_browser(pid)
            update_user(pid, remark=remark_line(email, password, backup, f"ERROR {exc}"))
        except Exception:
            pass
        status[email] = {"result": f"ERROR:{exc}", "profile_id": pid}
        save_status(status)


def main() -> int:
    only = set(a.lower() for a in sys.argv[1:]) if len(sys.argv) > 1 else set()
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
        {"result": "LOGIN_OK", "note": "prior (recovery email)", "profile_id": "k1gtykla"},
    )
    status.setdefault(
        "maetafoterway@gmail.com",
        {"result": "LOGIN_OK", "note": "prior (backup code)", "profile_id": "k1guwqdg"},
    )

    for acc in accounts:
        if only and acc["email"].lower() not in only:
            continue
        profiles = list_group_profiles(gid)
        process_one(acc, gid, profiles, status)
        time.sleep(2)

    print("\n======== STATUS ========")
    for k, v in status.items():
        print(f"{k}\t{v.get('result')}\t{v.get('profile_id')}\t{v.get('proxy','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
