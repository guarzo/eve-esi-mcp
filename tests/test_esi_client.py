from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

from eve_esi_mcp.config import get_settings  # noqa: E402
from eve_esi_mcp.esi_client import ESIClient, ESIError  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_ESI_MCP_CONTACT", "test@example.com")
    monkeypatch.setenv("EVE_ESI_MCP_CACHE_DIR", str(tmp_path / "cache"))
    # Force a fresh settings instance.
    import eve_esi_mcp.config as cfg
    cfg._settings = None
    s = get_settings()
    return ESIClient(s)


async def test_get_json_ok(client):
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/universe/regions/").mock(
            return_value=httpx.Response(200, json=[10000002, 10000043])
        )
        data = await client.get_json("/universe/regions/")
    assert data == [10000002, 10000043]


async def test_pagination_walks_all_pages(client):
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/markets/10000002/orders/", params={"page": 1, "order_type": "all"}).mock(
            return_value=httpx.Response(
                200,
                headers={"X-Pages": "3"},
                json=[{"id": 1}, {"id": 2}],
            )
        )
        router.get("/markets/10000002/orders/", params={"page": 2, "order_type": "all"}).mock(
            return_value=httpx.Response(200, headers={"X-Pages": "3"}, json=[{"id": 3}])
        )
        router.get("/markets/10000002/orders/", params={"page": 3, "order_type": "all"}).mock(
            return_value=httpx.Response(200, headers={"X-Pages": "3"}, json=[{"id": 4}])
        )
        rows = await client.get_all_pages(
            "/markets/10000002/orders/", params={"order_type": "all"}
        )
    ids = sorted(r["id"] for r in rows)
    assert ids == [1, 2, 3, 4]


async def test_420_raises_and_is_retryable(client):
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/universe/regions/").mock(
            return_value=httpx.Response(
                420,
                headers={
                    "X-ESI-Error-Limit-Remain": "0",
                    "X-ESI-Error-Limit-Reset": "1",
                },
                text="error limited",
            )
        )
        with pytest.raises(ESIError) as exc:
            await client.get_json("/universe/regions/")
    assert exc.value.status == 420


async def test_4xx_not_retried(client):
    with respx.mock(base_url="https://esi.evetech.net") as router:
        route = router.get("/universe/systems/999/").mock(
            return_value=httpx.Response(404, text="not found")
        )
        with pytest.raises(ESIError) as exc:
            await client.get_json("/universe/systems/999/")
    assert exc.value.status == 404
    assert route.call_count == 1  # no retry
