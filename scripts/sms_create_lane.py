#!/usr/bin/env python3
"""Auto-create DiddySMS Snap signups into a Warming AdsPower group.

Lane map (4 creation VPS → 2 warming groups):

    VPS 1 + VPS 2  →  SnappyOfficial - Warming SMS 1
    VPS 3 + VPS 4  →  SnappyOfficial - Warming SMS 2

Each VPS keeps a unique name prefix (V1-SMS, V2-SMS, …) so two creators
writing into the same group do not race on serial numbers.

Requires:
  - AdsPower open with Local API on :50325
  - SnappyMake dashboard running (`run.bat` / `python -m app`) on :8787

Examples:

    python scripts/sms_create_lane.py --vps 1
    python scripts/sms_create_lane.py --vps 3 --target 600 --batch 5

Env overrides:
    SNAPPY_VPS=1
    SNAPPY_API=http://127.0.0.1:8787
    SNAPPY_TARGET=600
    SNAPPY_BATCH=5
    SNAPPY_WARMING_GROUP_1=SnappyOfficial - Warming SMS 1
    SNAPPY_WARMING_GROUP_2=SnappyOfficial - Warming SMS 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ads.client import AdsPowerClient
from app.services import load_runtime_settings
from app.services.gmail_login import group_id_by_name

DEFAULT_GROUPS = {
    1: "SnappyOfficial - Warming SMS 1",
    2: "SnappyOfficial - Warming SMS 2",
}

# VPS number → warming lane (1 or 2)
VPS_TO_LANE = {
    1: 1,
    2: 1,
    3: 2,
    4: 2,
}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    return int(raw)


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read())


def post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def ensure_warming_group(name: str) -> str:
    conf = load_runtime_settings()
    client = AdsPowerClient(conf["api_base"], conf.get("api_key") or "", min_interval=1.05)
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
    conf = load_runtime_settings()
    client = AdsPowerClient(conf["api_base"], conf.get("api_key") or "", min_interval=1.05)
    try:
        n = 0
        for p in client.list_profiles(group_id=group_id) or []:
            remark = str(p.get("remark") or "")
            if "DiddySMS:" in remark or "snapchat.com/web/" in remark.lower():
                n += 1
        return n
    finally:
        client.close()


def wait_idle(base: str) -> dict:
    while True:
        try:
            d = get(base, "/api/runs/current")
        except Exception as exc:
            print(f"wait_idle {exc}", flush=True)
            time.sleep(4)
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

    print(f"sms-create-lane VPS={vps} lane={lane}", flush=True)
    print(f"  group  {group_name}", flush=True)
    print(f"  prefix {prefix}", flush=True)
    print(f"  target {args.target} good in group", flush=True)
    print(f"  batch  {args.batch}", flush=True)
    print(f"  api    {args.api}", flush=True)

    try:
        get(args.api, "/api/runs/current")
    except Exception as exc:
        print(
            f"Cannot reach SnappyMake at {args.api}: {exc}\n"
            "Start the dashboard first (run.bat / python -m app).",
            flush=True,
        )
        return 1

    gid = ensure_warming_group(group_name)
    print(f"  group_id {gid}", flush=True)

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
            wait_idle(args.api)
            n = count_good(gid)
            print(f"good={n}/{args.target} · next batch {args.batch}", flush=True)
            if n >= args.target:
                print(f"target reached ({n}>={args.target}). stop.", flush=True)
                return 0
            left = args.target - n
            body = dict(payload)
            body["count"] = min(args.batch, left)
            print(f"start batch count={body['count']}", flush=True)
            started = post(args.api, "/api/runs", body)
        except urllib.error.HTTPError as exc:
            err = exc.read().decode()[:400]
            print(f"POST fail {exc.code} {err}", flush=True)
            time.sleep(20)
            continue
        except Exception as exc:
            print(f"loop err {exc}", flush=True)
            time.sleep(15)
            continue

        print(f"run {started.get('id')} {started.get('status')}", flush=True)
        last_len = 0
        while True:
            time.sleep(6)
            try:
                d = get(args.api, "/api/runs/current")
            except Exception as exc:
                print(f"poll err {exc}", flush=True)
                time.sleep(5)
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
                    print(f"  {msg[:220]}", flush=True)
            last_len = len(logs)
            if d.get("status") not in {"running", "queued", "cancelling"}:
                print(
                    f"batch {d.get('status')} ok={d.get('ok')}/{d.get('requested')} "
                    f"err={d.get('error')}",
                    flush=True,
                )
                break

        if args.once:
            return 0
        time.sleep(8)


if __name__ == "__main__":
    raise SystemExit(main())
