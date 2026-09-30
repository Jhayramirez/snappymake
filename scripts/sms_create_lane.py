#!/usr/bin/env python3
"""Auto-create DiddySMS Snap signups into a Warming AdsPower group.

Lane map (4 creation VPS → 2 warming groups):

    VPS 1 + VPS 2  →  SnappyOfficial - Warming SMS 1
    VPS 3 + VPS 4  →  SnappyOfficial - Warming SMS 2

Resume: progress is the AdsPower group count. Restart / crash / reboot is fine —
re-run the same --vps and it continues until --target good profiles exist.

After each batch, local AdsPower cache is cleared for this VPS's prefixes
(history / images / extension / storage). Cookies are kept so Snap stays logged in.

Requires:
  - AdsPower open with Local API on :50325
  - SnappyMake dashboard running (`run.bat`) on :8787

Examples:

    python scripts/sms_create_lane.py --vps 1
    python scripts/sms_create_lane.py --vps 3 --target 600 --batch 5

Logs: data/logs/sms_create_vps{N}.log
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ads import AdsPowerError
from app.ads.client import AdsPowerClient
from app.services import load_runtime_settings
from app.services.gmail_login import group_id_by_name

DEFAULT_GROUPS = {
    1: "SnappyOfficial - Warming SMS 1",
    2: "SnappyOfficial - Warming SMS 2",
}

VPS_TO_LANE = {
    1: 1,
    2: 1,
    3: 2,
    4: 2,
}

LOG_DIR = ROOT / "data" / "logs"


class TeeLog:
    """Print + append to a rotating-ish per-VPS log file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    return int(raw)


def get(base: str, path: str, timeout: float = 20) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(base: str, path: str, body: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def make_client() -> AdsPowerClient:
    conf = load_runtime_settings()
    return AdsPowerClient(conf["api_base"], conf.get("api_key") or "", min_interval=1.05)


def ensure_warming_group(name: str) -> str:
    client = make_client()
    try:
        gid = group_id_by_name(client, name)
        if gid:
            return gid
        data = client.create_group(name, remark="SnappyOfficial Warming SMS")
        gid = str(data.get("group_id") or "") or group_id_by_name(client, name)
        if not gid:
            raise SystemExit(f"Could not create AdsPower group: {name}")
        return gid
    finally:
        client.close()


def count_good(group_id: str) -> int:
    client = make_client()
    try:
        n = 0
        for p in client.list_profiles(group_id=group_id) or []:
            remark = str(p.get("remark") or "")
            if "DiddySMS:" in remark or "snapchat.com/web/" in remark.lower():
                n += 1
        return n
    finally:
        client.close()


def close_open_browsers(log: TeeLog) -> None:
    client = make_client()
    try:
        active = list(client.local_active() or [])
        for item in active:
            pid = item if isinstance(item, str) else str(
                (item or {}).get("user_id") or (item or {}).get("profile_id") or ""
            )
            if not pid:
                continue
            try:
                client.stop_browser(pid)
                log(f"closed leftover browser {pid}")
            except Exception as exc:
                log(f"close leftover warn {pid}: {exc}")
    except Exception as exc:
        log(f"active list warn: {exc}")
    finally:
        client.close()


def clear_lane_cache(group_id: str, prefix: str, log: TeeLog) -> int:
    """Clear disk-heavy local cache for this VPS's profiles. Keep cookies."""
    client = make_client()
    cleared = 0
    try:
        open_ids = set(client.local_active() or [])
        needle = f"{prefix}-"
        ids: list[str] = []
        for p in client.list_profiles(group_id=group_id) or []:
            name = str(p.get("name") or "")
            pid = str(p.get("profile_id") or p.get("user_id") or "")
            if not pid or not name.startswith(needle):
                continue
            if pid in open_ids:
                try:
                    client.stop_browser(pid)
                    time.sleep(0.8)
                except Exception:
                    continue
            ids.append(pid)
        # AdsPower accepts batches; keep chunks small.
        for i in range(0, len(ids), 20):
            chunk = ids[i : i + 20]
            try:
                client.delete_profile_cache(chunk)
                cleared += len(chunk)
            except AdsPowerError as exc:
                log(f"cache clear warn ({len(chunk)}): {exc}")
            except Exception as exc:
                log(f"cache clear err: {exc}")
    finally:
        client.close()
    return cleared


def wait_for_api(base: str, log: TeeLog, *, forever: bool = True) -> bool:
    """Block until dashboard answers. Survives VPS reboot / run.bat restart."""
    attempt = 0
    while True:
        attempt += 1
        try:
            get(base, "/api/runs/current", timeout=8)
            if attempt > 1:
                log(f"dashboard back online at {base}")
            return True
        except Exception as exc:
            if attempt == 1 or attempt % 10 == 0:
                log(f"waiting for dashboard {base} ({exc})")
            if not forever and attempt >= 30:
                return False
            time.sleep(5)


def wait_idle(base: str, log: TeeLog) -> dict:
    while True:
        try:
            d = get(base, "/api/runs/current")
        except Exception as exc:
            log(f"wait_idle lost dashboard: {exc}")
            wait_for_api(base, log)
            continue
        if d.get("status") not in {"running", "queued", "cancelling"}:
            return d
        time.sleep(3)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMS create lane for a creation VPS")
    p.add_argument(
        "--vps",
        type=int,
        choices=sorted(VPS_TO_LANE),
        default=_env_int("SNAPPY_VPS", 0) or None,
        help="Creation VPS number 1–4 (or set SNAPPY_VPS)",
    )
    p.add_argument(
        "--target",
        type=int,
        default=_env_int("SNAPPY_TARGET", 600),
        help="Stop when this many good profiles are in the warming group (default 600)",
    )
    p.add_argument(
        "--batch",
        type=int,
        default=_env_int("SNAPPY_BATCH", 5),
        help="Profiles per Start call (default 5, max 50)",
    )
    p.add_argument(
        "--api",
        default=(os.environ.get("SNAPPY_API") or "http://127.0.0.1:8787").rstrip("/"),
        help="SnappyMake dashboard API base",
    )
    p.add_argument(
        "--group-name",
        default="",
        help="Override AdsPower warming group name for this VPS",
    )
    p.add_argument(
        "--no-cache-clear",
        action="store_true",
        help="Skip AdsPower local cache clear after each batch",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single batch then exit (smoke test)",
    )
    args = p.parse_args()
    if not args.vps:
        p.error("Pass --vps 1|2|3|4 or set SNAPPY_VPS")
    args.batch = max(1, min(50, int(args.batch)))
    args.target = max(1, int(args.target))
    return args


def warming_group_name(vps: int, override: str = "") -> str:
    if override.strip():
        return override.strip()
    lane = VPS_TO_LANE[vps]
    env_key = f"SNAPPY_WARMING_GROUP_{lane}"
    return (os.environ.get(env_key) or "").strip() or DEFAULT_GROUPS[lane]


def main() -> int:
    args = parse_args()
    vps = int(args.vps)
    lane = VPS_TO_LANE[vps]
    group_name = warming_group_name(vps, args.group_name)
    prefix = f"V{vps}-SMS"
    log = TeeLog(LOG_DIR / f"sms_create_vps{vps}.log")

    log(f"=== sms-create-lane start VPS={vps} lane={lane} ===")
    log(f"group   {group_name}")
    log(f"prefix  {prefix}")
    log(f"target  {args.target} good in group (resume = recount AdsPower)")
    log(f"batch   {args.batch}")
    log(f"api     {args.api}")
    log(f"log     {log.path}")
    log(f"cache   {'off' if args.no_cache_clear else 'clear after batch (keep cookies)'}")

    if not wait_for_api(args.api, log, forever=True):
        return 1

    close_open_browsers(log)
    gid = ensure_warming_group(group_name)
    log(f"group_id {gid}")

    n0 = count_good(gid)
    log(f"resume count good={n0}/{args.target}")
    if n0 >= args.target:
        log(f"target already reached ({n0}>={args.target}). nothing to do.")
        return 0

    payload = {
        "name_prefix": prefix,
        "action": "snapchat_signup",
        "close_after": True,
        "proxy_mode": "none",
        "fingerprint_mode": "random",
        "auto_username": True,
        "auto_password": True,
        "bitmoji_gender": "female",
        "os": "win11",
        "group_id": gid,
        "platform": "snapchat.com",
        "count": args.batch,
    }

    while True:
        try:
            wait_idle(args.api, log)
            n = count_good(gid)
            log(f"good={n}/{args.target} · next batch {args.batch}")
            if n >= args.target:
                log(f"target reached ({n}>={args.target}). stop.")
                return 0
            left = args.target - n
            body = dict(payload)
            body["count"] = min(args.batch, left)
            log(f"start batch count={body['count']}")
            started = post(args.api, "/api/runs", body)
        except urllib.error.HTTPError as exc:
            err = exc.read().decode()[:400]
            log(f"POST fail {exc.code} {err}")
            time.sleep(20)
            continue
        except Exception as exc:
            log(f"loop err {exc}")
            wait_for_api(args.api, log)
            time.sleep(8)
            continue

        log(f"run {started.get('id')} {started.get('status')}")
        last_len = 0
        while True:
            time.sleep(6)
            try:
                d = get(args.api, "/api/runs/current")
            except Exception as exc:
                log(f"poll lost dashboard: {exc}")
                wait_for_api(args.api, log)
                continue
            logs = d.get("logs") or []
            for lg in logs[last_len:]:
                msg = lg.get("message") or ""
                step = lg.get("step") or ""
                if msg.startswith("DiddySMS poll"):
                    continue
                interesting = step in {
                    "create",
                    "proxy",
                    "otp",
                    "delete",
                    "done",
                    "error",
                } or msg.startswith(
                    (
                        "Netlox",
                        "Created ",
                        "DiddySMS +",
                        "DiddySMS ·",
                        "Finished",
                        "phone_rejected",
                        "process_error_stuck",
                    )
                )
                if interesting:
                    log(f"  {msg[:220]}")
            last_len = len(logs)
            if d.get("status") not in {"running", "queued", "cancelling"}:
                log(
                    f"batch {d.get('status')} ok={d.get('ok')}/{d.get('requested')} "
                    f"err={d.get('error')}"
                )
                break

        if not args.no_cache_clear:
            try:
                close_open_browsers(log)
                n_clear = clear_lane_cache(gid, prefix, log)
                log(f"cache cleared for {n_clear} {prefix}-* profiles")
            except Exception as exc:
                log(f"cache clear skip: {exc}")

        if args.once:
            return 0
        time.sleep(8)


if __name__ == "__main__":
    raise SystemExit(main())
