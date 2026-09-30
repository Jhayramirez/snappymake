from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from app.ads.proxies import proxy_session_tags

OS_CHOICES = {
    "win11": "Windows 11",
    "win10": "Windows 10",
    "macos": "Mac OS X 13",
    "linux": "Linux",
}

KERNEL_CHOICES = ("chrome", "firefox")
WEBRTC_CHOICES = ("disabled", "proxy", "forward")
ENGLISH_PROXY_COUNTRIES = {"us", "usa", "gb", "uk", "ca", "au", "nz", "ie"}
CPU_CHOICES = ("4", "6", "8", "16")
RAM_CHOICES = ("4", "8")
CHROME_VERSIONS = tuple(f"{v}.0.0.0" for v in range(128, 142))
FIREFOX_VERSIONS = tuple(str(v) for v in range(128, 142))
KERNEL_ROOTS = (
    Path.home() / "Library/Application Support/adspower_global/cwd_global",
    Path.home() / ".config/adspower_global/cwd_global",
    # Windows AdsPower Global
    Path.home() / "AppData/Roaming/adspower_global/cwd_global",
    Path.home() / "AppData/Local/adspower_global/cwd_global",
    Path(os.environ.get("APPDATA", "")) / "adspower_global/cwd_global",
    Path(os.environ.get("LOCALAPPDATA", "")) / "adspower_global/cwd_global",
)


def installed_kernels(kind: str = "chrome") -> list[dict[str, Any]]:
    prefix = "chrome_" if kind == "chrome" else "firefox_"
    found: list[dict[str, Any]] = []
    now = time.time()
    seen: set[str] = set()
    for root in KERNEL_ROOTS:
        if not root.is_dir():
            continue
        for folder in root.glob(f"{prefix}*"):
            has_app = (folder / "SunBrowser.app").exists() or (folder / "chrome.exe").exists()
            has_fx = (folder / "FlowerBrowser.app").exists() or (folder / "firefox.exe").exists()
            if kind == "chrome" and not has_app:
                continue
            if kind == "firefox" and not has_fx:
                continue
            version = folder.name.split("_", 1)[-1]
            if not version.isdigit() or version in seen:
                continue
            age = now - folder.stat().st_mtime
            ready = age > 600 or any(folder.glob("browser_key_*"))
            seen.add(version)
            found.append({"version": version, "ready": ready, "kind": kind})
    found.sort(key=lambda item: int(item["version"]), reverse=True)
    return found


def installed_chrome_kernels() -> list[str]:
    ready = [item["version"] for item in installed_kernels("chrome") if item["ready"]]
    all_versions = [item["version"] for item in installed_kernels("chrome")]
    return ready or all_versions


def sanitize_kernel_version(value: Any) -> str:
    """AdsPower only accepts ua_auto | latest | major Chrome ints (e.g. 152).

    Full strings like 152.0.0.0 or unknown tokens get coerced.
    """
    raw = str(value or "").strip().lower()
    if raw in {"ua_auto", "latest"}:
        return raw
    if raw in {"", "auto", "installed", "unavailable", "none"}:
        return preferred_chrome_kernel()
    # 152.0.0.0 → 152
    major = raw.split(".", 1)[0]
    if major.isdigit() and 50 <= int(major) <= 999:
        return major
    return preferred_chrome_kernel()


def preferred_chrome_kernel() -> str:
    installed = installed_chrome_kernels()
    # Prefer a real on-disk kernel. If none found (common on fresh Windows VPS
    # before path scan / download), "latest" lets AdsPower pick — ua_auto alone
    # can fail on update/start on some AdsPower builds.
    return installed[0] if installed else "latest"


def sanitize_fingerprint(fp: dict[str, Any] | None) -> dict[str, Any]:
    """Ensure browser_kernel_config.version is AdsPower-legal before API calls."""
    out = dict(fp or {})
    cfg = dict(out.get("browser_kernel_config") or {})
    version = sanitize_kernel_version(cfg.get("version"))
    cfg["version"] = version
    cfg["type"] = "chrome"
    out["browser_kernel_config"] = cfg
    random_ua = dict(out.get("random_ua") or {})
    random_ua["ua_browser"] = ["chrome"]
    if version in {"ua_auto", "latest"}:
        random_ua.pop("ua_version", None)
    else:
        random_ua["ua_version"] = [version]
    out["random_ua"] = random_ua
    return out


def kernel_catalog() -> dict[str, Any]:
    chrome = installed_kernels("chrome")
    firefox = installed_kernels("firefox")
    preferred = preferred_chrome_kernel()
    options: list[dict[str, Any]] = []
    if preferred not in {"ua_auto", "latest"}:
        options.append(
            {
                "value": f"chrome:{preferred}",
                "kernel": "chrome",
                "version": preferred,
                "label": f"SunBrowser Chrome {preferred} · always latest downloaded",
            }
        )
    else:
        options.append(
            {
                "value": "auto",
                "kernel": "chrome",
                "version": preferred,
                "label": "SunBrowser · AdsPower latest (no Chrome kernel scanned on disk)",
            }
        )
    for item in chrome:
        if item["version"] == preferred:
            continue
        mark = "not used" if item["ready"] else "not ready"
        options.append(
            {
                "value": f"chrome:{item['version']}",
                "kernel": "chrome",
                "version": item["version"],
                "disabled": True,
                "label": f"SunBrowser Chrome {item['version']} ({mark})",
            }
        )
    for item in firefox:
        options.append(
            {
                "value": f"firefox:{item['version']}",
                "kernel": "firefox",
                "version": item["version"],
                "disabled": True,
                "label": f"FlowerBrowser Firefox {item['version']} (not used)",
            }
        )
    if not firefox:
        options.append(
            {
                "value": "firefox:unavailable",
                "kernel": "firefox",
                "version": "",
                "disabled": True,
                "label": "FlowerBrowser (not used)",
            }
        )
    return {
        "preferred": preferred,
        "chrome": chrome,
        "firefox": firefox,
        "options": options,
    }


def generate_user_agent(os_key: str, kernel: str) -> str:
    chrome = random.choice(CHROME_VERSIONS)
    firefox = random.choice(FIREFOX_VERSIONS)
    if kernel == "firefox":
        if os_key in {"win10", "win11"}:
            return (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{firefox}.0) "
                f"Gecko/20100101 Firefox/{firefox}.0"
            )
        if os_key == "macos":
            return (
                f"Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:{firefox}.0) "
                f"Gecko/20100101 Firefox/{firefox}.0"
            )
        return (
            f"Mozilla/5.0 (X11; Linux x86_64; rv:{firefox}.0) "
            f"Gecko/20100101 Firefox/{firefox}.0"
        )
    if os_key in {"win10", "win11"}:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome} Safari/537.36"
        )
    if os_key == "macos":
        mac = random.choice(("10_15_7", "13_6_1", "14_4_1"))
        return (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {mac}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome} Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome} Safari/537.36"
    )


def browser_from_ua(ua: str, kernel_fallback: str = "") -> str:
    if not ua:
        return kernel_fallback or "—"
    if "Edg/" in ua:
        match = re.search(r"Edg/([\d.]+)", ua)
        return f"Edge {match.group(1)}" if match else "Edge"
    if "Firefox/" in ua:
        match = re.search(r"Firefox/([\d.]+)", ua)
        return f"Firefox {match.group(1)}" if match else "Firefox"
    if "Chrome/" in ua:
        match = re.search(r"Chrome/([\d.]+)", ua)
        return f"Chrome {match.group(1)}" if match else "Chrome"
    return kernel_fallback or "—"


def parse_kernel_choice(_payload: dict[str, Any] | None = None) -> tuple[str, str]:
    # New profiles always pin the newest Chrome kernel already on disk.
    return "chrome", preferred_chrome_kernel()


def default_fingerprint(
    os_key: str = "win11",
    kernel: str = "chrome",
    webrtc: str = "disabled",
    kernel_version: str | None = None,
) -> dict[str, Any]:
    os_name = OS_CHOICES.get(os_key, OS_CHOICES["win11"])
    kernel = "chrome"
    kernel_version = sanitize_kernel_version(kernel_version)
    random_ua: dict[str, Any] = {
        "ua_browser": [kernel],
        "ua_system_version": [os_name],
    }
    if kernel_version not in {"ua_auto", "latest"}:
        random_ua["ua_version"] = [kernel_version]
    return {
        "automatic_timezone": "1",
        "language_switch": "0",
        "language": ["en-US", "en"],
        "page_language_switch": "1",
        "page_language": "en-US",
        "webrtc": webrtc,
        "location": "ask",
        "location_switch": "1",
        "flash": "block",
        "fonts": ["all"],
        "canvas": "1",
        "webgl_image": "1",
        "webgl": "3",
        "audio": "1",
        "do_not_track": "default",
        "hardware_concurrency": "4",
        "device_memory": "8",
        "scan_port_type": "1",
        "media_devices": "1",
        "client_rects": "1",
        "device_name_switch": "1",
        "speech_switch": "1",
        "mac_address_config": {"model": "1", "address": ""},
        "gpu": "0",
        "screen_resolution": "random",
        "browser_kernel_config": {"version": kernel_version, "type": kernel},
        "random_ua": random_ua,
    }


def random_fingerprint() -> dict[str, Any]:
    # Blend into the common crowd: Windows-heavy, some macOS, no Linux desktop
    # (rare for Snapchat web → smaller crowd = easier to flag).
    os_key = random.choices(["win11", "win10", "macos"], weights=[50, 35, 15])[0]
    fp = default_fingerprint(os_key, "chrome", "disabled", kernel_version=preferred_chrome_kernel())
    # Bias toward modern desktop specs; avoid the uncommon 4-core tier.
    fp["hardware_concurrency"] = random.choices(["6", "8", "16"], weights=[15, 55, 30])[0]
    # navigator.deviceMemory is spec-capped at 8 — never report higher (would be an impossible value).
    fp["device_memory"] = random.choices(["8", "4"], weights=[80, 20])[0]
    fp["do_not_track"] = random.choice(("default", "true", "false"))
    fp["gpu"] = random.choice(("0", "1"))
    return fp


def resolve_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("fingerprint_mode") or "selective").lower()
    if mode == "random":
        return random_fingerprint()
    _kernel, version = parse_kernel_choice(payload)
    return default_fingerprint(
        payload.get("os") or "win11",
        "chrome",
        payload.get("webrtc") or "disabled",
        kernel_version=version,
    )


def align_fingerprint_to_proxy(
    fingerprint: dict[str, Any],
    *,
    proxy_mode: str = "none",
    proxy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """When a proxy is on: WebRTC = proxy IP, timezone from exit IP. US sticky → language from IP."""
    fp = dict(fingerprint)
    using_proxy = (proxy_mode or "none") in {"list", "saved"}
    if proxy and str(proxy.get("proxy_soft") or "") == "no_proxy":
        using_proxy = False
    if not using_proxy:
        return fp
    fp["webrtc"] = "proxy"
    fp["automatic_timezone"] = "1"
    tags = proxy_session_tags(proxy)
    country = (tags.get("country") or "").lower()
    if country in ENGLISH_PROXY_COUNTRIES:
        fp["language_switch"] = "1"
    else:
        fp["language_switch"] = "0"
    fp["language"] = ["en-US", "en"]
    fp["page_language_switch"] = "1"
    fp["page_language"] = "en-US"
    return fp


FP_LABELS = {
    "os_name": "OS",
    "kernel": "Kernel",
    "kernel_type": "Kernel type",
    "kernel_version": "Kernel version",
    "user_agent": "User agent",
    "ua": "User agent",
    "ua_browser": "UA browser",
    "ua_version": "UA version",
    "ua_system_version": "OS (UA)",
    "webrtc": "WebRTC",
    "timezone": "Timezone",
    "automatic_timezone": "Timezone from IP",
    "screen": "Screen",
    "screen_resolution": "Screen",
    "language": "Language",
    "language_switch": "Language from IP",
    "page_language": "Page language",
    "canvas": "Canvas",
    "webgl": "WebGL",
    "webgl_image": "WebGL image",
    "webgl_config": "WebGL config",
    "audio": "Audio",
    "hardware_concurrency": "CPU cores",
    "device_memory": "Device memory (GB)",
    "do_not_track": "Do not track",
    "gpu": "GPU / hardware accel",
    "flash": "Flash",
    "fonts": "Fonts",
    "location": "Geolocation prompt",
    "location_switch": "Location from IP",
    "client_rects": "ClientRects",
    "speech_switch": "Speech voices",
    "media_devices": "Media devices",
    "media_devices_num": "Media device counts",
    "device_name_switch": "Device name",
    "device_name": "Custom device name",
    "mac_address_config": "MAC address",
    "scan_port_type": "Port scan protection",
    "browser_kernel_config": "Browser kernel",
    "random_ua": "Random UA rules",
    "tls_switch": "TLS switch",
    "tls": "TLS ciphers",
}


def describe_fingerprint(fp: dict[str, Any] | None) -> dict[str, str]:
    if not fp:
        return {
            "browser": "—",
            "kernel": "—",
            "os_name": "—",
            "user_agent": "—",
            "webrtc": "—",
            "timezone": "—",
            "screen": "—",
            "cpu": "—",
            "memory": "—",
            "canvas": "—",
            "webgl": "—",
            "audio": "—",
            "gpu": "—",
            "languages": "—",
        }
    kernel_cfg = fp.get("browser_kernel_config") or {}
    random_ua = fp.get("random_ua") or {}
    systems = random_ua.get("ua_system_version") or []
    browsers = random_ua.get("ua_browser") or []
    ua_versions = random_ua.get("ua_version") or []
    kernel_type = kernel_cfg.get("type") or (browsers[0] if browsers else "")
    version = kernel_cfg.get("version") or "ua_auto"
    kernel_label = f"{kernel_type} {version}".strip() if kernel_type else "—"
    os_name = systems[0] if systems else os_from_ua(fp.get("ua") or "")
    ua = str(fp.get("ua") or fp.get("user_agent") or "").strip()
    if not ua:
        parts = [p for p in (kernel_label, os_name, " ".join(ua_versions)) if p and p != "—"]
        ua = " · ".join(parts) if parts else "—"
    browser = browser_from_ua(ua, kernel_type or kernel_label)
    return {
        "browser": browser,
        "kernel": kernel_label or "—",
        "kernel_type": kernel_type or "—",
        "kernel_version": str(version or "—"),
        "os_name": os_name or "—",
        "user_agent": ua,
        "webrtc": str(fp.get("webrtc") or "—"),
        "timezone": "auto (from IP)" if str(fp.get("automatic_timezone")) == "1" else str(fp.get("timezone") or "—"),
        "screen": str(fp.get("screen_resolution") or "—"),
        "cpu": str(fp.get("hardware_concurrency") or "—"),
        "memory": str(fp.get("device_memory") or "—"),
        "canvas": str(fp.get("canvas") or "—"),
        "webgl": str(fp.get("webgl") or "—"),
        "audio": str(fp.get("audio") or "—"),
        "gpu": str(fp.get("gpu") or "—"),
        "languages": (
            f"{_fmt_value(fp.get('language'))} (custom)"
            if str(fp.get("language_switch")) == "0"
            else "from IP"
            if str(fp.get("language_switch")) == "1"
            else _fmt_value(fp.get("language"))
        ),
    }


def flatten_fingerprint(fp: dict[str, Any] | None) -> list[dict[str, str]]:
    described = describe_fingerprint(fp)
    rows: list[dict[str, str]] = [
        {"label": "Browser", "value": described.get("browser") or "—"},
        {"label": "OS", "value": described["os_name"]},
        {"label": "Kernel", "value": described["kernel"]},
        {"label": "User agent", "value": described["user_agent"]},
        {"label": "WebRTC", "value": described["webrtc"]},
        {"label": "Timezone", "value": described["timezone"]},
        {"label": "Screen", "value": described["screen"]},
        {"label": "CPU cores", "value": described["cpu"]},
        {"label": "Memory (GB)", "value": described["memory"]},
        {"label": "Canvas", "value": described["canvas"]},
        {"label": "WebGL", "value": described["webgl"]},
        {"label": "Audio", "value": described["audio"]},
        {"label": "GPU", "value": described["gpu"]},
        {"label": "Languages", "value": described["languages"]},
    ]
    seen = {r["label"].lower() for r in rows}
    if not isinstance(fp, dict):
        return rows
    skip = {"ua", "user_agent"}
    for key, value in fp.items():
        if key in skip:
            continue
        label = FP_LABELS.get(key, key.replace("_", " "))
        if label.lower() in seen:
            continue
        rows.append({"label": label, "value": _fmt_value(value)})
        seen.add(label.lower())
    return rows


def fingerprint_from_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("fingerprint_config", "fingerprint", "finger_print"):
        value = row.get(key)
        if isinstance(value, dict) and value:
            return value
    assembled: dict[str, Any] = {}
    if row.get("ua"):
        assembled["ua"] = row["ua"]
    if row.get("user_agent"):
        assembled["user_agent"] = row["user_agent"]
    for key in ("browser_kernel_config", "random_ua", "user_proxy_config"):
        if isinstance(row.get(key), dict):
            if key != "user_proxy_config":
                assembled[key] = row[key]
    return assembled


def _fmt_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def os_from_ua(ua: str) -> str:
    if not ua:
        return ""
    patterns = (
        (r"Windows NT 10\.0", "Windows 10/11"),
        (r"Windows NT 6\.1", "Windows 7"),
        (r"Mac OS X ([0-9_]+)", "macOS"),
        (r"Android ([0-9.]+)", "Android"),
        (r"iPhone|iPad", "iOS"),
        (r"Linux", "Linux"),
    )
    for pattern, label in patterns:
        if re.search(pattern, ua, re.I):
            match = re.search(pattern, ua, re.I)
            if match and match.lastindex:
                return f"{label} {match.group(1).replace('_', '.')}"
            return label
    return ""
