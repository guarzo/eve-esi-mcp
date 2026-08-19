"""PLEX and other globally-traded items live in region 19000001, not a normal region.

Asking for PLEX in The Forge returns an empty order book and stale history, which the
original code reported as `best_bid: null` with `isError: false` — indistinguishable
from "this item is illiquid here". These tests pin the corrected behaviour.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

from eve_esi_mcp.ids import (  # noqa: E402
    GLOBAL_MARKET_REGION_ID,
    is_global_market_type,
    region_id,
)


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


def test_global_market_region_is_known_by_name():
    assert region_id("global") == GLOBAL_MARKET_REGION_ID
    assert GLOBAL_MARKET_REGION_ID == 19000001


def test_plex_is_flagged_as_a_global_market_type():
    assert is_global_market_type(44992) is True
    assert is_global_market_type(34) is False


async def test_market_orders_redirects_global_types_to_the_global_region():
    from eve_esi_mcp.tools.market import market_orders

    base = "https://esi.evetech.net"
    with respx.mock(base_url=base) as router:
        route = router.get(f"/markets/{GLOBAL_MARKET_REGION_ID}/orders/").mock(
            return_value=httpx.Response(
                200,
                headers={"X-Pages": "1"},
                json=[{"price": 4862000.0, "is_buy_order": False, "location_id": 1}],
            )
        )
        result = await market_orders(region="The Forge", type_id=44992)

    # It must have gone to the global region, not The Forge.
    assert route.called
    assert result["items"][0]["price"] == 4862000.0
    assert result["region_id"] == GLOBAL_MARKET_REGION_ID
    assert result["region_redirected_from"] == 10000002
    assert "note" in result and "19000001" in result["note"]


async def test_best_bid_ask_on_plex_does_not_silently_report_null():
    from eve_esi_mcp.tools.market import best_bid_ask

    base = "https://esi.evetech.net"
    with respx.mock(base_url=base) as router:
        router.get(f"/markets/{GLOBAL_MARKET_REGION_ID}/orders/").mock(
            return_value=httpx.Response(
                200,
                headers={"X-Pages": "1"},
                json=[
                    {"price": 4862000.0, "is_buy_order": False},
                    {"price": 4700000.0, "is_buy_order": True},
                ],
            )
        )
        router.get(f"/markets/{GLOBAL_MARKET_REGION_ID}/history/").mock(
            return_value=httpx.Response(
                200,
                json=[{"date": "2026-08-18", "average": 4800000.0, "volume": 1000}],
            )
        )
        result = await best_bid_ask(region="Jita", type_id=44992)

    assert result["best_ask"] == 4862000.0
    assert result["best_bid"] == 4700000.0
    assert result["region_id"] == GLOBAL_MARKET_REGION_ID


async def test_empty_order_book_is_explicitly_signalled_not_silently_null():
    """A genuinely empty book must say so, so the model cannot read it as a price."""
    from eve_esi_mcp.tools.market import best_bid_ask

    base = "https://esi.evetech.net"
    with respx.mock(base_url=base) as router:
        router.get("/markets/10000002/orders/").mock(
            return_value=httpx.Response(200, headers={"X-Pages": "1"}, json=[])
        )
        router.get("/markets/10000002/history/").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await best_bid_ask(region="The Forge", type_id=99999)

    assert result["best_bid"] is None
    assert result["best_ask"] is None
    assert result["no_orders"] is True
    assert "note" in result


def test_region_id_accepts_a_hub_name():
    """Models and traders say "Jita", not "The Forge"."""
    assert region_id("Jita") == 10000002
    assert region_id("amarr") == 10000043


async def test_compare_hubs_collapses_global_items_to_one_row():
    from eve_esi_mcp.tools.arbitrage import compare_hubs

    base = "https://esi.evetech.net"
    with respx.mock(base_url=base) as router:
        router.get(f"/markets/{GLOBAL_MARKET_REGION_ID}/orders/").mock(
            return_value=httpx.Response(
                200,
                headers={"X-Pages": "1"},
                json=[
                    {"price": 4862000.0, "is_buy_order": False},
                    {"price": 4700000.0, "is_buy_order": True},
                ],
            )
        )
        router.get(f"/markets/{GLOBAL_MARKET_REGION_ID}/history/").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await compare_hubs(type_id=44992)

    assert len(result["rows"]) == 1
    assert result["rows"][0]["hub"] == "global"
    assert result["gross_spread_isk"] is None
    assert "no inter-hub spread" in result["note"]
