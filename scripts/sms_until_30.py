#!/usr/bin/env python3
"""Keep creating DiddySMS Snap signups in Official SMS group until you stop this script."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BATCH = 10
GID = "10749351"
BASE = "http://127.0.0.1:8787"
PAYLOAD = {
    "name_prefix": "SMS",
    "action": "snapchat_signup",
    "close_after": True,
    "proxy_mode": "none",
    "fingerprint_mode": "random",
    "auto_username": True,
    "auto_password": True,
    "bitmoji_gender": "female",
    "os": "win11",
    "group_id": GID,
    "platform": "snapchat.com",
}


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read())


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def count_ok() -> int:
    from pathlib import Path
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.services import make_client

    try:
        client = make_client()
    except Exception as exc:
        print("count_ok settings", exc, flush=True)
        return 0
    try:
        n = 0
        for p in client.list_profiles(group_id=GID):
            remark = str(p.get("remark") or "")
            if "DiddySMS:" in remark or "snapchat.com/web/" in remark:
                n += 1
        return n
    except Exception as exc:
        print("count_ok", exc, flush=True)
        return 0
    finally:
        try:
            client.close()
        except Exception:
            pass


def wait_idle() -> dict:
    while True:
        try:
            d = get("/api/runs/current")
        except Exception as exc:
            print("wait_idle", exc, flush=True)
            time.sleep(4)
            continue
        st = d.get("status")
        if st not in {"running", "queued", "cancelling"}:
            return d
        time.sleep(3)


def main() -> None:
    print("sms-until-stop start", flush=True)
    time.sleep(3)
    while True:
        try:
            wait_idle()
            n = count_ok()
            print(f"good={n} · next batch {BATCH}", flush=True)
            body = dict(PAYLOAD)
            body["count"] = BATCH
            print(f"start batch count={BATCH}", flush=True)
            started = post("/api/runs", body)
        except urllib.error.HTTPError as exc:
            err = exc.read().decode()[:300]
            print("POST fail", exc.code, err, flush=True)
            time.sleep(20)
            continue
        except Exception as exc:
            print("loop err", exc, flush=True)
            time.sleep(15)
            continue
        print("run", started.get("id"), started.get("status"), flush=True)
        last_len = 0
        while True:
            time.sleep(6)
            try:
                d = get("/api/runs/current")
            except Exception as exc:
                print("poll err", exc, flush=True)
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
                        "after_submit",
                        "typed_phone",
                        "clicked_use_phone",
                        "clicked_accept_all_cookies",
                        "process_error_stuck",
                        "phone_rejected",
                        "phone_reordered",
                        "Finished",
                        "Created SMS",
                        "DiddySMS +",
                        "DiddySMS ·",
                        "web_add_friends_added",
                        "bitmoji_done",
                    )
                )
                if interesting:
                    print(" ", msg[:220], flush=True)
            last_len = len(logs)
            if d.get("status") not in {"running", "queued", "cancelling"}:
                print(
                    f"batch {d.get('status')} ok={d.get('ok')}/{d.get('requested')} err={d.get('error')}",
                    flush=True,
                )
                break
        try:
            from app.services.sheets import sync_sms_official_now

            sheet = sync_sms_official_now(force=True)
            print(
                f"sheet {sheet.get('sms_official_tab')} rows={sheet.get('sms_profiles')}",
                flush=True,
            )
        except Exception as exc:
            print("sheet sync warn", exc, flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
