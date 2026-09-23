from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.ads import AdsPowerError

DEFAULT_BASES = (
    "http://127.0.0.1:50325",
    "http://local.adspower.net:50325",
    "http://local.adspower.com:50325",
    "http://localhost:50325",
)

PING_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
START_TIMEOUT = httpx.Timeout(180.0, connect=8.0)
STOP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Keep AdsPower windows from jumping in front of Cursor while CDP still runs.
BACKGROUND_LAUNCH_ARGS = [
    "--disable-notifications",
    "--window-position=40,60",
]


def no_focus_enabled() -> bool:
    return os.environ.get("SNAPPY_NO_FOCUS", "1") != "0"


def background_launch_args() -> list[str]:
    if no_focus_enabled():
        return ["--disable-notifications", "--window-position=-2400,-200"]
    return list(BACKGROUND_LAUNCH_ARGS)


def release_user_focus() -> None:
    """Hide AdsPower Chrome so it does not steal the frontmost app."""
    if not no_focus_enabled():
        return
    script = (
        'tell application "System Events"\n'
        '  repeat with procName in {"SunBrowse", "SunBrowser"}\n'
        "    try\n"
        "      set visible of process procName to false\n"
        "    end try\n"
        "  end repeat\n"
        "end tell"
    )
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

LOCAL_API_CANDIDATES = (
    Path.home() / "Library/Application Support/adspower_global/cwd_global/source/local_api",
    Path.home() / ".config/adspower_global/cwd_global/source/local_api",
)


def pack_session(profile_id: str, data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    ws = data.get("ws") or {}
    puppeteer = ws.get("puppeteer") if isinstance(ws, dict) else None
    status = str(data.get("status") or "").lower()
    if not puppeteer:
        return None
    if status and status not in {"active", "opened", "open", "1", "true"}:
        return None
    return {
        "ok": True,
        "profile_id": profile_id,
        "ws": ws,
        "puppeteer": puppeteer,
        "selenium": ws.get("selenium"),
        "debug_port": data.get("debug_port"),
        "webdriver": data.get("webdriver"),
    }


class AdsPowerClient:
    def __init__(self, base_url: str = "", api_key: str = "", min_interval: float = 0.55):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key.strip()
        self.min_interval = min_interval
        self._last_call = 0.0
        self._client = httpx.Client(timeout=DEFAULT_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def reset(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._client = httpx.Client(timeout=DEFAULT_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _wait(self) -> None:
        if self.min_interval <= 0:
            self._last_call = time.monotonic()
            return
        gap = time.monotonic() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.monotonic()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        retries: int = 3,
        timeout: httpx.Timeout | float | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise AdsPowerError("AdsPower API base URL is not set")
        last_error: Exception | None = None
        for attempt in range(retries):
            self._wait()
            try:
                response = self._client.request(
                    method,
                    self._url(path),
                    params=params,
                    json=json_body,
                    headers=self._headers(),
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = AdsPowerError(
                    f"AdsPower timed out at {self.base_url}{path}: {exc}",
                    payload={"timeout": True, "path": path},
                )
                if attempt + 1 >= retries:
                    break
                time.sleep(0.8 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                last_error = AdsPowerError(f"Cannot reach AdsPower at {self.base_url}: {exc}")
                if attempt + 1 >= retries:
                    break
                time.sleep(0.4 * (attempt + 1))
                continue

            try:
                payload = response.json()
            except json.JSONDecodeError:
                raise AdsPowerError(
                    f"AdsPower returned non-JSON ({response.status_code})",
                    payload={"text": response.text[:400]},
                )

            code = payload.get("code")
            msg = str(payload.get("msg") or payload.get("message") or "")
            if response.status_code == 429 or "too many" in msg.lower():
                time.sleep(1.1 * (attempt + 1))
                last_error = AdsPowerError(msg or "AdsPower rate limited", code=code, payload=payload)
                continue
            if response.status_code >= 400:
                raise AdsPowerError(msg or f"HTTP {response.status_code}", code=code, payload=payload)
            if code not in (0, "0", None):
                raise AdsPowerError(msg or "AdsPower request failed", code=code, payload=payload)
            return payload
        raise last_error or AdsPowerError("AdsPower request failed")

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/status")

    def ping(self) -> bool:
        try:
            payload = self.request("GET", "/status", retries=1, timeout=PING_TIMEOUT)
            return payload.get("code") in (0, "0")
        except AdsPowerError:
            return False

    def list_groups(self, page_size: int = 2000) -> list[dict[str, Any]]:
        old = self.min_interval
        self.min_interval = max(old, 1.05)
        try:
            payload = self.request("GET", "/api/v1/group/list", params={"page": 1, "page_size": page_size})
        finally:
            self.min_interval = old
        data = payload.get("data") or {}
        return data.get("list") or []

    def create_group(self, name: str, remark: str = "SnappyMake") -> dict[str, Any]:
        payload = self.request(
            "POST",
            "/api/v1/group/create",
            json_body={"group_name": name, "remark": remark},
        )
        return payload.get("data") or {}

    def regroup_profiles(self, profile_ids: list[str], group_id: str) -> dict[str, Any]:
        """Move profiles into a group via /api/v1/user/regroup.

        AdsPower's v2 profile update silently ignores group_id — moves must use this.
        """
        ids = [str(pid).strip() for pid in profile_ids if str(pid).strip()]
        gid = str(group_id or "").strip()
        if not ids:
            raise AdsPowerError("No profile IDs to regroup")
        if not gid:
            raise AdsPowerError("group_id required to regroup")
        payload = self.request(
            "POST",
            "/api/v1/user/regroup",
            json_body={"user_ids": ids, "group_id": gid},
        )
        return payload.get("data") or {}

    def list_categories(self, page_size: int = 100) -> list[dict[str, Any]]:
        old = self.min_interval
        self.min_interval = max(old, 1.05)
        try:
            payload = self.request(
                "GET",
                "/api/v2/category/list",
                params={"page": 1, "limit": page_size},
            )
        finally:
            self.min_interval = old
        data = payload.get("data") or {}
        return data.get("list") or []

    def list_profiles(
        self,
        group_id: str | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        page = 1
        old = self.min_interval
        self.min_interval = max(old, 1.05)
        try:
            while True:
                body: dict[str, Any] = {
                    "page": page,
                    "limit": page_size,
                    "sort_type": "created_time",
                    "sort_order": "desc",
                }
                if group_id:
                    body["group_id"] = str(group_id)
                payload = self.request("POST", "/api/v2/browser-profile/list", json_body=body)
                data = payload.get("data") or {}
                chunk = data.get("list") or []
                profiles.extend(chunk)
                if len(chunk) < page_size:
                    break
                page += 1
        finally:
            self.min_interval = old
        return profiles

    def create_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = self.request("POST", "/api/v2/browser-profile/create", json_body=body)
        return payload.get("data") or {}

    def update_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = self.request("POST", "/api/v2/browser-profile/update", json_body=body)
        return payload.get("data") or {}

    def delete_profiles(self, profile_ids: list[str]) -> dict[str, Any]:
        ids = [str(pid).strip() for pid in profile_ids if str(pid).strip()]
        if not ids:
            raise AdsPowerError("No profile IDs to delete")
        payload = self.request(
            "POST",
            "/api/v2/browser-profile/delete",
            json_body={"profile_id": ids},
        )
        return payload.get("data") or {}

    def start_browser(
        self,
        profile_id: str,
        *,
        headless: bool = False,
        last_opened_tabs: str = "1",
        proxy_detection: str = "0",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        read_timeout = START_TIMEOUT.read if timeout is None else max(timeout, 5.0)
        start_timeout = httpx.Timeout(read_timeout, connect=8.0)
        try:
            payload = self.request(
                "POST",
                "/api/v2/browser-profile/start",
                json_body={
                    "profile_id": profile_id,
                    "headless": "1" if headless else "0",
                    "last_opened_tabs": last_opened_tabs,
                    "proxy_detection": proxy_detection,
                    "cdp_mask": "1",
                    "launch_args": background_launch_args(),
                },
                retries=1,
                timeout=start_timeout,
            )
            data = payload.get("data") or {}
            release_user_focus()
            return data
        except AdsPowerError as exc:
            if exc.is_kernel_download or exc.is_already_open:
                raise
            if not exc.is_timeout and "404" not in str(exc).lower():
                raise
            self.reset()
            payload = self.request(
                "GET",
                "/api/v1/browser/start",
                params={
                    "user_id": profile_id,
                    "open_tabs": "0",
                    "ip_tab": "0",
                    "headless": "1" if headless else "0",
                    "launch_args": json.dumps(background_launch_args()),
                },
                retries=1,
                timeout=start_timeout,
            )
            data = payload.get("data") or {}
            release_user_focus()
            return data

    def stop_browser(self, profile_id: str) -> dict[str, Any]:
        payload = self.request(
            "POST",
            "/api/v2/browser-profile/stop",
            json_body={"profile_id": profile_id},
            retries=2,
            timeout=STOP_TIMEOUT,
        )
        return payload.get("data") or {}

    def browser_active(self, profile_id: str) -> dict[str, Any]:
        last_error: AdsPowerError | None = None
        for path, params in (
            ("/api/v2/browser-profile/active", {"profile_id": profile_id}),
            ("/api/v1/browser/active", {"user_id": profile_id}),
        ):
            try:
                payload = self.request("GET", path, params=params, retries=1, timeout=PING_TIMEOUT)
                return payload.get("data") or {}
            except AdsPowerError as exc:
                last_error = exc
                if exc.is_timeout:
                    self.reset()
                continue
        if last_error:
            raise last_error
        return {}

    def session_alive(self, session: dict[str, Any] | None) -> bool:
        if not session:
            return False
        ws = session.get("ws") or {}
        selenium = str(ws.get("selenium") or "")
        port = session.get("debug_port")
        hostport = selenium if ":" in selenium else (f"127.0.0.1:{port}" if port else "")
        if not hostport:
            return bool(ws.get("puppeteer"))
        try:
            response = self._client.get(
                f"http://{hostport}/json/version",
                timeout=httpx.Timeout(2.0, connect=1.0),
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def debug_session(self, profile_id: str) -> dict[str, Any] | None:
        try:
            packed = pack_session(profile_id, self.browser_active(profile_id))
            if packed:
                return packed
        except AdsPowerError:
            pass
        for item in self.local_sessions():
            if item.get("profile_id") == profile_id:
                packed = pack_session(profile_id, item)
                if packed:
                    return packed
        return None

    def local_sessions(self) -> list[dict[str, Any]]:
        try:
            payload = self.request("GET", "/api/v1/browser/local-active", retries=1, timeout=PING_TIMEOUT)
        except AdsPowerError:
            return []
        data = payload.get("data") or {}
        raw: list[Any] = []
        if isinstance(data, list):
            raw = data
        elif isinstance(data, dict):
            raw = data.get("list") or data.get("user_ids") or []
        sessions: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                sessions.append({"profile_id": item})
            elif isinstance(item, dict):
                sessions.append(
                    {
                        "profile_id": str(item.get("user_id") or item.get("profile_id") or ""),
                        "ws": item.get("ws") or {},
                        "debug_port": item.get("debug_port"),
                        "webdriver": item.get("webdriver"),
                        "status": item.get("status") or "Active",
                    }
                )
        return [s for s in sessions if s.get("profile_id")]

    def local_active(self) -> list[str]:
        return [item["profile_id"] for item in self.local_sessions()]

    def fallback_localhost(self) -> bool:
        current = (self.base_url or "").lower()
        if "127.0.0.1" in current or "localhost" in current:
            return False
        for alt in ("http://127.0.0.1:50325", "http://localhost:50325"):
            probe = AdsPowerClient(alt, self.api_key)
            try:
                if probe.ping():
                    self.base_url = alt
                    self.reset()
                    return True
            finally:
                probe.close()
        return False


def read_local_api_file() -> str | None:
    for path in LOCAL_API_CANDIDATES:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if text.startswith("http"):
                return text.split()[0].rstrip("/")
    return None


def detect_base_url(preferred: str = "", api_key: str = "") -> str:
    candidates = []
    for value in (preferred, read_local_api_file(), *DEFAULT_BASES):
        if value and value not in candidates:
            candidates.append(value.rstrip("/"))
    last_error: AdsPowerError | None = None
    for base in candidates:
        client = AdsPowerClient(base, api_key)
        try:
            if client.ping():
                return base
        except AdsPowerError as exc:
            last_error = exc
        finally:
            client.close()
    if last_error:
        raise last_error
    raise AdsPowerError(
        "AdsPower Local API is not reachable. Open AdsPower and enable Local API (port 50325)."
    )
