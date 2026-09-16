"""Proxy Pool Manager.

A small pool of proxies, each with a capacity (how many successful accounts may
share the same exit IP). Rotation is configurable (setting `proxy_rotation`):
"spread" (default) round-robins new profiles onto the least-used eligible proxy,
while "fill" (legacy) fills the first proxy to its cap before advancing. The cap
counts *successful* accounts only — failed or deleted profiles free their slot
immediately.

Proxies are bound to a profile AT CREATION for pool mode (Option A), so signup
runs directly on the pool proxy IP.
"""

from __future__ import annotations

from typing import Any

from app.ads.proxies import (
    parse_proxy_block_report,
    proxy_geo_label,
    summarize_proxy,
    test_proxy,
)
from app.db import (
    delete_proxy,
    get_proxy,
    increment_proxy_success,
    proxy_pool_rows,
    reset_proxy_fail,
    set_proxy_disabled,
    set_proxy_success,
    upsert_proxy,
)
from app.db import get_setting, set_setting


def get_cap() -> int:
    return int(get_setting("proxy_cap") or 3)


def set_cap(n: int) -> int:
    cap = max(1, int(n or 3))
    set_setting("proxy_cap", str(cap))
    return cap


def get_fail_limit() -> int:
    return int(get_setting("proxy_fail_limit") or 5)


def set_fail_limit(n: int) -> int:
    limit = max(1, int(n or 5))
    set_setting("proxy_fail_limit", str(limit))
    return limit


_ROTATION_MODES = ("spread", "fill")


def get_rotation() -> str:
    """Current pool rotation strategy: 'spread' (round-robin) or 'fill' (legacy)."""
    mode = (get_setting("proxy_rotation") or "spread").strip().lower()
    return mode if mode in _ROTATION_MODES else "spread"


def set_rotation(mode: str) -> str:
    value = (str(mode or "").strip().lower())
    if value not in _ROTATION_MODES:
        value = "spread"
    set_setting("proxy_rotation", value)
    return value


def wake_proxy(key: str) -> dict[str, Any]:
    """Clear a proxy's failure streak so it returns to the rotation."""
    if not get_proxy(key):
        return {"ok": False, "detail": "Proxy not found", "key": key}
    reset_proxy_fail(key)
    return {"ok": True, "key": key, "woken": True}


def _effective_cap(row: dict[str, Any], global_cap: int) -> int:
    override = row.get("cap_override")
    if override is not None:
        return max(1, int(override))
    return global_cap


def _proxy_key(config: dict[str, Any] | None) -> str:
    return summarize_proxy(config)


def _resolve_profile_proxy(profile: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort proxy config for a live profile."""
    proxy = profile.get("proxy")
    if isinstance(proxy, dict) and proxy.get("proxy_soft") not in (None, "no_proxy"):
        # A real user_proxy_config (has host) is what we key on.
        if proxy.get("proxy_host"):
            return proxy
    # Fall back to a pending proxy stashed in the merge-after flow, if present.
    pending = profile.get("pending_proxy")
    if isinstance(pending, dict) and pending.get("proxy_host"):
        return pending
    if isinstance(proxy, dict) and proxy.get("proxy_host"):
        return proxy
    return None


def reconcile_success_counts(client) -> dict[str, int]:
    """Recount how many live profiles sit on each proxy key and persist it.

    - Counts every live SnappyMake profile whose proxy resolves to a pool key.
    - Excludes profiles flagged dead in profile_life.
    - Auto-registers any in-use proxy that is missing from the pool.
    Returns {key: count}.
    """
    from app.services.profiles import list_dashboard_profiles

    dashboard = list_dashboard_profiles(client, group_only=True)
    profiles = dashboard.get("profiles", [])

    counts: dict[str, int] = {}
    autoreg: dict[str, dict[str, Any]] = {}
    for prof in profiles:
        if str(prof.get("life") or "").lower() == "dead":
            continue
        config = _resolve_profile_proxy(prof)
        if not config:
            continue
        key = _proxy_key(config)
        if key in ("—", "no proxy", ""):
            continue
        counts[key] = counts.get(key, 0) + 1
        autoreg.setdefault(key, config)

    existing = {row["key"] for row in proxy_pool_rows()}
    # Auto-register any in-use proxy the pool has never seen.
    for key, config in autoreg.items():
        if key not in existing:
            upsert_proxy(
                key,
                raw_line=summarize_proxy(config),
                config_json=config,
                geo_label=proxy_geo_label(config),
            )
            existing.add(key)

    # Persist counts for every known proxy (zero for the unused ones).
    for key in existing:
        set_proxy_success(key, counts.get(key, 0))
    return counts


def pool_snapshot(client=None) -> dict[str, Any]:
    """Join stored rows with derived usage; optionally reconcile first."""
    if client is not None:
        try:
            reconcile_success_counts(client)
        except Exception:
            # Snapshot should still render even if AdsPower is unreachable.
            pass

    global_cap = get_cap()
    fail_limit = get_fail_limit()
    rows = proxy_pool_rows()
    proxies: list[dict[str, Any]] = []
    available = 0
    full = 0
    resting = 0
    for row in rows:
        cap = _effective_cap(row, global_cap)
        success = int(row.get("success_count") or 0)
        fail_streak = int(row.get("fail_streak") or 0)
        disabled = bool(row.get("disabled"))
        remaining = max(0, cap - success)
        # Priority: disabled > full > resting > available.
        if disabled:
            status = "disabled"
        elif success >= cap:
            status = "full"
        elif fail_streak >= fail_limit:
            status = "resting"
        else:
            status = "available"
        if status == "available":
            available += 1
        elif status == "full":
            full += 1
        elif status == "resting":
            resting += 1
        proxies.append(
            {
                "key": row["key"],
                "label": summarize_proxy(row.get("config")) or row["key"],
                "geo": row.get("geo_label") or "",
                "using": success,  # derived "profiles using" == persisted success_count
                "success_count": success,
                "fail_streak": fail_streak,
                "cap": cap,
                "remaining": remaining,
                "status": status,
                "disabled": disabled,
                "raw_line": row.get("raw_line") or "",
            }
        )
    return {
        "ok": True,
        "cap": global_cap,
        "fail_limit": fail_limit,
        "rotation": get_rotation(),
        "total": len(rows),
        "available": available,
        "full": full,
        "resting": resting,
        "proxies": proxies,
    }


def add_proxies(block: str, default_type: str = "", test: bool = False) -> dict[str, Any]:
    """Parse + validate a paste block, dedupe by key, and store new proxies."""
    report = parse_proxy_block_report(block, default_type=default_type)
    existing = {row["key"] for row in proxy_pool_rows()}

    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    tested: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in report["ok"]:
        config = item["config"]
        key = summarize_proxy(config)
        if key in existing or key in seen:
            skipped.append({"key": key, "raw": item.get("raw") or ""})
            continue
        seen.add(key)
        upsert_proxy(
            key,
            raw_line=item.get("raw") or key,
            config_json=config,
            geo_label=proxy_geo_label(config),
        )
        added.append({"key": key, "geo": proxy_geo_label(config)})
        if test:
            result = test_proxy(config)
            tested.append({"key": key, **result})
            if not result.get("ok"):
                set_proxy_disabled(key, True)

    out: dict[str, Any] = {
        "ok": True,
        "added": added,
        "skipped": skipped,
        "invalid": report["errors"],
    }
    if test:
        out["tested"] = tested
    return out


def remove_proxy(key: str, hard: bool = False) -> dict[str, Any]:
    """Soft-remove (disable) by default so history is kept; hard delete on request."""
    if not get_proxy(key):
        return {"ok": False, "detail": "Proxy not found", "key": key}
    if hard:
        delete_proxy(key)
        return {"ok": True, "key": key, "removed": True}
    set_proxy_disabled(key, True)
    return {"ok": True, "key": key, "disabled": True}


def _select_proxy(
    rows: list[dict[str, Any]],
    reserved: dict[str, int],
    global_cap: int,
    fail_limit: int,
    strategy: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Pure selection helper (no DB / locking) so it can be unit-tested.

    `rows` are expected in added_at ASC order (as returned by proxy_pool_rows()).
    Builds the eligible set (not disabled, not resting, used < cap) then picks:
      - "fill":   the first eligible row (fill-then-advance, legacy).
      - "spread": the eligible row with the fewest total assignments
                  (success_count + reserved), ties broken by added_at order.
    `used = success_count + reserved.get(key, 0)`. Cap counts successes only.
    Returns (config, key) or (None, None).
    """
    eligible: list[tuple[dict[str, Any], str, int]] = []
    for row in rows:
        if row.get("disabled"):
            continue
        if int(row.get("fail_streak") or 0) >= fail_limit:
            continue  # resting — give this exit IP a break
        key = row["key"]
        cap = _effective_cap(row, global_cap)
        used = int(row.get("success_count") or 0) + int(reserved.get(key, 0))
        if used < cap:
            eligible.append((row, key, used))

    if not eligible:
        return None, None

    if strategy == "fill":
        row, key, _used = eligible[0]
        return row.get("config") or {}, key

    # spread — minimum `used`, stable so ties keep added_at order.
    row, key, _used = min(eligible, key=lambda item: item[2])
    return row.get("config") or {}, key


def next_available_proxy(
    reserved: dict[str, int] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick a pool proxy with spare capacity per the active rotation strategy.

    Rotation is configurable (setting `proxy_rotation`):
      - "spread" (default): round-robin — the eligible proxy with the fewest
        total assignments (success_count + reserved), ties by added_at ASC.
      - "fill" (legacy): the first eligible proxy, filling it to cap first.
    Skips disabled, full, and resting proxies (fail_streak >= fail_limit).
    `reserved` accounts for in-flight, not-yet-persisted assignments in a run.
    Returns (config, key) or (None, None).

    Acquires runs._lock so pool reads stay consistent with the running loop.
    """
    from app.services import runs as _runs

    reserved = reserved or {}
    global_cap = get_cap()
    fail_limit = get_fail_limit()
    strategy = get_rotation()
    with _runs._lock:
        return _select_proxy(
            proxy_pool_rows(), reserved, global_cap, fail_limit, strategy
        )
