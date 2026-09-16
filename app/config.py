from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "snappymake.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    adspower_api_base: str = "http://127.0.0.1:50325"
    adspower_api_key: str = ""
    adspower_plan_cap: int = 0
    snappymake_host: str = "127.0.0.1"
    snappymake_port: int = 8787
    snappymake_group_name: str = "SnappyMake"
    snapchat_default_password: str = "PCGpp00##"
    gmail_imap_host: str = "imap.gmail.com"
    gmail_imap_port: int = 993


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
