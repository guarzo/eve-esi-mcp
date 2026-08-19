"""The firehose tools. Measured live before this change:

  /markets/prices/    ~1.1 MB  (~274k tokens)
  /industry/systems/  ~1.9 MB  (~489k tokens)
  /sovereignty/systems/ ~1.3 MB (~320k tokens)

Each exceeded a standard context window in a single zero-argument call.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")


@pytest.fixture(autouse=True)
def _fresh_client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_ESI_MCP_CONTACT", "test@example.com")
    monkeypatch.setenv("EVE_ESI_MCP_CACHE_DIR", str(tmp_path / "cache"))
    import eve_esi_mcp.config as cfg
    import eve_esi_mcp.esi_client as ec

    cfg._settings = None
    ec._client = None
    yield
    cfg._settings = None
    ec._client = None


def _prices(n):
    return [
        {"type_id": i, "adjusted_price": float(i), "average_price": float(i)}
        for i in range(n)
    ]


async def test_market_prices_is_bounded_by_default():
    from eve_esi_mcp.tools.market import market_prices

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/markets/prices/").mock(
            return_value=httpx.Response(200, json=_prices(14_000))
        )
        result = await market_prices()

    assert result["truncated"] is True
    assert result["total"] == 14_000
    assert result["returned"] < 14_000
    assert "note" in result


async def test_market_prices_filters_to_requested_types_without_truncating():
    """The useful call is 'price these specific items', which should be exact."""
    from eve_esi_mcp.tools.market import market_prices

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/markets/prices/").mock(
            return_value=httpx.Response(200, json=_prices(14_000))
        )
        result = await market_prices(type_ids=[34, 35, 36])

    assert result["returned"] == 3
    assert result["truncated"] is False
    assert {r["type_id"] for r in result["items"]} == {34, 35, 36}


async def test_industry_systems_is_bounded_by_default():
    from eve_esi_mcp.tools.industry import industry_systems

    rows = [
        {
            "solar_system_id": i,
            "cost_indices": [{"activity": "manufacturing", "cost_index": i / 10000}],
        }
        for i in range(8000)
    ]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/industry/systems/").mock(
            return_value=httpx.Response(200, json=rows)
        )
        result = await industry_systems(activity="manufacturing")

    assert result["truncated"] is True
    assert result["total"] == 8000
    assert result["returned"] < 8000


async def test_build_cost_estimate_still_sees_all_prices_it_needs():
    """Bounding the public tool must not break the internal consumer."""
    from eve_esi_mcp.tools.industry import build_cost_estimate

    prices = _prices(14_000)
    idx = [
        {
            "solar_system_id": 30000142,
            "cost_indices": [{"activity": "manufacturing", "cost_index": 0.05}],
        }
    ]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/markets/prices/").mock(return_value=httpx.Response(200, json=prices))
        router.get("/industry/systems/").mock(return_value=httpx.Response(200, json=idx))
        result = await build_cost_estimate(
            product_type_id=587,
            # type_id 13000 sits far past any default row cap.
            materials=[{"type_id": 13000, "quantity_per_run": 10}],
            system_id=30000142,
        )

    assert result["cost_index"] == 0.05
    assert result["materials"][0]["adjusted_price"] == 13000.0
    assert result["eiv_isk"] > 0


async def test_region_wide_market_orders_signal_truncation():
    from eve_esi_mcp.tools.market import market_orders

    page = [{"price": 1.0, "is_buy_order": False, "location_id": 1}] * 50
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/markets/10000002/orders/").mock(
            return_value=httpx.Response(200, headers={"X-Pages": "414"}, json=page)
        )
        result = await market_orders(region="The Forge")

    assert result["truncated"] is True
    assert result["pages_available"] == 414
    assert result["pages_fetched"] < 414
    assert "note" in result


async def test_system_kills_and_jumps_are_bounded():
    from eve_esi_mcp.tools.activity import system_jumps, system_kills

    kills = [{"system_id": i, "ship_kills": i} for i in range(5000)]
    jumps = [{"system_id": i, "ship_jumps": i} for i in range(5000)]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/universe/system_kills/").mock(
            return_value=httpx.Response(200, json=kills)
        )
        router.get("/universe/system_jumps/").mock(
            return_value=httpx.Response(200, json=jumps)
        )
        k = await system_kills()
        j = await system_jumps()

    assert k["truncated"] is True and k["total"] == 5000
    assert j["truncated"] is True and j["total"] == 5000


async def test_hottest_systems_still_ranks_across_the_full_dataset():
    """Ranking must consider every system, not just the first N returned."""
    from eve_esi_mcp.tools.activity import hottest_systems

    rows = [{"system_id": i, "ship_kills": i} for i in range(5000)]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/universe/system_kills/").mock(
            return_value=httpx.Response(200, json=rows)
        )
        result = await hottest_systems(kind="ship_kills", limit=3)

    top = result["items"] if isinstance(result, dict) else result
    assert [r["system_id"] for r in top] == [4999, 4998, 4997]
