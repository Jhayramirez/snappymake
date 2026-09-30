"""Google Sheets live mirror for SnappyMake.

Tabs:
  1. AdsPower Profiles — dashboard rows + username/password + created/last open
  2. Proxy Pool Status — Manage Proxy table
  3. Official Profiles SMS Method — SnappyMake Official SMS group (DiddySMS)

Auth: service-account JSON (default `secrets/google-sheets.json`).
Updates are push-on-event (debounced full rewrite of both tabs).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.db import get_setting, set_setting

PROFILES_TAB = "AdsPower Profiles"
PROXY_TAB = "Proxy Pool Status"
SMS_OFFICIAL_TAB = "Official Profiles SMS Method"
SMS_OFFICIAL_GID = "10749351"
DEFAULT_CREDS = ROOT / "secrets" / "google-sheets.json"
DEFAULT_SHEET_ID = "1iBsEmI2ZMpQ2Vx5KnNjuz3z6upJIXdveMZ9P6KVMFrA"

EMAIL_RE = re.compile(r"Email:\s*([^\s·|]+)", re.I)
SMS_PHONE_RE = re.compile(r"Phone:\s*([+\d]+)", re.I)
SMS_DIDDY_RE = re.compile(r"DiddySMS:\s*(\S+)", re.I)
SMS_NAME_RE = re.compile(r"SnappyMake run \S+ ·\s*([^·]+?)\s*·")
SMS_SERIAL_RE = re.compile(r"SMS-(\d+)", re.I)

PROFILE_HEADERS = [
    "Profile No",
    "Name",
    "Life",
    "Profile ID",
    "Status",
    "Browser",
    "OS",
    "Proxy type",
    "Proxy",
    "Bucket",
    "Username",
    "Password",
    "Email",
    "Remark",
    "Cookie",
    "Created at",
    "Last open",
    "Succeeded at",
]

PROXY_HEADERS = [
    "Key",
    "Geo",
    "Using",
    "Cap",
    "Remaining",
    "Fails",
    "Status",
    "Disabled",
    "Raw line",
]

_lock = threading.Lock()
_timer: threading.Timer | None = None
_pending_client_factory = None


def sheets_enabled() -> bool:
    return (get_setting("sheets_enabled") or "0") == "1"


def sheets_id() -> str:
    return (get_setting("sheets_id") or DEFAULT_SHEET_ID).strip()


def sheets_creds_path() -> Path:
    raw = (get_setting("sheets_creds_path") or str(DEFAULT_CREDS)).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def set_sheets_config(
    *,
    enabled: bool | None = None,
    sheet_id: str | None = None,
    creds_path: str | None = None,
) -> dict[str, Any]:
    if enabled is not None:
        set_setting("sheets_enabled", "1" if enabled else "0")
    if sheet_id is not None:
        set_setting("sheets_id", sheet_id.strip())
    if creds_path is not None:
        set_setting("sheets_creds_path", creds_path.strip())
    return sheets_status()


def sheets_status() -> dict[str, Any]:
    path = sheets_creds_path()
    return {
        "enabled": sheets_enabled(),
        "sheet_id": sheets_id(),
        "creds_path": str(path),
        "creds_exists": path.is_file(),
        "profiles_tab": PROFILES_TAB,
        "proxy_tab": PROXY_TAB,
        "sms_official_tab": SMS_OFFICIAL_TAB,
    }


def _fmt_ts(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return ""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    # AdsPower sometimes returns ms.
    if n > 10_000_000_000:
        n = n // 1000
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return str(value)


def _email_from_remark(remark: str) -> str:
    match = EMAIL_RE.search(remark or "")
    return match.group(1).strip() if match else ""


def _unix_seconds(value: Any) -> int:
    if value in (None, "", 0, "0"):
        return 0
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    if n >= 100_000_000_000:
        n = n // 1000
    return n


def _age_label(created: Any) -> str:
    n = _unix_seconds(created)
    if not n:
        return ""
    sec = max(0, int(time.time()) - n)
    if sec < 3600:
        return "less than 1 hour"
    hours = sec // 3600
    if hours < 24:
        return "1 hour" if hours == 1 else f"{hours} hours"
    days = hours // 24
    return "1 day" if days == 1 else f"{days} days"


def _warmup_stage_label(remark: str) -> str:
    text = remark or ""
    if "Warm Up Stage : Done" in text:
        return "Done"
    if "Warm Up Stage : Stage 1 Done" in text or "Stage 1 Done" in text:
        return "Stage 1 Done"
    return ""


def _sms_is_good(remark: str) -> bool:
    text = remark or ""
    return "DiddySMS:" in text or "snapchat.com/web/" in text.lower()


def _sms_serial(profile: dict[str, Any]) -> int:
    raw = str(profile.get("profile_no") or "")
    if raw.isdigit():
        return int(raw)
    name = str(profile.get("name") or "")
    m = SMS_SERIAL_RE.search(name)
    return int(m.group(1)) if m else 0


def _sms_official_row(
    profile: dict[str, Any],
    open_ids: set[str],
    lives: dict[str, str] | None = None,
) -> list[Any]:
    from app.ads.proxies import summarize_proxy
    from app.services.profiles import extract_snap_web_url

    remark = str(profile.get("remark") or "")
    pid = str(profile.get("profile_id") or profile.get("user_id") or "")
    name = str(profile.get("name") or "")
    phone_m = SMS_PHONE_RE.search(remark)
    first_m = SMS_NAME_RE.search(remark)
    web = extract_snap_web_url(remark) or ""
    created = profile.get("created_time")
    proxy = profile.get("user_proxy_config") or {}
    life = str((lives or {}).get(pid) or profile.get("life") or "").strip().lower()
    if life == "logout":
        status = "Logged out"
        happened = "Login page. Account logged out."
    elif life == "dead":
        status = "Failed creation"
        happened = "Signup never finished. No Snapchat account (login/signup kick)."
    elif (time.time() - _unix_seconds(created)) >= 96 * 3600 and _unix_seconds(created):
        status = "Ready to use"
        happened = "4 days+. Account is ready to use."
    elif web:
        status = "Snapchat created"
        happened = "SMS worked and a Snapchat account was created."
    else:
        status = "Snapchat created"
        happened = "SMS signup finished (DiddySMS number used)."
    return [
        str(_sms_serial(profile) or profile.get("profile_no") or ""),
        pid,
        name,
        _fmt_ts(created),
        _age_label(created),
        _warmup_stage_label(remark),
        life if life in {"dead", "logout"} else (life or "live"),
        "Official SMS Method",
        "open" if pid in open_ids else "closed",
        status,
        happened,
        (first_m.group(1).strip() if first_m else ""),
        profile.get("username") or "",
        profile.get("password") or "",
        phone_m.group(1) if phone_m else "",
        summarize_proxy(proxy if isinstance(proxy, dict) else None),
        _fmt_ts(profile.get("last_open_time")),
    ]


def sync_sms_official_now(client=None, *, force: bool = False) -> dict[str, Any]:
    """Rewrite Official Profiles SMS Method from AdsPower group 10749351."""
    if not force and not sheets_enabled():
        return {"ok": True, "skipped": True, "reason": "disabled", **sheets_status()}

    status = sheets_status()
    if not status["creds_exists"]:
        return {"ok": False, "detail": f"Missing credentials at {status['creds_path']}", **status}
    sid = status["sheet_id"]
    if not sid:
        return {"ok": False, "detail": "Sheet ID not set", **status}

    own_client = client is None
    if own_client:
        from app.ads.client import AdsPowerClient
        from app.services import load_runtime_settings

        conf = load_runtime_settings()
        client = AdsPowerClient(conf["api_base"], conf.get("api_key") or "")
    try:
        profiles = list(client.list_profiles(group_id=SMS_OFFICIAL_GID) or [])
        try:
            open_ids = {str(x) for x in (client.local_active() or [])}
        except Exception:
            open_ids = set()
        from app.db import get_all_profile_life

        lives = get_all_profile_life(collapse_logout=False)
        good = [p for p in profiles if _sms_is_good(str(p.get("remark") or ""))]
        good.sort(key=_sms_serial)
        rows = [_sms_official_row(p, open_ids, lives) for p in good]
        gc = _client()
        spreadsheet = gc.open_by_key(sid)
        n = _write_tab(
            spreadsheet,
            SMS_OFFICIAL_TAB,
            SMS_OFFICIAL_HEADERS,
            rows,
            SMS_OFFICIAL_WIDTHS,
            highlight_alive=True,
        )
        return {
            "ok": True,
            "sheet_id": sid,
            "title": spreadsheet.title,
            "sms_official_tab": SMS_OFFICIAL_TAB,
            "sms_profiles": n,
            "sms_group_total": len(profiles),
            "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit",
            **status,
            "enabled": sheets_enabled(),
        }
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass


def _client():
    import gspread
    from google.oauth2.service_account import Credentials

    path = sheets_creds_path()
    if not path.is_file():
        raise FileNotFoundError(f"Service account JSON not found: {path}")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(path), scopes=scopes)
    return gspread.authorize(creds)


def _ensure_worksheet(spreadsheet, title: str, cols: int):
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=2000, cols=max(cols, 12))


def _col_letter(n: int) -> str:
    """1-based column index → A, B, … AA."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


# Pixel widths tuned so headers + values don't overlap / crush each other.
PROFILE_WIDTHS = {
    "Profile No": 90,
    "Name": 120,
    "Life": 70,
    "Profile ID": 150,
    "Status": 80,
    "Browser": 100,
    "OS": 110,
    "Proxy type": 100,
    "Proxy": 220,
    "Bucket": 90,
    "Username": 150,
    "Password": 140,
    "Email": 200,
    "Remark": 280,
    "Cookie": 70,
    "Created at": 150,
    "Last open": 150,
    "Succeeded at": 150,
}

PROXY_WIDTHS = {
    "Key": 260,
    "Geo": 140,
    "Using": 70,
    "Cap": 60,
    "Remaining": 90,
    "Fails": 70,
    "Status": 100,
    "Disabled": 80,
    "Raw line": 360,
}

SMS_OFFICIAL_HEADERS = [
    "Profile #",
    "Profile ID",
    "Name",
    "Created",
    "Age",
    "Warm up",
    "Life",
    "Method",
    "Browser",
    "Snapchat status",
    "What happened",
    "Snapchat name",
    "Snapchat username",
    "Password",
    "Phone",
    "Proxy",
    "Last open",
]

SMS_OFFICIAL_WIDTHS = {
    "Profile #": 90,
    "Profile ID": 130,
    "Name": 120,
    "Created": 170,
    "Age": 140,
    "Warm up": 140,
    "Life": 80,
    "Method": 160,
    "Browser": 80,
    "Snapchat status": 220,
    "What happened": 420,
    "Snapchat name": 130,
    "Snapchat username": 160,
    "Password": 140,
    "Phone": 150,
    "Proxy": 220,
    "Last open": 160,
}

# Short columns get centered; long text columns clip (ellipsis) so rows stay even.
CENTER_HEADERS = {
    "Profile No",
    "Profile #",
    "Life",
    "Status",
    "Browser",
    "Bucket",
    "Cookie",
    "Using",
    "Cap",
    "Remaining",
    "Fails",
    "Disabled",
    "Warm up",
    "Age",
    "Life",
}
CLIP_HEADERS = {"Proxy", "Remark", "Email", "Raw line", "Key", "User agent", "What happened"}


def _style_tab(
    spreadsheet,
    ws,
    headers: list[str],
    widths: dict[str, int],
    n_data: int,
    *,
    highlight_alive: bool = False,
) -> None:
    """Apply a clean readable look: frozen bold header, widths, filters, clip."""
    cols = len(headers)
    rows = max(n_data + 1, 2)  # include header
    sheet_id = ws.id
    end_col = _col_letter(cols)

    # Clear old basic filter then set a fresh one over the used range.
    requests: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.11, "green": 0.16, "blue": 0.22},
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "fontSize": 11,
                            "bold": True,
                            "fontFamily": "Manrope",
                        },
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "OVERFLOW_CELL",
                    }
                },
                "fields": (
                    "userEnteredFormat(backgroundColor,textFormat,"
                    "horizontalAlignment,verticalAlignment,wrapStrategy)"
                ),
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 34},
                "fields": "pixelSize",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(rows, 50),
                    "startColumnIndex": 0,
                    "endColumnIndex": cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "fontSize": 10,
                            "fontFamily": "IBM Plex Mono",
                        },
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                    }
                },
                "fields": "userEnteredFormat(textFormat,verticalAlignment,wrapStrategy)",
            }
        },
    ]

    # Dead rows sit on top of zebra so the light red always wins.
    if n_data > 0 and "Life" in headers:
        life_col = _col_letter(headers.index("Life") + 1)
        requests.append(
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "endRowIndex": rows,
                                "startColumnIndex": 0,
                                "endColumnIndex": cols,
                            }
                        ],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [
                                    {
                                        "userEnteredValue": (
                                            f'=OR(LOWER(${life_col}2)="dead",'
                                            f'LOWER(${life_col}2)="logout")'
                                        ),
                                    }
                                ],
                            },
                            "format": {
                                "backgroundColor": {
                                    "red": 1.0,
                                    "green": 0.82,
                                    "blue": 0.82,
                                }
                            },
                        },
                    },
                    "index": 0,
                }
            }
        )
    # Official mail only: confirmed still-inside rows sit under dead red.
    if n_data > 0 and highlight_alive and "Snapchat status" in headers:
        status_col = _col_letter(headers.index("Snapchat status") + 1)
        requests.append(
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "endRowIndex": rows,
                                "startColumnIndex": 0,
                                "endColumnIndex": cols,
                            }
                        ],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [
                                    {
                                        "userEnteredValue": (
                                            f'=OR(${status_col}2="Still inside",'
                                            f'${status_col}2="Ready to use")'
                                        ),
                                    }
                                ],
                            },
                            "format": {
                                "backgroundColor": {
                                    "red": 0.82,
                                    "green": 0.93,
                                    "blue": 0.85,
                                }
                            },
                        },
                    },
                    "index": 1 if "Life" in headers else 0,
                }
            }
        )
    if n_data > 0:
        zebra_index = 0
        if "Life" in headers:
            zebra_index += 1
        if highlight_alive and "Snapchat status" in headers:
            zebra_index += 1
        requests.append(
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "endRowIndex": rows,
                                "startColumnIndex": 0,
                                "endColumnIndex": cols,
                            }
                        ],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{"userEnteredValue": "=ISEVEN(ROW())"}],
                            },
                            "format": {
                                "backgroundColor": {
                                    "red": 0.96,
                                    "green": 0.97,
                                    "blue": 0.98,
                                }
                            },
                        },
                    },
                    "index": zebra_index,
                }
            }
        )

    for i, header in enumerate(headers):
        px = int(widths.get(header, 120))
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
        )
        if header in CENTER_HEADERS:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": max(rows, 50),
                            "startColumnIndex": i,
                            "endColumnIndex": i + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "horizontalAlignment": "CENTER",
                            }
                        },
                        "fields": "userEnteredFormat.horizontalAlignment",
                    }
                }
            )

    # Basic filter so columns are sortable/filterable.
    requests.append(
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": rows,
                        "startColumnIndex": 0,
                        "endColumnIndex": cols,
                    }
                }
            }
        }
    )

    # Drop any previous conditional rules that may pile up on re-sync.
    # (We add one zebra rule each sync — clear existing first.)
    try:
        meta = spreadsheet.fetch_sheet_metadata(
            params={"fields": "sheets(properties(sheetId),conditionalFormats)"}
        )
        for sheet in meta.get("sheets") or []:
            if sheet.get("properties", {}).get("sheetId") != sheet_id:
                continue
            rules = sheet.get("conditionalFormats") or []
            deletes = []
            for idx in range(len(rules) - 1, -1, -1):
                deletes.append(
                    {
                        "deleteConditionalFormatRule": {
                            "sheetId": sheet_id,
                            "index": idx,
                        }
                    }
                )
            requests = deletes + requests
    except Exception:
        pass

    spreadsheet.batch_update({"requests": requests})
    # Keep unused note of clip headers so intent is documented for readers.
    _ = (end_col, CLIP_HEADERS)


def _write_tab(
    spreadsheet,
    title: str,
    headers: list[str],
    rows: list[list[Any]],
    widths: dict[str, int],
    *,
    highlight_alive: bool = False,
) -> int:
    ws = _ensure_worksheet(spreadsheet, title, len(headers))
    values = [headers, *rows]
    # Wipe formatting + values so old merged cells / leftover styles don't
    # cause overlapping text on the next push.
    try:
        spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "updateCells": {
                            "range": {"sheetId": ws.id},
                            "fields": "userEnteredValue,userEnteredFormat,dataValidation,note",
                        }
                    },
                    {
                        "unmergeCells": {
                            "range": {"sheetId": ws.id},
                        }
                    },
                ]
            }
        )
    except Exception:
        try:
            ws.clear()
        except Exception:
            pass

    try:
        ws.resize(rows=max(len(values) + 20, 80), cols=len(headers))
    except Exception:
        pass

    ws.update(f"A1:{_col_letter(len(headers))}{len(values)}", values, value_input_option="RAW")
    try:
        _style_tab(
            spreadsheet, ws, headers, widths, len(rows), highlight_alive=highlight_alive
        )
    except Exception:
        # Data is already written — styling failure shouldn't fail the sync.
        pass
    return len(rows)


def _profile_row(p: dict[str, Any]) -> list[Any]:
    remark = p.get("remark") or ""
    return [
        p.get("profile_no") or "",
        p.get("display_name") or p.get("name") or "",
        p.get("life") or "",
        p.get("profile_id") or "",
        "open" if p.get("browser_open") else "closed",
        p.get("browser") or p.get("kernel") or "",
        p.get("os_name") or "",
        p.get("proxy_type") or "",
        p.get("proxy_label") or "",
        p.get("age_bucket") or p.get("current_bucket") or "",
        p.get("username") or "",
        p.get("password") or "",
        _email_from_remark(remark) or p.get("email") or "",
        remark,
        "yes" if p.get("cookie") else "",
        _fmt_ts(p.get("created_time")),
        _fmt_ts(p.get("last_open_time")),
        _fmt_ts(p.get("succeeded_at")),
    ]


def _proxy_row(r: dict[str, Any]) -> list[Any]:
    return [
        r.get("key") or "",
        r.get("geo") or "",
        r.get("using") if r.get("using") is not None else r.get("success_count") or 0,
        r.get("cap") or "",
        r.get("remaining") if r.get("remaining") is not None else "",
        r.get("fail_streak") or 0,
        r.get("status") or "",
        "yes" if r.get("disabled") else "",
        r.get("raw_line") or r.get("label") or "",
    ]


def sync_now(client=None, *, force: bool = False) -> dict[str, Any]:
    """Full rewrite of both tabs. No-op when disabled unless force=True (test/manual)."""
    if not force and not sheets_enabled():
        return {"ok": True, "skipped": True, "reason": "disabled", **sheets_status()}

    status = sheets_status()
    if not status["creds_exists"]:
        return {"ok": False, "detail": f"Missing credentials at {status['creds_path']}", **status}
    sid = status["sheet_id"]
    if not sid:
        return {"ok": False, "detail": "Sheet ID not set", **status}

    from app.services.profiles import list_dashboard_profiles
    from app.services.proxy_pool import pool_snapshot

    own_client = client is None
    if own_client:
        from app.ads.client import AdsPowerClient
        from app.services import load_runtime_settings

        conf = load_runtime_settings()
        client = AdsPowerClient(conf["api_base"], conf.get("api_key") or "")
    try:
        dashboard = list_dashboard_profiles(client, group_only=True)
        profiles = dashboard.get("profiles") or []
        pool = pool_snapshot(client=None)  # don't re-reconcile mid-event
        profile_rows = [_profile_row(p) for p in profiles]
        proxy_rows = [_proxy_row(r) for r in (pool.get("proxies") or [])]

        gc = _client()
        spreadsheet = gc.open_by_key(sid)
        n_profiles = _write_tab(
            spreadsheet, PROFILES_TAB, PROFILE_HEADERS, profile_rows, PROFILE_WIDTHS
        )
        n_proxies = _write_tab(
            spreadsheet, PROXY_TAB, PROXY_HEADERS, proxy_rows, PROXY_WIDTHS
        )
        sms = {}
        try:
            sms = sync_sms_official_now(client, force=force)
        except Exception:
            sms = {"ok": False, "sms_profiles": 0}
        return {
            "ok": True,
            "sheet_id": sid,
            "title": spreadsheet.title,
            "profiles": n_profiles,
            "proxies": n_proxies,
            "sms_profiles": sms.get("sms_profiles") or 0,
            "rotation": pool.get("rotation"),
            "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit",
            **status,
            "enabled": sheets_enabled(),
        }
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass


def schedule_sync(client_factory=None, delay: float = 2.0) -> None:
    """Debounced background push. Safe to call from many event sites."""
    if not sheets_enabled():
        return
    global _timer, _pending_client_factory
    with _lock:
        _pending_client_factory = client_factory
        if _timer is not None:
            _timer.cancel()

        def _run():
            factory = None
            with _lock:
                factory = _pending_client_factory
            client = None
            try:
                if factory:
                    client = factory()
                sync_now(client)
            except Exception:
                pass
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

        _timer = threading.Timer(delay, _run)
        _timer.daemon = True
        _timer.start()


def test_connection() -> dict[str, Any]:
    """Open the spreadsheet and list tabs — no rewrite."""
    status = sheets_status()
    if not status["creds_exists"]:
        return {"ok": False, "detail": f"Missing credentials at {status['creds_path']}", **status}
    if not status["sheet_id"]:
        return {"ok": False, "detail": "Sheet ID not set", **status}
    try:
        gc = _client()
        sh = gc.open_by_key(status["sheet_id"])
        tabs = [ws.title for ws in sh.worksheets()]
        return {
            "ok": True,
            "title": sh.title,
            "tabs": tabs,
            "url": f"https://docs.google.com/spreadsheets/d/{status['sheet_id']}/edit",
            **status,
        }
    except Exception as exc:
        detail = str(exc) or repr(exc)
        cause = getattr(exc, "__cause__", None)
        if cause and str(cause):
            detail = f"{detail}: {cause}" if detail else str(cause)
        return {"ok": False, "detail": detail or "Sheets connection failed", **status}
