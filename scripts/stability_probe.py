#!/usr/bin/env python3
"""Isolate WHY freshly-created AdsPower profiles crash while existing/manual ones
don't. Opens a profile via the AdsPower API and polls local-active to see if it
stays alive — no Playwright, no login. Pure browser-stability test.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail_batch_login_v3 as B  # noqa: E402

OFFICIAL_GID = "10722026"
UA = B.UA


def active_ids():
    d = B.api_get("/api/v1/browser/local-active")
    return {x.get("user_id") for x in (d.get("data") or {}).get("list") or []}


def start(pid, tabs=True):
    params = {"user_id": pid, "ip_tab": "0", "headless": "0"}
    params["open_tabs"] = "1" if tabs else "0"
    return B.api_get("/api/v1/browser/start", params, timeout=120)


def probe(name, pid, seconds=16):
    print(f"\n--- PROBE: {name} ({pid}) ---", flush=True)
    d = start(pid)
    ws = ((d.get("data") or {}).get("ws") or {}).get("puppeteer") or ""
    print("  start code", d.get("code"), "ws", ws[:60], flush=True)
    alive_count = 0
    for i in range(seconds // 2):
        time.sleep(2)
        a = pid in active_ids()
        alive_count += 1 if a else 0
        print(f"  t={(i+1)*2}s active={a}", flush=True)
        if not a:
            print(f"  >>> {name}: DIED at ~{(i+1)*2}s", flush=True)
            break
    else:
        print(f"  >>> {name}: STABLE ({seconds}s)", flush=True)
    B.stop_browser(pid)
    B.stop_all_browsers()
    time.sleep(3)


def create_minimal(email):
    """A bare profile: no fingerprint_config, no tabs, no UA override."""
    body = {
        "name": email,
        "group_id": OFFICIAL_GID,
        "user_proxy_config": {"proxy_soft": "no_proxy"},
    }
    d = B.api_post("/api/v2/browser-profile/create", body)
    data = d.get("data") or {}
    pid = data.get("profile_id") or data.get("id") or ""
    return pid[0] if isinstance(pid, list) else str(pid)


def main():
    existing = sys.argv[1] if len(sys.argv) > 1 else "k1guxndl"

    # Test A: an existing profile (should behave like a manual open)
    probe("EXISTING", existing)

    # Test B: profile created by our real create_profile (fingerprint + UA + tab)
    pidB = B.create_profile("probe_full@gmail.com", OFFICIAL_GID, "pw", "1234 5678")
    print("\ncreated FULL profile", pidB, flush=True)
    time.sleep(2)
    probe("FULL create_profile()", pidB)
    B.delete_profile(pidB)

    # Test C: a minimal profile (no fingerprint_config, no tabs)
    pidC = create_minimal("probe_minimal@gmail.com")
    print("\ncreated MINIMAL profile", pidC, flush=True)
    time.sleep(2)
    probe("MINIMAL", pidC, seconds=16)
    B.delete_profile(pidC)


if __name__ == "__main__":
    main()
