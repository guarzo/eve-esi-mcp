"""Wallet transactions, and the completeness guarantee the eve-indy tracker needs.

/characters/{id}/wallet/transactions/ paginates with a `from_id` cursor, NOT the
`X-Pages` header every other paginated endpoint uses — verified against the
2026-08-18 spec. Walking it wrong returns page 1 forever and looks like success.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_ESI_MCP_CONTACT", "test@example.com")
    monkeypatch.setenv("EVE_ESI_MCP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("EVE_ESI_MCP_DATA_DIR", str(tmp_path / "data"))
    import eve_esi_mcp.config as cfg
    import eve_esi_mcp.esi_client as ec

    cfg._settings = None
    ec._client = None
    from eve_esi_mcp import sso

    sso.save_character(
        character_id=1001,
        character_name="Alice",
        tokens={
            "access_token": "token-1001",
            "refresh_token": "r",
            "expires_in": 1200,
        },
        scope="esi-wallet.read_character_wallet.v1",
    )
    yield
    cfg._settings = None
    ec._client = None


def _tx(tid):
    return {
        "transaction_id": tid,
        "date": "2026-08-19T12:00:00Z",
        "type_id": 34,
        "location_id": 60003760,
        "quantity": 10,
        "unit_price": 3.8,
        "is_buy": True,
        "is_personal": True,
        "client_id": 99,
        "journal_ref_id": tid * 10,
    }


async def test_transactions_returns_first_page_by_default():
    from eve_esi_mcp.tools.character import my_wallet_transactions

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/wallet/transactions/").mock(
            return_value=httpx.Response(200, json=[_tx(500), _tx(499)])
        )
        result = await my_wallet_transactions()

    assert result["returned"] == 2
    assert result["character_id"] == 1001
    assert result["items"][0]["transaction_id"] == 500


async def test_complete_walks_the_from_id_cursor_until_exhausted():
    from eve_esi_mcp.tools.character import my_wallet_transactions

    pages = {
        None: [_tx(500), _tx(499)],
        "499": [_tx(498), _tx(497)],
        "497": [],
    }
    seen_cursors = []

    def _handler(request):
        cur = request.url.params.get("from_id")
        seen_cursors.append(cur)
        return httpx.Response(200, json=pages[cur])

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/wallet/transactions/").mock(side_effect=_handler)
        result = await my_wallet_transactions(complete=True)

    # Cursor must be the smallest (oldest) id of the previous page: ESI returns
    # only transactions *before* the referenced id.
    assert seen_cursors == [None, "499", "497"]
    assert result["returned"] == 4
    assert result["truncated"] is False


async def test_complete_never_returns_a_silent_partial():
    """The tracker computes rolling-average cost from this. A quiet truncation
    produces a confidently wrong P&L, so hitting the safety cap must be fatal."""
    from eve_esi_mcp.esi_client import ESIError
    from eve_esi_mcp.tools.character import MAX_CURSOR_PAGES, my_wallet_transactions

    counter = {"n": 0}

    def _endless(request):
        counter["n"] += 1
        base = 10_000_000 - counter["n"] * 2
        return httpx.Response(200, json=[_tx(base), _tx(base - 1)])

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/wallet/transactions/").mock(side_effect=_endless)
        with pytest.raises(ESIError):
            await my_wallet_transactions(complete=True)

    assert counter["n"] == MAX_CURSOR_PAGES


async def test_complete_journal_returns_every_row_untruncated():
    from eve_esi_mcp.tools.character import my_wallet_journal

    rows = [{"id": i, "ref_type": "brokers_fee", "amount": -1.0} for i in range(900)]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/wallet/journal/").mock(
            return_value=httpx.Response(200, headers={"X-Pages": "1"}, json=rows)
        )
        capped_result = await my_wallet_journal()
        full = await my_wallet_journal(complete=True)

    assert capped_result["truncated"] is True and capped_result["returned"] == 200
    assert full["truncated"] is False and full["returned"] == 900


async def test_complete_assets_returns_every_row_untruncated():
    from eve_esi_mcp.tools.character import my_assets

    rows = [{"item_id": i, "type_id": 34, "quantity": 1} for i in range(700)]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/assets/").mock(
            return_value=httpx.Response(200, headers={"X-Pages": "1"}, json=rows)
        )
        full = await my_assets(complete=True)

    assert full["truncated"] is False and full["returned"] == 700


async def test_journal_docstring_no_longer_claims_to_be_transactions():
    """These are different endpoints with different meanings; the tracker uses
    both, and conflating them corrupts the cashflow model."""
    from eve_esi_mcp.tools.character import my_wallet_journal, my_wallet_transactions

    assert "journal" in my_wallet_journal.__doc__.lower()
    assert "ISK movements" in my_wallet_journal.__doc__
    assert "transactions" not in my_wallet_journal.__doc__.split(".")[0].lower()
    assert "unit_price" in my_wallet_transactions.__doc__
