"""In-app background runner for the Gmail Login pipeline.

Dashboard Start runs the full Official path: Gmail first → Netlox proxy →
Snapchat Sign up with Google → Bitmoji → web Chat → confirm email.

Playwright's sync API runs in a worker thread; status is polled over HTTP.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

# Make the standalone scripts importable (they hold the proven login machinery).
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from app.services import gmail_login as G  # noqa: E402

_LOG_CAP = 400
_lock = threading.Lock()
_thread: threading.Thread | None = None
_caffeine: subprocess.Popen | None = None
_state: dict[str, Any] = {
    "running": False,
    "current": "",
    "started_at": 0,
    "finished_at": 0,
    "stop_requested": False,
    "stop_at_first": True,
    "inject_proxy": True,
    "processed": 0,
    "total": 0,
    "tally": {"login_ok": 0, "wrong_password": 0, "captcha": 0, "selfie": 0, "error": 0},
    "log": [],
    "last_error": "",
}


def _log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    with _lock:
        _state["log"].append(line)
        if len(_state["log"]) > _LOG_CAP:
            _state["log"] = _state["log"][-_LOG_CAP:]
        if msg.startswith("=== ACCOUNT "):
            _state["current"] = msg.replace("=== ACCOUNT ", "").strip()
        if "ACCOUNT DONE" in msg or msg.startswith("  ") and "next mailbox" in msg:
            _state["processed"] = int(_state.get("processed") or 0) + 1
    print("[gmail-login-run]", msg, flush=True)


def status() -> dict[str, Any]:
    with _lock:
        s = dict(_state)
        s["tally"] = dict(_state["tally"])
        s["log"] = list(_state["log"])[-120:]
    return s


def is_running() -> bool:
    with _lock:
        return bool(_state["running"])


def _keep_awake() -> None:
    """Stop display/system sleep so Playwright + AdsPower don't freeze on black screen."""
    global _caffeine
    _let_sleep()
    try:
        _caffeine = subprocess.Popen(
            ["caffeinate", "-dims", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _log("keeping Mac awake (screen will not sleep during this run)")
    except Exception as exc:
        _log(f"caffeinate warn: {exc}")


def _let_sleep() -> None:
    global _caffeine
    proc = _caffeine
    _caffeine = None
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:
        pass


def start(inject_proxy: bool = True, stop_at_first: bool = True) -> dict[str, Any]:
    global _thread
    with _lock:
        if _state["running"]:
            return {"ok": False, "detail": "A login run is already in progress."}
        _state.update(
            {
                "running": True,
                "current": "",
                "started_at": int(time.time()),
                "finished_at": 0,
                "stop_requested": False,
                "stop_at_first": stop_at_first,
                "inject_proxy": inject_proxy,
                "processed": 0,
                "total": 0,
                "tally": {"login_ok": 0, "wrong_password": 0, "captcha": 0, "selfie": 0, "error": 0},
                "log": [],
                "last_error": "",
            }
        )
    _thread = threading.Thread(
        target=_run, args=(inject_proxy, stop_at_first), daemon=True
    )
    _thread.start()
    _log(f"run started · inject_proxy={inject_proxy} · stop_at_first={stop_at_first}")
    return {"ok": True, "status": status()}


def stop() -> dict[str, Any]:
    with _lock:
        if not _state["running"]:
            return {"ok": False, "detail": "No run in progress."}
        _state["stop_requested"] = True
    _log("stop requested — will halt after the current account")
    return {"ok": True, "status": status()}


def _stop_requested() -> bool:
    with _lock:
        return bool(_state["stop_requested"])


def _run(inject_proxy: bool, stop_at_first: bool) -> None:
    try:
        _keep_awake()
        import importlib
        import official_full_run as R

        R = importlib.reload(R)
        R._on_log = _log
        R._should_stop = _stop_requested
        R._stop_after_signed_up = stop_at_first
        R._inject_proxy = inject_proxy
        snap = G.snapshot()
        with _lock:
            _state["total"] = max(0, int(snap["total"]) - int(snap["signed_up"]))
        _log(
            "full run: Gmail first → proxy → Sign up with Google → "
            "Bitmoji → Chat → confirm email"
        )
        _log(
            f"pool total={snap['total']} pending={snap['pending']} "
            f"login_ok={snap['logged_in']} signed_up={snap['signed_up']}"
        )
        code = R.main()
        snap = G.snapshot()
        with _lock:
            _state["tally"]["login_ok"] = int(snap.get("logged_in") or 0)
        _log(f"pipeline exit {code} · signed_up={snap['signed_up']}")
    except Exception as exc:
        with _lock:
            _state["last_error"] = str(exc)
        _log("RUN CRASHED: " + str(exc))
        traceback.print_exc()
    finally:
        try:
            import official_full_run as R

            R._on_log = None
            R._should_stop = None
        except Exception:
            pass
        with _lock:
            _state["running"] = False
            _state["current"] = ""
            _state["finished_at"] = int(time.time())
        _let_sleep()
        _log("run finished")
