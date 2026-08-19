from __future__ import annotations

from typing import Any

from ..esi_client import get_client
from ..limits import capped, tool_limit
from ..sso import character_id, get_valid_access_token


async def _auth() -> tuple[str, int]:
    token = await get_valid_access_token()
    cid = await character_id()
    if not token or not cid:
        raise RuntimeError("Not logged in. Run sso_login first.")
    return token, cid


async def my_wallet() -> dict[str, Any]:
    """Wallet balance (requires esi-wallet.read_character_wallet.v1)."""
    token, cid = await _auth()
    balance = await get_client().get_json(
        f"/characters/{cid}/wallet/", auth_token=token
    )
    return {"character_id": cid, "balance_isk": balance}


async def my_wallet_journal(limit: int | None = None) -> dict[str, Any]:
    """Recent wallet transactions, newest first (ESI returns them in that order)."""
    token, cid = await _auth()
    rows = await get_client().get_all_pages(
        f"/characters/{cid}/wallet/journal/", auth_token=token
    )
    return capped(rows, limit=tool_limit(limit), extra={"character_id": cid})


async def my_assets(limit: int | None = None) -> dict[str, Any]:
    """Assets the character owns (requires esi-assets.read_assets.v1).

    A hoarder's asset list runs to tens of thousands of rows, so this is capped;
    the envelope reports the true total.
    """
    token, cid = await _auth()
    rows = await get_client().get_all_pages(
        f"/characters/{cid}/assets/", auth_token=token
    )
    return capped(rows, limit=tool_limit(limit), extra={"character_id": cid})


async def my_open_orders(limit: int | None = None) -> dict[str, Any]:
    """Open market orders (requires esi-markets.read_character_orders.v1)."""
    token, cid = await _auth()
    rows = await get_client().get_json(
        f"/characters/{cid}/orders/", auth_token=token
    )
    return capped(rows, limit=tool_limit(limit), extra={"character_id": cid})


async def my_skills() -> dict[str, Any]:
    """Trained skills (requires esi-skills.read_skills.v1). Filter Accounting /
    Broker Relations / hauling skills to plug into arbitrage math."""
    token, cid = await _auth()
    return await get_client().get_json(
        f"/characters/{cid}/skills/", auth_token=token
    )


async def my_industry_jobs(
    include_completed: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Industry jobs (requires esi-industry.read_character_jobs.v1)."""
    token, cid = await _auth()
    rows = await get_client().get_json(
        f"/characters/{cid}/industry/jobs/",
        params={"include_completed": str(include_completed).lower()},
        auth_token=token,
    )
    return capped(rows, limit=tool_limit(limit), extra={"character_id": cid})
