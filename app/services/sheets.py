"""Google Sheets live mirror for SnappyMake.

Two tabs:
  1. AdsPower Profiles — dashboard rows + username/password + created/last open
  2. Proxy Pool Status — Manage Proxy table

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
DEFAULT_CREDS = ROOT / "secrets" / "google-sheets.json"
DEFAULT_SHEET_ID = "1iBsEmI2ZMpQ2Vx5KnNjuz3z6upJIXdveMZ9P6KVMFrA"

EMAIL_RE = re.compile(r"Email:\s*([^\s·|]+)", re.I)

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

# Short columns get centered; long text columns clip (ellipsis) so rows stay even.
CENTER_HEADERS = {
    "Profile No",
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
}
CLIP_HEADERS = {"Proxy", "Remark", "Email", "Raw line", "Key", "User agent"}


def _style_tab(spreadsheet, ws, headers: list[str], widths: dict[str, int], n_data: int) -> None:
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

    # Alternating body rows for scanability.
    if n_data > 0:
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
                    "index": 0,
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
            for idx in range(len(rules) - 1, -1, -1):
                requests.insert(
                    0,
                    {
                        "deleteConditionalFormatRule": {
                            "sheetId": sheet_id,
                            "index": idx,
                        }
                    },
                )
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
        _style_tab(spreadsheet, ws, headers, widths, len(rows))
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
        return {
            "ok": True,
            "sheet_id": sid,
            "title": spreadsheet.title,
            "profiles": n_profiles,
            "proxies": n_proxies,
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
