from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.ads import AdsPowerError
from app.ads.client import AdsPowerClient
from app.ads.proxies import parse_proxy_block_report, test_proxy_block
from app.ads.fingerprints import kernel_catalog
from app.db import init_db
from app.inject import SnapchatInjector
from app.mail import add_accounts as add_gmail_accounts
from app.mail import fetch_inbox, fetch_message_body, list_folders, pool_snapshot, release_mailbox, remove_account, update_account
from app.mail import latest_signals_for
from app.mail import anymessage
from app.mail.anymessage import AnyMessageError
from app.mail.gmail_imap import GmailImapError
from app.services import load_runtime_settings, save_runtime_settings
from app.services import proxy_pool as proxy_pool_service
from app.services.profiles import (
    backfill_credential_remarks,
    backfill_snap_web_tabs,
    close_browser,
    create_profiles,
    delete_profiles,
    list_dashboard_profiles,
    open_browser,
    assign_profile_proxy,
    update_credentials,
    update_profile_life,
)
from app.services.quota import connection_snapshot
from app.services import runs as runs_service
from app.services.runs import cancel_run, current_run, get_run, start_run
from app.services.groups import bucket_counts, sync_groups
from app.services.sheets import (
    schedule_sync,
    set_sheets_config,
    sheets_status,
    sync_now,
    test_connection as sheets_test_connection,
)
from app.services import snaps21 as snaps21_service
from app.services import qa as qa_service
from app.services import gmail_login as gmail_login_service
from app.services import gmail_login_runner as gmail_login_runner_service

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def _asset_version() -> str:
    """Cache-busting token = newest mtime of the front-end assets, so browsers
    reload app.js / app.css automatically after an update."""
    latest = 0.0
    for rel in ("static/js/app.js", "static/js/gmail_login.js", "static/css/app.css"):
        try:
            latest = max(latest, (ROOT / rel).stat().st_mtime)
        except OSError:
            pass
    return str(int(latest))


GROUP_SYNC_INTERVAL_SECONDS = 20 * 60


def _run_in_progress() -> bool:
    with runs_service._lock:
        return any(
            r.get("status") in {"queued", "running", "cancelling"}
            for r in runs_service._runs.values()
        )


async def _group_sync_loop() -> None:
    """Background auto-mover: every ~20 min, if enabled and no run is active,
    move profiles into their age buckets. Gated by the `auto_move_enabled`
    setting (default OFF) and cancelled cleanly on shutdown."""
    while True:
        try:
            await asyncio.sleep(GROUP_SYNC_INTERVAL_SECONDS)
            conf = load_runtime_settings()
            if not conf.get("auto_move_enabled"):
                continue
            if _run_in_progress():
                continue  # never contend with a live run
            client = _client()
            try:
                await asyncio.to_thread(sync_groups, client, False)
            finally:
                client.close()
        except asyncio.CancelledError:
            break
        except Exception:
            # A transient AdsPower/DB hiccup must not kill the loop.
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    task = asyncio.create_task(_group_sync_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task


app = FastAPI(title="SnappyMake", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*|http://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


class SettingsIn(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    plan_cap: int | None = None
    group_name: str | None = None
    name_prefix: str | None = None
    cloud_token: str | None = None
    otp_provider: str | None = None
    anymessage_token: str | None = None
    anymessage_site: str | None = None
    anymessage_domain: str | None = None
    diddysms_key: str | None = None
    diddysms_service: str | None = None
    proxy_cap: int | None = None
    proxy_fail_limit: int | None = None
    bucket_warm_hours: int | None = None
    bucket_ready_hours: int | None = None
    auto_move_enabled: bool | None = None
    sheets_enabled: bool | None = None
    sheets_id: str | None = None
    sheets_creds_path: str | None = None
    snaps21_enabled: bool | None = None
    snaps21_base: str | None = None


class CreateIn(BaseModel):
    count: int = Field(1, ge=1, le=50)
    name_prefix: str = "SM"
    auto_name: bool = True
    name: str | None = None
    remark: str = "Created by SnappyMake"
    platform: str = ""
    username: str = ""
    password: str = ""
    fakey: str = ""
    auto_username: bool = False
    auto_password: bool = False
    username_prefix: str = "snap"
    os: str = "win11"
    kernel: str = "chrome"
    kernel_version: str = "auto"
    webrtc: str = "disabled"
    fingerprint_mode: str = "selective"
    tabs: str = ""
    cookie: str = ""
    proxy_mode: str = "none"
    proxy_list: str = ""
    proxy_id: str = "random"
    proxy_type: str = ""
    bitmoji_gender: str = "female"


class CredentialsIn(BaseModel):
    name: str | None = None
    username: str | None = None
    password: str | None = None
    fakey: str | None = None
    platform: str | None = None
    remark: str | None = None
    cookie: str | None = None


class InjectCommitIn(BaseModel):
    username: str = ""
    password: str = ""
    fakey: str = ""
    platform: str = "snapchat.com"
    name: str | None = None
    remark: str | None = None


class OpenIn(BaseModel):
    headless: bool = False


class DeleteIn(BaseModel):
    profile_ids: list[str] = Field(default_factory=list)


class LifeIn(BaseModel):
    status: str = ""


class ProfileProxyIn(BaseModel):
    proxy_list: str = ""
    proxy_type: str = ""
    clear: bool = False


class ProxyIn(BaseModel):
    proxy_list: str
    proxy_type: str = ""


class ProxyPoolAddIn(BaseModel):
    proxy_list: str
    proxy_type: str = ""
    test: bool = False


class ProxyPoolRemoveIn(BaseModel):
    key: str
    hard: bool = False


class ProxyPoolWakeIn(BaseModel):
    key: str


class ProxyCapIn(BaseModel):
    cap: int | None = Field(None, ge=1)
    fail_limit: int | None = Field(None, ge=1)
    rotation: str | None = None


class GroupsSyncIn(BaseModel):
    dry_run: bool = False


class GmailAccountsIn(BaseModel):
    text: str


class GmailEmailIn(BaseModel):
    email: str


class GmailUpdateIn(BaseModel):
    email: str
    new_email: str | None = None
    app_password: str | None = None


class GmailLoginImportIn(BaseModel):
    text: str
    exclude_used_group: bool = True


class GmailLoginCodeIn(BaseModel):
    email: str
    ordinal: int
    used: bool


class GmailLoginStatusIn(BaseModel):
    email: str
    login_status: str | None = None
    snap_status: str | None = None
    profile_id: str | None = None
    proxy_label: str | None = None
    last_error: str | None = None


class GmailLoginRunIn(BaseModel):
    inject_proxy: bool = True
    stop_at_first: bool = True


class RunIn(CreateIn):
    fingerprint_mode: str = "random"
    action: str = "snapchat_signup"
    start_url: str = "https://accounts.snapchat.com/v2/signup"
    close_after: bool = False
    merge_after_create: bool = False
    dwell_seconds: float = Field(6, ge=0, le=120)
    auto_username: bool = True
    auto_password: bool = True
    platform: str = "snapchat.com"
    group_id: str = ""


def _client() -> AdsPowerClient:
    conf = load_runtime_settings()
    return AdsPowerClient(conf["api_base"], conf["api_key"])


def _raise(exc: AdsPowerError) -> None:
    status = 409 if exc.is_quota or "already in progress" in str(exc).lower() else 502
    raise HTTPException(status_code=status, detail=str(exc))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"title": "SnappyMake", "asset_version": _asset_version()}
    )


@app.get("/api/state")
def state():
    conf = load_runtime_settings()
    client = _client()
    try:
        conn = connection_snapshot(client)
        if not conn["connected"]:
            return {
                "connection": conn,
                "settings": _public_settings(conf),
                "group": None,
                "profiles": [],
                "quota": {
                    "used": 0,
                    "cap": conf["plan_cap"] or None,
                    "remaining": None,
                    "unknown_cap": not conf["plan_cap"],
                    "group_used": 0,
                    "plan_label": "offline",
                },
                "run": current_run(),
                "kernels": kernel_catalog(),
                "gmail": pool_snapshot(),
            }
        data = list_dashboard_profiles(client)
        return {
            "connection": conn,
            "settings": _public_settings(conf),
            "run": current_run(),
            "kernels": kernel_catalog(),
            "gmail": pool_snapshot(),
            **data,
        }
    except AdsPowerError as exc:
        return {
            "connection": {
                "connected": conn.get("connected", False) if "conn" in locals() else False,
                "api_base": conf["api_base"],
                "message": str(exc),
            },
            "settings": _public_settings(conf),
            "group": None,
            "profiles": [],
            "quota": {
                "used": 0,
                "cap": conf["plan_cap"] or None,
                "remaining": None,
                "unknown_cap": not conf["plan_cap"],
                "group_used": 0,
                "plan_label": "error",
            },
            "run": current_run(),
            "kernels": kernel_catalog(),
            "gmail": pool_snapshot(),
        }
    finally:
        client.close()


@app.get("/gmail", response_class=HTMLResponse)
def gmail_page(request: Request):
    return templates.TemplateResponse(request, "gmail.html", {"title": "Gmail pool"})


@app.get("/gmail-login", response_class=HTMLResponse)
def gmail_login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "gmail_login.html",
        {"title": "Gmail Login", "asset_version": _asset_version()},
    )


@app.get("/qa", response_class=HTMLResponse)
def qa_page(request: Request):
    return templates.TemplateResponse(
        request, "qa.html", {"title": "SnapX QA", "asset_version": _asset_version()}
    )


class QaRunIn(BaseModel):
    profile_ids: list[str] | None = None
    bucket: str | None = None
    count: int = Field(1, ge=1, le=10)
    close_after: bool = True
    only_available: bool = True


class Snaps21ConfigIn(BaseModel):
    enabled: bool | None = None
    base: str | None = None


@app.get("/api/qa/profiles")
def qa_profiles(bucket: str | None = None):
    client = _client()
    try:
        b = (bucket or "").strip().lower()
        if b in {"", "all"}:
            b = None
        elif b not in {"new", "warm", "ready"}:
            raise HTTPException(status_code=400, detail="bucket must be all|new|warm|ready")
        return qa_service.list_qa_profiles(client, bucket=b)
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/qa/run")
def qa_run(payload: QaRunIn):
    return qa_service.start_qa_run(
        payload.profile_ids,
        bucket=payload.bucket,
        count=payload.count,
        close_after=payload.close_after,
        only_available=payload.only_available,
    )


@app.get("/api/qa/run/current")
def qa_run_current():
    return {"run": qa_service.current_qa_run()}


@app.get("/api/qa/run/{run_id}")
def qa_run_get(run_id: str):
    run = qa_service.get_qa_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": run}


@app.post("/api/qa/run/{run_id}/cancel")
def qa_run_cancel(run_id: str):
    return qa_service.cancel_qa_run(run_id)


@app.get("/api/qa/snaps21")
def qa_snaps21_status():
    return {
        "enabled": snaps21_service.snaps21_enabled(),
        "base": snaps21_service.get_base_url(),
        "ping": snaps21_service.ping(),
    }


@app.put("/api/qa/snaps21")
def qa_snaps21_config(payload: Snaps21ConfigIn):
    if payload.enabled is not None:
        snaps21_service.set_enabled(bool(payload.enabled))
    if payload.base is not None:
        snaps21_service.set_base_url(payload.base)
    return {
        "ok": True,
        "enabled": snaps21_service.snaps21_enabled(),
        "base": snaps21_service.get_base_url(),
        "ping": snaps21_service.ping(),
    }


def _gmail_pool():
    return pool_snapshot(include_secrets=True)


@app.get("/api/gmail")
def read_gmail_pool():
    return _gmail_pool()


@app.post("/api/gmail/accounts")
def add_gmail(payload: GmailAccountsIn):
    result = add_gmail_accounts(payload.text)
    return {**result, "pool": _gmail_pool()}


@app.patch("/api/gmail/accounts")
def patch_gmail(payload: GmailUpdateIn):
    try:
        saved = update_account(
            payload.email,
            email=payload.new_email,
            app_password=payload.app_password,
        )
    except GmailImapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **saved, "pool": _gmail_pool()}


@app.post("/api/gmail/accounts/release")
def release_gmail(payload: GmailEmailIn):
    release_mailbox(payload.email)
    return {"ok": True, "pool": _gmail_pool()}


@app.post("/api/gmail/accounts/remove")
def remove_gmail(payload: GmailEmailIn):
    return {"ok": remove_account(payload.email), "pool": _gmail_pool()}


@app.get("/api/gmail/folders")
def gmail_folders(account: str):
    try:
        return {"ok": True, "folders": list_folders(account)}
    except GmailImapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/gmail/messages")
def gmail_messages(account: str, folder: str = "INBOX"):
    try:
        messages, total = fetch_inbox(account, folder=folder, limit=30)
        return {"ok": True, "account": account, "folder": folder, "total": total, "messages": messages}
    except GmailImapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/gmail/message")
def gmail_message(account: str, folder: str = "INBOX", uid: str = ""):
    if not uid:
        raise HTTPException(status_code=400, detail="Missing message uid")
    try:
        return {"ok": True, **fetch_message_body(account, folder, uid)}
    except GmailImapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Gmail Login pool (password + backup codes) — separate from the IMAP pool
# --------------------------------------------------------------------------- #


@app.get("/api/gmail-login")
def read_gmail_login():
    return gmail_login_service.snapshot()


@app.post("/api/gmail-login/import")
def import_gmail_login(payload: GmailLoginImportIn):
    exclude: set[str] = set()
    exclude_error: str | None = None
    if payload.exclude_used_group:
        client = _client()
        try:
            exclude = gmail_login_service.excluded_emails(client)
        except AdsPowerError as exc:
            exclude_error = str(exc)  # AdsPower offline → import without exclusion
        finally:
            client.close()
    result = gmail_login_service.import_accounts(payload.text, exclude)
    if exclude_error:
        result["exclude_error"] = exclude_error
    return result


@app.post("/api/gmail-login/code")
def toggle_gmail_login_code(payload: GmailLoginCodeIn):
    return {"ok": True, "pool": gmail_login_service.toggle_code(
        payload.email, payload.ordinal, payload.used
    )}


@app.post("/api/gmail-login/status")
def set_gmail_login_status(payload: GmailLoginStatusIn):
    try:
        pool = gmail_login_service.set_status(
            payload.email,
            login_status=payload.login_status,
            snap_status=payload.snap_status,
            profile_id=payload.profile_id,
            proxy_label=payload.proxy_label,
            last_error=payload.last_error,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "pool": pool}


@app.post("/api/gmail-login/remove")
def remove_gmail_login(payload: GmailEmailIn):
    return gmail_login_service.remove(payload.email)


@app.post("/api/gmail-login/official-group")
def create_official_group():
    client = _client()
    try:
        return {"ok": True, **gmail_login_service.ensure_official_group(client)}
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.get("/api/gmail-login/run")
def gmail_login_run_status():
    return gmail_login_runner_service.status()


@app.post("/api/gmail-login/run/start")
def gmail_login_run_start(payload: GmailLoginRunIn):
    result = gmail_login_runner_service.start(
        inject_proxy=payload.inject_proxy, stop_at_first=payload.stop_at_first
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("detail", "already running"))
    return result


@app.post("/api/gmail-login/run/stop")
def gmail_login_run_stop():
    result = gmail_login_runner_service.stop()
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("detail", "not running"))
    return result


EMAIL_IN_REMARK = re.compile(r"Email:\s*([^\s·|]+@[^\s·|]+)", re.I)
AMID_IN_REMARK = re.compile(r"AMID:\s*([^\s·|]+)", re.I)
SITE_IN_REMARK = re.compile(r"Site:\s*([^\s·|]+)", re.I)


@app.get("/api/ext/context")
def ext_context():
    """Open AdsPower profiles + the email parsed from their remark."""
    client = _client()
    try:
        dashboard = list_dashboard_profiles(client, group_only=False)
    except AdsPowerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()
    accounts = []
    for prof in dashboard.get("profiles", []):
        if not prof.get("browser_open"):
            continue
        remark = prof.get("remark") or ""
        match = EMAIL_IN_REMARK.search(remark)
        am = AMID_IN_REMARK.search(remark)
        site_m = SITE_IN_REMARK.search(remark)
        accounts.append(
            {
                "profile_id": prof.get("profile_id"),
                "name": prof.get("display_name") or prof.get("name"),
                "email": match.group(1) if match else None,
                "provider": "anymessage" if am else "imap",
                "am_id": am.group(1) if am else "",
                "site": site_m.group(1) if site_m else "",
            }
        )
    return {"ok": True, "accounts": accounts}


@app.get("/api/ext/latest")
def ext_latest(account: str):
    account = (account or "").strip()
    if not account:
        raise HTTPException(status_code=400, detail="Missing account")
    try:
        return {"ok": True, **latest_signals_for(account)}
    except GmailImapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class BackfillIn(BaseModel):
    dry_run: bool = True


@app.post("/api/ext/backfill-remarks")
def ext_backfill_remarks(payload: BackfillIn):
    client = _client()
    try:
        return backfill_credential_remarks(client, dry_run=payload.dry_run)
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/ext/backfill-web-tabs")
def ext_backfill_web_tabs(payload: BackfillIn):
    """Pin each profile's remark Snapchat Web URL (…/web/<uuid>) as its AdsPower startup tab."""
    client = _client()
    try:
        return backfill_snap_web_tabs(client, dry_run=payload.dry_run)
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.put("/api/settings")
def update_settings(payload: SettingsIn):
    saved = save_runtime_settings(payload.model_dump(exclude_none=True))
    return _public_settings(saved)


@app.get("/api/diddysms/balance")
def diddysms_balance():
    from app.mail.diddysms import DiddySmsError, balance as diddy_balance, get_service

    conf = load_runtime_settings()
    key = (conf.get("diddysms_key") or "").strip()
    if not key:
        return {"ok": False, "detail": "No DiddySMS API key set in Settings."}
    try:
        svc = (conf.get("diddysms_service") or "snapchat").strip() or "snapchat"
        info = get_service(key, svc)
        return {
            "ok": True,
            "balance": diddy_balance(key),
            "service": info.get("name") or svc,
            "price": info.get("price"),
            "stock": info.get("stock"),
        }
    except DiddySmsError as exc:
        return {"ok": False, "detail": str(exc)}


@app.get("/api/anymessage/balance")
def anymessage_balance():
    conf = load_runtime_settings()
    token = (conf.get("anymessage_token") or "").strip()
    if not token:
        return {"ok": False, "detail": "No AnyMessage token set in Settings."}
    try:
        return {"ok": True, "balance": anymessage.balance(token)}
    except AnyMessageError as exc:
        return {"ok": False, "detail": str(exc)}


def _ext_am_token(token: str | None = None) -> str:
    """Token from query, else Settings (dashboard), used by extension endpoints."""
    conf = load_runtime_settings()
    return (token or conf.get("anymessage_token") or "").strip()


@app.get("/api/ext/anymessage/balance")
def ext_anymessage_balance(token: str | None = None):
    """Extension-facing balance check (same shape as the companion)."""
    tok = _ext_am_token(token)
    if not tok:
        raise HTTPException(status_code=400, detail="Missing AnyMessage token")
    try:
        return {"ok": True, "balance": anymessage.balance(tok)}
    except AnyMessageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ext/anymessage")
def ext_anymessage(
    email: str | None = None,
    site: str | None = None,
    id: str | None = None,
    token: str | None = None,
    reorder: str | None = None,
):
    """Extension-facing AnyMessage reorder/read (mirrors companion)."""
    tok = _ext_am_token(token)
    conf = load_runtime_settings()
    email_addr = (email or "").strip()
    aid = (id or "").strip()
    site_name = (site or conf.get("anymessage_site") or "snapchat.com").strip()
    reorder_q = (reorder or "").strip()
    do_reorder = (reorder_q == "1") if reorder_q else (not aid)
    if not tok:
        raise HTTPException(status_code=400, detail="Missing AnyMessage token")
    if not (email_addr or aid):
        raise HTTPException(status_code=400, detail="Provide email or id")
    try:
        result = anymessage.latest_signals(
            tok,
            activation_id=aid,
            email=email_addr,
            site=site_name,
            do_reorder=do_reorder,
        )
        return {"ok": True, **result, "reordered": do_reorder}
    except AnyMessageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/profiles")
def create(payload: CreateIn):
    client = _client()
    try:
        result = create_profiles(client, payload.model_dump())
        schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/profiles/delete")
def remove_profiles(payload: DeleteIn):
    client = _client()
    try:
        result = delete_profiles(client, payload.profile_ids)
        schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.patch("/api/profiles/{profile_id}")
def patch_profile(profile_id: str, payload: CredentialsIn):
    client = _client()
    try:
        result = update_credentials(client, profile_id, payload.model_dump(exclude_none=True))
        schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.patch("/api/profiles/{profile_id}/proxy")
def patch_profile_proxy(profile_id: str, payload: ProfileProxyIn):
    client = _client()
    try:
        result = assign_profile_proxy(
            client,
            profile_id,
            proxy_list=payload.proxy_list,
            proxy_type=payload.proxy_type,
            clear=payload.clear,
        )
        schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.patch("/api/profiles/{profile_id}/life")
def patch_profile_life(profile_id: str, payload: LifeIn):
    try:
        result = update_profile_life(profile_id, payload.status)
        schedule_sync(_client)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/profiles/{profile_id}/open")
def open_profile(profile_id: str, payload: OpenIn | None = None):
    client = _client()
    try:
        result = open_browser(client, profile_id, headless=bool(payload and payload.headless))
        schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/profiles/{profile_id}/close")
def close_profile(profile_id: str):
    client = _client()
    try:
        result = close_browser(client, profile_id)
        schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/profiles/{profile_id}/inject/start")
def inject_start(profile_id: str):
    client = _client()
    try:
        return SnapchatInjector(client).start_session(profile_id)
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/profiles/{profile_id}/inject/commit")
def inject_commit(profile_id: str, payload: InjectCommitIn):
    client = _client()
    try:
        return SnapchatInjector(client).commit_after_signup(profile_id, payload.model_dump())
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/proxy/parse")
def proxy_parse(payload: ProxyIn):
    return parse_proxy_block_report(payload.proxy_list, default_type=payload.proxy_type)


@app.post("/api/proxy/test")
def proxy_test(payload: ProxyIn):
    return test_proxy_block(payload.proxy_list, default_type=payload.proxy_type)


@app.get("/api/proxy/pool")
def read_proxy_pool():
    client = _client()
    try:
        proxy_pool_service.reconcile_success_counts(client)
        return proxy_pool_service.pool_snapshot()
    except AdsPowerError:
        # AdsPower offline — still return the stored pool without reconcile.
        return proxy_pool_service.pool_snapshot()
    finally:
        client.close()


@app.post("/api/proxy/pool")
def add_proxy_pool(payload: ProxyPoolAddIn):
    result = proxy_pool_service.add_proxies(
        payload.proxy_list,
        default_type=payload.proxy_type,
        test=payload.test,
    )
    schedule_sync(_client)
    return {**result, "pool": proxy_pool_service.pool_snapshot()}


@app.post("/api/proxy/pool/remove")
def remove_proxy_pool(payload: ProxyPoolRemoveIn):
    result = proxy_pool_service.remove_proxy(payload.key, hard=payload.hard)
    schedule_sync(_client)
    return {**result, "pool": proxy_pool_service.pool_snapshot()}


@app.post("/api/proxy/pool/wake")
def wake_proxy_pool(payload: ProxyPoolWakeIn):
    result = proxy_pool_service.wake_proxy(payload.key)
    schedule_sync(_client)
    return {**result, "pool": proxy_pool_service.pool_snapshot()}


@app.put("/api/proxy/pool/cap")
def set_proxy_pool_cap(payload: ProxyCapIn):
    cap = proxy_pool_service.get_cap()
    fail_limit = proxy_pool_service.get_fail_limit()
    rotation = proxy_pool_service.get_rotation()
    if payload.cap is not None:
        cap = proxy_pool_service.set_cap(payload.cap)
    if payload.fail_limit is not None:
        fail_limit = proxy_pool_service.set_fail_limit(payload.fail_limit)
    if payload.rotation is not None:
        rotation = proxy_pool_service.set_rotation(payload.rotation)
    schedule_sync(_client)
    return {"ok": True, "cap": cap, "fail_limit": fail_limit, "rotation": rotation}


@app.get("/api/groups")
def read_groups():
    client = _client()
    try:
        return bucket_counts(client)
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


@app.post("/api/groups/sync")
def sync_groups_endpoint(payload: GroupsSyncIn | None = None):
    client = _client()
    try:
        result = sync_groups(client, dry_run=bool(payload and payload.dry_run))
        if not (payload and payload.dry_run):
            schedule_sync(_client)
        return result
    except AdsPowerError as exc:
        _raise(exc)
    finally:
        client.close()


class SheetsConfigIn(BaseModel):
    enabled: bool | None = None
    sheet_id: str | None = None
    creds_path: str | None = None


@app.get("/api/sheets/status")
def read_sheets_status():
    return sheets_status()


@app.post("/api/sheets/test")
def test_sheets():
    return sheets_test_connection()


@app.post("/api/sheets/sync")
def sync_sheets():
    client = _client()
    try:
        # Manual sync always runs (force), even if the toggle is off — so the
        # first push works right after saving settings.
        return sync_now(client, force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()


@app.put("/api/sheets/config")
def put_sheets_config(payload: SheetsConfigIn):
    return set_sheets_config(
        enabled=payload.enabled,
        sheet_id=payload.sheet_id,
        creds_path=payload.creds_path,
    )


@app.post("/api/runs")
def create_run(payload: RunIn):
    try:
        return start_run(payload.model_dump())
    except AdsPowerError as exc:
        _raise(exc)


@app.get("/api/runs/current")
def read_current_run():
    return current_run() or {"status": "idle"}


@app.get("/api/runs/{run_id}")
def read_run(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/runs/{run_id}/cancel")
def stop_run(run_id: str):
    try:
        return cancel_run(run_id)
    except AdsPowerError as exc:
        _raise(exc)


def _mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "••••"
    return value[:3] + "••••" + value[-2:]


def _public_settings(conf: dict) -> dict:
    out = dict(conf)
    out["api_key"] = _mask(conf.get("api_key"))
    out["anymessage_token"] = _mask(conf.get("anymessage_token"))
    out["diddysms_key"] = _mask(conf.get("diddysms_key"))
    return out
