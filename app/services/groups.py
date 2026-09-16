"""Age-based AdsPower group auto-mover.

AdsPower's Local API only supports a FLAT list of groups (no nested subgroups),
so the age tree is emulated with three name-prefixed groups:

    <base> | New              (age < warm_hours)
    <base> | 24hrs Pass       (warm_hours <= age < ready_hours)
    <base> | 4days Pass Ready (age >= ready_hours)

where <base> is the existing `group_name` setting. The age clock is anchored to
each profile's SUCCESS time (`profile_life.succeeded_at`), falling back to the
AdsPower `created_time` for legacy profiles that never recorded a success.
"""

from __future__ import annotations

import time
from typing import Any

from app.ads import AdsPowerError
from app.ads.client import AdsPowerClient
from app.services import load_runtime_settings


def to_seconds(ts: Any) -> int:
    """Normalize an AdsPower timestamp to unix seconds.

    AdsPower `created_time` is unix seconds (10 digits); some payloads use
    milliseconds (13 digits). Mirror the frontend's length-based heuristic:
    treat >= 12-digit values as milliseconds.
    """
    if not ts and ts != 0:
        return 0
    try:
        n = int(float(ts))
    except (TypeError, ValueError):
        return 0
    if n < 0:
        return 0
    if n >= 100_000_000_000:  # 12+ digits => milliseconds
        n //= 1000
    return n


def compute_bucket(anchor_ts: Any, now: int, warm_h: int, ready_h: int) -> str:
    """Return the age bucket key ('new' | 'warm' | 'ready') for an anchor time.

    A missing/zero/future anchor is treated as brand new.
    """
    anchor = to_seconds(anchor_ts)
    if anchor <= 0:
        return "new"
    age_hours = (int(now) - anchor) / 3600.0
    if age_hours < 0:
        return "new"
    if age_hours < float(warm_h):
        return "new"
    if age_hours < float(ready_h):
        return "warm"
    return "ready"


def bucket_counts(client: AdsPowerClient) -> dict[str, Any]:
    """Per-bucket counts by computed age, plus the resolved group tree + thresholds."""
    from app.services.profiles import list_dashboard_profiles, BUCKET_LABELS

    conf = load_runtime_settings()
    dashboard = list_dashboard_profiles(client, group_only=True)
    tree = dashboard.get("group_tree") or {}
    counts = {"new": 0, "warm": 0, "ready": 0}
    for prof in dashboard.get("profiles", []):
        bucket = prof.get("age_bucket")
        if bucket in counts:
            counts[bucket] += 1
    base = (conf.get("group_name") or "SnappyMake").strip() or "SnappyMake"
    buckets = [
        {
            "key": key,
            "label": BUCKET_LABELS[key],
            "name": f"{base} | {BUCKET_LABELS[key]}",
            "group_id": str(tree.get(key) or ""),
            "count": counts[key],
        }
        for key in ("new", "warm", "ready")
    ]
    return {
        "ok": True,
        "base": base,
        "buckets": buckets,
        "counts": counts,
        "total": sum(counts.values()),
        "thresholds": {
            "warm_hours": int(conf.get("bucket_warm_hours") or 24),
            "ready_hours": int(conf.get("bucket_ready_hours") or 96),
        },
        "auto_move_enabled": bool(conf.get("auto_move_enabled")),
    }


def sync_groups(client: AdsPowerClient, dry_run: bool = False) -> dict[str, Any]:
    """Move each SnappyMake profile into the group matching its age bucket.

    Only moves a profile when its current group differs from the target bucket
    group. Skips dead profiles. Uses AdsPower `/api/v1/user/regroup` (v2 update
    ignores group_id). Batches by destination group. Returns a move/count report.
    """
    from app.services.profiles import ensure_group_tree, list_dashboard_profiles

    tree = ensure_group_tree(client)
    if len([g for g in tree.values() if g]) < 3:
        return {
            "ok": False,
            "detail": "Could not resolve all three age-bucket groups in AdsPower.",
            "moved": {"new": 0, "warm": 0, "ready": 0},
            "counts": {"new": 0, "warm": 0, "ready": 0},
            "skipped": 0,
            "errors": [],
            "total": 0,
        }

    dashboard = list_dashboard_profiles(client, group_only=True)
    profiles = dashboard.get("profiles", [])

    moved = {"new": 0, "warm": 0, "ready": 0}
    counts = {"new": 0, "warm": 0, "ready": 0}
    skipped = 0
    errors: list[dict[str, str]] = []
    # bucket -> list of profile_ids that need to move there
    pending: dict[str, list[str]] = {"new": [], "warm": [], "ready": []}

    for prof in profiles:
        pid = str(prof.get("profile_id") or "")
        if not pid:
            continue
        if str(prof.get("life") or "").lower() == "dead":
            skipped += 1
            continue
        created_by = str(prof.get("created_by") or "snappymake").lower()
        if created_by and created_by != "snappymake":
            skipped += 1
            continue

        bucket = prof.get("age_bucket") or "new"
        if bucket not in counts:
            bucket = "new"
        counts[bucket] += 1

        target_gid = str(tree.get(bucket) or "")
        if not target_gid:
            skipped += 1
            continue
        if str(prof.get("group_id")) == target_gid:
            continue  # already in the right bucket
        pending[bucket].append(pid)

    if dry_run:
        for bucket, ids in pending.items():
            moved[bucket] = len(ids)
        return {
            "ok": True,
            "dry_run": True,
            "moved": moved,
            "counts": counts,
            "skipped": skipped,
            "errors": errors,
            "total": len(profiles),
        }

    old_interval = client.min_interval
    client.min_interval = max(old_interval, 1.05)
    try:
        for bucket, ids in pending.items():
            if not ids:
                continue
            target_gid = str(tree.get(bucket) or "")
            if not target_gid:
                continue
            # AdsPower regroup accepts batches; chunk to stay safe.
            chunk_size = 50
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i : i + chunk_size]
                try:
                    client.regroup_profiles(chunk, target_gid)
                    moved[bucket] += len(chunk)
                except AdsPowerError as exc:
                    # Fall back to one-by-one so one bad id doesn't block the rest.
                    for pid in chunk:
                        try:
                            client.regroup_profiles([pid], target_gid)
                            moved[bucket] += 1
                        except AdsPowerError as one_exc:
                            errors.append({"profile_id": pid, "error": str(one_exc)})
    finally:
        client.min_interval = old_interval

    return {
        "ok": True,
        "dry_run": False,
        "moved": moved,
        "counts": counts,
        "skipped": skipped,
        "errors": errors,
        "total": len(profiles),
    }
