"""In-app background runner for the Gmail Login pipeline.

Lets the dashboard "Start" button drive the same proven flow as
scripts/official_login_runner.py (create profile in SnappyMake Official →
login Gmail with backup code → inject Netlox proxy on success), while writing
results to the gmail_login table and exposing live status to the UI.

Playwright's sync API runs happily inside a plain worker thread (no asyncio
loop there), so we run the loop on a daemon thread and poll status over HTTP.
"""

from __future__ import annotations

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
    "tally": {"login_ok": 0, "captcha": 0, "selfie": 0, "error": 0},
    "log": [],
    "last_error": "",
}


def _log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    with _lock:
        _state["log"].append(line)
        if len(_state["log"]) > _LOG_CAP:
            _state["log"] = _state["log"][-_LOG_CAP:]
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
                "tally": {"login_ok": 0, "captcha": 0, "selfie": 0, "error": 0},
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
        import official_login_runner as R  # heavy import (playwright); do it here

        gid = R.official_group_id()
        if not gid:
            _log("ERROR: could not resolve 'SnappyMake Official' group")
            return
        accts = R.pending_accounts()
        with _lock:
            _state["total"] = len(accts)
        _log(f"group {gid} · pending accounts: {len(accts)}")
        if not accts:
            _log("nothing to do — no pending accounts")
            return

        for acc in accts:
            if _stop_requested():
                _log("stopped by user")
                break
            email = acc["email"]
            with _lock:
                _state["current"] = email
            _log(f"→ {email}: starting")
            try:
                outcome = R.run_one(acc, gid, inject_proxy)
            except Exception as exc:  # never let one account kill the loop
                outcome = "error"
                _log(f"   {email}: hard error {str(exc)[:160]}")
                try:
                    G.set_status(email, login_status="error", last_error=str(exc)[:300])
                except Exception:
                    pass
            with _lock:
                _state["processed"] += 1
                if outcome in _state["tally"]:
                    _state["tally"][outcome] += 1
            _log(f"   {email}: {outcome}")
            if outcome == "login_ok" and stop_at_first:
                _log(f"FIRST GOOD LOGIN → {email} · stopping (uncheck 'stop at first' to continue)")
                break
    except Exception as exc:
        with _lock:
            _state["last_error"] = str(exc)
        _log("RUN CRASHED: " + str(exc))
        traceback.print_exc()
    finally:
        with _lock:
            _state["running"] = False
            _state["current"] = ""
            _state["finished_at"] = int(time.time())
        _log("run finished")
