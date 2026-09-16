from __future__ import annotations

import re
import time
from typing import Any, Callable

from app.ads import AdsPowerError
from app.ads.client import AdsPowerClient, pack_session
from app.ads.fingerprints import (
    align_fingerprint_to_proxy,
    describe_fingerprint,
    fingerprint_from_row,
    flatten_fingerprint,
    resolve_fingerprint,
)
from app.ads.proxies import (
    generate_password,
    generate_profile_name,
    generate_username,
    no_proxy,
    parse_proxy_block,
    parse_proxy_line,
    proxy_geo_label,
    proxy_type_of,
    summarize_proxy,
)
from app.db import (
    get_all_profile_cache,
    get_all_profile_life,
    get_all_profile_succeeded,
    get_profile_cache,
    get_setting,
    set_profile_life,
    set_setting,
    upsert_profile_cache,
    delete_profile_cache,
)
from app.services import load_runtime_settings, save_runtime_settings
from app.services.quota import assert_can_create, quota_snapshot

DEFAULT_START_TAB = "https://accounts.snapchat.com/v2/signup"
# Session deep-link saved into AdsPower remark after onboard, e.g.
# https://www.snapchat.com/web/84f9f5a1-913d-5a20-b465-1abaeb6d0735
SNAP_WEB_URL_RE = re.compile(
    r"https?://(?:www\.)?snapchat\.com/web/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    re.I,
)


def extract_snap_web_url(text: str | None) -> str | None:
    """Pull the Snapchat Web session URL (…/web/<uuid>) from remark or a page URL."""
    raw = str(text or "")
    m = SNAP_WEB_URL_RE.search(raw)
    if not m:
        return None
    url = m.group(0).rstrip(".,);]")
    # Normalize host so AdsPower tabs stay consistent.
    if url.lower().startswith("http://"):
        url = "https://" + url[7:]
    if "://snapchat.com/" in url.lower():
        url = re.sub(r"https?://snapchat\.com/", "https://www.snapchat.com/", url, count=1, flags=re.I)
    return url


def set_profile_startup_tabs(
    client: AdsPowerClient, profile_id: str, tabs: list[str]
) -> dict[str, Any]:
    """Set AdsPower profile startup tabs (open these URLs when the browser starts)."""
    pid = str(profile_id or "").strip()
    clean = [str(t).strip() for t in tabs if str(t).strip()]
    if not pid or not clean:
        raise ValueError("profile_id and tabs required")
    client.update_profile({"profile_id": pid, "tabs": clean})
    return {"ok": True, "profile_id": pid, "tabs": clean}


def backfill_snap_web_tabs(client: AdsPowerClient, dry_run: bool = False) -> dict[str, Any]:
    """For every SnappyMake profile with a /web/<uuid> in remark, pin that URL as the startup tab."""
    dashboard = list_dashboard_profiles(client, group_only=True)
    profiles = dashboard.get("profiles") or []
    updated: list[dict[str, Any]] = []
    skipped = 0
    missing = 0
    errors: list[dict[str, Any]] = []
    for prof in profiles:
        pid = str(prof.get("profile_id") or "")
        remark = str(prof.get("remark") or "")
        web = extract_snap_web_url(remark)
        if not web:
            missing += 1
            continue
        if dry_run:
            updated.append({"profile_id": pid, "tabs": [web], "dry_run": True})
            continue
        try:
            set_profile_startup_tabs(client, pid, [web])
            updated.append({"profile_id": pid, "tabs": [web]})
        except Exception as exc:
            errors.append({"profile_id": pid, "error": str(exc)})
            skipped += 1
    return {
        "ok": True,
        "dry_run": dry_run,
        "updated": len(updated),
        "missing_web_url": missing,
        "errors": len(errors),
        "items": updated[:50],
        "error_items": errors[:20],
    }


# Age-based bucket tree, emulated with flat AdsPower groups named "<base> | <label>".
# Order matters: youngest first. Labels are exactly as confirmed by the user.
BUCKETS = ("new", "warm", "ready")
BUCKET_LABELS = {
    "new": "New",
    "warm": "24hrs Pass",
    "ready": "4days Pass Ready",
}
BUCKET_SETTING_KEYS = {
    "new": "group_id_new",
    "warm": "group_id_warm",
    "ready": "group_id_ready",
}


def base_group_name() -> str:
    """The base group name comes from the existing `group_name` setting."""
    conf = load_runtime_settings()
    return (conf.get("group_name") or "SnappyMake").strip() or "SnappyMake"


def bucket_group_name(base: str, bucket: str) -> str:
    return f"{base} | {BUCKET_LABELS[bucket]}"


def _groups_by_name(groups: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(g.get("group_name", "")).strip().lower(): str(g.get("group_id"))
        for g in groups
    }


def ensure_group_tree(client: AdsPowerClient, *, force: bool = False) -> dict[str, str]:
    """Find-or-create the three age buckets, cache their ids, return {bucket: group_id}.

    Emulates the age tree with flat, name-prefixed groups. Case-insensitive name
    match; re-lists after any create to resolve race duplicates. Falls back to
    cached ids when AdsPower is unreachable so the dashboard still renders.

    Cache-first: when all three bucket ids are already cached in settings
    (`group_id_new/warm/ready`), the tree is resolved from cache WITHOUT any
    AdsPower `list_groups`/`create_group` call — this is the dashboard hot path.
    AdsPower is only queried when a cached id is missing (first-ever load) or a
    refresh is explicitly forced. Cached ids are cleared when the base group is
    renamed (see `save_runtime_settings`), so a rename still re-resolves.
    """
    base = base_group_name()
    wanted = {b: bucket_group_name(base, b) for b in BUCKETS}
    if not force:
        cached = {b: str(get_setting(BUCKET_SETTING_KEYS[b]) or "") for b in BUCKETS}
        if all(cached.values()):
            return cached
    try:
        groups = client.list_groups()
    except AdsPowerError:
        groups = []
    by_name = _groups_by_name(groups)

    result: dict[str, str] = {}
    created_any = False
    for bucket, name in wanted.items():
        gid = by_name.get(name.strip().lower())
        if gid:
            result[bucket] = gid
            continue
        try:
            created = client.create_group(name, remark=f"SnappyMake {BUCKET_LABELS[bucket]}")
        except AdsPowerError:
            created = {}
        created_any = True
        gid = str(created.get("group_id") or "")
        if gid:
            result[bucket] = gid

    # Re-list after creating (or if any bucket is still unresolved) to dedupe races.
    if created_any or len(result) < len(BUCKETS):
        try:
            groups = client.list_groups()
        except AdsPowerError:
            groups = []
        if groups:
            by_name = _groups_by_name(groups)
            for bucket, name in wanted.items():
                gid = by_name.get(name.strip().lower())
                if gid:
                    result[bucket] = gid

    # Cache resolved ids; fall back to cached ids for anything unresolved (offline).
    for bucket in BUCKETS:
        gid = result.get(bucket)
        if gid:
            set_setting(BUCKET_SETTING_KEYS[bucket], str(gid))
        else:
            cached = get_setting(BUCKET_SETTING_KEYS[bucket])
            if cached:
                result[bucket] = str(cached)
    return result


def new_bucket_group_id(client: AdsPowerClient) -> str:
    """Group id fresh profiles are created into (the `| New` bucket).

    Falls back to the bare base group id if the tree can't be resolved.
    """
    tree = ensure_group_tree(client)
    gid = tree.get("new")
    if gid:
        return str(gid)
    base = ensure_snappymake_group(client)
    return str(base.get("group_id") or "0")


def snappymake_group_ids(client: AdsPowerClient) -> set[str]:
    """Set of all group ids that belong to SnappyMake: base + the three buckets."""
    ids: set[str] = set()
    base = ensure_snappymake_group(client)
    if base.get("group_id"):
        ids.add(str(base["group_id"]))
    for gid in ensure_group_tree(client).values():
        if gid:
            ids.add(str(gid))
    return ids


def ensure_snappymake_group(client: AdsPowerClient, *, force: bool = False) -> dict[str, Any]:
    conf = load_runtime_settings()
    name = conf["group_name"] or "SnappyMake"
    cached_id = conf.get("group_id") or ""
    # Cache-first: trust the cached base group id and skip the AdsPower
    # list_groups round-trip on the hot path. Only hit AdsPower when the id is
    # missing (first-ever load) or a refresh is explicitly forced. The cache is
    # cleared on a base-group rename (see `save_runtime_settings`).
    if cached_id and not force:
        return {"group_id": str(cached_id), "group_name": name}
    try:
        groups = client.list_groups()
    except AdsPowerError as exc:
        if cached_id:
            return {"group_id": str(cached_id), "group_name": name, "remark": str(exc)}
        return {"group_id": "0", "group_name": "Ungrouped", "remark": str(exc)}
    for group in groups:
        if str(group.get("group_name", "")).strip().lower() == name.strip().lower():
            set_setting("group_id", str(group.get("group_id")))
            return group
    try:
        created = client.create_group(name, remark="All SnappyMake profiles")
    except AdsPowerError:
        created = {}
    if created.get("group_id"):
        set_setting("group_id", str(created["group_id"]))
        return created
    try:
        groups = client.list_groups()
    except AdsPowerError:
        groups = []
    for group in groups:
        if str(group.get("group_name", "")).strip().lower() == name.strip().lower():
            set_setting("group_id", str(group.get("group_id")))
            return group
    if cached_id:
        return {"group_id": str(cached_id), "group_name": name}
    return {"group_id": "0", "group_name": "Ungrouped"}


EMPTY_CATEGORY_NAMES = (
    "blank",
    "none",
    "empty",
    "no extension",
    "no extensions",
    "no ext",
    "snappymake none",
)


def _category_name(row: dict[str, Any]) -> str:
    return str(row.get("category_name") or row.get("name") or "").strip()


def _category_id(row: dict[str, Any]) -> str:
    return str(row.get("category_id") or row.get("id") or "").strip()


def ensure_empty_extension_category(client: AdsPowerClient) -> dict[str, str]:
    """Always use the AdsPower BLANK extension team so profiles get no extras."""
    cached_id = (get_setting("empty_category_id") or "").strip()
    try:
        categories = client.list_categories()
    except AdsPowerError as exc:
        if cached_id:
            return {"category_id": cached_id, "category_name": "BLANK", "remark": str(exc)}
        raise AdsPowerError(
            "Cannot list extension categories. Create one named BLANK with no extensions in AdsPower."
        ) from exc

    blank = None
    fallback = None
    for row in categories:
        name = _category_name(row).lower()
        if name == "blank" or name.startswith("blank "):
            blank = row
            break
        if fallback is None and name in EMPTY_CATEGORY_NAMES:
            fallback = row
    chosen = blank or fallback
    if chosen is None:
        raise AdsPowerError(
            "No BLANK extension team found. In AdsPower, create a category named BLANK with zero extensions."
        )
    category_id = _category_id(chosen)
    name = _category_name(chosen) or "BLANK"
    if cached_id != category_id:
        set_setting("empty_category_id", category_id)
    return {"category_id": category_id, "category_name": name}


def pin_blank_extensions(
    client: AdsPowerClient, profile_id: str, category_id: str | None = None
) -> None:
    cid = str(category_id or ensure_empty_extension_category(client)["category_id"])
    try:
        client.update_profile(
            {
                "profile_id": profile_id,
                "category_id": cid,
                "sys_app_cate_id": cid,
            }
        )
    except AdsPowerError:
        pass


def next_serial(prefix: str, existing_names: list[str]) -> int:
    highest = 0
    needle = f"{prefix}-"
    for name in existing_names:
        if not name.startswith(needle):
            continue
        tail = name[len(needle) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


def merge_profile(row: dict[str, Any], cache: dict[str, Any] | None, active_ids: set[str]) -> dict[str, Any]:
    cache = cache or {}
    cached_fp = cache.get("fingerprint") if isinstance(cache.get("fingerprint"), dict) else {}
    api_fp = fingerprint_from_row(row)
    fingerprint = {**(cached_fp or {}), **(api_fp or {})} or None
    proxy = row.get("user_proxy_config") or cache.get("proxy") or {}
    described = describe_fingerprint(fingerprint if isinstance(fingerprint, dict) else None)
    ua = described["user_agent"]
    if (not ua or ua == "—") and cache.get("user_agent"):
        ua = cache["user_agent"]
    credentials = cache.get("credentials") or {}
    cookie = row.get("cookie") or credentials.get("cookie") or cache.get("cookie") or ""
    remark = row.get("remark") or credentials.get("remark") or ""
    profile_id = str(row.get("profile_id") or "")
    merged = {
        **row,
        "profile_id": profile_id,
        "display_name": row.get("name") or "Untitled",
        "remark": remark,
        "cookie": cookie,
        "proxy_label": summarize_proxy(proxy if isinstance(proxy, dict) else None),
        "proxy_type": proxy_type_of(proxy if isinstance(proxy, dict) else None),
        "proxy": proxy,
        "fingerprint": fingerprint,
        "fingerprint_fields": flatten_fingerprint(fingerprint if isinstance(fingerprint, dict) else None),
        "browser": described.get("browser") or described.get("kernel"),
        "kernel": cache.get("kernel") or described["kernel"],
        "os_name": cache.get("os_name") or described["os_name"],
        "user_agent": ua,
        "webrtc": described.get("webrtc"),
        "timezone": described.get("timezone"),
        "screen": described.get("screen"),
        "cpu": described.get("cpu"),
        "memory": described.get("memory"),
        "username": row.get("username") or credentials.get("username") or "",
        "password": row.get("password") or credentials.get("password") or "",
        "fakey": row.get("fakey") or credentials.get("fakey") or "",
        "platform": row.get("platform") or credentials.get("platform") or "",
        "browser_open": profile_id in active_ids,
        "life": str((cache or {}).get("life") or ""),
        "web_url": extract_snap_web_url(remark) or "",
        "succeeded_at": (cache or {}).get("succeeded_at") or None,
        "cached": bool(cache),
        "all_fields": row,
    }
    return merged


def list_dashboard_profiles(
    client: AdsPowerClient, group_only: bool = True, bucket: str | None = None
) -> dict[str, Any]:
    from app.services.groups import compute_bucket, to_seconds

    conf = load_runtime_settings()
    group = ensure_snappymake_group(client)
    group_id = str(group.get("group_id") or conf.get("group_id") or "")
    tree = ensure_group_tree(client)
    # The full set of SnappyMake group ids: base + the three age buckets. Membership
    # is matched against this SET so profiles stay visible after being moved between
    # buckets (the dashboard, delete guard, proxy reconcile and quota all rely on it).
    sm_ids = {group_id} | {str(gid) for gid in tree.values() if gid}
    id_to_bucket = {str(gid): b for b, gid in tree.items() if gid}
    all_profiles = client.list_profiles()
    if group_only:
        group_profiles = [p for p in all_profiles if str(p.get("group_id")) in sm_ids]
    else:
        group_profiles = all_profiles
    cache = get_all_profile_cache()
    lives = get_all_profile_life()
    succeeded = get_all_profile_succeeded()
    warm_h = int(conf.get("bucket_warm_hours") or 24)
    ready_h = int(conf.get("bucket_ready_hours") or 96)
    now = int(time.time())
    active_ids = set(client.local_active())
    merged = []
    for row in group_profiles:
        pid = str(row.get("profile_id") or "")
        cached = dict(cache.get(pid) or {})
        if lives.get(pid):
            cached["life"] = lives[pid]
        succ = succeeded.get(pid)
        if succ:
            cached["succeeded_at"] = succ
        item = merge_profile(row, cached, active_ids)
        anchor = succ or to_seconds(row.get("created_time"))
        item["age_bucket"] = compute_bucket(anchor, now, warm_h, ready_h)
        item["current_bucket"] = id_to_bucket.get(str(row.get("group_id")))
        merged.append(item)
    if bucket in BUCKETS:
        merged = [p for p in merged if p.get("age_bucket") == bucket]
    quota = quota_snapshot(all_profiles, sm_ids)
    return {
        "group": group,
        "group_tree": tree,
        "profiles": merged,
        "quota": quota,
        "active_ids": sorted(active_ids),
    }


def create_profiles(client: AdsPowerClient, payload: dict[str, Any]) -> dict[str, Any]:
    conf = load_runtime_settings()
    count = max(1, int(payload.get("count") or 1))
    dashboard = list_dashboard_profiles(client, group_only=False)
    assert_can_create(dashboard["quota"], count)

    tree = dashboard.get("group_tree") or ensure_group_tree(client)
    # Fresh profiles land in the `| New` bucket, not the bare base group.
    group_id = str(tree.get("new") or dashboard["group"].get("group_id"))
    sm_ids = {str(dashboard["group"].get("group_id"))} | {str(g) for g in tree.values() if g}
    prefix = (payload.get("name_prefix") or conf.get("name_prefix") or "SM").strip()
    group_names = [
        str(p.get("name") or p.get("display_name") or "")
        for p in dashboard["profiles"]
        if str(p.get("group_id")) in sm_ids
    ]
    serial = next_serial(prefix, group_names)

    fingerprint_mode = str(payload.get("fingerprint_mode") or "selective").lower()

    proxies: list[dict[str, str]] = []
    proxy_mode = payload.get("proxy_mode") or "none"
    pool_reserved: dict[str, int] = {}
    if proxy_mode == "list":
        try:
            proxies = parse_proxy_block(
                payload.get("proxy_list") or "",
                default_type=str(payload.get("proxy_type") or ""),
            )
        except ValueError as exc:
            raise AdsPowerError(str(exc)) from exc
    elif proxy_mode == "saved":
        proxy_id = (payload.get("proxy_id") or "").strip()
        if not proxy_id:
            raise AdsPowerError("Saved proxy needs a proxy ID, or use random.")
    elif proxy_mode == "pool":
        from app.services.proxy_pool import next_available_proxy, reconcile_success_counts

        reconcile_success_counts(client)
        cfg, _key = next_available_proxy(pool_reserved)
        if cfg is None:
            raise AdsPowerError(
                "All proxies are full or the pool is empty. Add proxies or raise the cap in Manage Proxy."
            )

    shared_fingerprint = None if fingerprint_mode == "random" else resolve_fingerprint(payload)
    payload = dict(payload)
    payload["sys_app_cate_id"] = ensure_empty_extension_category(client)["category_id"]

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i in range(count):
        assigned_proxy = None
        assigned_key = None
        if proxy_mode == "pool":
            from app.services.proxy_pool import next_available_proxy

            assigned_proxy, assigned_key = next_available_proxy(pool_reserved)
            if assigned_proxy is None:
                errors.append(
                    {
                        "index": i,
                        "name": generate_profile_name(prefix, serial + i),
                        "error": "Pool exhausted — all proxies are full. Raise the cap or add proxies.",
                        "quota": False,
                    }
                )
                break
            pool_reserved[assigned_key] = pool_reserved.get(assigned_key, 0) + 1
        try:
            created.append(
                create_one_profile(
                    client,
                    payload,
                    index=i,
                    serial=serial + i,
                    group_id=group_id,
                    proxies=proxies,
                    shared_fingerprint=shared_fingerprint,
                    assigned_proxy=assigned_proxy,
                )
            )
        except AdsPowerError as exc:
            # Creation failed — free the reserved pool slot so it can be reused.
            if proxy_mode == "pool" and assigned_key:
                pool_reserved[assigned_key] = max(0, pool_reserved.get(assigned_key, 0) - 1)
            name = generate_profile_name(prefix, serial + i)
            errors.append({"index": i, "name": name, "error": str(exc), "quota": exc.is_quota})
            if exc.is_quota:
                break

    return {
        "created": created,
        "errors": errors,
        "requested": count,
        "ok": len(created),
        "group_id": group_id,
    }


def create_one_profile(
    client: AdsPowerClient,
    payload: dict[str, Any],
    *,
    index: int,
    serial: int,
    group_id: str,
    proxies: list[dict[str, str]] | None = None,
    shared_fingerprint: dict[str, Any] | None = None,
    assigned_proxy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prefix = (payload.get("name_prefix") or "SM").strip()
    auto_name = bool(payload.get("auto_name", True))
    name = (
        generate_profile_name(prefix, serial)
        if auto_name
        else (payload.get("name") or generate_profile_name(prefix, serial))
    )
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if payload.get("auto_username"):
        from app.inject.identity import normalize_gender

        username = generate_username(
            payload.get("username_prefix") or "snap",
            gender=normalize_gender(payload.get("bitmoji_gender")),
        )
    if payload.get("auto_password"):
        password = generate_password()
    fakey = (payload.get("fakey") or "").strip()
    platform = (payload.get("platform") or "").strip()
    remark = (payload.get("remark") or "Created by SnappyMake").strip()
    cookie = (payload.get("cookie") or "").strip()
    tabs = [t.strip() for t in str(payload.get("tabs") or "").splitlines() if t.strip()]
    if not tabs:
        tabs = [DEFAULT_START_TAB]
    proxy_mode = payload.get("proxy_mode") or "none"
    merge_after = bool(payload.get("merge_after_create")) and proxy_mode == "list"
    proxy_cfg: dict[str, Any] | None = None
    pending_proxy: dict[str, Any] | None = None
    proxy_key: str | None = None
    if proxy_mode == "list":
        if not proxies:
            raise AdsPowerError("Paste at least one proxy, or switch to no proxy.")
        pending_proxy = proxies[index % len(proxies)]
        if merge_after:
            proxy_cfg = no_proxy()
        else:
            proxy_cfg = pending_proxy
    elif proxy_mode == "pool":
        # Option A: bind the assigned pool proxy at creation so signup runs on it.
        if not assigned_proxy:
            raise AdsPowerError(
                "All proxies are full or the pool is empty. Add proxies or raise the cap in Manage Proxy."
            )
        proxy_cfg = assigned_proxy
        proxy_key = summarize_proxy(assigned_proxy)
    elif proxy_mode != "saved":
        proxy_cfg = no_proxy()
    # Pool proxies are real exit IPs bound at creation, so align like a live proxy.
    align_mode = "none" if merge_after else ("list" if proxy_mode == "pool" else proxy_mode)
    fingerprint = align_fingerprint_to_proxy(
        dict(shared_fingerprint or resolve_fingerprint(payload)),
        proxy_mode=align_mode,
        proxy=proxy_cfg,
    )
    described = describe_fingerprint(fingerprint)
    geo = proxy_geo_label(pending_proxy if merge_after else proxy_cfg)
    if geo:
        remark = (
            f"{remark} · merge {geo} after Bitmoji" if merge_after else f"{remark} · {geo}"
        ).strip(" ·")
    blank = ensure_empty_extension_category(client)
    blank_id = str(blank["category_id"])

    body: dict[str, Any] = {
        "name": name,
        "group_id": group_id,
        "remark": remark,
        "fingerprint_config": fingerprint,
        "sys_app_cate_id": blank_id,
        "category_id": blank_id,
    }
    if platform:
        body["platform"] = platform
    if username:
        body["username"] = username
    if password:
        body["password"] = password
    if fakey:
        body["fakey"] = fakey
    if tabs:
        body["tabs"] = tabs
    if cookie:
        body["cookie"] = cookie
        body["ignore_cookie_error"] = "1"

    if proxy_mode == "saved":
        body["proxyid"] = payload.get("proxy_id") or "random"
    elif proxy_cfg is not None:
        body["user_proxy_config"] = proxy_cfg

    result = client.create_profile(body)
    profile_id = str(result.get("profile_id") or "")
    # v2 create often drops sys_app_cate_id and leaves category 0 (team extensions).
    pin_blank_extensions(client, profile_id, blank_id)
    upsert_profile_cache(
        profile_id,
        {
            "fingerprint": fingerprint,
            "proxy": body.get("user_proxy_config") or {"proxyid": body.get("proxyid")},
            "pending_proxy": pending_proxy if merge_after else None,
            "kernel": described["kernel"],
            "os_name": described["os_name"],
            "user_agent": described["user_agent"],
            "credentials": {
                "username": username,
                "password": password,
                "fakey": fakey,
                "platform": platform,
                "name": name,
                "cookie": cookie,
                "remark": remark,
            },
            "created_by": "snappymake",
        },
    )
    return {
        **result,
        "profile_id": profile_id,
        "name": name,
        "username": username,
        "password": password,
        "fakey": fakey,
        "platform": platform,
        "os_name": described["os_name"],
        "kernel": described["kernel"],
        "fingerprint": fingerprint,
        "pending_proxy": pending_proxy if merge_after else None,
        "proxy_key": proxy_key,
    }


def update_credentials(client: AdsPowerClient, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"profile_id": profile_id}
    for key in ("name", "username", "password", "fakey", "platform", "remark", "cookie"):
        if key in payload and payload[key] is not None:
            body[key] = payload[key]
    if "tabs" in payload and payload["tabs"] is not None:
        tabs = payload["tabs"]
        if isinstance(tabs, str):
            tabs = [t.strip() for t in tabs.splitlines() if t.strip()]
        body["tabs"] = list(tabs)
    client.update_profile(body)
    upsert_profile_cache(
        profile_id,
        {
            "credentials": {
                "username": payload.get("username", ""),
                "password": payload.get("password", ""),
                "fakey": payload.get("fakey", ""),
                "platform": payload.get("platform", ""),
                "name": payload.get("name", ""),
                "cookie": payload.get("cookie", ""),
                "remark": payload.get("remark", ""),
            }
        },
    )
    return {"ok": True, "profile_id": profile_id}


REMARK_EMAIL_RE = re.compile(r"Email:\s*([^\s·|]+@[^\s·|]+)", re.I)
REMARK_PASS_RE = re.compile(r"Pass:\s*([^\s·|]+)", re.I)


def _resolve_backfill_email(prof: dict[str, Any], email_by_pid: dict[str, str]) -> str | None:
    """Email source priority: remark → stored username → gmail pool mapping."""
    remark = prof.get("remark") or ""
    m = REMARK_EMAIL_RE.search(remark)
    if m:
        return m.group(1).strip()
    uname = (prof.get("username") or "").strip()
    if "@" in uname:
        return uname
    mapped = email_by_pid.get(str(prof.get("profile_id") or ""))
    if mapped and "@" in mapped:
        return mapped
    return None


def backfill_credential_remarks(client: AdsPowerClient, dry_run: bool = False) -> dict[str, Any]:
    """Fill missing `Email:`/`Pass:` in SnappyMake profile remarks.

    Uses the same delimited format the companion parses:
        ` · Email: <addr> · Pass: <app_password>`
    Existing remark text is preserved; only missing pieces are appended.
    """
    # Local imports to avoid import cycles at module load.
    from app.db import gmail_pool_rows
    from app.mail.gmail_imap import load_accounts

    dashboard = list_dashboard_profiles(client, group_only=True)
    profiles = dashboard.get("profiles", [])

    pw_by_email = {a["email"].lower(): a["app_password"] for a in load_accounts()}
    email_by_pid = {
        str(r.get("profile_id")): r["email"]
        for r in gmail_pool_rows()
        if r.get("profile_id") and r.get("email")
    }

    report: list[dict[str, Any]] = []
    counts = {
        "added_email_pass": 0,
        "added_pass": 0,
        "added_email": 0,
        "skipped_has_both": 0,
        "no_email_found": 0,
        "no_password_found": 0,
    }

    for prof in profiles:
        pid = str(prof.get("profile_id") or "")
        name = prof.get("display_name") or prof.get("name") or ""
        remark = prof.get("remark") or ""
        has_email = bool(REMARK_EMAIL_RE.search(remark))
        has_pass = bool(REMARK_PASS_RE.search(remark))

        def add(action: str, email: str | None = None):
            counts[action] += 1
            report.append({"profile_id": pid, "name": name, "email": email, "action": action})

        if has_email and has_pass:
            add("skipped_has_both", REMARK_EMAIL_RE.search(remark).group(1))
            continue

        email = _resolve_backfill_email(prof, email_by_pid)
        if not email:
            add("no_email_found")
            continue

        need_email = not has_email
        need_pass = not has_pass
        password = pw_by_email.get(email.lower()) if need_pass else None
        if need_pass and not password:
            add("no_password_found", email)
            continue

        suffix = ""
        if need_email:
            suffix += f" · Email: {email}"
        if need_pass:
            suffix += f" · Pass: {password}"
        new_remark = (remark + suffix).strip(" ·").strip() if not remark else remark + suffix

        if need_email and need_pass:
            action = "added_email_pass"
        elif need_pass:
            action = "added_pass"
        else:
            action = "added_email"

        if not dry_run:
            update_credentials(
                client,
                pid,
                {
                    "name": name,
                    "username": prof.get("username") or "",
                    "password": prof.get("password") or "",
                    "fakey": prof.get("fakey") or "",
                    "platform": prof.get("platform") or "",
                    "cookie": prof.get("cookie") or "",
                    "remark": new_remark,
                },
            )
        add(action, email)

    return {
        "ok": True,
        "dry_run": dry_run,
        "total": len(profiles),
        "summary": counts,
        "profiles": report,
    }


def merge_proxy_into_profile(
    client: AdsPowerClient, profile_id: str, proxy_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Attach a proxy to an existing profile. Browser must be closed first."""
    cache = get_profile_cache(profile_id) or {}
    fingerprint = dict(cache.get("fingerprint") or {})
    if not fingerprint:
        fingerprint = resolve_fingerprint({})
    fingerprint = align_fingerprint_to_proxy(
        fingerprint, proxy_mode="list", proxy=proxy_cfg
    )
    client.update_profile(
        {
            "profile_id": profile_id,
            "user_proxy_config": proxy_cfg,
            "fingerprint_config": fingerprint,
        }
    )
    upsert_profile_cache(
        profile_id,
        {
            "fingerprint": fingerprint,
            "proxy": proxy_cfg,
            "pending_proxy": None,
        },
    )
    return {
        "ok": True,
        "profile_id": profile_id,
        "label": summarize_proxy(proxy_cfg),
        "geo": proxy_geo_label(proxy_cfg),
    }


def assign_profile_proxy(
    client: AdsPowerClient,
    profile_id: str,
    *,
    proxy_list: str = "",
    proxy_type: str = "",
    clear: bool = False,
) -> dict[str, Any]:
    """Close if open, then set this profile's proxy (or clear to no proxy)."""
    pid = str(profile_id or "").strip()
    dashboard = list_dashboard_profiles(client, group_only=True)
    allowed = {str(p.get("profile_id")): p for p in dashboard["profiles"]}
    if pid not in allowed:
        raise AdsPowerError(f"Not in the SnappyMake group: {pid}")
    if allowed[pid].get("browser_open"):
        close_browser(client, pid)
        time.sleep(1.5)
    if clear:
        cache = get_profile_cache(pid) or {}
        fingerprint = dict(cache.get("fingerprint") or {})
        if fingerprint:
            fingerprint["webrtc"] = "disabled"
        body: dict[str, Any] = {
            "profile_id": pid,
            "user_proxy_config": no_proxy(),
        }
        if fingerprint:
            body["fingerprint_config"] = fingerprint
        client.update_profile(body)
        upsert_profile_cache(
            pid,
            {
                "fingerprint": fingerprint or cache.get("fingerprint"),
                "proxy": no_proxy(),
                "pending_proxy": None,
            },
        )
        return {"ok": True, "profile_id": pid, "label": "no proxy", "geo": ""}
    line = ""
    for raw in (proxy_list or "").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#"):
            line = raw
            break
    if not line:
        raise AdsPowerError("Paste one proxy line (host:port:user:pass).")
    try:
        cfg = parse_proxy_line(line, default_type=proxy_type)
    except ValueError as exc:
        raise AdsPowerError(str(exc)) from exc
    return merge_proxy_into_profile(client, pid, cfg)


def update_profile_life(profile_id: str, status: str) -> dict[str, Any]:
    pid = str(profile_id or "").strip()
    value = set_profile_life(pid, status)
    return {"ok": True, "profile_id": pid, "life": value}


def open_browser(
    client: AdsPowerClient,
    profile_id: str,
    headless: bool = False,
    *,
    wait_for_kernel: bool = True,
    timeout: float = 600,
    attempt_timeout: float = 75,
    poll: float = 5,
    on_wait: Callable[[str, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    tabs: list[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: AdsPowerError | None = None
    last_start = 0.0
    started = False
    switched = False

    def remaining() -> float:
        return deadline - time.monotonic()

    def wait_note(message: str) -> None:
        left = max(int(remaining()), 0)
        if on_wait:
            on_wait(message, left)

    def describe(exc: AdsPowerError) -> str:
        if exc.is_kernel_download:
            return f"AdsPower is installing a kernel ({exc}). Waiting instead of retrying start."
        if exc.is_already_open:
            return "AdsPower says the profile is already opening — checking debug port."
        if exc.is_timeout:
            return "AdsPower start hung. Checking if the browser actually opened."
        return str(exc)

    def existing() -> dict[str, Any] | None:
        session = client.debug_session(profile_id)
        if session and client.session_alive(session):
            return session
        return None

    def resolve_tabs() -> list[str]:
        if tabs:
            clean = [str(t).strip() for t in tabs if str(t).strip()]
            if clean:
                return clean
        cache = get_profile_cache(profile_id) or {}
        creds = cache.get("credentials") if isinstance(cache.get("credentials"), dict) else {}
        remark = str((creds or {}).get("remark") or cache.get("remark") or "")
        web = extract_snap_web_url(remark)
        if web:
            return [web]
        return [DEFAULT_START_TAB]

    def pin_before_open() -> None:
        blank_id = str(ensure_empty_extension_category(client)["category_id"])
        try:
            client.update_profile(
                {
                    "profile_id": profile_id,
                    "category_id": blank_id,
                    "sys_app_cate_id": blank_id,
                    "tabs": resolve_tabs(),
                }
            )
        except AdsPowerError:
            pass

    session = existing()
    if session:
        return session

    stale = client.debug_session(profile_id)
    if stale:
        try:
            client.stop_browser(profile_id)
            time.sleep(1)
        except AdsPowerError:
            pass

    pin_before_open()
    died_once = False

    while True:
        if should_stop and should_stop():
            raise AdsPowerError("Cancelled while waiting for AdsPower to open the browser")
        left = remaining()
        if left <= 0:
            raise last_error or AdsPowerError("Timed out waiting for AdsPower to open the browser")

        session = existing()
        if session:
            return session

        can_start = (not started) or (time.monotonic() - last_start >= 25)
        if can_start:
            try:
                data = client.start_browser(
                    profile_id,
                    headless=headless,
                    timeout=min(attempt_timeout, max(left, 8.0)),
                )
                packed = pack_session(profile_id, data) or pack_session(
                    profile_id, {**(data or {}), "status": "Active"}
                )
                if packed:
                    time.sleep(2)
                    still = existing()
                    if still:
                        return still
                    if not died_once:
                        died_once = True
                        started = False
                        last_start = 0.0
                        pin_before_open()
                        wait_note("Browser opened then quit. Restarting with a start tab.")
                        continue
                    return packed
                started = True
                last_start = time.monotonic()
            except AdsPowerError as exc:
                last_error = exc
                started = True
                last_start = time.monotonic()
                if exc.is_already_open:
                    session = existing()
                    if session:
                        return session
                if not wait_for_kernel or not exc.is_start_busy:
                    raise
                if exc.is_timeout and not switched:
                    switched = client.fallback_localhost()
                    if switched:
                        save_runtime_settings({"api_base": client.base_url})
                        wait_note(describe(exc) + f" Switched API to {client.base_url}")
                        last_start = 0.0
                        started = False
                        continue
                wait_note(describe(exc))
                time.sleep(min(poll if exc.is_kernel_download else 2, max(remaining(), 1)))
                continue

        wait_note(describe(last_error) if last_error else "Waiting for AdsPower browser…")
        time.sleep(min(poll, max(remaining(), 1)))
    raise AdsPowerError(f"Timed out waiting for browser: {last_error}")


def close_browser(client: AdsPowerClient, profile_id: str) -> dict[str, Any]:
    client.stop_browser(profile_id)
    return {"ok": True, "profile_id": profile_id}


def delete_profiles(client: AdsPowerClient, profile_ids: list[str]) -> dict[str, Any]:
    wanted = [str(pid).strip() for pid in profile_ids if str(pid).strip()]
    if not wanted:
        raise AdsPowerError("Select at least one profile to delete.")
    dashboard = list_dashboard_profiles(client, group_only=True)
    allowed = {str(p.get("profile_id")): p for p in dashboard["profiles"]}
    unknown = [pid for pid in wanted if pid not in allowed]
    if unknown:
        raise AdsPowerError(f"Not in the SnappyMake group: {', '.join(unknown[:8])}")
    closed: list[str] = []
    for pid in wanted:
        if allowed[pid].get("browser_open"):
            try:
                client.stop_browser(pid)
                closed.append(pid)
            except AdsPowerError:
                pass
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for i in range(0, len(wanted), 100):
        chunk = wanted[i : i + 100]
        try:
            client.delete_profiles(chunk)
            deleted.extend(chunk)
        except AdsPowerError as exc:
            if len(chunk) == 1:
                errors.append({"profile_id": chunk[0], "error": str(exc)})
                continue
            for pid in chunk:
                try:
                    client.delete_profiles([pid])
                    deleted.append(pid)
                except AdsPowerError as one:
                    errors.append({"profile_id": pid, "error": str(one)})
    if deleted:
        delete_profile_cache(deleted)
    if not deleted and errors:
        raise AdsPowerError(errors[0]["error"])
    return {
        "ok": len(deleted),
        "requested": len(wanted),
        "deleted": deleted,
        "closed": closed,
        "errors": errors,
    }


def _cli_backfill() -> None:
    """One-off runner:  python -m app.services.profiles [--apply]"""
    import json as _json
    import sys as _sys

    from app.ads.client import AdsPowerClient as _Client

    dry_run = "--apply" not in _sys.argv
    conf = load_runtime_settings()
    client = _Client(conf["api_base"], conf["api_key"])
    try:
        result = backfill_credential_remarks(client, dry_run=dry_run)
    finally:
        client.close()
    print(_json.dumps(result, indent=2, ensure_ascii=False))
    mode = "DRY-RUN (no writes)" if dry_run else "APPLIED"
    print(f"\n{mode} · {result['total']} profiles · " +
          " · ".join(f"{k}={v}" for k, v in result["summary"].items()))


if __name__ == "__main__":
    _cli_backfill()
