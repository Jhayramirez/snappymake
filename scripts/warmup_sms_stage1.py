#!/usr/bin/env python3
"""SMS Official warmup Stage 1: open 1-day+ Snaps one by one, add 2–4 USA names.

Keeps the existing AdsPower remark and appends:
    Warm Up Stage : Stage 1 Done
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SNAPPY_NO_FOCUS", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ads import AdsPowerError
from app.db import get_all_profile_life, init_db
from app.inject.snapchat import SNAPCHAT_WELCOME_URL, run_page_action
from app.services import make_client
from app.services.profiles import (
    close_browser,
    extract_snap_web_url,
    open_browser,
    update_credentials,
    update_profile_life,
)
from app.services.sheets import sync_sms_official_now

SMS_GROUP_ID = "10749351"
STAGE1 = "Warm Up Stage : Stage 1 Done"
STAGE_DONE = "Warm Up Stage : Done"
MIN_AGE_HOURS = 24
STOP_FILE = ROOT / "data" / "warmup_sms_stop"
LOG_FILE = ROOT / "data" / "warmup_sms_stage1.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _created_ts(profile: dict) -> int:
    try:
        ts = int(float(profile.get("created_time") or 0))
    except (TypeError, ValueError):
        return 0
    if ts >= 100_000_000_000:
        ts //= 1000
    return ts


def _is_good(remark: str) -> bool:
    text = remark or ""
    return "DiddySMS:" in text or "snapchat.com/web/" in text.lower()


def _already_warmed(remark: str) -> bool:
    text = remark or ""
    return STAGE1 in text or STAGE_DONE in text or "Stage 1 Done" in text


def failed_ids_from_log() -> list[str]:
    if not LOG_FILE.is_file():
        return []
    last = ""
    ids: list[str] = []
    for line in LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "] SMS-" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part.startswith("SMS-") and i + 1 < len(parts) and parts[i + 1].startswith("k"):
                    last = parts[i + 1]
                    break
        if "→ FAIL" in line or "→ LOGGED" in line:
            if last and last not in ids:
                ids.append(last)
    return ids


def qualifying(client, *, only_ids: set[str] | None = None) -> list[dict]:
    now = time.time()
    lives = get_all_profile_life()
    rows: list[dict] = []
    for p in client.list_profiles(group_id=SMS_GROUP_ID):
        remark = str(p.get("remark") or "")
        if not _is_good(remark) or _already_warmed(remark):
            continue
        pid = str(p.get("profile_id") or p.get("user_id") or "")
        if lives.get(pid) == "dead":
            continue
        ts = _created_ts(p)
        age_h = (now - ts) / 3600 if ts else -1
        if age_h < MIN_AGE_HOURS:
            continue
        if only_ids is not None and pid not in only_ids:
            continue
        rows.append(
            {
                "profile_id": pid,
                "name": p.get("name") or pid,
                "remark": remark,
                "web_url": extract_snap_web_url(remark) or "",
                "age_h": age_h,
            }
        )
    rows.sort(key=lambda r: r["age_h"], reverse=True)
    return rows


def append_stage1(client, pid: str, remark: str) -> str:
    text = (remark or "").strip()
    if _already_warmed(text):
        return text
    new = f"{text} · {STAGE1}" if text else STAGE1
    update_credentials(client, pid, {"remark": new})
    return new


def warmup_one(client, row: dict) -> str:
    pid = row["profile_id"]
    web_url = row["web_url"]
    notes: list[str] = []

    def on_step(msg: str) -> None:
        notes.append(msg)
        log(f"  {row['name']} · {msg}")

    opened_id = ""
    try:
        session = open_browser(
            client,
            pid,
            headless=False,
            tabs=[web_url] if web_url else None,
            on_wait=lambda msg, left: log(f"  wait {msg} · {left}s"),
        )
        opened_id = pid
        ws = session.get("puppeteer") or ""
        if isinstance(ws, dict):
            ws = ws.get("puppeteer") or ws.get("puppeteer_ws") or ""
        if not isinstance(ws, str) or not ws.strip():
            return "FAIL:no_cdp"
        result = run_page_action(
            ws.strip(),
            action="snapchat_warmup",
            start_url=web_url or SNAPCHAT_WELCOME_URL,
            web_session_url=web_url,
            dwell_seconds=2,
            on_step=on_step,
        )
        all_notes = list(result.get("notes") or []) + notes
        if result.get("logged_out") or any(str(n).startswith("account_logged_out") for n in all_notes):
            update_profile_life(pid, "dead")
            try:
                sync_sms_official_now(client, force=True)
            except Exception as exc:
                log(f"  sheet sync failed {exc}")
            return "LOGGED_OUT"
        added = [n for n in all_notes if str(n).startswith("web_add_friends_added")]
        if not added:
            fail = [n for n in all_notes if "add_friends" in str(n) and "fail" in str(n)]
            return f"FAIL_ADD:{fail[0] if fail else 'no_add'}"
        append_stage1(client, pid, row["remark"])
        try:
            sync_sms_official_now(client, force=True)
        except Exception as exc:
            log(f"  sheet sync failed {exc}")
        return "OK:" + ",".join(str(n) for n in added)
    finally:
        if opened_id:
            try:
                close_browser(client, opened_id)
            except AdsPowerError as exc:
                log(f"  close failed {exc}")


def main() -> int:
    init_db()
    STOP_FILE.unlink(missing_ok=True)
    client = make_client()
    try:
        retry = "--retry-failed" in sys.argv
        only_ids = set(failed_ids_from_log()) if retry else None
        rows = qualifying(client, only_ids=only_ids)
        label = "retry failed" if retry else f"≥ {MIN_AGE_HOURS}h"
        log(f"SMS warmup Stage 1 · {len(rows)} account(s) · {label}")
        ok = fail = skip = 0
        for i, row in enumerate(rows, 1):
            if STOP_FILE.exists():
                log("stop file present — exiting")
                break
            log(f"[{i}/{len(rows)}] {row['name']} {row['profile_id']} age={row['age_h']:.1f}h web={bool(row['web_url'])}")
            try:
                status = warmup_one(client, row)
            except Exception as exc:
                status = f"ERROR:{exc}"
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
            log(f"  → {status} · ok={ok} fail={fail}")
            time.sleep(2)
        log(f"done ok={ok} fail={fail} skip={skip} of {len(rows)}")
        return 0 if fail == 0 else 1
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
