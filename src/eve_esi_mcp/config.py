from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "eve-esi-mcp"


def _default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "eve-esi-mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVE_ESI_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    contact: str = Field(
        default="",
        description=(
            "Contact string baked into the ESI User-Agent header. Email, app URL, or "
            "Discord handle. Required by ESI ToS so CCP can reach out about misbehaving apps."
        ),
    )
    app_name: str = Field(default="eve-esi-mcp")
    app_version: str = Field(default="0.1.0")
    cache_dir: Path = Field(default_factory=_default_cache_dir)
    data_dir: Path = Field(default_factory=_default_data_dir)
    base_url: str = Field(default="https://esi.evetech.net/latest")
    page_concurrency: int = Field(default=8, ge=1, le=32)
    error_limit_floor: int = Field(
        default=10,
        description="When X-ESI-Error-Limit-Remain <= this, sleep until reset.",
    )

    # SSO (optional)
    sso_client_id: str = Field(default="", alias="EVE_SSO_CLIENT_ID")
    sso_callback_port: int = Field(default=8765, alias="EVE_SSO_CALLBACK_PORT")
    sso_scopes: str = Field(
        default="esi-wallet.read_character_wallet.v1 esi-assets.read_assets.v1 "
        "esi-markets.read_character_orders.v1 esi-skills.read_skills.v1 "
        "esi-industry.read_character_jobs.v1",
        alias="EVE_SSO_SCOPES",
    )

    def user_agent(self) -> str:
        contact = self.contact.strip() or "no-contact-set"
        return f"{self.app_name}/{self.app_version} ({contact}) +https://github.com/"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.cache_dir.mkdir(parents=True, exist_ok=True)
        _settings.data_dir.mkdir(parents=True, exist_ok=True)
    return _settings
