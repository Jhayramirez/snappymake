#!/usr/bin/env python3
"""Batch: create AdsPower profile (no proxy) → Gmail login (multi backup codes) →
skip cards → close → inject Netlox HTTP → update remark.

Skip accounts listed in SKIP_EMAILS. On reCAPTCHA: remark CAPTCHA UPON LOGIN and continue.
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
SKIP_EMAILS = {"pashtrsafre@gmail.com"}  # prior captcha — do not retry

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


def api_post(path: str, body: dict, timeout: float = 60) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
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
        email, password = parts[0], parts[1]
        codes = [c.replace(" ", "") for c in parts[2:] if c.strip()]
        rows.append({"email": email, "password": password, "codes": codes})
    return rows


def group_id() -> str:
    d = api_get("/api/v1/group/list", {"page_size": 200})
    for g in (d.get("data") or {}).get("list") or []:
        if g.get("group_name") == GROUP_NAME:
            return str(g.get("group_id"))
    created = api_post("/api/v1/group/create", {"group_name": GROUP_NAME, "remark": "Gmail test"})
    return str((created.get("data") or {}).get("group_id") or "")


def find_profile_by_name(email: str, gid: str) -> dict | None:
    d = api_get("/api/v1/user/list", {"group_id": gid, "page_size": 100})
    for x in (d.get("data") or {}).get("list") or []:
        if (x.get("name") or "") == email:
            return x
    return None


def update_remark(pid: str, remark: str) -> None:
    api_post("/api/v1/user/update", {"user_id": pid, "remark": remark})


def append_remark(pid: str, email: str, password: str, backup: str, extra: str) -> None:
    base = f"Gmail · {email} · Pass: {password} · Backup: {backup}"
    if extra:
        base = f"{base} · {extra}"
    update_remark(pid, base)


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
    # AdsPower v1 update accepts user_proxy_config
    api_post(
        "/api/v1/user/update",
        {"user_id": pid, "user_proxy_config": cfg},
    )
    return label


def create_profile(email: str, gid: str, password: str, backup: str) -> str:
    body = {
        "name": email,
        "group_id": gid,
        "tabs": ["https://www.gmail.com"],
        "user_proxy_config": {"proxy_soft": "no_proxy"},
        "fingerprint_config": {
            "automatic_timezone": "1",
            "language": ["en-US", "en"],
            "flash": "block",
            "fonts": ["all"],
            "webrtc": "disabled",
            "ua": UA,
        },
        "remark": f"Gmail · {email} · Pass: {password} · Backup: {backup} · proxy pending",
    }
    d = api_post("/api/v2/browser-profile/create", body)
    data = d.get("data") or {}
    pid = data.get("profile_id") or data.get("id") or ""
    if not pid and isinstance(data, list) and data:
        pid = data[0]
    if not pid:
        # some versions return id string directly under data as profile_id list
        raise RuntimeError(f"create failed: {d}")
    return str(pid)


def start_browser(pid: str) -> str:
    d = api_get(
        "/api/v1/browser/start",
        {"user_id": pid, "open_tabs": "1", "ip_tab": "0", "headless": "0"},
        timeout=90,
    )
    if str(d.get("code")) != "0":
        raise RuntimeError(f"start failed: {d}")
    ws = ((d.get("data") or {}).get("ws") or {}).get("puppeteer") or ""
    if not ws:
        raise RuntimeError(f"no ws: {d}")
    return ws


def stop_browser(pid: str) -> None:
    try:
        api_get("/api/v1/browser/stop", {"user_id": pid}, timeout=30)
    except Exception:
        pass


def human_type(page, locator, text: str) -> None:
    locator.click(timeout=20000)
    page.wait_for_timeout(random.randint(120, 280))
    try:
        locator.fill("")
    except Exception:
        pass
    for ch in text:
        locator.type(ch, delay=random.randint(45, 120))
        if random.random() < 0.05:
            page.wait_for_timeout(random.randint(60, 180))
    page.wait_for_timeout(random.randint(150, 300))


def find_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    for pg in reversed(pages):
        host = urlparse(pg.url or "").netloc.lower()
        if host in ("accounts.google.com", "mail.google.com", "gds.google.com"):
            return pg
    return pages[-1] if pages else None


def click_named(page, *names) -> str | None:
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


def visible_editable(page, selectors, skip_password=False, skip_email=False):
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


def pick_challenge_option(page, needles: list[str]) -> str | None:
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


def skip_post_login(page, browser) -> bool:
    for step in range(14):
        page = find_page(browser) or page
        host = urlparse(page.url).netloc.lower()
        print(f"  [{step}] {host}{urlparse(page.url).path}")
        if host == "mail.google.com" and "#inbox" in (page.url or ""):
            return True
        if host == "mail.google.com":
            return True
        hit = page.evaluate(
            """() => {
              const prefer=['skip','not now','cancel','no thanks','later','remind me later','done','got it','laktawan','ok'];
              const nodes=Array.from(document.querySelectorAll('button,a,div[role="button"],span'));
              for (const key of prefer) {
                for (const n of nodes) {
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
            page.wait_for_timeout(1600)
            continue
        try:
            page.goto(
                "https://mail.google.com/mail/u/0/#inbox",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            page.wait_for_timeout(2000)
        except Exception as exc:
            print("  goto", exc)
            break
    page = find_page(browser) or page
    return "mail.google.com" in (page.url or "")


def login_gmail(ws: str, email: str, password: str, codes: list[str]) -> str:
    """Returns LOGIN_OK | HIT_RECAPTCHA | FAIL:..."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        page = None
        for _ in range(50):
            page = find_page(browser)
            if page and (
                "accounts.google" in page.url
                or "mail.google" in page.url
                or "gmail" in page.url.lower()
            ):
                break
            time.sleep(0.35)
        if not page:
            return "FAIL:no_page"
        try:
            page.bring_to_front()
        except Exception:
            pass
        print("  start", page.url)

        # Already inbox?
        if "mail.google.com" in (page.url or "") and "accounts.google" not in (page.url or ""):
            print("  already inbox")
            return "LOGIN_OK"

        if "accounts.google" not in (page.url or ""):
            try:
                page.goto(
                    "https://accounts.google.com/signin/v2/identifier?service=mail"
                    "&continue=https://mail.google.com/mail/u/0/",
                    wait_until="commit",
                    timeout=90000,
                )
            except Exception as exc:
                print("  goto warn", exc)
            page.wait_for_timeout(2000)
            page = find_page(browser) or page

        # Email
        field = None
        for _ in range(30):
            if "recaptcha" in (page.url or ""):
                return "HIT_RECAPTCHA"
            field = visible_editable(
                page, ['input[type="email"]', "#identifierId", 'input[name="identifier"]']
            )
            if field:
                break
            # maybe already past email
            if visible_editable(page, ['input[type="password"]', 'input[name="Passwd"]']):
                break
            page.wait_for_timeout(350)
        if field:
            human_type(page, field, email)
            print("  email", click_named(page, "Next", "Susunod", "Continue"), page.url)
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        # Password
        field = None
        for _ in range(40):
            if "recaptcha" in (page.url or ""):
                return "HIT_RECAPTCHA"
            field = visible_editable(
                page, ['input[type="password"]', 'input[name="Passwd"]', "#password input"]
            )
            if field:
                break
            if "challenge/bc" in (page.url or "") or "challenge/selection" in (page.url or ""):
                break
            page.wait_for_timeout(400)
        if field:
            human_type(page, field, password)
            print("  pass", click_named(page, "Next", "Susunod", "Continue"), page.url)
        page.wait_for_timeout(1000)
        if "recaptcha" in (page.url or ""):
            return "HIT_RECAPTCHA"

        # Challenge selection → backup code
        for needles in (
            ["try another way", "another way", "sumubok ng iba"],
            ["backup code", "8-digit", "8 digit", "ilagay ang isa"],
        ):
            hit = pick_challenge_option(page, needles)
            print("  pick", needles[0], hit)
            page.wait_for_timeout(1200)

        # Try each backup code; do NOT redirect until accepted
        if "challenge/bc" in (page.url or "") or visible_editable(
            page,
            ['input[type="tel"]', 'input[name="totpPin"]', 'input[name="Pin"]'],
            skip_email=True,
            skip_password=True,
        ):
            ok = False
            for code in codes:
                print(f"  try_code {code}")
                field = None
                for _ in range(20):
                    field = visible_editable(
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
                print("  typed", field.input_value(), click_named(page, "Next", "Susunod", "Continue"))
                accepted = False
                for _ in range(30):
                    page.wait_for_timeout(500)
                    if "challenge/bc" not in (page.url or ""):
                        accepted = True
                        break
                    if strong_backup_error(page):
                        break
                print("  after_code", page.url)
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

        if not skip_post_login(page, browser):
            page = find_page(browser) or page
            if "mail.google.com" not in (page.url or ""):
                return f"FAIL:not_inbox:{page.url}"
        print("  LOGIN_OK", page.url)
        return "LOGIN_OK"


def save_status(status: dict) -> None:
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def main() -> int:
    only = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    gid = group_id()
    print("GROUP", gid)
    accounts = load_accounts()
    status: dict = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except Exception:
            status = {}

    # Also track prior successes not in batch
    for email, info in {
        "zainamurillo981@gmail.com": {"result": "LOGIN_OK", "note": "prior (recovery email)"},
        "maetafoterway@gmail.com": {"result": "LOGIN_OK", "note": "prior (backup code)"},
    }.items():
        status.setdefault(email, info)

    for acc in accounts:
        email = acc["email"]
        if only and email not in only:
            continue
        if email in SKIP_EMAILS:
            print(f"\n=== SKIP {email} (prior captcha) ===")
            # ensure remark tagged
            existing = find_profile_by_name(email, gid)
            if existing:
                rem = existing.get("remark") or ""
                if "CAPTCHA UPON LOGIN" not in rem:
                    update_remark(
                        existing["user_id"],
                        (rem + " · CAPTCHA UPON LOGIN").strip(" ·"),
                    )
            status[email] = {
                "result": "SKIPPED_CAPTCHA",
                "profile_id": (existing or {}).get("user_id"),
                "remark": "CAPTCHA UPON LOGIN",
            }
            save_status(status)
            continue

        print(f"\n=== {email} ===")
        existing = find_profile_by_name(email, gid)
        pid = None
        already_ok = False

        if existing:
            pid = existing["user_id"]
            rem = (existing.get("remark") or "").lower()
            print(f"  existing profile {pid}")
            # If already logged + proxy, skip
            if "proxy:" in rem and "captcha" not in rem and "pending" not in rem:
                print("  already has proxy — skip")
                status[email] = {
                    "result": "ALREADY_DONE",
                    "profile_id": pid,
                    "remark": existing.get("remark"),
                }
                save_status(status)
                continue
            # nodmock / logged but proxy pending → just inject
            if email == "nodmockquorcite@gmail.com" or "proxy pending" in rem:
                # check if browser open and inbox
                already_ok = True

        backup_disp = " ".join(acc["codes"][0][i : i + 4] for i in range(0, 8, 4)) if acc["codes"] else ""

        try:
            if not pid:
                pid = create_profile(email, gid, acc["password"], backup_disp)
                print("  created", pid)
                time.sleep(1.2)

            if already_ok and email == "nodmockquorcite@gmail.com":
                # verify inbox then stop + proxy
                # ensure started
                active = api_get("/api/v1/browser/local-active")
                open_ids = {
                    x.get("user_id")
                    for x in ((active.get("data") or {}).get("list") or [])
                }
                if pid not in open_ids:
                    ws = start_browser(pid)
                else:
                    ws = next(
                        (x.get("ws") or {}).get("puppeteer")
                        for x in ((active.get("data") or {}).get("list") or [])
                        if x.get("user_id") == pid
                    )
                result = login_gmail(ws, email, acc["password"], acc["codes"])
            else:
                stop_browser(pid)
                time.sleep(0.8)
                ws = start_browser(pid)
                print("  ws", ws[:60], "...")
                time.sleep(2)
                result = login_gmail(ws, email, acc["password"], acc["codes"])

            print("  RESULT", result)

            if result == "HIT_RECAPTCHA":
                append_remark(
                    pid,
                    email,
                    acc["password"],
                    backup_disp,
                    "CAPTCHA UPON LOGIN",
                )
                stop_browser(pid)
                status[email] = {
                    "result": "HIT_RECAPTCHA",
                    "profile_id": pid,
                    "remark": "CAPTCHA UPON LOGIN",
                }
                save_status(status)
                continue

            if result != "LOGIN_OK":
                append_remark(pid, email, acc["password"], backup_disp, f"FAIL {result}")
                stop_browser(pid)
                status[email] = {"result": result, "profile_id": pid}
                save_status(status)
                continue

            stop_browser(pid)
            time.sleep(1.0)
            label = inject_proxy(pid)
            print("  proxy", label)
            append_remark(
                pid,
                email,
                acc["password"],
                backup_disp,
                f"Proxy: {label} · LOGIN_OK",
            )
            status[email] = {
                "result": "LOGIN_OK",
                "profile_id": pid,
                "proxy": label,
            }
            save_status(status)
        except Exception as exc:
            print("  ERROR", exc)
            if pid:
                try:
                    stop_browser(pid)
                    append_remark(
                        pid,
                        email,
                        acc["password"],
                        backup_disp,
                        f"ERROR {exc}",
                    )
                except Exception:
                    pass
            status[email] = {"result": f"ERROR:{exc}", "profile_id": pid}
            save_status(status)

    print("\n======== STATUS ========")
    for k, v in status.items():
        print(f"{k}\t{v.get('result')}\t{v.get('profile_id')}\t{v.get('proxy') or v.get('remark') or ''}")
    save_status(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
