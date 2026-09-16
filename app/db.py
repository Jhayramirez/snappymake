from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import DB_PATH


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_cache (
                profile_id TEXT PRIMARY KEY,
                fingerprint_json TEXT,
                proxy_json TEXT,
                kernel TEXT,
                os_name TEXT,
                user_agent TEXT,
                credentials_json TEXT,
                created_by TEXT DEFAULT 'snappymake',
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS gmail_pool (
                email TEXT PRIMARY KEY,
                used INTEGER NOT NULL DEFAULT 0,
                profile_id TEXT,
                used_at INTEGER,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS profile_life (
                profile_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT '',
                succeeded_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS gmail_login (
                email TEXT PRIMARY KEY,
                password TEXT NOT NULL DEFAULT '',
                login_status TEXT NOT NULL DEFAULT 'pending',
                profile_id TEXT,
                proxy_label TEXT DEFAULT '',
                snap_status TEXT NOT NULL DEFAULT 'none',
                group_name TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                created_at INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS gmail_backup_code (
                email TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                code TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                used_at INTEGER,
                PRIMARY KEY (email, ordinal)
            );

            CREATE TABLE IF NOT EXISTS proxy_pool (
                key TEXT PRIMARY KEY,
                raw_line TEXT NOT NULL,
                config_json TEXT NOT NULL,
                geo_label TEXT DEFAULT '',
                cap_override INTEGER,
                success_count INTEGER NOT NULL DEFAULT 0,
                fail_streak INTEGER NOT NULL DEFAULT 0,
                disabled INTEGER NOT NULL DEFAULT 0,
                added_at INTEGER,
                updated_at INTEGER
            );
            """
        )
        # Additive migrations for existing DBs (ignore "duplicate column").
        for stmt in (
            "ALTER TABLE proxy_pool ADD COLUMN fail_streak INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE profile_life ADD COLUMN succeeded_at INTEGER",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass


def get_setting(key: str, default: str | None = None) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def upsert_profile_cache(profile_id: str, payload: dict[str, Any]) -> None:
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM profile_cache WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        current = dict(existing) if existing else {}
        if existing and payload.get("credentials") and existing["credentials_json"]:
            try:
                old_cred = json.loads(existing["credentials_json"])
            except json.JSONDecodeError:
                old_cred = {}
            payload = {**payload, "credentials": {**old_cred, **(payload.get("credentials") or {})}}
        merged = {**current, **payload, "profile_id": profile_id}
        conn.execute(
            """
            INSERT INTO profile_cache (
                profile_id, fingerprint_json, proxy_json, kernel, os_name,
                user_agent, credentials_json, created_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
            ON CONFLICT(profile_id) DO UPDATE SET
                fingerprint_json = excluded.fingerprint_json,
                proxy_json = excluded.proxy_json,
                kernel = excluded.kernel,
                os_name = excluded.os_name,
                user_agent = excluded.user_agent,
                credentials_json = excluded.credentials_json,
                created_by = excluded.created_by,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                _as_json(merged.get("fingerprint_json") or merged.get("fingerprint")),
                _as_json(merged.get("proxy_json") or merged.get("proxy")),
                merged.get("kernel"),
                merged.get("os_name"),
                merged.get("user_agent"),
                _as_json(merged.get("credentials_json") or merged.get("credentials")),
                merged.get("created_by") or "snappymake",
            ),
        )


def get_profile_cache(profile_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM profile_cache WHERE profile_id = ?", (profile_id,)
        ).fetchone()
    return _row_to_cache(row) if row else None


def get_all_profile_cache() -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM profile_cache").fetchall()
    return {row["profile_id"]: _row_to_cache(row) for row in rows}


def delete_profile_cache(profile_ids: list[str]) -> None:
    ids = [str(pid).strip() for pid in profile_ids if str(pid).strip()]
    if not ids:
        return
    with db() as conn:
        conn.executemany(
            "DELETE FROM profile_cache WHERE profile_id = ?",
            [(pid,) for pid in ids],
        )
        conn.executemany(
            "DELETE FROM profile_life WHERE profile_id = ?",
            [(pid,) for pid in ids],
        )


def get_all_profile_life() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT profile_id, status FROM profile_life").fetchall()
    out: dict[str, str] = {}
    for row in rows:
        status = str(row["status"] or "").strip().lower()
        if status in {"live", "dead", "logout"}:
            # legacy "logout" → same chip as dead (Logout/Dead)
            out[str(row["profile_id"])] = "dead" if status == "logout" else status
    return out


def get_all_profile_succeeded() -> dict[str, int]:
    """Map of profile_id -> succeeded_at (unix seconds) for profiles that have one."""
    with db() as conn:
        rows = conn.execute(
            "SELECT profile_id, succeeded_at FROM profile_life WHERE succeeded_at IS NOT NULL"
        ).fetchall()
    out: dict[str, int] = {}
    for row in rows:
        ts = row["succeeded_at"]
        if ts:
            out[str(row["profile_id"])] = int(ts)
    return out


def set_profile_succeeded(profile_id: str, ts: int | None = None) -> int:
    """Stamp when a profile's signup/onboard action succeeded (unix seconds).

    Upserts into profile_life without disturbing an existing life status.
    Defaults to now if ts is not provided. Returns the stored timestamp.
    """
    pid = str(profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id required")
    when = int(ts if ts is not None else time.time())
    with db() as conn:
        conn.execute(
            """
            INSERT INTO profile_life (profile_id, status, succeeded_at, updated_at)
            VALUES (?, '', ?, strftime('%s','now'))
            ON CONFLICT(profile_id) DO UPDATE SET
                succeeded_at = excluded.succeeded_at,
                updated_at = excluded.updated_at
            """,
            (pid, when),
        )
    return when


def set_profile_life(profile_id: str, status: str) -> str:
    pid = str(profile_id or "").strip()
    value = str(status or "").strip().lower()
    # "logout" is accepted as an alias for dead (UI label: Logout/Dead)
    if value == "logout":
        value = "dead"
    if value not in {"", "live", "dead"}:
        raise ValueError("status must be live, dead/logout, or empty")
    if not pid:
        raise ValueError("profile_id required")
    with db() as conn:
        if not value:
            # Clear the live/dead flag but preserve succeeded_at: drop the row only
            # if there is no success timestamp to keep, otherwise blank the status.
            row = conn.execute(
                "SELECT succeeded_at FROM profile_life WHERE profile_id = ?", (pid,)
            ).fetchone()
            if row and row["succeeded_at"] is not None:
                conn.execute(
                    "UPDATE profile_life SET status = '', updated_at = strftime('%s','now') WHERE profile_id = ?",
                    (pid,),
                )
            else:
                conn.execute("DELETE FROM profile_life WHERE profile_id = ?", (pid,))
            return ""
        conn.execute(
            """
            INSERT INTO profile_life (profile_id, status, updated_at)
            VALUES (?, ?, strftime('%s','now'))
            ON CONFLICT(profile_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (pid, value),
        )
    return value


def _as_json(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _row_to_cache(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("fingerprint_json", "proxy_json", "credentials_json"):
        raw = data.get(key)
        if raw:
            try:
                data[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                data[key.replace("_json", "")] = raw
        else:
            data[key.replace("_json", "")] = None
    return data


def gmail_used_set() -> set[str]:
    return {row["email"].lower() for row in gmail_pool_rows() if row["used"]}


def gmail_pool_rows() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT email, used, profile_id, used_at, last_error FROM gmail_pool"
        ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "email": row["email"],
                "used": bool(row["used"]),
                "profile_id": row["profile_id"] or "",
                "used_at": int(row["used_at"] or 0),
                "last_error": row["last_error"] or "",
            }
        )
    return out


def mark_gmail_used(email: str, profile_id: str = "") -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO gmail_pool (email, used, profile_id, used_at)
            VALUES (?, 1, ?, strftime('%s','now'))
            ON CONFLICT(email) DO UPDATE SET
                used = 1,
                profile_id = excluded.profile_id,
                used_at = excluded.used_at
            """,
            (email.strip().lower(), profile_id),
        )


def mark_gmail_unused(email: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE gmail_pool SET used = 0, profile_id = NULL, used_at = NULL WHERE email = ?",
            (email.strip().lower(),),
        )


def rename_gmail_email(old_email: str, new_email: str) -> None:
    old = (old_email or "").strip().lower()
    new = (new_email or "").strip().lower()
    if not old or not new or old == new:
        return
    with db() as conn:
        row = conn.execute("SELECT email FROM gmail_pool WHERE email = ?", (old,)).fetchone()
        if row is None:
            return
        clash = conn.execute("SELECT email FROM gmail_pool WHERE email = ?", (new,)).fetchone()
        if clash:
            conn.execute("DELETE FROM gmail_pool WHERE email = ?", (old,))
            return
        conn.execute("UPDATE gmail_pool SET email = ? WHERE email = ?", (new, old))


def set_gmail_error(email: str, message: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO gmail_pool (email, used, last_error)
            VALUES (?, 0, ?)
            ON CONFLICT(email) DO UPDATE SET last_error = excluded.last_error
            """,
            (email.strip().lower(), message[:500]),
        )


# --------------------------------------------------------------------------- #
# Gmail Login pool (password + backup codes) — distinct from the IMAP gmail_pool
# --------------------------------------------------------------------------- #

GMAIL_LOGIN_STATES = {"pending", "login_ok", "captcha", "selfie", "error"}
GMAIL_SNAP_STATES = {"none", "signed_up", "failed"}


def gmail_login_emails() -> set[str]:
    with db() as conn:
        rows = conn.execute("SELECT email FROM gmail_login").fetchall()
    return {str(r["email"]).lower() for r in rows}


def gmail_login_rows() -> list[dict[str, Any]]:
    with db() as conn:
        accts = conn.execute(
            """
            SELECT email, password, login_status, profile_id, proxy_label,
                   snap_status, group_name, last_error, created_at, updated_at
            FROM gmail_login
            ORDER BY created_at ASC, email ASC
            """
        ).fetchall()
        codes = conn.execute(
            "SELECT email, ordinal, code, used, used_at FROM gmail_backup_code ORDER BY email, ordinal"
        ).fetchall()
    by_email: dict[str, list[dict[str, Any]]] = {}
    for c in codes:
        by_email.setdefault(c["email"], []).append(
            {
                "ordinal": int(c["ordinal"]),
                "code": c["code"],
                "used": bool(c["used"]),
                "used_at": int(c["used_at"] or 0),
            }
        )
    out: list[dict[str, Any]] = []
    for row in accts:
        out.append(
            {
                "email": row["email"],
                "password": row["password"] or "",
                "login_status": row["login_status"] or "pending",
                "profile_id": row["profile_id"] or "",
                "proxy_label": row["proxy_label"] or "",
                "snap_status": row["snap_status"] or "none",
                "group_name": row["group_name"] or "",
                "last_error": row["last_error"] or "",
                "created_at": int(row["created_at"] or 0),
                "updated_at": int(row["updated_at"] or 0),
                "codes": by_email.get(row["email"], []),
            }
        )
    return out


def upsert_gmail_login(email: str, password: str, codes: list[str]) -> str:
    """Insert or update a login account + its backup codes.

    Returns "added" or "updated". Existing per-code `used` flags are preserved
    when the same code string is re-imported at the same position.
    """
    e = (email or "").strip().lower()
    if not e:
        raise ValueError("email required")
    norm_codes = [str(c).strip() for c in codes if str(c).strip()]
    with db() as conn:
        existing = conn.execute("SELECT email FROM gmail_login WHERE email = ?", (e,)).fetchone()
        conn.execute(
            """
            INSERT INTO gmail_login (email, password, created_at, updated_at)
            VALUES (?, ?, strftime('%s','now'), strftime('%s','now'))
            ON CONFLICT(email) DO UPDATE SET
                password = excluded.password,
                updated_at = excluded.updated_at
            """,
            (e, password or ""),
        )
        # Preserve used-state for codes that already exist at the same ordinal.
        prev = {
            (int(r["ordinal"])): (r["code"], int(r["used"]), r["used_at"])
            for r in conn.execute(
                "SELECT ordinal, code, used, used_at FROM gmail_backup_code WHERE email = ?",
                (e,),
            ).fetchall()
        }
        conn.execute("DELETE FROM gmail_backup_code WHERE email = ?", (e,))
        for i, code in enumerate(norm_codes):
            keep_used, keep_at = 0, None
            if i in prev and prev[i][0] == code:
                keep_used, keep_at = prev[i][1], prev[i][2]
            conn.execute(
                "INSERT INTO gmail_backup_code (email, ordinal, code, used, used_at) VALUES (?, ?, ?, ?, ?)",
                (e, i, code, keep_used, keep_at),
            )
    return "updated" if existing else "added"


def set_gmail_login_fields(email: str, **fields: Any) -> None:
    e = (email or "").strip().lower()
    if not e:
        return
    allowed = {
        "password",
        "login_status",
        "profile_id",
        "proxy_label",
        "snap_status",
        "group_name",
        "last_error",
    }
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    cols = ", ".join(f"{k} = ?" for k in sets)
    vals = list(sets.values())
    with db() as conn:
        conn.execute(
            f"UPDATE gmail_login SET {cols}, updated_at = strftime('%s','now') WHERE email = ?",
            (*vals, e),
        )


def set_backup_code_used(email: str, ordinal: int, used: bool) -> None:
    e = (email or "").strip().lower()
    with db() as conn:
        conn.execute(
            """
            UPDATE gmail_backup_code
            SET used = ?, used_at = CASE WHEN ? THEN strftime('%s','now') ELSE NULL END
            WHERE email = ? AND ordinal = ?
            """,
            (1 if used else 0, 1 if used else 0, e, int(ordinal)),
        )


def remove_gmail_login(email: str) -> bool:
    e = (email or "").strip().lower()
    with db() as conn:
        cur = conn.execute("DELETE FROM gmail_login WHERE email = ?", (e,))
        conn.execute("DELETE FROM gmail_backup_code WHERE email = ?", (e,))
    return cur.rowcount > 0


def _row_to_proxy(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw = data.get("config_json")
    if raw:
        try:
            data["config"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data["config"] = {}
    else:
        data["config"] = {}
    data["disabled"] = bool(data.get("disabled"))
    data["success_count"] = int(data.get("success_count") or 0)
    data["fail_streak"] = int(data.get("fail_streak") or 0)
    data["cap_override"] = (
        int(data["cap_override"]) if data.get("cap_override") is not None else None
    )
    data["added_at"] = int(data.get("added_at") or 0)
    data["updated_at"] = int(data.get("updated_at") or 0)
    return data


def proxy_pool_rows() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM proxy_pool ORDER BY added_at ASC, key ASC"
        ).fetchall()
    return [_row_to_proxy(row) for row in rows]


def get_proxy(key: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM proxy_pool WHERE key = ?", (key,)).fetchone()
    return _row_to_proxy(row) if row else None


def upsert_proxy(
    key: str,
    raw_line: str,
    config_json: Any,
    geo_label: str = "",
    cap_override: int | None = None,
) -> None:
    payload = _as_json(config_json) or "{}"
    cap = int(cap_override) if cap_override is not None else None
    with db() as conn:
        conn.execute(
            """
            INSERT INTO proxy_pool (
                key, raw_line, config_json, geo_label, cap_override,
                success_count, disabled, added_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, 0, strftime('%s','now'), strftime('%s','now'))
            ON CONFLICT(key) DO UPDATE SET
                raw_line = excluded.raw_line,
                config_json = excluded.config_json,
                geo_label = excluded.geo_label,
                cap_override = excluded.cap_override,
                updated_at = excluded.updated_at
            """,
            (key, raw_line, payload, geo_label or "", cap),
        )


def set_proxy_disabled(key: str, flag: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE proxy_pool SET disabled = ?, updated_at = strftime('%s','now') WHERE key = ?",
            (1 if flag else 0, key),
        )


def delete_proxy(key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM proxy_pool WHERE key = ?", (key,))


def increment_proxy_success(key: str, n: int = 1) -> None:
    # A success also clears the consecutive-failure streak.
    with db() as conn:
        conn.execute(
            "UPDATE proxy_pool SET success_count = success_count + ?, fail_streak = 0, updated_at = strftime('%s','now') WHERE key = ?",
            (int(n), key),
        )


def set_proxy_success(key: str, n: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE proxy_pool SET success_count = ?, updated_at = strftime('%s','now') WHERE key = ?",
            (max(0, int(n)), key),
        )


def increment_proxy_fail(key: str, n: int = 1) -> int:
    """Atomically bump the consecutive-failure streak; return the new streak."""
    with db() as conn:
        conn.execute(
            "UPDATE proxy_pool SET fail_streak = fail_streak + ?, updated_at = strftime('%s','now') WHERE key = ?",
            (int(n), key),
        )
        row = conn.execute(
            "SELECT fail_streak FROM proxy_pool WHERE key = ?", (key,)
        ).fetchone()
    return int(row["fail_streak"]) if row else 0


def reset_proxy_fail(key: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE proxy_pool SET fail_streak = 0, updated_at = strftime('%s','now') WHERE key = ?",
            (key,),
        )
