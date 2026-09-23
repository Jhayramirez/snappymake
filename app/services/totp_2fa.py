"""2fa.cn-compatible TOTP (RFC 6238 / Google Authenticator).

2fa.cn is a browser TOTP generator: paste the authenticator secret (双重密钥),
get a 6-digit code that refreshes every 30 seconds. There is no public API, so
we generate the same codes locally instead of driving their website.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time
from urllib.parse import parse_qs, unquote, urlparse

_BASE32 = re.compile(r"^[A-Z2-7]+=*$")
_OTPAUTH_SECRET = re.compile(r"(?:^|[?&])secret=([^&]+)", re.I)


def normalize_secret(raw: str | None) -> str:
    """Accept otpauth:// URIs, spaced secrets, or plain Base32."""
    text = unquote(str(raw or "")).strip()
    if not text:
        return ""
    if text.lower().startswith("otpauth://"):
        qs = parse_qs(urlparse(text).query)
        text = (qs.get("secret") or [""])[0]
        if not text:
            m = _OTPAUTH_SECRET.search(str(raw or ""))
            text = unquote(m.group(1)) if m else ""
    secret = re.sub(r"[\s\-]", "", text).upper()
    if not secret:
        return ""
    pad = (-len(secret)) % 8
    if pad:
        secret += "=" * pad
    try:
        base64.b32decode(secret, casefold=True)
    except Exception:
        return ""
    return secret


def looks_like_totp_secret(raw: str | None) -> bool:
    text = str(raw or "").strip()
    if not text or "@" in text:
        return False
    if text.lower().startswith("otpauth://") and "secret=" in text.lower():
        return bool(normalize_secret(text))
    compact = re.sub(r"[\s\-]", "", text).upper()
    if not compact or compact.isdigit() or len(compact) < 16:
        return False
    if not _BASE32.match(compact + ("=" * ((-len(compact)) % 8))):
        return False
    return bool(normalize_secret(compact))


def remaining_seconds(period: int = 30, now: float | None = None) -> int:
    t = time.time() if now is None else now
    return period - (int(t) % period)


def generate_code(secret: str, *, now: float | None = None, digits: int = 6, period: int = 30) -> str:
    """Return the current 6-digit code (same algorithm as 2fa.cn / Google Authenticator)."""
    key_b32 = normalize_secret(secret)
    if not key_b32:
        raise ValueError("invalid TOTP secret")
    key = base64.b32decode(key_b32, casefold=True)
    counter = int((time.time() if now is None else now) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{truncated % (10 ** digits):0{digits}d}"


def next_code(secret: str, *, min_remaining: int = 4) -> str:
    """Wait out a nearly-expired window, then return a fresh code."""
    left = remaining_seconds()
    if left < min_remaining:
        time.sleep(left + 0.35)
    return generate_code(secret)
