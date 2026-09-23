from __future__ import annotations

from typing import Any

from app.ads.client import AdsPowerClient
from app.config import settings
from app.db import get_setting, set_setting


SETTING_KEYS = {
    "api_base": "adspower_api_base",
    "api_key": "adspower_api_key",
    "plan_cap": "adspower_plan_cap",
    "group_name": "snappymake_group_name",
    "name_prefix": "name_prefix",
    "cloud_token": "cloud_token",
    "otp_provider": "otp_provider",
    "anymessage_token": "anymessage_token",
    "anymessage_site": "anymessage_site",
    "anymessage_domain": "anymessage_domain",
    "diddysms_key": "diddysms_key",
    "diddysms_service": "diddysms_service",
    "proxy_cap": "proxy_cap",
    "proxy_fail_limit": "proxy_fail_limit",
    "proxy_rotation": "proxy_rotation",
    "bucket_warm_hours": "bucket_warm_hours",
    "bucket_ready_hours": "bucket_ready_hours",
    "auto_move_enabled": "auto_move_enabled",
    "sheets_enabled": "sheets_enabled",
    "sheets_id": "sheets_id",
    "sheets_creds_path": "sheets_creds_path",
    "snaps21_enabled": "snaps21_enabled",
    "snaps21_base": "snaps21_base",
}


def load_runtime_settings() -> dict[str, Any]:
    provider = (get_setting("otp_provider") or "imap").strip().lower()
    if provider not in {"imap", "anymessage", "diddysms"}:
        provider = "imap"
    stored = {
        "api_base": get_setting("api_base") or settings.adspower_api_base,
        "api_key": get_setting("api_key") if get_setting("api_key") is not None else settings.adspower_api_key,
        "plan_cap": int(get_setting("plan_cap") or settings.adspower_plan_cap or 0),
        "group_name": get_setting("group_name") or settings.snappymake_group_name,
        "name_prefix": get_setting("name_prefix") or "SM",
        "cloud_token": get_setting("cloud_token") or "",
        "group_id": get_setting("group_id") or "",
        "otp_provider": provider,
        "anymessage_token": get_setting("anymessage_token") or "",
        "anymessage_site": get_setting("anymessage_site") or "snapchat.com",
        "anymessage_domain": get_setting("anymessage_domain") or "gmail,gmail.com",
        "diddysms_key": get_setting("diddysms_key") or "",
        "diddysms_service": get_setting("diddysms_service") or "snapchat",
        "proxy_cap": int(get_setting("proxy_cap") or 3),
        "proxy_fail_limit": int(get_setting("proxy_fail_limit") or 5),
        "proxy_rotation": (get_setting("proxy_rotation") or "spread").strip().lower()
        if (get_setting("proxy_rotation") or "spread").strip().lower() in {"spread", "fill"}
        else "spread",
        "bucket_warm_hours": int(get_setting("bucket_warm_hours") or 24),
        "bucket_ready_hours": int(get_setting("bucket_ready_hours") or 96),
        "auto_move_enabled": (get_setting("auto_move_enabled") or "0") == "1",
        "group_id_new": get_setting("group_id_new") or "",
        "group_id_warm": get_setting("group_id_warm") or "",
        "group_id_ready": get_setting("group_id_ready") or "",
        "sheets_enabled": (get_setting("sheets_enabled") or "0") == "1",
        "sheets_id": get_setting("sheets_id")
        or "1iBsEmI2ZMpQ2Vx5KnNjuz3z6upJIXdveMZ9P6KVMFrA",
        "sheets_creds_path": get_setting("sheets_creds_path") or "secrets/google-sheets.json",
        "snaps21_enabled": (get_setting("snaps21_enabled") or "0") == "1",
        "snaps21_base": get_setting("snaps21_base") or "https://21snaps.laravel.cloud",
    }
    return stored


def save_runtime_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_runtime_settings()
    old_group_name = current.get("group_name")
    for key in (
        "api_base",
        "api_key",
        "group_name",
        "name_prefix",
        "cloud_token",
        "otp_provider",
        "anymessage_token",
        "anymessage_site",
        "anymessage_domain",
        "diddysms_key",
        "diddysms_service",
    ):
        if key in payload and payload[key] is not None:
            value = str(payload[key]).strip()
            if key == "otp_provider":
                value = value.lower() or "imap"
                if value not in {"imap", "anymessage", "diddysms"}:
                    value = "imap"
            set_setting(key, value)
            current[key] = value
    if "plan_cap" in payload and payload["plan_cap"] is not None:
        cap = int(payload["plan_cap"] or 0)
        set_setting("plan_cap", str(cap))
        current["plan_cap"] = cap
    if "proxy_cap" in payload and payload["proxy_cap"] is not None:
        proxy_cap = max(1, int(payload["proxy_cap"] or 3))
        set_setting("proxy_cap", str(proxy_cap))
        current["proxy_cap"] = proxy_cap
    if "proxy_fail_limit" in payload and payload["proxy_fail_limit"] is not None:
        fail_limit = max(1, int(payload["proxy_fail_limit"] or 5))
        set_setting("proxy_fail_limit", str(fail_limit))
        current["proxy_fail_limit"] = fail_limit
    if "proxy_rotation" in payload and payload["proxy_rotation"] is not None:
        rotation = str(payload["proxy_rotation"]).strip().lower()
        if rotation not in {"spread", "fill"}:
            rotation = "spread"
        set_setting("proxy_rotation", rotation)
        current["proxy_rotation"] = rotation
    if "bucket_warm_hours" in payload and payload["bucket_warm_hours"] is not None:
        warm_h = max(1, int(payload["bucket_warm_hours"] or 24))
        set_setting("bucket_warm_hours", str(warm_h))
        current["bucket_warm_hours"] = warm_h
    if "bucket_ready_hours" in payload and payload["bucket_ready_hours"] is not None:
        ready_h = max(1, int(payload["bucket_ready_hours"] or 96))
        set_setting("bucket_ready_hours", str(ready_h))
        current["bucket_ready_hours"] = ready_h
    if "auto_move_enabled" in payload and payload["auto_move_enabled"] is not None:
        enabled = "1" if payload["auto_move_enabled"] in (True, 1, "1", "true", "True", "on") else "0"
        set_setting("auto_move_enabled", enabled)
        current["auto_move_enabled"] = enabled == "1"
    if "sheets_enabled" in payload and payload["sheets_enabled"] is not None:
        enabled = "1" if payload["sheets_enabled"] in (True, 1, "1", "true", "True", "on") else "0"
        set_setting("sheets_enabled", enabled)
        current["sheets_enabled"] = enabled == "1"
    if "sheets_id" in payload and payload["sheets_id"] is not None:
        value = str(payload["sheets_id"]).strip()
        set_setting("sheets_id", value)
        current["sheets_id"] = value
    if "sheets_creds_path" in payload and payload["sheets_creds_path"] is not None:
        value = str(payload["sheets_creds_path"]).strip() or "secrets/google-sheets.json"
        set_setting("sheets_creds_path", value)
        current["sheets_creds_path"] = value
    if "snaps21_enabled" in payload and payload["snaps21_enabled"] is not None:
        enabled = "1" if payload["snaps21_enabled"] in (True, 1, "1", "true", "True", "on") else "0"
        set_setting("snaps21_enabled", enabled)
        current["snaps21_enabled"] = enabled == "1"
    if "snaps21_base" in payload and payload["snaps21_base"] is not None:
        value = str(payload["snaps21_base"]).strip().rstrip("/") or "https://21snaps.laravel.cloud"
        set_setting("snaps21_base", value)
        current["snaps21_base"] = value
    # If the base group name changed, drop cached group ids so the next
    # dashboard load re-resolves (and, if needed, recreates) the group tree by
    # name. Keeps the cache-first hot path correct across renames.
    if current.get("group_name") != old_group_name:
        for key in ("group_id", "group_id_new", "group_id_warm", "group_id_ready"):
            set_setting(key, "")
        for key in ("group_id", "group_id_new", "group_id_warm", "group_id_ready"):
            current[key] = ""
    return current


def make_client() -> AdsPowerClient:
    conf = load_runtime_settings()
    return AdsPowerClient(conf["api_base"], conf["api_key"])
