from __future__ import annotations

import random
import re
import string
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx

NETLOX_HOST = "proxy.netloxproxies.com"
NETLOX_PORT = "8080"
NETLOX_USER_PREFIX = "SL5PYGQLDA"
NETLOX_PASS = "kgywfgsjnm"
NETLOX_LIFE = "10080"

PROXY_TYPES = ("http", "https", "socks5", "socks4")


def parse_proxy_line(line: str, default_type: str = "") -> dict[str, str]:
    """Accept common proxy paste formats and return AdsPower user_proxy_config."""
    raw = line.strip()
    if not raw:
        raise ValueError("Empty proxy line")
    raw = raw.replace(" ", "")

    protocol = (default_type or "http").lower()
    user = ""
    password = ""
    host = ""
    port = ""

    if "://" not in raw:
        type_prefix = re.match(r"^(https?|socks5|socks4)[:|](.+)$", raw, re.I)
        if type_prefix:
            protocol = type_prefix.group(1).lower()
            raw = type_prefix.group(2)

    if "://" in raw:
        parsed = urlparse(raw)
        protocol = (parsed.scheme or protocol or "http").lower()
        user = parsed.username or ""
        password = parsed.password or ""
        host = parsed.hostname or ""
        port = str(parsed.port or "")
        if not port and parsed.netloc:
            maybe = parsed.netloc.split("@")[-1]
            if ":" in maybe:
                host, port = maybe.rsplit(":", 1)
                host = host.strip("[]")
    elif "@" in raw:
        left, right = raw.rsplit("@", 1)
        if left.count(":") == 1 and not left.startswith("["):
            host, port = left.split(":")
            if ":" in right:
                user, password = right.split(":", 1)
            else:
                user = right
        else:
            if ":" in left:
                user, password = left.split(":", 1)
            else:
                user = left
            host, port = _split_hostport(right)
    else:
        parts = raw.split(":")
        if len(parts) >= 2 and parts[-1].lower() in PROXY_TYPES:
            protocol = parts[-1].lower()
            parts = parts[:-1]
        if len(parts) == 2:
            host, port = parts
        elif len(parts) >= 4:
            if parts[0].lower() in PROXY_TYPES:
                protocol = parts[0].lower()
                host, port, user = parts[1], parts[2], parts[3]
                password = ":".join(parts[4:])
            else:
                host, port, user = parts[0], parts[1], parts[2]
                password = ":".join(parts[3:])
        else:
            raise ValueError(f"Unrecognized proxy format: {line.strip()}")

    if protocol == "socks4":
        protocol = "socks5"
    if protocol not in {"http", "https", "socks5"}:
        protocol = "http"
    if not host or not port:
        raise ValueError(f"Proxy needs host and port: {line.strip()}")
    if not str(port).isdigit():
        raise ValueError(f"Proxy port must be a number: {line.strip()}")

    config: dict[str, str] = {
        "proxy_soft": "other",
        "proxy_type": protocol,
        "proxy_host": host,
        "proxy_port": str(port),
    }
    if user:
        config["proxy_user"] = user
    if password:
        config["proxy_password"] = password
    tags = proxy_session_tags(config)
    if tags:
        config["proxy_geo"] = tags
    return config


_SESSION_TAG = re.compile(r"(?:^|_)(country|city|state|session|lifetime)-([^_]+)", re.I)


def proxy_session_tags(config: dict[str, Any] | None) -> dict[str, str]:
    """Read Mars-style sticky tags from user/password (country-us_city-abilene_session-…)."""
    if not config:
        return {}
    blob = f"{config.get('proxy_user') or ''}_{config.get('proxy_password') or ''}"
    tags: dict[str, str] = {}
    for match in _SESSION_TAG.finditer(blob):
        tags[match.group(1).lower()] = match.group(2).lower()
    return tags


def proxy_geo_label(config: dict[str, Any] | None) -> str:
    tags = proxy_session_tags(config)
    parts: list[str] = []
    if tags.get("country"):
        parts.append(tags["country"].upper())
    if tags.get("city"):
        parts.append(tags["city"].title())
    if tags.get("lifetime"):
        parts.append(f"sticky {tags['lifetime']}")
    elif tags.get("session"):
        parts.append("sticky")
    return " ".join(parts)


def parse_proxy_block(block: str, default_type: str = "") -> list[dict[str, str]]:
    report = parse_proxy_block_report(block, default_type=default_type)
    if report["errors"]:
        err = report["errors"][0]
        raise ValueError(f"Line {err['line']}: {err['error']}")
    if not report["ok"]:
        raise ValueError("No valid proxies in the list")
    return [item["config"] for item in report["ok"]]


def parse_proxy_block_report(block: str, default_type: str = "") -> dict[str, Any]:
    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, line in enumerate(block.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            config = parse_proxy_line(line, default_type=default_type)
            parsed.append({"line": index, "raw": line, "config": config, "label": summarize_proxy(config)})
        except ValueError as exc:
            errors.append({"line": index, "raw": line, "error": str(exc)})
    return {"ok": parsed, "errors": errors, "count": len(parsed)}


def no_proxy() -> dict[str, str]:
    return {"proxy_soft": "no_proxy"}


def random_netlox_us() -> tuple[dict[str, str], str]:
    """Sticky US Netlox session — same inject Official Gmail uses before Snap."""
    states = httpx.get(
        "https://netloxproxies.com/api/locations/countries/US/states",
        timeout=20,
    ).json()["states"]
    st = random.choice(states)
    cities = httpx.get(
        f"https://netloxproxies.com/api/locations/countries/US/states/{st['code']}/cities",
        timeout=20,
    ).json()["cities"]
    city = random.choice(cities)
    sess = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    user = f"{NETLOX_USER_PREFIX}_US-{st['code']}-{city['code']}_{NETLOX_LIFE}_{sess}"
    label = f"{st.get('name') or st['code']}/{city.get('name') or city['code']}"
    cfg = {
        "proxy_soft": "other",
        "proxy_type": "http",
        "proxy_host": NETLOX_HOST,
        "proxy_port": NETLOX_PORT,
        "proxy_user": user,
        "proxy_password": NETLOX_PASS,
    }
    return cfg, label


def summarize_proxy(config: dict[str, Any] | None) -> str:
    if not config:
        return "—"
    soft = config.get("proxy_soft") or "other"
    if soft == "no_proxy":
        return "no proxy"
    kind = proxy_type_of(config)
    host = config.get("proxy_host") or config.get("proxy_url") or ""
    port = config.get("proxy_port") or ""
    user = config.get("proxy_user") or ""
    auth = f"{user}@" if user else ""
    if host and port:
        return f"{kind}://{auth}{host}:{port}"
    if host:
        return f"{kind} {host}"
    return str(soft)


def proxy_type_of(config: dict[str, Any] | None) -> str:
    if not config:
        return "—"
    if (config.get("proxy_soft") or "") == "no_proxy":
        return "no_proxy"
    return str(config.get("proxy_type") or config.get("proxy_soft") or "http")


def httpx_proxy_url(config: dict[str, str]) -> str:
    kind = proxy_type_of(config)
    host = config.get("proxy_host") or ""
    port = config.get("proxy_port") or ""
    user = config.get("proxy_user") or ""
    password = config.get("proxy_password") or ""
    auth = ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    scheme = "socks5" if kind.startswith("socks") else "http"
    return f"{scheme}://{auth}{host}:{port}"


def _lookup_ip_country(ip: str, timeout: float = 6.0) -> str:
    """Best-effort country code for an exit IP (empty string on any failure)."""
    if not ip:
        return ""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode")
            resp.raise_for_status()
            body = resp.json()
        if body.get("status") == "success":
            return str(body.get("countryCode") or "").lower()
    except Exception:
        return ""
    return ""


def test_proxy(config: dict[str, str], timeout: float = 12.0) -> dict[str, Any]:
    started = time.monotonic()
    url = httpx_proxy_url(config)
    kind = proxy_type_of(config)
    try:
        with httpx.Client(proxy=url, timeout=timeout, follow_redirects=True) as client:
            response = client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            data = response.json()
        elapsed = round((time.monotonic() - started) * 1000)
        ip = data.get("ip") or ""
        # Cross-check: does the real exit country match the requested sticky tag?
        expected = (proxy_session_tags(config).get("country") or "").lower()
        actual = _lookup_ip_country(ip)
        geo_ok = True
        geo_note = ""
        if expected and actual:
            geo_ok = expected == actual
            geo_note = (
                f" · geo {actual.upper()} ✓"
                if geo_ok
                else f" · ⚠ geo mismatch: wanted {expected.upper()} got {actual.upper()}"
            )
        elif expected and not actual:
            geo_note = " · geo unverified"
        return {
            "ok": True,
            "proxy_type": kind,
            "label": summarize_proxy(config),
            "ip": ip,
            "country": actual,
            "expected_country": expected,
            "geo_ok": geo_ok,
            "ms": elapsed,
            "message": f"{kind} ok · {ip} · {elapsed}ms{geo_note}",
        }
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "proxy_type": kind,
            "label": summarize_proxy(config),
            "ip": "",
            "ms": elapsed,
            "message": str(exc),
        }


def test_proxy_block(block: str, default_type: str = "") -> dict[str, Any]:
    report = parse_proxy_block_report(block, default_type=default_type)
    results = []
    for item in report["ok"]:
        result = test_proxy(item["config"])
        result["line"] = item["line"]
        result["raw"] = item["raw"]
        results.append(result)
    return {
        "parsed": report["ok"],
        "parse_errors": report["errors"],
        "tests": results,
        "passed": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
    }


def _split_hostport(value: str) -> tuple[str, str]:
    value = value.strip().strip("[]")
    if value.count(":") == 1:
        host, port = value.split(":")
        return host, port
    raise ValueError(f"Expected host:port, got {value}")


def generate_profile_name(prefix: str, index: int) -> str:
    clean = (prefix or "SM").strip().replace(" ", "-")
    return f"{clean}-{index:04d}"


def generate_username(prefix: str = "snap", *, gender: str = "female") -> str:
    from app.inject.identity import generate_username as identity_username
    return identity_username(prefix, gender=gender)


def generate_password(length: int = 16) -> str:
    from app.inject.identity import generate_password as default_password
    return default_password(length)
