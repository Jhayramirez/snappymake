#!/usr/bin/env python3
"""Sync the Official Google Sheet whenever gmail_login status changes."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.db import init_db
from app.services import gmail_login as G
import sync_official_sheet as S

RUNNER = int(os.environ.get("SNAPPY_WATCH_PID") or "0")


def fingerprint():
    return tuple(
        (
            r.get("email"),
            r.get("login_status"),
            r.get("snap_status"),
            (r.get("last_error") or "")[:60],
        )
        for r in G.snapshot()["accounts"]
    )


def main() -> int:
    init_db()
    last = None
    print("sheet watcher on", flush=True)
    while True:
        if RUNNER:
            try:
                os.kill(RUNNER, 0)
            except OSError:
                print("runner gone — last sync", flush=True)
                try:
                    S.main()
                except Exception as exc:
                    print("final sync warn", exc, flush=True)
                return 0
        time.sleep(20)
        now = fingerprint()
        if last is not None and now != last:
            print(time.strftime("%H:%M:%S"), "mail status changed — syncing sheet", flush=True)
            try:
                S.main()
            except Exception as exc:
                print("sheet sync warn", exc, flush=True)
        last = now


if __name__ == "__main__":
    raise SystemExit(main())
