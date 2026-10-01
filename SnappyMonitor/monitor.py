#!/usr/bin/env python3
"""SnappyMonitor — screenshot every open AdsPower browser, batch-POST every N minutes.

Payload per profile (2 fields only):
  { "profile": "#serial · name · remark", "screenshot": "<base64 jpeg>" }

Batch:
  { "ts": "<iso8601>", "items": [ ... ] }

Usage (from repo root, with .venv active):
  python SnappyMonitor/monitor.py           # loop every interval_seconds
  python SnappyMonitor/monitor.py --once    # one cycle then exit
  python SnappyMonitor/monitor.py --dry-run # capture + save local, skip POST
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ads.client import AdsPowerClient, detect_base_url, pack_session  # noqa: E402
from app.ads import AdsPowerError  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


DEFAULTS: dict[str, Any] = {
    "adspower_base": "http://127.0.0.1:50325",
    "adspower_api_key": "",
    "post_url": "https://21snaps.laravel.cloud/api/browser-shots",
    "post_token": "",
    "latest_url": "https://21snaps.laravel.cloud/api/browser-shots/latest",
    "interval_seconds": 300,
    "jpeg_quality": 55,
    "max_width": 1280,
    "concurrency": 6,
    "save_local": True,
    "local_dir": "shots",
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg_path = path or (HERE / "config.json")
    example = HERE / "config.example.json"
    if not cfg_path.exists() and example.exists():
        cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[config] created {cfg_path} from example — edit post_url / token")
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    # Prefer SnappyMake runtime settings when local config left blank.
    try:
        from app.services import load_runtime_settings

        rt = load_runtime_settings()
        if not (cfg.get("adspower_api_key") or "").strip():
            cfg["adspower_api_key"] = rt.get("api_key") or ""
        if cfg.get("adspower_base") in ("", None, DEFAULTS["adspower_base"]):
            if rt.get("api_base"):
                cfg["adspower_base"] = rt["api_base"]
    except Exception:
        pass
    return cfg


def make_client(cfg: dict[str, Any]) -> AdsPowerClient:
    key = (cfg.get("adspower_api_key") or "").strip()
    base = (cfg.get("adspower_base") or "").strip()
    try:
        return detect_base_url(key)
    except AdsPowerError:
        return AdsPowerClient(base or "http://127.0.0.1:50325", key)


def profile_meta(client: AdsPowerClient, profile_id: str) -> dict[str, str]:
    """Best-effort name / serial / remark for the label string."""
    pid = str(profile_id).strip()
    out = {"id": pid, "serial": "", "name": "", "remark": ""}
    for attempt in (
        ("GET", "/api/v1/user/list", {"user_id": pid, "page_size": 1}, None),
        ("POST", "/api/v2/browser-profile/list", None, {"profile_id": [pid], "page": 1, "limit": 1}),
    ):
        method, path, params, body = attempt
        try:
            payload = client.request(method, path, params=params, json_body=body, retries=1)
        except AdsPowerError:
            continue
        data = payload.get("data") or {}
        rows = data.get("list") if isinstance(data, dict) else None
        if not rows and isinstance(data, list):
            rows = data
        if not rows:
            continue
        row = rows[0] if isinstance(rows[0], dict) else {}
        out["serial"] = str(row.get("serial_number") or row.get("serial") or "")
        out["name"] = str(row.get("name") or row.get("username") or "")
        out["remark"] = str(row.get("remark") or "")
        break
    return out


def format_profile(meta: dict[str, str]) -> str:
    parts: list[str] = []
    if meta.get("serial"):
        parts.append(f"#{meta['serial']}")
    if meta.get("name"):
        parts.append(meta["name"])
    if meta.get("remark"):
        remark = meta["remark"].replace("\n", " ").strip()
        if len(remark) > 120:
            remark = remark[:117] + "..."
        parts.append(remark)
    if not parts:
        parts.append(meta.get("id") or "unknown")
    return " · ".join(parts)


def compress_jpeg(png_or_jpeg: bytes, *, quality: int, max_width: int) -> bytes:
    if Image is None:
        return png_or_jpeg
    img = Image.open(BytesIO(png_or_jpeg))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if max_width and img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((max_width, max(1, int(img.height * ratio))), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=max(20, min(95, int(quality))), optimize=True)
    return buf.getvalue()


def screenshot_session(
    ws_url: str,
    *,
    quality: int,
    max_width: int,
) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_url)
        try:
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("no browser context")
            pages = contexts[0].pages
            if not pages:
                raise RuntimeError("no open tabs")
            # Prefer the last non-blank tab (usually the active one).
            page = pages[-1]
            for candidate in reversed(pages):
                url = (candidate.url or "").lower()
                if url and url not in {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}:
                    page = candidate
                    break
            raw = page.screenshot(type="jpeg", quality=quality, full_page=False)
            return compress_jpeg(raw, quality=quality, max_width=max_width)
        finally:
            # Never browser.close() — that kills the AdsPower profile.
            # Dropping the CDP client without close() leaves the browser running.
            pass


def capture_one(
    client: AdsPowerClient,
    session: dict[str, Any],
    *,
    quality: int,
    max_width: int,
    meta_cache: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    pid = str(session.get("profile_id") or "").strip()
    if not pid:
        return None
    packed = pack_session(pid, session)
    ws = (packed or {}).get("puppeteer") or ""
    if not ws:
        # Refresh active status for ws endpoint.
        try:
            packed = pack_session(pid, client.browser_active(pid))
            ws = (packed or {}).get("puppeteer") or ""
        except AdsPowerError:
            ws = ""
    if not ws:
        print(f"  skip {pid}: no CDP websocket")
        return None

    if pid not in meta_cache:
        try:
            meta_cache[pid] = profile_meta(client, pid)
        except Exception as exc:
            meta_cache[pid] = {"id": pid, "serial": "", "name": "", "remark": str(exc)[:40]}
    label = format_profile(meta_cache[pid])

    try:
        jpeg = screenshot_session(ws, quality=quality, max_width=max_width)
    except Exception as exc:
        print(f"  fail {label}: {exc}")
        return None

    return {
        "profile": label,
        "screenshot": base64.b64encode(jpeg).decode("ascii"),
        "_pid": pid,
        "_bytes": jpeg,
    }


def collect_all(client: AdsPowerClient, cfg: dict[str, Any]) -> list[dict[str, str]]:
    sessions = client.local_sessions()
    print(f"[open] {len(sessions)} AdsPower browser(s)")
    if not sessions:
        return []

    quality = int(cfg.get("jpeg_quality") or 55)
    max_width = int(cfg.get("max_width") or 1280)
    workers = max(1, min(int(cfg.get("concurrency") or 6), len(sessions)))
    meta_cache: dict[str, dict[str, str]] = {}
    items: list[dict[str, Any]] = []

    # Meta fetch is rate-limited on AdsPower — do it serially first for open set.
    for s in sessions:
        pid = str(s.get("profile_id") or "").strip()
        if pid and pid not in meta_cache:
            try:
                meta_cache[pid] = profile_meta(client, pid)
            except Exception:
                meta_cache[pid] = {"id": pid, "serial": "", "name": "", "remark": ""}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                capture_one,
                client,
                s,
                quality=quality,
                max_width=max_width,
                meta_cache=meta_cache,
            )
            for s in sessions
        ]
        for fut in as_completed(futures):
            row = fut.result()
            if row:
                items.append(row)

    # Stable order by profile label
    items.sort(key=lambda r: r.get("profile") or "")
    print(f"[shot] {len(items)}/{len(sessions)} ok")
    return items


def save_local(items: list[dict[str, Any]], cfg: dict[str, Any], ts: str) -> Path | None:
    if not cfg.get("save_local"):
        return None
    stamp = ts.replace(":", "").replace("-", "")[:15]
    out_dir = HERE / str(cfg.get("local_dir") or "shots") / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(items, 1):
        raw = row.get("_bytes") or base64.b64decode(row["screenshot"])
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in row["profile"])[:80]
        (out_dir / f"{i:02d}_{safe or 'profile'}.jpg").write_bytes(raw)
    print(f"[local] {out_dir}")
    return out_dir


def post_batch(items: list[dict[str, Any]], cfg: dict[str, Any], ts: str, *, dry_run: bool) -> None:
    payload = {
        "ts": ts,
        "items": [{"profile": r["profile"], "screenshot": r["screenshot"]} for r in items],
    }
    url = (cfg.get("post_url") or "").strip()
    if dry_run or not url:
        print(f"[post] skipped (dry-run or empty post_url) · {len(payload['items'])} items")
        return

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = (cfg.get("post_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # ~50 JPEGs can be several MB — generous timeout.
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), trust_env=False) as http:
        resp = http.post(url, json=payload, headers=headers)
        print(f"[post] {resp.status_code} · {len(payload['items'])} items · {resp.text[:200]}")
        resp.raise_for_status()


def run_cycle(client: AdsPowerClient, cfg: dict[str, Any], *, dry_run: bool) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"\n=== cycle {ts} ===")
    items = collect_all(client, cfg)
    if items:
        save_local(items, cfg, ts)
        post_batch(items, cfg, ts, dry_run=dry_run)
    else:
        print("[idle] nothing to post")


def main() -> int:
    parser = argparse.ArgumentParser(description="SnappyMonitor — AdsPower open-browser screenshots")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Capture/save only, do not POST")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.json")
    args = parser.parse_args()

    cfg = load_config(args.config)
    interval = max(30, int(cfg.get("interval_seconds") or 300))
    print(
        f"SnappyMonitor · every {interval}s · quality={cfg.get('jpeg_quality')} "
        f"· concurrency={cfg.get('concurrency')} · post={cfg.get('post_url') or '(none)'}"
    )

    client = make_client(cfg)
    try:
        if not client.ping():
            print("AdsPower Local API not reachable on :50325 — open AdsPower first.")
            return 1
    except Exception as exc:
        print(f"AdsPower ping failed: {exc}")
        return 1

    try:
        while True:
            try:
                run_cycle(client, cfg, dry_run=args.dry_run)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[error] cycle failed: {exc}")
            if args.once:
                break
            print(f"[sleep] {interval}s …")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
