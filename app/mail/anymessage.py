"""AnyMessage (anymessage.shop) short-term email provider.

An alternative to the Gmail/IMAP pool for fetching signup OTPs and
verification links. Short-term flow:

    order_email(site, domain) -> {id, email}
    (use that email to sign up)
    get_message(id)           -> HTML once the mail arrives
    reorder(email, site)      -> re-request the same address (new id)
    cancel(id)                -> drop an unused activation

Token is passed in by the caller (read from runtime settings), never hardcoded.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from app.mail.gmail_imap import (
    CODE_RE,
    _strip_html,
    extract_otp,
    extract_verification_link,
)

API_BASE = "https://api.anymessage.shop"
DEFAULT_SITE = "snapchat.com"
# GMAIL ONLY (by request). `domain` takes ONE domain token per order. We accept
# a comma list and try each in order until one has stock, but the list is hard-
# filtered to Gmail tokens and every ordered address is verified to be @gmail.com
# before we accept it. Invalid tokens return {"value":"domain"}; out-of-stock
# ones return {"value":"no emails"}.
DEFAULT_DOMAIN = "gmail,gmail.com"

# Only these domain tokens are ever ordered, no matter what the settings say.
GMAIL_TOKENS = ("gmail", "gmail.com")


class AnyMessageError(RuntimeError):
    pass


def _request(path: str, params: dict[str, Any], *, timeout: float = 20.0, raw: bool = False):
    url = f"{API_BASE}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AnyMessageError(f"AnyMessage request failed: {exc}") from exc
    if raw:
        return resp.text
    try:
        return resp.json()
    except ValueError as exc:
        raise AnyMessageError(f"AnyMessage returned non-JSON: {resp.text[:200]}") from exc


def _check(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AnyMessageError("Unexpected AnyMessage response")
    if data.get("status") == "error":
        raise AnyMessageError(str(data.get("value") or "unknown error"))
    return data


def _require_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise AnyMessageError("Missing AnyMessage API token (set it in Settings).")
    return token


def balance(token: str) -> float:
    data = _check(_request("/user/balance", {"token": _require_token(token)}))
    try:
        return float(data.get("balance") or 0)
    except (TypeError, ValueError):
        return 0.0


def order_email(
    token: str,
    *,
    site: str = DEFAULT_SITE,
    domain: str = DEFAULT_DOMAIN,
    regex: str = "",
    subject: str = "",
) -> dict[str, str]:
    """Order one mailbox for `site`.

    `domain` may be a comma-separated list; we try each token in order and
    return the first one that has stock. Raises with the last error otherwise.
    """
    token = _require_token(token)
    requested = [d.strip().lower() for d in (domain or "").replace(" ", "").split(",") if d.strip()]
    # GMAIL ONLY: drop anything that isn't a Gmail token, no matter what's asked.
    domains = [d for d in requested if d in GMAIL_TOKENS] or list(GMAIL_TOKENS)
    last_err = "no gmail stock"
    for d in domains:
        params: dict[str, Any] = {"token": token, "site": site, "domain": d}
        if regex:
            params["regex"] = regex
        if subject:
            params["subject"] = subject
        data = _request("/email/order", params)
        if isinstance(data, dict) and data.get("status") == "error":
            last_err = f"{d}: {data.get('value') or 'error'}"
            continue
        data = _check(data)
        addr = str(data.get("email") or "")
        # Safety net: never accept a non-Gmail address; cancel it and keep trying.
        if not addr.lower().endswith("@gmail.com"):
            last_err = f"{d}: non-gmail address {addr}"
            try:
                cancel(token, str(data.get("id") or ""))
            except Exception:
                pass
            continue
        return {
            "id": str(data.get("id") or ""),
            "email": addr,
            "site": site,
            "domain": d,
        }
    raise AnyMessageError(f"gmail order failed ({last_err})")


def get_message_html(token: str, activation_id: str) -> str | None:
    """Return the message HTML, or None while still waiting."""
    data = _request("/email/getmessage", {"token": _require_token(token), "id": str(activation_id)})
    if isinstance(data, dict) and data.get("status") == "error":
        value = str(data.get("value") or "")
        if value == "wait message":
            return None
        raise AnyMessageError(value or "no message")
    if isinstance(data, dict):
        msg = data.get("message")
        return msg if isinstance(msg, str) and msg.strip() else None
    return None


def reorder(
    token: str,
    *,
    activation_id: str = "",
    email: str = "",
    site: str = DEFAULT_SITE,
) -> dict[str, str]:
    """Re-request an email. By id, or by email+site."""
    params: dict[str, Any] = {"token": _require_token(token)}
    if activation_id:
        params["id"] = str(activation_id)
    elif email:
        params["email"] = email
        params["site"] = site
    else:
        raise AnyMessageError("reorder needs an activation id or email+site")
    data = _check(_request("/email/reorder", params))
    return {"id": str(data.get("id") or ""), "email": str(data.get("email") or ""), "site": site}


def cancel(token: str, activation_id: str) -> bool:
    try:
        _check(_request("/email/cancel", {"token": _require_token(token), "id": str(activation_id)}))
        return True
    except AnyMessageError:
        return False


def _otp_from_html(html: str) -> str | None:
    text = _strip_html(html or "")
    code = extract_otp("", text, "snapchat.com")
    if code:
        return code
    match = CODE_RE.search(text)
    if match and match.group(1) != "000000":
        return match.group(1)
    return None


def wait_for_otp(
    token: str,
    activation_id: str,
    *,
    timeout: float = 120,
    poll: float = 5,
    on_wait: Callable[[str], None] | None = None,
) -> str:
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        left = int(deadline - time.time())
        if on_wait:
            on_wait(f"AnyMessage poll {attempt} for {activation_id} · {left}s left")
        try:
            html = get_message_html(token, activation_id)
        except AnyMessageError as exc:
            if on_wait:
                on_wait(str(exc))
            raise
        if html:
            code = _otp_from_html(html)
            if code:
                return code
        time.sleep(poll)
    raise AnyMessageError(f"Timed out waiting for AnyMessage OTP on {activation_id}")


def latest_signals(
    token: str,
    *,
    activation_id: str = "",
    email: str = "",
    site: str = DEFAULT_SITE,
    do_reorder: bool = True,
) -> dict[str, Any]:
    """Fetch the newest link + OTP for an activation.

    If `do_reorder` and we only have email+site (or the current id has no
    message), re-request the same address to pull a fresh verification mail.
    """
    token = _require_token(token)
    aid = str(activation_id or "")
    html: str | None = None
    if aid:
        try:
            html = get_message_html(token, aid)
        except AnyMessageError:
            html = None
    if html is None and do_reorder and (email or aid):
        info = reorder(token, activation_id=aid, email=email, site=site)
        aid = info.get("id") or aid
        email = info.get("email") or email
        # give it a brief moment, then read once
        time.sleep(2)
        try:
            html = get_message_html(token, aid)
        except AnyMessageError:
            html = None
    link = extract_verification_link("", _strip_html(html or ""), html or "") if html else None
    otp = _otp_from_html(html) if html else None
    return {
        "id": aid,
        "email": email,
        "site": site,
        "link": link,
        "otp": otp,
        "has_message": bool(html),
    }
