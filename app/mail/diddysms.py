"""DiddySMS (api.diddysms.com) temporary US numbers for SMS OTP.

Buy a number for a service (Snapchat = ``snapchat``), poll until ``sms_code``
lands, then complete. Cancel unused orders (60s cooldown after purchase).
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

API_BASE = "https://api.diddysms.com/v1"
DEFAULT_SERVICE = "snapchat"
CANCEL_COOLDOWN_S = 61.0


class DiddySmsError(RuntimeError):
    pass


def _require_key(key: str) -> str:
    text = (key or "").strip()
    if not text:
        raise DiddySmsError("Missing DiddySMS API key (set it in Settings).")
    return text


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_key(key)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _unwrap(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DiddySmsError(f"Unexpected DiddySMS response: {data!r}"[:240])
    detail = data.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        err = detail["error"]
        code = err.get("code") or "ERROR"
        msg = err.get("message") or str(err)
        raise DiddySmsError(f"{code}: {msg}")
    return data


def _request(
    method: str,
    path: str,
    key: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{API_BASE}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, headers=_headers(key), json=json_body)
    except httpx.HTTPError as exc:
        raise DiddySmsError(f"DiddySMS request failed: {exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise DiddySmsError(f"DiddySMS non-JSON ({resp.status_code}): {resp.text[:200]}") from exc
    if resp.status_code >= 400:
        try:
            _unwrap(data)
        except DiddySmsError:
            raise
        raise DiddySmsError(f"HTTP {resp.status_code}: {str(data)[:200]}")
    return _unwrap(data)


def balance(key: str) -> float:
    data = _request("GET", "/balance", key)
    try:
        return float(data.get("balance") or 0)
    except (TypeError, ValueError):
        return 0.0


def get_service(key: str, name: str = DEFAULT_SERVICE) -> dict[str, Any]:
    return _request("GET", f"/services/{name}", key)


def buy_number(key: str, *, service: str = DEFAULT_SERVICE) -> dict[str, Any]:
    data = _request("POST", "/orders", key, json_body={"service": service})
    order = data.get("order") if isinstance(data.get("order"), dict) else data
    phone = str(order.get("phone_number") or "").strip()
    oid = order.get("id")
    if not phone or oid in (None, ""):
        raise DiddySmsError(f"DiddySMS order missing phone/id: {order}")
    return {
        "id": oid,
        "phone_number": phone,
        "national": national_us(phone),
        "e164": e164_us(phone),
        "service": str(order.get("service") or service),
        "price": order.get("price"),
        "status": str(order.get("status") or ""),
        "created_at": str(order.get("created_at") or ""),
        "expires_at": str(order.get("expires_at") or ""),
        "sms_code": order.get("sms_code"),
    }


def get_order(key: str, order_id: Any) -> dict[str, Any]:
    data = _request("GET", f"/orders/{order_id}", key)
    order = data.get("order") if isinstance(data.get("order"), dict) else data
    return order


def complete(key: str, order_id: Any) -> bool:
    try:
        _request("POST", f"/orders/{order_id}/complete", key)
        return True
    except DiddySmsError:
        return False


def cancel(key: str, order_id: Any) -> bool:
    try:
        _request("POST", f"/orders/{order_id}/cancel", key)
        return True
    except DiddySmsError:
        return False


def cancel_after_cooldown(key: str, order_id: Any, *, bought_at: float | None = None) -> bool:
    """Orders cannot be cancelled within 60s of purchase."""
    if bought_at:
        wait = CANCEL_COOLDOWN_S - (time.time() - bought_at)
        if wait > 0:
            time.sleep(wait)
    return cancel(key, order_id)


def wait_for_sms(
    key: str,
    order_id: Any,
    *,
    timeout: float = 180,
    poll: float = 4,
    on_wait: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        if should_stop and should_stop():
            raise DiddySmsError("cancelled")
        attempt += 1
        left = int(deadline - time.time())
        if on_wait:
            on_wait(f"DiddySMS poll {attempt} · order {order_id} · {left}s left")
        order = get_order(key, order_id)
        code = str(order.get("sms_code") or "").strip()
        digits = "".join(ch for ch in code if ch.isdigit())
        if digits:
            return digits
        status = str(order.get("status") or "").lower()
        if status in {"cancelled", "canceled", "expired", "refunded"}:
            raise DiddySmsError(f"DiddySMS order {order_id} is {status}")
        time.sleep(poll)
    raise DiddySmsError(f"Timed out waiting for SMS on order {order_id}")


def national_us(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def e164_us(phone: str) -> str:
    nat = national_us(phone)
    return f"+1{nat}" if nat else ""
