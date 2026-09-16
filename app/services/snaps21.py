"""21snaps SnapX client — pull / report Snapchat usernames to add."""

from __future__ import annotations

from typing import Any

import httpx

from app.db import get_setting, set_setting

DEFAULT_BASE = "https://21snaps.laravel.cloud"


class Snaps21Error(Exception):
    pass


def get_base_url() -> str:
    return (get_setting("snaps21_base") or DEFAULT_BASE).rstrip("/")


def set_base_url(url: str) -> str:
    value = (url or DEFAULT_BASE).strip().rstrip("/")
    set_setting("snaps21_base", value)
    return value


def snaps21_enabled() -> bool:
    return (get_setting("snaps21_enabled") or "0") == "1"


def set_enabled(on: bool) -> bool:
    set_setting("snaps21_enabled", "1" if on else "0")
    return on


def _get(path: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    url = f"{get_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(url, params=params or {})
            data = res.json() if res.content else {}
            if not isinstance(data, dict):
                data = {"raw": data}
            data["_http_status"] = res.status_code
            return data
    except httpx.HTTPError as exc:
        raise Snaps21Error(f"21snaps GET {path} failed: {exc}") from exc


def _post(path: str, body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    url = f"{get_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.post(url, json=body)
            data = res.json() if res.content else {}
            if not isinstance(data, dict):
                data = {"raw": data}
            data["_http_status"] = res.status_code
            return data
    except httpx.HTTPError as exc:
        raise Snaps21Error(f"21snaps POST {path} failed: {exc}") from exc


def check_status(used_by: str, platform: str = "snapchat") -> dict[str, Any]:
    """Availability for a Snap account (`can_start_automation`)."""
    return _get(
        "/api/snaps/status",
        {"used_by": used_by, "platform": platform},
    )


def claim_unused(used_by: str, platform: str = "snapchat") -> dict[str, Any]:
    """Reserve one unused username for this account."""
    return _get(
        "/api/snaps/unused",
        {"used_by": used_by, "platform": platform},
    )


def update_usage(
    username: str,
    used_by: str,
    used_by_status: str = "Added",
) -> dict[str, Any]:
    """Report add result: Added | Not Found | Added Already."""
    status = used_by_status if used_by_status in {"Added", "Not Found", "Added Already"} else "Added"
    return _post(
        "/api/snaps/update-usage",
        {
            "username": username,
            "used_by": used_by,
            "used_by_status": status,
        },
    )


def ping() -> dict[str, Any]:
    """Light connectivity check against the SnapX status endpoint shape."""
    try:
        data = _get("/api/snaps/status", {"used_by": "__snappymake_ping__"}, timeout=8.0)
        return {
            "ok": True,
            "base": get_base_url(),
            "enabled": snaps21_enabled(),
            "sample": {
                "success": data.get("success"),
                "message": data.get("message"),
                "reason": data.get("reason"),
                "http": data.get("_http_status"),
            },
        }
    except Exception as exc:
        return {"ok": False, "base": get_base_url(), "enabled": snaps21_enabled(), "detail": str(exc)}
