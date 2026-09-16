from __future__ import annotations

from typing import Any

from app.ads import AdsPowerError
from app.ads.client import AdsPowerClient, detect_base_url
from app.db import get_setting
from app.services import load_runtime_settings, save_runtime_settings


def connection_snapshot(client: AdsPowerClient | None = None) -> dict[str, Any]:
    conf = load_runtime_settings()
    own_client = client is None
    try:
        if client is None:
            client = AdsPowerClient(conf["api_base"], conf["api_key"])
        if client.base_url:
            try:
                client.status()
                preferred = "http://127.0.0.1:50325"
                if "local.adspower." in (client.base_url or "") and preferred != client.base_url:
                    probe = AdsPowerClient(preferred, conf["api_key"])
                    try:
                        if probe.ping():
                            save_runtime_settings({"api_base": preferred})
                            client.base_url = preferred
                    finally:
                        probe.close()
                return {
                    "connected": True,
                    "api_base": client.base_url,
                    "message": "AdsPower Local API is live",
                }
            except AdsPowerError:
                pass
        base = detect_base_url(conf["api_base"], conf["api_key"])
        if base != conf["api_base"]:
            save_runtime_settings({"api_base": base})
            conf["api_base"] = base
        client.base_url = base
        client.status()
        return {
            "connected": True,
            "api_base": base,
            "message": "AdsPower Local API is live",
        }
    except AdsPowerError as exc:
        return {
            "connected": False,
            "api_base": conf["api_base"],
            "message": str(exc),
        }
    finally:
        if own_client and client is not None:
            client.close()


def quota_snapshot(
    profiles: list[dict[str, Any]], group_id: str | set[str] | None = None
) -> dict[str, Any]:
    cap = int(get_setting("plan_cap") or 0)
    used = len(profiles)
    group_used = used
    if group_id:
        # Accept a single id or a set of SnappyMake group ids (base + age buckets).
        wanted = {str(g) for g in group_id} if isinstance(group_id, (set, list, tuple)) else {str(group_id)}
        group_used = sum(1 for p in profiles if str(p.get("group_id")) in wanted)
    remaining = None if cap <= 0 else max(cap - used, 0)
    return {
        "used": used,
        "cap": cap or None,
        "remaining": remaining,
        "unknown_cap": cap <= 0,
        "group_used": group_used,
        "plan_label": "premium (cap unknown)" if cap <= 0 else f"{used} / {cap}",
    }


def assert_can_create(quota: dict[str, Any], count: int) -> None:
    remaining = quota.get("remaining")
    if remaining is None:
        return
    if count > remaining:
        raise AdsPowerError(
            f"Safety stop: only {remaining} profile slot(s) left on this cap. "
            f"Asked to create {count}."
        )
