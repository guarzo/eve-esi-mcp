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
    base_url: str = Field(default="https://esi.evetech.net")
    compatibility_date: str = Field(
        default="2026-08-18",
        description=(
            "Sent as X-Compatibility-Date. ESI versions by date rather than by path; "
            "omitting the header pins you to 2020-01-01. Valid values are published at "
            "https://esi.evetech.net/meta/compatibility-dates — bumping this can change "
            "response shapes, so check /meta/changelog first."
        ),
    )
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
        return f"{self.app_name}/{self.app_version} ({contact})"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        # 0700: these directories hold ESI response bodies and SSO token material.
        _settings.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir's mode is ignored when the directory already exists.
        _settings.cache_dir.chmod(0o700)
        _settings.data_dir.chmod(0o700)
    return _settings
