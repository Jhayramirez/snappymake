from __future__ import annotations

from typing import Any

from app.ads.client import AdsPowerClient
from app.inject.snapchat import SNAPCHAT_SIGNUP_URL, run_page_action
from app.services.profiles import open_browser, update_credentials


class SnapchatInjector:
    def __init__(self, client: AdsPowerClient):
        self.client = client

    def start_session(self, profile_id: str) -> dict[str, Any]:
        opened = open_browser(self.client, profile_id, headless=False)
        return {
            **opened,
            "signup_url": SNAPCHAT_SIGNUP_URL,
            "note": "Connect Playwright with chromium.connect_over_cdp(puppeteer_ws).",
        }

    def commit_after_signup(self, profile_id: str, credentials: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "platform": credentials.get("platform") or "snapchat.com",
            "username": credentials.get("username") or "",
            "password": credentials.get("password") or "",
            "fakey": credentials.get("fakey") or "",
            "name": credentials.get("name"),
            "remark": credentials.get("remark") or "Snapchat via SnappyMake",
        }
        return update_credentials(self.client, profile_id, payload)


def attach_and_act(puppeteer_ws: str, **kwargs: Any) -> dict[str, Any]:
    return run_page_action(puppeteer_ws, **kwargs)
