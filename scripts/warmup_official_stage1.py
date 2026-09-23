#!/usr/bin/env python3
"""SnappyMake Official (Gmail) Stage 1: open each live profile, land on Snap web.

Skip Life=dead. One AdsPower at a time.
Welcome → Chat → Snapchat for Web. If still inside, add 2–4 USA names.
If login/signup kick, mark dead and the Official Profiles sheet goes light red.

Keeps the existing AdsPower remark and appends:
    Warm Up Stage : Stage 1 Done
"""
from __future__ import annotations

import importlib.util
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
from app.services import gmail_login as G
from app.services import make_client
from app.services.profiles import (
    close_browser,
    extract_snap_web_url,
    open_browser,
    update_credentials,
    update_profile_life,
)

STAGE1 = "Warm Up Stage : Stage 1 Done"
STAGE_DONE = "Warm Up Stage : Done"
STOP_FILE = ROOT / "data" / "warmup_official_stop"
LOG_FILE = ROOT / "data" / "warmup_official_stage1.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _already_warmed(remark: str) -> bool:
    text = remark or ""
    return STAGE1 in text or STAGE_DONE in text or "Stage 1 Done" in text


def sync_official_sheet() -> None:
    path = ROOT / "scripts" / "sync_official_sheet.py"
    spec = importlib.util.spec_from_file_location("sync_official_sheet", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sync_official_sheet.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main()
    log(f"  sheet sync rc={rc}")


def close_all(client) -> None:
    try:
        active = list(client.local_active() or [])
    except Exception as exc:
        log(f"  active list failed {exc}")
        return
    ids: list[str] = []
    for item in active:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            ids.append(str(item.get("user_id") or item.get("profile_id") or ""))
    for pid in [x for x in ids if x]:
        try:
            close_browser(client, pid)
            log(f"  closed {pid}")
        except Exception as exc:
            log(f"  close {pid} {exc}")


def qualifying(client) -> list[dict]:
    info = G.ensure_official_group(client)
    gid = str(info.get("group_id") or "")
    lives = get_all_profile_life()
    rows: list[dict] = []
    for p in client.list_profiles(group_id=gid) or []:
        name = str(p.get("name") or "")
        if "@" not in name:
            continue
        pid = str(p.get("profile_id") or p.get("user_id") or "")
        if lives.get(pid) == "dead":
            continue
        remark = str(p.get("remark") or "")
        if _already_warmed(remark):
            continue
        rows.append(
            {
                "profile_id": pid,
                "name": name,
                "profile_no": str(p.get("profile_no") or ""),
                "remark": remark,
                "web_url": extract_snap_web_url(remark) or "",
            }
        )
    rows.sort(key=lambda r: int(r["profile_no"]) if r["profile_no"].isdigit() else 0)
    return rows


def append_stage1(client, pid: str, remark: str) -> str:
    text = (remark or "").strip()
    if _already_warmed(text):
        return text
    new = f"{text} · {STAGE1}" if text else STAGE1
    update_credentials(client, pid, {"remark": new})
    return new


def _inside(notes: list[str]) -> bool:
    good = {
        "qa_ready_to_add",
        "qa_already_on_web",
        "qa_web_session_ok",
        "web_onboard_chat",
        "qa_reuse_snapchat_web_tab",
    }
    for n in notes:
        s = str(n)
        if s in good or s.startswith("web_add_friends_added"):
            return True
    return False


def warmup_one(client, row: dict) -> str:
    pid = row["profile_id"]
    web_url = row["web_url"]
    notes: list[str] = []

    def on_step(msg: str) -> None:
        notes.append(msg)
        log(f"  {row['profile_no'] or row['name']} · {msg}")

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
                sync_official_sheet()
            except Exception as exc:
                log(f"  sheet sync failed {exc}")
            return "LOGGED_OUT"
        added = [n for n in all_notes if str(n).startswith("web_add_friends_added")]
        if added:
            append_stage1(client, pid, row["remark"])
            try:
                sync_official_sheet()
            except Exception as exc:
                log(f"  sheet sync failed {exc}")
            return "OK:" + ",".join(str(n) for n in added)
        if _inside(all_notes):
            text = (row["remark"] or "").strip()
            if "Still inside" not in text and not _already_warmed(text):
                update_credentials(
                    client, pid, {"remark": f"{text} · Still inside" if text else "Still inside"}
                )
            try:
                sync_official_sheet()
            except Exception as exc:
                log(f"  sheet sync failed {exc}")
            return "INSIDE_NO_ADD"
        return "FAIL:not_inside"
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
        close_all(client)
        rows = qualifying(client)
        log(f"Official Gmail warmup Stage 1 · {len(rows)} leftover(s) · skip dead + Stage 1 done")
        ok = inside = dead = fail = 0
        for i, row in enumerate(rows, 1):
            if STOP_FILE.exists():
                log("stop file present — exiting")
                break
            log(
                f"[{i}/{len(rows)}] #{row['profile_no']} {row['name']} "
                f"{row['profile_id']} web={bool(row['web_url'])}"
            )
            try:
                status = warmup_one(client, row)
            except Exception as exc:
                status = f"ERROR:{exc}"
            if status.startswith("OK"):
                ok += 1
            elif status == "LOGGED_OUT":
                dead += 1
            elif status == "INSIDE_NO_ADD":
                inside += 1
            else:
                fail += 1
            log(f"  → {status} · ok={ok} inside={inside} dead={dead} fail={fail}")
            time.sleep(2)
        try:
            sync_official_sheet()
        except Exception as exc:
            log(f"  final sheet sync failed {exc}")
        log(f"done ok={ok} inside_no_add={inside} dead={dead} fail={fail} of {len(rows)}")
        return 0 if fail == 0 else 1
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
