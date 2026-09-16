"""SnapX QA runner — availability check → open AdsPower → add friends → report."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

from app.ads import AdsPowerError
from app.inject.snapchat import SNAPCHAT_WELCOME_URL, run_page_action
from app.services import load_runtime_settings, make_client
from app.services.profiles import (
    close_browser,
    extract_snap_web_url,
    list_dashboard_profiles,
    open_browser,
    update_profile_life,
    backfill_snap_web_tabs,
)
from app.services import snaps21

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


def _used_by_for(profile: dict[str, Any]) -> str:
    """Prefer Snap username; fall back to AdsPower name / id."""
    for key in ("username", "display_name", "name", "profile_id"):
        value = str(profile.get(key) or "").strip()
        if value:
            return value[:255]
    return "unknown"


def _log(run: dict[str, Any], kind: str, message: str, **extra: Any) -> None:
    entry = {"ts": time.time(), "kind": kind, "message": message, **extra}
    with _lock:
        run.setdefault("logs", []).append(entry)


def profile_availability(profile: dict[str, Any]) -> dict[str, Any]:
    """Combine local profile health with 21snaps SnapX status."""
    used_by = _used_by_for(profile)
    life = str(profile.get("life") or "").lower()
    local_ok = life not in {"dead", "logout"}
    local_reason = ""
    if not local_ok:
        local_reason = "marked Logout/Dead"

    snap: dict[str, Any] = {}
    snap_ok = False
    snap_reason = "21snaps not checked"
    if snaps21.snaps21_enabled():
        try:
            snap = snaps21.check_status(used_by, platform="snapchat")
            if snap.get("reason") == "automation_disabled":
                snap_ok = False
                snap_reason = "SnapX automation disabled"
            elif snap.get("can_start_automation"):
                snap_ok = True
                snap_reason = snap.get("message") or "ready"
            else:
                snap_ok = False
                snap_reason = snap.get("message") or "cooldown / limit"
        except snaps21.Snaps21Error as exc:
            snap_ok = False
            snap_reason = str(exc)
            snap = {"success": False, "message": str(exc)}
    else:
        snap_reason = "21snaps sync OFF in QA settings"

    available = bool(local_ok and snap_ok)
    return {
        "profile_id": profile.get("profile_id"),
        "profile_no": profile.get("profile_no") or "",
        "name": profile.get("display_name") or profile.get("name"),
        "username": profile.get("username") or "",
        "used_by": used_by,
        "bucket": profile.get("age_bucket") or profile.get("current_bucket") or "new",
        "life": life,
        "web_url": extract_snap_web_url(profile.get("remark") or "") or profile.get("web_url") or "",
        "browser_open": bool(profile.get("browser_open")),
        "available": available,
        "local_ok": local_ok,
        "local_reason": local_reason,
        "snap_ok": snap_ok,
        "snap_reason": snap_reason,
        "snap": {
            "can_start_automation": snap.get("can_start_automation"),
            "has_processing_username": snap.get("has_processing_username"),
            "processing_username": (snap.get("processing_username") or {}).get("username"),
            "usage": snap.get("usage"),
            "next_available_at": snap.get("next_available_at"),
            "wait_seconds": snap.get("wait_seconds"),
            "message": snap.get("message"),
        },
    }


def list_qa_profiles(client, bucket: str | None = None) -> dict[str, Any]:
    dashboard = list_dashboard_profiles(client, group_only=True, bucket=bucket or None)
    profiles = dashboard.get("profiles") or []
    rows = [profile_availability(p) for p in profiles]
    available = sum(1 for r in rows if r["available"])
    return {
        "ok": True,
        "bucket": bucket or "all",
        "total": len(rows),
        "available": available,
        "profiles": rows,
        "group_tree": dashboard.get("group_tree"),
        "snaps21": {
            "enabled": snaps21.snaps21_enabled(),
            "base": snaps21.get_base_url(),
        },
    }


def _claim_usernames(used_by: str, count: int) -> list[dict[str, Any]]:
    claimed: list[dict[str, Any]] = []
    for _ in range(max(1, count)):
        data = snaps21.claim_unused(used_by, platform="snapchat")
        if not data.get("success") or not (data.get("data") or {}).get("username"):
            # If we already have one processing, claim_unused returns it —
            # treat first failure after some claims as stop.
            if claimed:
                break
            raise snaps21.Snaps21Error(data.get("message") or "No unused username available")
        row = data["data"]
        uname = str(row.get("username") or "").strip()
        if not uname:
            break
        if any(c.get("username") == uname for c in claimed):
            # Same processing username returned again — only use once.
            break
        claimed.append({"id": row.get("id"), "username": uname})
    return claimed


def run_qa_one(
    client,
    profile: dict[str, Any],
    *,
    count: int = 1,
    close_after: bool = True,
    on_step: Callable[[str], None] | None = None,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check availability → open → add claimed SnapX usernames → report usage."""
    avail = profile_availability(profile)
    pid = str(profile.get("profile_id") or "")
    used_by = avail["used_by"]
    if run:
        _log(run, "check", f"{avail.get('name')} · {avail.get('snap_reason')}", profile_id=pid)

    if not avail["available"]:
        return {
            "ok": False,
            "skipped": True,
            "profile_id": pid,
            "used_by": used_by,
            "reason": avail["snap_reason"] if not avail["snap_ok"] else avail["local_reason"],
            "availability": avail,
        }

    if not snaps21.snaps21_enabled():
        return {"ok": False, "skipped": True, "profile_id": pid, "reason": "21snaps sync OFF"}

    claimed = _claim_usernames(used_by, count)
    usernames = [c["username"] for c in claimed]
    if run:
        _log(run, "claim", f"Claimed {', '.join(usernames)}", profile_id=pid)

    web_url = (
        extract_snap_web_url(profile.get("remark") or "")
        or str(profile.get("web_url") or "").strip()
        or ""
    )
    opened_id = ""
    results: list[dict[str, Any]] = []
    try:
        session = open_browser(
            client,
            pid,
            headless=False,
            tabs=[web_url] if web_url else None,
        )
        opened_id = pid
        # AdsPower pack_session returns puppeteer=str URL and ws=dict — never pass ws.
        ws = session.get("puppeteer") or ""
        if isinstance(ws, dict):
            ws = ws.get("puppeteer") or ws.get("puppeteer_ws") or ""
        if not isinstance(ws, str) or not ws.strip():
            raise AdsPowerError(f"No CDP websocket after open · keys={list(session.keys())}")
        if on_step:
            on_step("browser open · CDP ready")
        if run:
            _log(
                run,
                "open",
                f"Browser open · CDP ready"
                + (f" · web {web_url}" if web_url else " · no web URL in remark"),
                profile_id=pid,
            )

        action_result = run_page_action(
            ws.strip(),
            action="snapchat_qa_add",
            start_url=web_url or SNAPCHAT_WELCOME_URL,
            username=str(profile.get("username") or ""),
            password=str(profile.get("password") or ""),
            friend_usernames=usernames,
            web_session_url=web_url,
            on_step=on_step,
        )
        notes = list(action_result.get("notes") or [])
        logged_out = bool(action_result.get("logged_out")) or any(
            str(n).startswith("account_logged_out") for n in notes
        )
        if logged_out:
            try:
                update_profile_life(pid, "dead")
                if run:
                    _log(
                        run,
                        "life",
                        "Marked Logout/Dead · web session → login/signup",
                        profile_id=pid,
                    )
            except Exception as life_exc:
                if run:
                    _log(run, "error", f"life update failed: {life_exc}", profile_id=pid)
            return {
                "ok": False,
                "logged_out": True,
                "profile_id": pid,
                "used_by": used_by,
                "claimed": claimed,
                "results": results,
                "action": action_result,
                "web_url": web_url,
                "reason": "web session redirected to login/signup · marked Logout/Dead",
                "availability": avail,
            }

        friend_results = action_result.get("friend_results") or []
        # Map inject outcomes → SnapX statuses.
        by_name = {str(r.get("username") or ""): r for r in friend_results if isinstance(r, dict)}
        for claim in claimed:
            uname = claim["username"]
            fr = by_name.get(uname) or {}
            status = str(fr.get("status") or "").strip()
            if status not in {"Added", "Not Found", "Added Already"}:
                # Fall back: if inject reported any adds for this name, count Added.
                status = "Added" if fr.get("added") else "Not Found"
            try:
                snaps21.update_usage(uname, used_by, status)
            except snaps21.Snaps21Error as exc:
                if run:
                    _log(run, "error", f"update-usage failed · {uname}: {exc}", profile_id=pid)
            results.append({"username": uname, "status": status, "detail": fr})
            if run:
                _log(run, "report", f"{uname} → {status}", profile_id=pid)

        return {
            "ok": True,
            "profile_id": pid,
            "used_by": used_by,
            "claimed": claimed,
            "results": results,
            "action": action_result,
            "web_url": web_url,
            "availability": avail,
        }
    except Exception as exc:
        # Release claims we couldn't attempt as Not Found so they don't stick in processing forever?
        # Safer: leave processing so a retry returns the same username.
        if run:
            _log(run, "error", str(exc), profile_id=pid)
        return {
            "ok": False,
            "profile_id": pid,
            "used_by": used_by,
            "claimed": claimed,
            "results": results,
            "error": str(exc),
            "availability": avail,
        }
    finally:
        if close_after and opened_id:
            try:
                close_browser(client, opened_id)
                if run:
                    _log(run, "close", f"Closed {opened_id}", profile_id=opened_id)
            except Exception as close_exc:
                if run:
                    _log(run, "error", f"Close failed: {close_exc}", profile_id=opened_id)


def current_qa_run() -> dict[str, Any] | None:
    with _lock:
        active = [r for r in _runs.values() if r.get("status") in {"queued", "running"}]
        return dict(active[-1]) if active else None


def get_qa_run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        run = _runs.get(run_id)
        return dict(run) if run else None


def start_qa_run(
    profile_ids: list[str] | None = None,
    *,
    bucket: str | None = None,
    count: int = 1,
    close_after: bool = True,
    only_available: bool = True,
) -> dict[str, Any]:
    """Start a background QA run for one profile, a list, or all (optionally filtered)."""
    run_id = uuid.uuid4().hex[:12]
    run: dict[str, Any] = {
        "id": run_id,
        "status": "queued",
        "ok": 0,
        "skipped": 0,
        "failed": 0,
        "total": 0,
        "bucket": bucket or "all",
        "count": max(1, int(count or 1)),
        "profile_ids": list(profile_ids or []),
        "only_available": only_available,
        "close_after": close_after,
        "results": [],
        "logs": [],
        "error": "",
        "started_at": time.time(),
    }
    with _lock:
        _runs[run_id] = run

    def worker() -> None:
        client = make_client()
        try:
            with _lock:
                run["status"] = "running"
            try:
                bf = backfill_snap_web_tabs(client, dry_run=False)
                _log(
                    run,
                    "tabs",
                    f"Pinned /web/<uuid> startup tabs · updated={bf.get('updated')} · "
                    f"missing={bf.get('missing_web_url')} · errors={bf.get('errors')}",
                )
            except Exception as bf_exc:
                _log(run, "tabs", f"web tab backfill skipped: {bf_exc}")
            dashboard = list_dashboard_profiles(client, group_only=True, bucket=bucket or None)
            profiles = dashboard.get("profiles") or []
            if profile_ids:
                wanted = {str(x) for x in profile_ids}
                profiles = [p for p in profiles if str(p.get("profile_id")) in wanted]
            run["total"] = len(profiles)
            _log(run, "plan", f"{len(profiles)} profile(s) · bucket={bucket or 'all'} · claim={run['count']}")

            for profile in profiles:
                if run.get("cancel"):
                    _log(run, "cancel", "Cancelled")
                    break
                pid = str(profile.get("profile_id") or "")

                def on_step(msg: str, _pid=pid) -> None:
                    _log(run, "action", msg, profile_id=_pid)

                if only_available:
                    avail = profile_availability(profile)
                    if not avail["available"]:
                        with _lock:
                            run["skipped"] += 1
                            run["results"].append(
                                {
                                    "profile_id": pid,
                                    "skipped": True,
                                    "reason": avail.get("snap_reason") or avail.get("local_reason"),
                                }
                            )
                        _log(
                            run,
                            "skip",
                            f"{profile.get('display_name') or pid} · {avail.get('snap_reason') or avail.get('local_reason')}",
                            profile_id=pid,
                        )
                        continue

                result = run_qa_one(
                    client,
                    profile,
                    count=run["count"],
                    close_after=close_after,
                    on_step=on_step,
                    run=run,
                )
                with _lock:
                    run["results"].append(result)
                    if result.get("ok"):
                        run["ok"] += 1
                    elif result.get("skipped"):
                        run["skipped"] += 1
                    else:
                        run["failed"] += 1

            with _lock:
                run["status"] = "done"
            _log(run, "done", f"Finished ok={run['ok']} skip={run['skipped']} fail={run['failed']}")
        except Exception as exc:
            with _lock:
                run["status"] = "error"
                run["error"] = str(exc)
            _log(run, "error", str(exc))
        finally:
            try:
                client.close()
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True, name=f"qa-{run_id}").start()
    return {"ok": True, "run_id": run_id, "run": dict(run)}


def cancel_qa_run(run_id: str) -> dict[str, Any]:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return {"ok": False, "detail": "run not found"}
        run["cancel"] = True
        return {"ok": True, "run_id": run_id}
