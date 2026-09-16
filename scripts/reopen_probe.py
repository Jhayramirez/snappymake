#!/usr/bin/env python3
"""Test: does a new profile survive on the SECOND open after crashing on first?

If yes, the fix is "open, if it dies just re-open the SAME profile" instead of
delete+recreate (which resets the expensive first-run every time).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail_batch_login_v3 as B  # noqa: E402

OFFICIAL_GID = "10722026"


def active_ids():
    d = B.api_get("/api/v1/browser/local-active")
    return {x.get("user_id") for x in (d.get("data") or {}).get("list") or []}


def open_and_watch(pid, label, seconds=18):
    print(f"\n--- {label} ---", flush=True)
    d = B.api_get(
        "/api/v1/browser/start",
        {"user_id": pid, "open_tabs": "1", "ip_tab": "0", "headless": "0"},
        timeout=120,
    )
    print("  start code", d.get("code"), flush=True)
    for i in range(seconds // 2):
        time.sleep(2)
        a = pid in active_ids()
        print(f"  t={(i+1)*2}s active={a}", flush=True)
        if not a:
            print(f"  >>> DIED at ~{(i+1)*2}s", flush=True)
            return False
    print("  >>> STABLE", flush=True)
    return True


def main():
    B.stop_all_browsers()
    pid = B.create_profile("reopen_probe@gmail.com", OFFICIAL_GID, "pw", "1234 5678")
    print("created", pid, flush=True)
    time.sleep(2)

    results = []
    for n in range(1, 5):  # up to 4 opens
        ok = open_and_watch(pid, f"OPEN #{n}")
        results.append(ok)
        B.stop_browser(pid)
        B.stop_all_browsers()
        time.sleep(4)
        if ok:
            break

    print("\nRESULTS:", results, flush=True)
    print("VERDICT:", "REOPEN WORKS" if any(results) else "REOPEN DID NOT HELP", flush=True)
    B.delete_profile(pid)


if __name__ == "__main__":
    main()
