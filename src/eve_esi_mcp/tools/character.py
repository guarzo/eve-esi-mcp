from __future__ import annotations

from typing import Any

from ..esi_client import ESIError, get_client
from ..limits import capped, tool_limit
from ..sso import CharacterSelectionError, get_valid_access_token, resolve_character

# /wallet/transactions/ has no X-Pages; it walks backwards on a `from_id` cursor.
# A complete walk is bounded so a runaway loop can't hammer CCP — but hitting the
# bound raises rather than returning a quiet partial, because the callers that ask
# for `complete` are computing money from it.
MAX_CURSOR_PAGES = 100


async def _auth(character: int | str | None = None) -> tuple[str, int]:
    """Access token + character_id for the selected character."""
    rec = await resolve_character(character)
    token = await get_valid_access_token(rec["character_id"])
    if not token:
        raise CharacterSelectionError(
            {
                "error": "token_refresh_failed",
                "message": (
                    f"Could not refresh the token for "
                    f"{rec.get('character_name', rec['character_id'])}. "
                    f"Run sso_login_start / sso_login_finish again."
                ),
                "available_characters": [],
            }
        )
    return token, int(rec["character_id"])


def _rows(
    rows: list[dict[str, Any]],
    cid: int,
    limit: int | None,
    complete: bool,
) -> dict[str, Any]:
    """Cap for conversational use, or return everything when `complete` is set."""
    if complete:
        return capped(rows, limit=None, extra={"character_id": cid, "complete": True})
    return capped(rows, limit=tool_limit(limit), extra={"character_id": cid})


async def my_wallet(character: int | str | None = None) -> dict[str, Any]:
    """Wallet balance (requires esi-wallet.read_character_wallet.v1)."""
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    balance = await get_client().get_json(
        f"/characters/{cid}/wallet/", auth_token=token
    )
    return {"character_id": cid, "balance_isk": balance}


async def my_wallet_journal(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Wallet journal (ISK movements: fees, taxes, transfers) — newest first.

    ESI retains only the last 30 days. Set `complete=True` to get every retained row
    untruncated, for callers that ingest this rather than read it.
    """
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_all_pages(
        f"/characters/{cid}/wallet/journal/", auth_token=token
    )
    return _rows(rows, cid, limit, complete)


async def my_wallet_transactions(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Market transactions (buys/sells) with per-item type_id, quantity, unit_price,
    is_buy — newest first. Requires esi-wallet.read_character_wallet.v1.

    This endpoint paginates on a `from_id` cursor rather than the `X-Pages` header
    the rest of ESI uses, so by default only the most recent page is returned. Set
    `complete=True` to walk the cursor back through everything ESI retains.
    """
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail

    client = get_client()
    path = f"/characters/{cid}/wallet/transactions/"

    if not complete:
        page = await client.get_json(path, auth_token=token)
        return _rows(page, cid, limit, complete=False)

    rows: list[dict[str, Any]] = []
    cursor: int | None = None
    for _ in range(MAX_CURSOR_PAGES):
        params = {"from_id": cursor} if cursor is not None else None
        page = await client.get_json(path, params=params, auth_token=token)
        if not page:
            break
        rows.extend(page)
        # ESI returns transactions strictly *before* from_id, so the next cursor is
        # the oldest (smallest) id on this page.
        cursor = min(int(r["transaction_id"]) for r in page)
    else:
        raise ESIError(
            200,
            (
                f"wallet transactions exceeded {MAX_CURSOR_PAGES} cursor pages for "
                f"character {cid} ({len(rows)} rows so far). Refusing to return a "
                f"partial set for a complete=True request."
            ),
            path,
        )
    return _rows(rows, cid, limit, complete=True)


async def my_assets(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Assets the character owns (requires esi-assets.read_assets.v1).

    A hoarder's asset list runs to tens of thousands of rows, so this is capped by
    default; the envelope reports the true total. Set `complete=True` for all of it.
    """
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_all_pages(
        f"/characters/{cid}/assets/", auth_token=token
    )
    return _rows(rows, cid, limit, complete)


async def my_open_orders(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Open market orders (requires esi-markets.read_character_orders.v1)."""
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_json(f"/characters/{cid}/orders/", auth_token=token)
    return _rows(rows, cid, limit, complete)


async def my_skills(character: int | str | None = None) -> dict[str, Any]:
    """Trained skills (requires esi-skills.read_skills.v1). Filter Accounting /
    Broker Relations / hauling skills to plug into arbitrage math."""
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    data = await get_client().get_json(f"/characters/{cid}/skills/", auth_token=token)
    return {"character_id": cid, **data} if isinstance(data, dict) else data


async def my_industry_jobs(
    character: int | str | None = None,
    include_completed: bool = False,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Industry jobs (requires esi-industry.read_character_jobs.v1).

    `include_completed` reaches back 90 days — ESI keeps no more than that, so a
    longer history has to be accumulated and stored by the caller.
    """
    try:
        token, cid = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_json(
        f"/characters/{cid}/industry/jobs/",
        params={"include_completed": str(include_completed).lower()},
        auth_token=token,
    )
    return _rows(rows, cid, limit, complete)
