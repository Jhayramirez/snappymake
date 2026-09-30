#!/usr/bin/env python3
"""Push Warming SMS 1–4 AdsPower groups to separate tabs on the Official sheet.

Creates the tabs if missing. Same columns as Official Profiles SMS Method
(Created, Age, Warm up, Life, Snapchat status, …).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db
from app.services.sheets import sync_warming_sms_now


def main() -> int:
    init_db()
    result = sync_warming_sms_now(force=True)
    if not result.get("ok"):
        print("fail", result.get("detail") or result, flush=True)
        return 1
    print("ok", result.get("title"), flush=True)
    tabs = result.get("warming_tabs") or {}
    for name, meta in tabs.items():
        print(
            f"  {name}: rows={meta.get('rows')} group_total={meta.get('group_total')} "
            f"gid={meta.get('group_id') or 'missing'}",
            flush=True,
        )
    print("warming_profiles", result.get("warming_profiles"), flush=True)
    print(result.get("url") or "", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
