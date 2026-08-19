"""ESI compatibility-date pinning, and keeping private data out of the shared cache."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

from eve_esi_mcp.config import get_settings  # noqa: E402
from eve_esi_mcp.esi_client import ESIClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_ESI_MCP_CONTACT", "test@example.com")
    monkeypatch.setenv("EVE_ESI_MCP_CACHE_DIR", str(tmp_path / "cache"))
    import eve_esi_mcp.config as cfg

    cfg._settings = None
    return ESIClient(get_settings())


def test_base_url_is_versionless():
    """/latest is the legacy alias; compatibility-date versioning replaces it."""
    import eve_esi_mcp.config as cfg

    cfg._settings = None
    s = get_settings()
    assert s.base_url == "https://esi.evetech.net"
    assert "/latest" not in s.base_url


def test_compatibility_date_is_pinned_to_a_real_esi_date():
    import eve_esi_mcp.config as cfg

    cfg._settings = None
    s = get_settings()
    # Must be one of the dates ESI publishes at /meta/compatibility-dates.
    assert s.compatibility_date == "2026-08-18"


async def test_requests_send_the_compatibility_date_header(client):
    seen = {}

    def _capture(request):
        seen.update(request.headers)
        return httpx.Response(200, json=[])

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/universe/regions/").mock(side_effect=_capture)
        await client.get_json("/universe/regions/")

    assert seen.get("x-compatibility-date") == "2026-08-18"


async def test_user_agent_has_contact_and_no_dangling_url(client):
    seen = {}

    def _capture(request):
        seen.update(request.headers)
        return httpx.Response(200, json=[])

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/universe/regions/").mock(side_effect=_capture)
        await client.get_json("/universe/regions/")

    ua = seen.get("user-agent", "")
    assert "test@example.com" in ua
    # The original ended in a bare "+https://github.com/" pointing nowhere.
    assert not ua.rstrip().endswith("github.com/")


async def test_authenticated_responses_are_not_written_to_the_shared_disk_cache(client):
    """hishel keys on method+URL+body only — an auth'd response would be served to
    anyone later requesting the same URL, and lands in a world-readable cache file."""
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/123/wallet/").mock(
            return_value=httpx.Response(
                200,
                headers={"Cache-Control": "public, max-age=300", "ETag": '"abc"'},
                json=1234567.89,
            )
        )
        await client.get_json("/characters/123/wallet/", auth_token="secret-token")

    cache_dir = get_settings().cache_dir / "http"
    blobs = list(cache_dir.rglob("*")) if cache_dir.exists() else []
    contents = b"".join(p.read_bytes() for p in blobs if p.is_file())
    assert b"1234567" not in contents, "private wallet data was written to disk cache"


def test_cache_directory_is_not_world_readable(client):
    cache_dir = get_settings().cache_dir
    mode = cache_dir.stat().st_mode & 0o777
    assert mode & 0o077 == 0, f"cache dir mode {oct(mode)} exposes data to other users"
