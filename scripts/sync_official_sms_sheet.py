#!/usr/bin/env python3
"""Push Official SMS profiles to their own tab on the Official Google Sheet."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db
from app.services.sheets import SMS_OFFICIAL_TAB, sync_sms_official_now


def main() -> int:
    init_db()
    result = sync_sms_official_now(force=True)
    if not result.get("ok"):
        print("fail", result.get("detail") or result, flush=True)
        return 1
    print("ok", result.get("title"), flush=True)
    print("tab", result.get("sms_official_tab") or SMS_OFFICIAL_TAB, flush=True)
    print(
        "rows",
        result.get("sms_profiles"),
        "group",
        result.get("sms_group_total"),
        flush=True,
    )
    print(result.get("url") or "", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
