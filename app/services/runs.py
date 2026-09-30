from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.ads import AdsPowerError
from app.ads.proxies import parse_proxy_block, random_netlox_us
from app.config import DATA_DIR
from app.ads.fingerprints import preferred_chrome_kernel, resolve_fingerprint
from app.inject.identity import build_identity, normalize_gender
from app.inject.snapchat import SNAPCHAT_SIGNUP_URL, run_page_action
from app.mail import claim_unused_mailbox, mark_gmail_used, pool_snapshot, wait_for_snapchat_otp
from app.mail import anymessage
from app.mail.anymessage import AnyMessageError
from app.mail import diddysms
from app.mail.diddysms import DiddySmsError
from app.db import (
    delete_profile_cache,
    increment_proxy_fail,
    increment_proxy_success,
    set_profile_succeeded,
)
from app.services import load_runtime_settings, make_client
from app.services.proxy_pool import (
    get_fail_limit,
    next_available_proxy,
    pool_snapshot as proxy_pool_snapshot,
    reconcile_success_counts,
)
from app.services.profiles import (
    close_browser,
    create_one_profile,
    delete_profiles,
    ensure_empty_extension_category,
    extract_snap_web_url,
    list_dashboard_profiles,
    merge_proxy_into_profile,
    next_serial,
    open_browser,
    update_credentials,
)
from app.services.quota import assert_can_create

_lock = threading.RLock()
_runs: dict[str, dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def snapshot(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"],
        "status": run["status"],
        "created_at": run["created_at"],
        "payload": {
            k: v
            for k, v in (run.get("payload") or {}).items()
            if k not in {"password", "fakey", "proxy_list"}
        },
        "logs": list(run["logs"]),
        "results": list(run["results"]),
        "error": run.get("error"),
        "ok": run.get("ok", 0),
        "requested": run.get("requested", 0),
    }


def get_run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        run = _runs.get(run_id)
        return snapshot(run) if run else None


def current_run() -> dict[str, Any] | None:
    with _lock:
        if not _runs:
            return None
        latest = max(_runs.values(), key=lambda r: r["created_at"])
        return snapshot(latest)


def log(run: dict[str, Any], step: str, message: str, **extra: Any) -> None:
    entry = {"ts": _now(), "step": step, "message": message, **extra}
    with _lock:
        run["logs"].append(entry)


def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        busy = any(r["status"] in {"queued", "running", "cancelling"} for r in _runs.values())
        if busy:
            raise AdsPowerError("A run is already in progress. Wait or cancel it.")
        run = {
            "id": uuid.uuid4().hex[:10],
            "status": "queued",
            "created_at": _now(),
            "payload": payload,
            "logs": [],
            "results": [],
            "error": None,
            "ok": 0,
            "requested": int(payload.get("count") or 1),
            "cancel": False,
        }
        _runs[run["id"]] = run
    thread = threading.Thread(target=execute_run, args=(run["id"],), daemon=True)
    thread.start()
    return snapshot(run)


def cancel_run(run_id: str) -> dict[str, Any]:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            raise AdsPowerError("Run not found")
        run["cancel"] = True
        if run["status"] in {"queued", "running"}:
            run["status"] = "cancelling"
    log(run, "cancel", "Cancel requested — will stop after this profile")
    return snapshot(run)


def execute_run(run_id: str) -> None:
    with _lock:
        run = _runs[run_id]
        payload = dict(run["payload"])
        run["status"] = "running"
    client = make_client()
    try:
        log(run, "connect", "Checking AdsPower…")
        dashboard = list_dashboard_profiles(client, group_only=False)
        count = max(1, int(payload.get("count") or 1))
        assert_can_create(dashboard["quota"], count)
        tree = dashboard.get("group_tree") or {}
        # Fresh profiles land in the `| New` bucket, not the bare base group,
        # unless the caller passed an explicit AdsPower group (SMS Official, etc).
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            group_id = str(tree.get("new") or dashboard["group"].get("group_id"))
        sm_ids = {str(dashboard["group"].get("group_id"))} | {str(g) for g in tree.values() if g}
        prefix = (payload.get("name_prefix") or "SM").strip()
        name_gids = set(sm_ids)
        if group_id:
            name_gids.add(group_id)
        names = [
            str(p.get("name") or p.get("display_name") or "")
            for p in dashboard["profiles"]
            if str(p.get("group_id")) in name_gids
        ]
        serial = next_serial(prefix, names)
        proxy_mode = str(payload.get("proxy_mode") or "none")
        proxies = []
        if proxy_mode == "list":
            proxies = parse_proxy_block(
                payload.get("proxy_list") or "",
                default_type=str(payload.get("proxy_type") or ""),
            )
        pool_reserved: dict[str, int] = {}
        if proxy_mode == "pool":
            reconcile_success_counts(client)
            preflight_cfg, _pk = next_available_proxy()
            if preflight_cfg is None:
                raise AdsPowerError(
                    "All proxies are full or the pool is empty. Add proxies or raise the cap in Manage Proxy."
                )
            ppool = proxy_pool_snapshot()
            log(
                run,
                "proxy",
                f"Proxy pool · {ppool['available']} available of {ppool['total']} · cap {ppool['cap']}",
            )
        fingerprint_mode = str(payload.get("fingerprint_mode") or "random").lower()
        shared_fingerprint = None if fingerprint_mode == "random" else resolve_fingerprint(payload)
        action = str(payload.get("action") or "snapchat_signup")
        close_after = bool(payload.get("close_after", False))
        dwell = float(payload.get("dwell_seconds") or 6)
        start_url = (payload.get("start_url") or SNAPCHAT_SIGNUP_URL).strip()
        auto_username = bool(payload.get("auto_username", True))
        auto_password = bool(payload.get("auto_password", True))
        payload["auto_username"] = auto_username
        payload["auto_password"] = auto_password
        payload["auto_name"] = True
        if not payload.get("platform"):
            payload["platform"] = "snapchat.com"
        empty_cat = ensure_empty_extension_category(client)
        payload["sys_app_cate_id"] = empty_cat["category_id"]

        merge_after = (
            bool(payload.get("merge_after_create"))
            and action == "snapchat_signup"
            and str(payload.get("proxy_mode") or "") == "list"
            and bool(proxies)
        )
        log(
            run,
            "plan",
            f"{count} profile(s) · fingerprint={fingerprint_mode} · kernel=SunBrowser Chrome {preferred_chrome_kernel()} · action={action} · gender={normalize_gender(payload.get('bitmoji_gender'))} · extensions={empty_cat['category_name']}"
            + (" · signup on home IP, merge proxy before Chat" if merge_after else "")
            + f" · then {'close' if close_after else 'leave open'}",
        )
        conf = load_runtime_settings()
        otp_provider = (conf.get("otp_provider") or "imap").lower()
        if action == "snapchat_signup":
            if otp_provider == "diddysms":
                if not (conf.get("diddysms_key") or "").strip():
                    raise AdsPowerError("DiddySMS is selected but no API key is set in Settings.")
                try:
                    bal = diddysms.balance(conf["diddysms_key"])
                    svc = (conf.get("diddysms_service") or "snapchat").strip() or "snapchat"
                    info = diddysms.get_service(conf["diddysms_key"], svc)
                    log(
                        run,
                        "otp",
                        f"DiddySMS · ${bal:.2f} · {svc} ${info.get('price')} · stock {info.get('stock')}",
                    )
                except DiddySmsError as exc:
                    raise AdsPowerError(f"DiddySMS check failed: {exc}") from exc
            elif otp_provider == "anymessage":
                if not (conf.get("anymessage_token") or "").strip():
                    raise AdsPowerError("AnyMessage is selected but no API token is set in Settings.")
                try:
                    bal = anymessage.balance(conf["anymessage_token"])
                    log(run, "otp", f"AnyMessage · balance ${bal}")
                except AnyMessageError as exc:
                    raise AdsPowerError(f"AnyMessage token check failed: {exc}") from exc
            else:
                pool = pool_snapshot()
                log(run, "imap", f"Gmail pool · {pool['unused']} unused of {pool['total']}")
                if pool["unused"] < count:
                    raise AdsPowerError(
                        f"Need {count} unused Gmail(s) for Snapchat OTP, only {pool['unused']} in the pool. "
                        "Paste more accounts in Settings (email + app password)."
                    )

        for i in range(count):
            with _lock:
                stopping = bool(run["cancel"])
                if stopping:
                    run["status"] = "cancelled"
            if stopping:
                log(run, "cancel", "Stopped before next profile")
                return
            profile = None
            opened_id = None
            mailbox = None
            should_close = close_after
            should_delete = False
            assigned_proxy = None
            assigned_key = None
            pool_committed = False
            if i:
                time.sleep(5)
            if proxy_mode == "pool":
                assigned_proxy, assigned_key = next_available_proxy(pool_reserved)
                if assigned_proxy is None:
                    log(
                        run,
                        "proxy",
                        "Pool exhausted mid-run — no available proxy (all full, resting, or disabled). Stopping.",
                    )
                    break
                pool_reserved[assigned_key] = pool_reserved.get(assigned_key, 0) + 1
            try:
                profile = create_one_profile(
                    client,
                    payload,
                    index=i,
                    serial=serial + i,
                    group_id=group_id,
                    proxies=proxies,
                    shared_fingerprint=shared_fingerprint,
                    assigned_proxy=assigned_proxy,
                )
                if assigned_key:
                    log(
                        run,
                        "proxy",
                        f"Bound pool proxy · {profile.get('proxy_key') or assigned_key}",
                        profile_id=profile["profile_id"],
                    )
                log(
                    run,
                    "create",
                    f"Created {profile['name']} · {profile.get('os_name')} · {profile.get('kernel')}",
                    profile_id=profile["profile_id"],
                )
                if (
                    action == "snapchat_signup"
                    and otp_provider == "diddysms"
                    and payload.get("inject_netlox", True)
                ):
                    cfg, label = random_netlox_us()
                    merged = merge_proxy_into_profile(client, profile["profile_id"], cfg)
                    geo = merged.get("geo") or merged.get("label") or label
                    log(
                        run,
                        "proxy",
                        f"Netlox inject · {label}" + (f" · {geo}" if geo and geo != label else ""),
                        profile_id=profile["profile_id"],
                    )
                elif action == "snapchat_signup" and otp_provider == "diddysms":
                    log(
                        run,
                        "proxy",
                        "No proxy · direct (inject_netlox=false)",
                        profile_id=profile["profile_id"],
                    )
                time.sleep(3.0)
                opened = open_browser(
                    client,
                    profile["profile_id"],
                    headless=False,
                    timeout=900,
                    attempt_timeout=140,
                    on_wait=lambda msg, left: log(
                        run,
                        "wait",
                        f"{msg} · {left}s left",
                        profile_id=profile["profile_id"],
                    ),
                    should_stop=lambda: bool(run.get("cancel")),
                )
                opened_id = profile["profile_id"]
                ws = opened.get("puppeteer") or ""
                log(run, "open", "Browser open · CDP ready", profile_id=profile["profile_id"])
                action_result: dict[str, Any] = {"ok": True, "action": "none"}
                if action != "none":
                    if not ws:
                        raise AdsPowerError("AdsPower did not return a Playwright websocket")
                    shot = DATA_DIR / "runs" / run_id / f"{profile['profile_id']}.png"
                    bitmoji_gender = normalize_gender(payload.get("bitmoji_gender"))
                    ident = build_identity(
                        username=profile.get("username") or "",
                        password=profile.get("password") or "",
                        gender=bitmoji_gender,
                    )
                    mailbox = None
                    otp_waiter = None
                    email_provider = None
                    phone_provider = None
                    if action == "snapchat_signup":
                        if otp_provider == "diddysms":
                            diddy_key = conf["diddysms_key"]
                            diddy_svc = (conf.get("diddysms_service") or "snapchat").strip() or "snapchat"
                            try:
                                order = diddysms.buy_number(diddy_key, service=diddy_svc)
                            except DiddySmsError as exc:
                                raise AdsPowerError(f"DiddySMS buy failed: {exc}") from exc
                            mailbox = {
                                "provider": "diddysms",
                                "id": order["id"],
                                "phone_number": order["phone_number"],
                                "e164": order["e164"],
                                "service": order["service"],
                                "bought_at": time.time(),
                            }
                            log(
                                run,
                                "otp",
                                f"DiddySMS {order['e164']} · id {order['id']} · ${order.get('price')}",
                                profile_id=profile["profile_id"],
                            )

                            def otp_waiter(oid=order["id"], pid=profile["profile_id"]):
                                return diddysms.wait_for_sms(
                                    diddy_key,
                                    oid,
                                    timeout=300,
                                    poll=5,
                                    on_wait=lambda msg: log(run, "otp", msg, profile_id=pid),
                                    should_stop=lambda: bool(run.get("cancel")),
                                )

                            def phone_provider(pid=profile["profile_id"]):
                                old_id = (mailbox or {}).get("id")
                                bought = (mailbox or {}).get("bought_at")
                                if old_id:
                                    try:
                                        if diddysms.cancel_after_cooldown(
                                            diddy_key, old_id, bought_at=bought
                                        ):
                                            log(run, "otp", f"DiddySMS canceled stale · {old_id}", profile_id=pid)
                                    except Exception:
                                        pass
                                try:
                                    neworder = diddysms.buy_number(diddy_key, service=diddy_svc)
                                except DiddySmsError as exc:
                                    log(run, "otp", f"DiddySMS reorder failed · {exc}", profile_id=pid)
                                    return None
                                mailbox["id"] = neworder["id"]
                                mailbox["phone_number"] = neworder["phone_number"]
                                mailbox["e164"] = neworder["e164"]
                                mailbox["bought_at"] = time.time()
                                log(
                                    run,
                                    "otp",
                                    f"DiddySMS re-rented {neworder['e164']} · id {neworder['id']}",
                                    profile_id=pid,
                                )

                                def _w(oid=neworder["id"], p=pid):
                                    return diddysms.wait_for_sms(
                                        diddy_key,
                                        oid,
                                        timeout=300,
                                        poll=5,
                                        on_wait=lambda m: log(run, "otp", m, profile_id=p),
                                        should_stop=lambda: bool(run.get("cancel")),
                                    )

                                return {
                                    "phone_number": neworder["phone_number"],
                                    "otp_waiter": _w,
                                }
                        elif otp_provider == "anymessage":
                            try:
                                order = anymessage.order_email(
                                    conf["anymessage_token"],
                                    site=conf.get("anymessage_site") or "snapchat.com",
                                    domain=conf.get("anymessage_domain") or "gmail,gmail.com",
                                )
                            except AnyMessageError as exc:
                                raise AdsPowerError(f"AnyMessage order failed: {exc}") from exc
                            mailbox = {
                                "email": order["email"],
                                "provider": "anymessage",
                                "id": order["id"],
                                "site": order["site"],
                            }
                            log(
                                run,
                                "otp",
                                f"AnyMessage {mailbox['email']} · id {mailbox['id']}",
                                profile_id=profile["profile_id"],
                            )

                            def otp_waiter(aid=order["id"], pid=profile["profile_id"]):
                                return anymessage.wait_for_otp(
                                    conf["anymessage_token"],
                                    aid,
                                    timeout=240,
                                    poll=6,
                                    on_wait=lambda msg: log(run, "otp", msg, profile_id=pid),
                                )
                        else:
                            mailbox = claim_unused_mailbox(profile["profile_id"])
                            if not mailbox:
                                raise AdsPowerError("No unused Gmail left in the IMAP pool.")
                            mailbox["provider"] = "imap"
                            log(
                                run,
                                "imap",
                                f"Using {mailbox['email']}",
                                profile_id=profile["profile_id"],
                            )
                            since = time.time() - 15

                            def otp_waiter(acct=mailbox, started=since, pid=profile["profile_id"]):
                                return wait_for_snapchat_otp(
                                    acct,
                                    since=started,
                                    timeout=90,
                                    on_wait=lambda msg: log(run, "imap", msg, profile_id=pid),
                                )

                        # Called when Snapchat says the email was already used by
                        # another account. Rent/reorder a fresh one, mutate the
                        # mailbox in place so later remark/cancel logic stays in
                        # sync, and hand back a matching OTP waiter.
                        def email_provider(pid=profile["profile_id"]):
                            if not mailbox:
                                return None
                            if mailbox.get("provider") == "anymessage":
                                old_id = mailbox.get("id")
                                if old_id:
                                    try:
                                        if anymessage.cancel(conf.get("anymessage_token") or "", old_id):
                                            log(run, "otp", f"AnyMessage canceled stale · {old_id}", profile_id=pid)
                                    except Exception:
                                        pass
                                try:
                                    neworder = anymessage.order_email(
                                        conf["anymessage_token"],
                                        site=conf.get("anymessage_site") or "snapchat.com",
                                        domain=conf.get("anymessage_domain") or "gmail,gmail.com",
                                    )
                                except AnyMessageError as exc:
                                    log(run, "otp", f"AnyMessage reorder failed · {exc}", profile_id=pid)
                                    return None
                                mailbox["email"] = neworder["email"]
                                mailbox["id"] = neworder["id"]
                                mailbox["site"] = neworder["site"]
                                log(
                                    run,
                                    "otp",
                                    f"AnyMessage re-rented {neworder['email']} · id {neworder['id']}",
                                    profile_id=pid,
                                )

                                def _w(aid=neworder["id"], p=pid):
                                    return anymessage.wait_for_otp(
                                        conf["anymessage_token"],
                                        aid,
                                        timeout=240,
                                        poll=6,
                                        on_wait=lambda m: log(run, "otp", m, profile_id=p),
                                    )

                                return {"email": neworder["email"], "otp_waiter": _w}
                            # IMAP: claim another unused Gmail from the pool.
                            newmb = claim_unused_mailbox(pid)
                            if not newmb:
                                log(run, "imap", "No unused Gmail left to re-rent", profile_id=pid)
                                return None
                            mailbox["email"] = newmb["email"]
                            mailbox["app_password"] = newmb.get("app_password")
                            mailbox["provider"] = "imap"
                            log(run, "imap", f"Re-rented {newmb['email']}", profile_id=pid)
                            started2 = time.time() - 15

                            def _w(acct=dict(newmb), s=started2, p=pid):
                                acct["provider"] = "imap"
                                return wait_for_snapchat_otp(
                                    acct,
                                    since=s,
                                    timeout=90,
                                    on_wait=lambda m: log(run, "imap", m, profile_id=p),
                                )

                            return {"email": newmb["email"], "otp_waiter": _w}

                    def _on_step(msg: str, pid=profile["profile_id"]) -> None:
                        log(run, "action", msg, profile_id=pid)
                        mb = mailbox or {}
                        if msg == "typed_otp" and mb.get("provider") == "imap" and mb.get("email"):
                            mark_gmail_used(mb["email"], pid)
                            log(run, "imap", f"Marked used after OTP · {mb['email']}", profile_id=pid)
                        if msg == "typed_otp" and mb.get("provider") == "diddysms" and mb.get("id"):
                            try:
                                if diddysms.complete(conf.get("diddysms_key") or "", mb["id"]):
                                    log(run, "otp", f"DiddySMS completed · {mb['id']}", profile_id=pid)
                            except Exception:
                                pass

                    action_result = run_page_action(
                        ws,
                        action=action,
                        start_url=start_url,
                        username=ident["username"],
                        password=ident["password"],
                        first_name=ident["first_name"],
                        last_name=ident.get("last_name") or "",
                        birth_year=ident["birth_year"],
                        birth_month=ident["birth_month"],
                        birth_day=ident["birth_day"],
                        dwell_seconds=0 if merge_after else dwell,
                        screenshot_path=None if (merge_after or otp_provider == "diddysms") else shot,
                        email=(mailbox or {}).get("email") or "",
                        phone_number=(mailbox or {}).get("phone_number") or "",
                        otp_waiter=otp_waiter,
                        email_provider=email_provider,
                        phone_provider=phone_provider,
                        on_step=_on_step,
                        until="bitmoji" if merge_after else "",
                        bitmoji_gender=bitmoji_gender,
                    )
                    if action_result.get("close_profile"):
                        should_close = True
                    if action_result.get("delete_profile"):
                        should_close = True
                        should_delete = True
                    pending_proxy = profile.get("pending_proxy")
                    first_notes = action_result.get("notes") or []
                    if (
                        merge_after
                        and pending_proxy
                        and not should_delete
                        and (
                            "bitmoji_done" in first_notes
                            or "proxy_merge_ready" in first_notes
                        )
                    ):
                        log(
                            run,
                            "proxy",
                            "Bitmoji done · close, inject proxy, reopen for Chat",
                            profile_id=profile["profile_id"],
                        )
                        try:
                            close_browser(client, opened_id)
                            log(run, "close", "Closed for proxy merge", profile_id=opened_id)
                        except Exception as close_exc:
                            log(run, "error", f"Close before merge failed: {close_exc}", profile_id=opened_id)
                        time.sleep(2)
                        merged = merge_proxy_into_profile(client, opened_id, pending_proxy)
                        geo = merged.get("geo") or merged.get("label") or "proxy"
                        log(
                            run,
                            "proxy",
                            f"Merged {geo} · reopening",
                            profile_id=profile["profile_id"],
                        )
                        time.sleep(1.2)
                        opened = open_browser(
                            client,
                            profile["profile_id"],
                            headless=False,
                            on_wait=lambda msg, left: log(
                                run,
                                "wait",
                                f"{msg} · {left}s left",
                                profile_id=profile["profile_id"],
                            ),
                            should_stop=lambda: bool(run.get("cancel")),
                        )
                        ws = opened.get("puppeteer") or ""
                        if not ws:
                            raise AdsPowerError("AdsPower did not return a Playwright websocket after proxy merge")
                        web_result = run_page_action(
                            ws,
                            action="snapchat_web_onboard",
                            start_url="",
                            username=action_result.get("username") or ident["username"],
                            password=action_result.get("password") or ident["password"],
                            first_name=ident["first_name"],
                            last_name=ident.get("last_name") or "",
                            birth_year=ident["birth_year"],
                            birth_month=ident["birth_month"],
                            birth_day=ident["birth_day"],
                            dwell_seconds=0,
                            screenshot_path=shot,
                            on_step=_on_step,
                        )
                        action_result["notes"] = first_notes + list(web_result.get("notes") or [])
                        action_result["url"] = web_result.get("url") or action_result.get("url")
                        action_result["title"] = web_result.get("title") or action_result.get("title")
                        if web_result.get("close_profile"):
                            should_close = True
                    elif merge_after and pending_proxy and not should_delete:
                        log(
                            run,
                            "proxy",
                            "Skip proxy merge — Bitmoji did not finish",
                            profile_id=profile["profile_id"],
                        )
                    filled = action_result.get("filled") or []
                    notes = action_result.get("notes") or []
                    if mailbox and mailbox.get("provider") == "imap":
                        if "typed_otp" in notes:
                            mark_gmail_used(mailbox["email"], profile["profile_id"])
                        else:
                            log(
                                run,
                                "imap",
                                f"Left unused — OTP not typed · {mailbox['email']}",
                                profile_id=profile["profile_id"],
                            )
                    typed_user = action_result.get("username") or ident["username"]
                    typed_pass = action_result.get("password") or ident["password"]
                    used_mail = action_result.get("email") or ((mailbox or {}).get("email") or "")
                    log(
                        run,
                        "action",
                        f"{action} → {action_result.get('title') or action_result.get('url')}"
                        + (f" · typed {', '.join(filled)}" if filled else "")
                        + (f" · {', '.join(notes)}" if notes else ""),
                        profile_id=profile["profile_id"],
                    )
                    remark = (
                        f"SnappyMake run {run_id} · {ident['first_name']} · "
                        f"{ident['birth_year']}-{ident['birth_month']:02d}-{ident['birth_day']:02d}"
                    )
                    if used_mail:
                        remark += f" · Email: {used_mail}"
                        used_pass = (mailbox or {}).get("app_password") or ""
                        if used_pass:
                            remark += f" · Pass: {used_pass}"
                    if (mailbox or {}).get("provider") == "anymessage":
                        remark += f" · AMID: {mailbox.get('id')} · Site: {mailbox.get('site')}"
                    if (mailbox or {}).get("provider") == "diddysms":
                        phone = (
                            action_result.get("phone_number")
                            or mailbox.get("e164")
                            or mailbox.get("phone_number")
                            or ""
                        )
                        remark += f" · Phone: {phone} · DiddySMS: {mailbox.get('id')}"
                    page_url = str(action_result.get("url") or "")
                    web_url = extract_snap_web_url(page_url) or extract_snap_web_url(
                        " ".join(str(n) for n in (action_result.get("notes") or []))
                    )
                    if web_url:
                        remark += f" · {web_url}"
                    elif page_url:
                        remark += f" · {page_url}"
                    if not should_delete:
                        cred_payload: dict[str, Any] = {
                            "platform": "snapchat.com",
                            "username": typed_user,
                            "password": typed_pass,
                            "name": profile.get("name"),
                            "remark": remark,
                        }
                        if web_url:
                            cred_payload["tabs"] = [web_url]
                        update_credentials(
                            client,
                            profile["profile_id"],
                            cred_payload,
                        )
                if should_delete:
                    dnotes = action_result.get("notes") or []
                    if "email_rejected" in dnotes:
                        reason = "email_rejected"
                        human = "Email rejected by Snapchat"
                    elif "phone_rejected" in dnotes:
                        reason = "phone_rejected"
                        human = "Phone number rejected by Snapchat"
                    elif "process_error_stuck" in dnotes:
                        reason = "process_error_stuck"
                        human = "Process error stuck"
                    else:
                        reason = "signup_incomplete"
                        human = "Signup not verified (no OTP / welcome)"
                    # Reclaim the wasted AnyMessage / DiddySMS order so it doesn't cost balance.
                    if (mailbox or {}).get("provider") == "anymessage" and (mailbox or {}).get("id"):
                        try:
                            if anymessage.cancel(conf.get("anymessage_token") or "", mailbox["id"]):
                                log(
                                    run,
                                    "otp",
                                    f"AnyMessage order canceled · {mailbox['id']}",
                                    profile_id=profile["profile_id"],
                                )
                        except Exception:
                            pass
                    if (mailbox or {}).get("provider") == "diddysms" and (mailbox or {}).get("id"):
                        try:
                            if diddysms.cancel_after_cooldown(
                                conf.get("diddysms_key") or "",
                                mailbox["id"],
                                bought_at=mailbox.get("bought_at"),
                            ):
                                log(
                                    run,
                                    "otp",
                                    f"DiddySMS order canceled · {mailbox['id']}",
                                    profile_id=profile["profile_id"],
                                )
                        except Exception:
                            pass
                    log(
                        run,
                        "delete",
                        f"{human} · close and delete, continue",
                        profile_id=profile["profile_id"],
                    )
                    with _lock:
                        run["results"].append(
                            {
                                "profile_id": profile["profile_id"],
                                "name": profile["name"],
                                "deleted": True,
                                "reason": reason,
                                "action": action_result,
                            }
                        )
                else:
                    # Stamp the success time so age-based group bucketing can anchor
                    # to when the signup/onboard actually succeeded (covers both the
                    # direct and merge-after flows, which converge here).
                    try:
                        set_profile_succeeded(profile["profile_id"])
                    except Exception:
                        pass
                    with _lock:
                        run["ok"] += 1
                        # Successful account — this proxy slot is now consumed and
                        # the failure streak is cleared (handled in the DB update).
                        if assigned_key:
                            increment_proxy_success(assigned_key)
                            pool_committed = True
                        run["results"].append(
                            {
                                "profile_id": profile["profile_id"],
                                "name": profile["name"],
                                "os_name": profile.get("os_name"),
                                "kernel": profile.get("kernel"),
                                "proxy_key": profile.get("proxy_key"),
                                "action": action_result,
                            }
                        )
            except Exception as exc:
                log(run, "error", str(exc), profile_id=(profile or {}).get("profile_id"))
                # Timeout / crash / open fail: do not leave a dead shell in the
                # warming group eating AdsPower disk. Close + wipe + delete.
                if profile and profile.get("profile_id") and action == "snapchat_signup":
                    should_close = True
                    should_delete = True
                    if not opened_id:
                        opened_id = str(profile["profile_id"])
                    log(
                        run,
                        "delete",
                        f"Fail cleanup · {type(exc).__name__}: {str(exc)[:120]}",
                        profile_id=opened_id,
                    )
                    if (mailbox or {}).get("provider") == "diddysms" and (mailbox or {}).get("id"):
                        try:
                            if diddysms.cancel_after_cooldown(
                                conf.get("diddysms_key") or "",
                                mailbox["id"],
                                bought_at=mailbox.get("bought_at"),
                            ):
                                log(
                                    run,
                                    "otp",
                                    f"DiddySMS order canceled · {mailbox['id']}",
                                    profile_id=opened_id,
                                )
                        except Exception:
                            pass
                    if (mailbox or {}).get("provider") == "anymessage" and (mailbox or {}).get("id"):
                        try:
                            if anymessage.cancel(conf.get("anymessage_token") or "", mailbox["id"]):
                                log(
                                    run,
                                    "otp",
                                    f"AnyMessage order canceled · {mailbox['id']}",
                                    profile_id=opened_id,
                                )
                        except Exception:
                            pass
                with _lock:
                    run["results"].append({"error": str(exc), "index": i})
                    if isinstance(exc, AdsPowerError) and exc.is_quota:
                        run["status"] = "error"
                        run["error"] = str(exc)
                        return
            finally:
                # Always free the in-flight reservation. On success we already
                # incremented success_count, so the slot stays consumed; on any
                # failure/delete the slot is freed for the next profile.
                if assigned_key:
                    if not pool_committed:
                        # Profile did not succeed (delete/failure/exception) —
                        # bump the consecutive-failure streak exactly once.
                        streak = increment_proxy_fail(assigned_key)
                        limit = get_fail_limit()
                        if streak - 1 < limit <= streak:
                            log(
                                run,
                                "proxy",
                                f"Proxy resting after {streak} fails · {assigned_key}",
                            )
                    pool_reserved[assigned_key] = max(
                        0, pool_reserved.get(assigned_key, 0) - 1
                    )
                if should_close and opened_id:
                    try:
                        close_browser(client, opened_id)
                        log(run, "close", f"Closed {opened_id}", profile_id=opened_id)
                    except Exception as close_exc:
                        log(run, "error", f"Close failed: {close_exc}", profile_id=opened_id)
                if should_delete and opened_id:
                    # Free local AdsPower disk before / after profile delete.
                    try:
                        client.delete_profile_cache(
                            [opened_id],
                            types=[
                                "local_storage",
                                "indexeddb",
                                "extension_cache",
                                "history",
                                "image_file",
                                "cookie",
                            ],
                        )
                        log(run, "cache", f"Cleared cache {opened_id}", profile_id=opened_id)
                    except Exception as cache_exc:
                        log(
                            run,
                            "cache",
                            f"Cache clear warn: {cache_exc}",
                            profile_id=opened_id,
                        )
                    try:
                        delete_profiles(client, [opened_id])
                        log(run, "delete", f"Deleted {opened_id}", profile_id=opened_id)
                    except Exception as del_exc:
                        try:
                            client.delete_profiles([opened_id])
                            log(run, "delete", f"Deleted {opened_id}", profile_id=opened_id)
                        except Exception:
                            log(run, "error", f"Delete failed: {del_exc}", profile_id=opened_id)
                    try:
                        delete_profile_cache([opened_id])
                    except Exception:
                        pass
                # Push dashboard mirror after each profile finishes (success or delete).
                try:
                    from app.services.sheets import schedule_sync
                    from app.services import make_client as _make_client

                    schedule_sync(_make_client)
                except Exception:
                    pass
        with _lock:
            run["status"] = "done"
        log(run, "done", f"Finished {run['ok']}/{count}")
        try:
            from app.services.sheets import schedule_sync
            from app.services import make_client as _make_client

            schedule_sync(_make_client, delay=1.0)
        except Exception:
            pass
    except Exception as exc:
        with _lock:
            run["status"] = "error"
            run["error"] = str(exc)
        log(run, "error", str(exc))
    finally:
        client.close()
