#!/usr/bin/env python3
"""Auto-create DiddySMS Snap signups into a Warming AdsPower group.

One creation VPS → one warming group:

    VPS 1  →  SnappyOfficial - Warming SMS 1
    VPS 2  →  SnappyOfficial - Warming SMS 2
    VPS 3  →  SnappyOfficial - Warming SMS 3
    VPS 4  →  SnappyOfficial - Warming SMS 4

Later you can regroup two Warming SMS groups onto one warming VPS for Stage 1.
Creation stays 1:1 so balancing is automatic.

Resume: progress is the AdsPower group count. Restart / crash / reboot is fine —
re-run the same --vps and it continues until --target good profiles exist.

After each batch, local AdsPower cache is cleared for this VPS's prefixes
(history / images / extension / storage). Cookies are kept so Snap stays logged in.

Requires:
  - AdsPower open with Local API on :50325
  - SnappyMake dashboard running (`run.bat`) on :8787

Examples:

    python scripts/sms_create_lane.py --vps 1
    python scripts/sms_create_lane.py --vps 3 --target 300 --batch 5

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
from app.db import delete_profile_cache, init_db
from app.services import load_runtime_settings
from app.services.gmail_login import group_id_by_name

DEFAULT_GROUPS = {
    1: "SnappyOfficial - Warming SMS 1",
    2: "SnappyOfficial - Warming SMS 2",
    3: "SnappyOfficial - Warming SMS 3",
    4: "SnappyOfficial - Warming SMS 4",
    5: "SnappyOfficial - Warming SMS 5",
}

# 1 creation VPS → 1 warming group (same number). Lane 5 = Mac / scratch.
VPS_TO_GROUP = {
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
}

LOG_DIR = ROOT / "data" / "logs"


class TeeLog:
    """Print + append to a per-VPS log file. Optional rich 2-column live UI."""

    def __init__(self, path: Path, *, ui: bool = False, title: str = "SMS create") -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ui = ui
        self.title = title
        self.meta: dict[str, str] = {
            "lane": "—",
            "mode": "—",
            "group": "—",
            "good": "—",
            "target": "—",
            "batch": "—",
            "run": "—",
            "last": "starting…",
        }
        self.lines: list[str] = []
        self._live = None
        self._Plain = None
        self._Panel = None
        self._Layout = None
        self._Table = None
        self._Text = None
        if ui:
            try:
                from rich.live import Live
                from rich.layout import Layout
                from rich.panel import Panel
                from rich.table import Table
                from rich.text import Text
                from rich.console import Group

                self._Live = Live
                self._Layout = Layout
                self._Panel = Panel
                self._Table = Table
                self._Text = Text
                self._Group = Group
            except ImportError:
                self.ui = False

    def set_meta(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            self.meta[key] = str(value)
        self._refresh()

    def start(self) -> None:
        if not self.ui:
            return
        self._live = self._Live(
            self._render(),
            refresh_per_second=4,
            screen=True,
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def _render(self):
        assert self._Table and self._Panel and self._Layout and self._Text and self._Group
        status = self._Table(show_header=False, box=None, padding=(0, 1))
        status.add_column("k", style="bold cyan", width=10)
        status.add_column("v", style="white")
        for key in ("lane", "mode", "group", "good", "target", "batch", "run", "last"):
            status.add_row(key, self.meta.get(key, "—"))
        left = self._Panel(
            status,
            title="[bold]STATUS[/]",
            border_style="cyan",
            padding=(1, 1),
        )
        tail = self.lines[-28:] or ["(waiting for logs…)"]
        log_text = self._Text("\n".join(tail))
        right = self._Panel(
            log_text,
            title=f"[bold]LOG[/]  {self.path.name}",
            border_style="green",
            padding=(1, 1),
        )
        layout = self._Layout()
        layout.split_row(
            self._Layout(left, name="status", ratio=1, minimum_size=28),
            self._Layout(right, name="logs", ratio=2),
        )
        return self._Group(
            self._Text(f"  {self.title}", style="bold magenta"),
            layout,
        )

    def _refresh(self) -> None:
        if self._live is not None:
            try:
                self._live.update(self._render())
            except Exception:
                pass

    def __call__(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        self.lines.append(line)
        if len(self.lines) > 400:
            self.lines = self.lines[-300:]
        # Parse progress into status column
        if "good=" in msg and "/" in msg:
            try:
                chunk = msg.split("good=", 1)[1]
                pair = chunk.split("·", 1)[0].strip()
                cur, tgt = pair.split("/", 1)
                self.meta["good"] = cur.strip()
                self.meta["target"] = tgt.strip()
            except Exception:
                pass
        if msg.startswith("run "):
            self.meta["run"] = msg[4:].strip()
        if "start batch" in msg:
            self.meta["last"] = msg
        elif msg.startswith("batch "):
            self.meta["last"] = msg
        elif "target reached" in msg or "Finished" in msg or "Created " in msg:
            self.meta["last"] = msg[:80]
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass
        if self._live is not None:
            self._refresh()
        else:
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    return int(raw)


# Bypass Windows/system HTTP_PROXY for localhost dashboard calls.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(base: str, path: str, timeout: float = 20) -> dict:
    with _OPENER.open(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(base: str, path: str, body: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _OPENER.open(req, timeout=timeout) as r:
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


def wipe_warming_group(group_id: str, log: TeeLog) -> int:
    """Delete every profile in this warming group (fresh run)."""
    client = make_client()
    deleted = 0
    try:
        profiles = list(client.list_profiles(group_id=group_id) or [])
        ids = [
            str(p.get("profile_id") or p.get("user_id") or "").strip()
            for p in profiles
        ]
        ids = [pid for pid in ids if pid]
        log(f"fresh wipe · {len(ids)} profile(s) in group {group_id}")
        if not ids:
            return 0
        open_ids = set()
        try:
            open_ids = set(client.local_active() or [])
        except Exception:
            pass
        for pid in ids:
            if pid in open_ids:
                try:
                    client.stop_browser(pid)
                    time.sleep(0.4)
                except Exception as exc:
                    log(f"  close before wipe warn {pid}: {exc}")
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            try:
                client.delete_profiles(chunk)
                deleted += len(chunk)
                log(f"  deleted {deleted}/{len(ids)}")
            except AdsPowerError as exc:
                log(f"  delete chunk fail: {exc}")
                raise
        try:
            delete_profile_cache(ids)
        except Exception as exc:
            log(f"  local cache wipe warn: {exc}")
        # Confirm empty
        left = len(client.list_profiles(group_id=group_id) or [])
        log(f"fresh wipe done · remaining in group={left}")
        return deleted
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
        choices=sorted(VPS_TO_GROUP),
        default=_env_int("SNAPPY_VPS", 0) or None,
        help="Creation VPS / lane number 1–5 (or set SNAPPY_VPS). 5 = Mac scratch.",
    )
    p.add_argument(
        "--target",
        type=int,
        default=_env_int("SNAPPY_TARGET", 300),
        help="Stop when this many good profiles are in the warming group (default 300)",
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
        "--fresh",
        action="store_true",
        help="Delete all profiles in this warming group, then create from 0",
    )
    p.add_argument(
        "--ui",
        action="store_true",
        help="Live 2-column STATUS | LOG grid (rich)",
    )
    p.add_argument(
        "--no-proxy",
        action="store_true",
        help="Skip Netlox inject — create/signup on direct IP (test)",
    )
    p.add_argument(
        "--isp",
        action="store_true",
        help="Use Manage Proxy ISP pool (no Netlox). Success remark tags Proxy: ISP.",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single batch then exit (smoke test)",
    )
    p.add_argument(
        "--fp-preset",
        default=(os.environ.get("SNAPPY_FP_PRESET") or "working").strip().lower(),
        choices=["working", "windows", "strict", "w", "a", "s"],
        help="Fingerprint preset: working (default) | windows | strict",
    )
    p.add_argument(
        "--chrome-kernel",
        default=(os.environ.get("SNAPPY_CHROME_KERNEL") or "152").strip().lower(),
        help="SunBrowser Chrome major: 152 (default) | 153 | latest",
    )
    p.add_argument(
        "--otp-provider",
        default=(os.environ.get("SNAPPY_OTP_PROVIDER") or "imap").strip().lower(),
        choices=["imap", "anymessage", "diddysms", "i", "am", "a", "d", "ds"],
        help="OTP source: imap (Gmail pool) | anymessage | diddysms",
    )
    args = p.parse_args()
    if not args.vps:
        p.error("Pass --vps 1|2|3|4|5 or set SNAPPY_VPS")
    if args.isp and args.no_proxy:
        p.error("Use either --isp or --no-proxy, not both")
    args.batch = max(1, min(50, int(args.batch)))
    args.target = max(1, int(args.target))
    # Normalize short aliases from run.bat
    fp_map = {"w": "working", "a": "windows", "s": "strict"}
    args.fp_preset = fp_map.get(args.fp_preset, args.fp_preset)
    kern = str(args.chrome_kernel or "152").strip().lower()
    if kern in {"l", "latest"}:
        args.chrome_kernel = "latest"
    elif kern.isdigit():
        args.chrome_kernel = kern
    else:
        # allow 152.0.0.0 style
        major = kern.split(".", 1)[0]
        args.chrome_kernel = major if major.isdigit() else "152"
    otp_map = {
        "i": "imap",
        "imap": "imap",
        "am": "anymessage",
        "a": "anymessage",
        "anymessage": "anymessage",
        "d": "diddysms",
        "ds": "diddysms",
        "diddysms": "diddysms",
    }
    args.otp_provider = otp_map.get(str(args.otp_provider).strip().lower(), "imap")
    return args


def warming_group_name(vps: int, override: str = "") -> str:
    if override.strip():
        return override.strip()
    group_n = VPS_TO_GROUP[vps]
    env_key = f"SNAPPY_WARMING_GROUP_{group_n}"
    return (os.environ.get(env_key) or "").strip() or DEFAULT_GROUPS[group_n]


def main() -> int:
    args = parse_args()
    vps = int(args.vps)
    group_n = VPS_TO_GROUP[vps]
    group_name = warming_group_name(vps, args.group_name)
    prefix = f"V{vps}-SMS"
    log = TeeLog(
        LOG_DIR / f"sms_create_vps{vps}.log",
        ui=bool(args.ui),
        title=f"SMS create · VPS {vps} · Warming SMS {group_n}",
    )
    log.set_meta(
        lane=str(vps),
        mode="FRESH" if args.fresh else "CONTINUE",
        group=group_name,
        target=str(args.target),
        batch=str(args.batch),
        fp=str(args.fp_preset),
        chrome=str(args.chrome_kernel),
        otp=str(args.otp_provider),
        good="—",
        run="—",
        last="booting",
    )
    log.start()
    try:
        return _run_lane(args, vps, group_n, group_name, prefix, log)
    finally:
        log.stop()


def _run_lane(args, vps: int, group_n: int, group_name: str, prefix: str, log: TeeLog) -> int:
    log(f"=== sms-create-lane start VPS={vps} → group {group_n} ===")
    log(f"group   {group_name}")
    log(f"prefix  {prefix}")
    log(f"mode    {'FRESH (wipe group first)' if args.fresh else 'CONTINUE (resume count)'}")
    log(f"target  {args.target} good in group")
    log(f"batch   {args.batch}")
    log(f"api     {args.api}")
    log(f"log     {log.path}")
    log(f"cache   {'off' if args.no_cache_clear else 'clear after batch (keep cookies)'}")
    if args.isp:
        proxy_label = "ISP pool (Manage Proxy · no Netlox)"
    elif args.no_proxy:
        proxy_label = "OFF (no Netlox)"
    else:
        proxy_label = "Netlox inject"
    payload_inject = False if (args.isp or args.no_proxy) else True
    log(f"proxy   {proxy_label}")
    log(f"inject  inject_netlox={payload_inject}")
    log(f"fp      {args.fp_preset}")
    log(f"chrome  {args.chrome_kernel}")
    log(f"otp     {args.otp_provider}")
    log(f"ui      {'grid' if args.ui and log.ui else 'plain'}")

    init_db()
    if not wait_for_api(args.api, log, forever=True):
        return 1

    close_open_browsers(log)
    gid = ensure_warming_group(group_name)
    log(f"group_id {gid}")

    if args.fresh:
        try:
            wiped = wipe_warming_group(gid, log)
            log(f"fresh · wiped {wiped} profile(s) · count resets to 0")
        except Exception as exc:
            log(f"fresh wipe FAILED: {exc}")
            return 1

    n0 = count_good(gid)
    log.set_meta(good=str(n0))
    log(f"{'fresh' if args.fresh else 'resume'} count good={n0}/{args.target}")
    if n0 >= args.target:
        log(f"target already reached ({n0}>={args.target}). nothing to do.")
        return 0

    payload = {
        "name_prefix": prefix,
        "action": "snapchat_signup",
        "close_after": True,
        "proxy_mode": "pool" if args.isp else "none",
        "inject_netlox": payload_inject,
        "fingerprint_mode": "random",
        "fp_preset": args.fp_preset,
        "chrome_kernel": args.chrome_kernel,
        "otp_provider": args.otp_provider,
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
            log.set_meta(good=str(n))
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
