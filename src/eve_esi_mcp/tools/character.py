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


CORP_ASSETS_SCOPE = "esi-assets.read_corporation_assets.v1"
CORP_BLUEPRINTS_SCOPE = "esi-corporations.read_blueprints.v1"
BLUEPRINTS_SCOPE = "esi-characters.read_blueprints.v1"


async def _auth(character: int | str | None = None) -> tuple[str, int, frozenset[str]]:
    """Access token, character_id, and GRANTED SCOPES for the selected character.

    The scopes come back because a token's own scope set is the only way to
    tell a permission the operator can fix (re-run SSO) from one they cannot
    (a missing in-game role). ESI reports both as 403, and a caller that cannot
    distinguish them writes off a corporation permanently for what is really a
    stale token. Resolved here rather than re-read per tool so it costs the one
    `resolve_character` call this function already makes.
    """
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
    return token, int(rec["character_id"]), frozenset((rec.get("scope") or "").split())


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
        token, cid, _scopes = await _auth(character)
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
        token, cid, _scopes = await _auth(character)
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
        token, cid, _scopes = await _auth(character)
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
        token, cid, _scopes = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_all_pages(
        f"/characters/{cid}/assets/", auth_token=token
    )
    return _rows(rows, cid, limit, complete)


async def my_corp_assets(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Assets owned by the character's CORPORATION.

    Requires esi-assets.read_corporation_assets.v1 AND the in-game **Director**
    role (ESI `x-required-roles`). Both failures arrive from ESI as a 403 and
    they need opposite responses, so this tool separates them:

      * missing SCOPE -> returned here as a structured `missing_scope` error,
        BEFORE any request. The token already tells us, so spending a 403
        against ESI's error budget to rediscover it is waste — and a caller
        classifying by status text would read it as a missing role and write
        the corporation off permanently, when re-running SSO would fix it.
        Adding the scope to the default set does not help an existing token:
        `_refresh` preserves the stored scope string, so a token issued before
        this feature never acquires it without a fresh login.
      * missing ROLE -> a real 403 from ESI, which the caller cannot avoid and
        must handle. Nothing here can predict it: roles are not exposed.

    Disjoint from `my_assets`: an item in a corp hangar is corp-owned and never
    appears in the character's own asset list, so the two can be summed without
    double-counting.

    The envelope carries `corporation_id` as well as `character_id`, because a
    corp snapshot must be keyed on the corporation rather than on whichever
    character happened to have the role.
    """
    try:
        token, cid, scopes = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    if CORP_ASSETS_SCOPE not in scopes:
        return {
            "error": "missing_scope",
            "message": (
                f"Character {cid} has no {CORP_ASSETS_SCOPE} scope. Existing "
                f"tokens do not gain new scopes on refresh — run sso_login "
                f"(or sso_login_start / sso_login_finish) for this character "
                f"to re-authorize with it."
            ),
            "required_scope": CORP_ASSETS_SCOPE,
            "character_id": cid,
        }
    # Public endpoint, no auth and no scope — the corporation a character
    # belongs to is not privileged information.
    profile = await get_client().get_json(f"/characters/{cid}/")
    corp_id = int(profile["corporation_id"])
    rows = await get_client().get_all_pages(
        f"/corporations/{corp_id}/assets/", auth_token=token
    )
    return {**_rows(rows, cid, limit, complete), "corporation_id": corp_id}


async def my_corp_blueprints(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Blueprints owned by the character's CORPORATION.

    Requires esi-corporations.read_blueprints.v1 AND the in-game **Director**
    role (ESI `x-required-roles`), and separates the two failures for the same
    reason `my_corp_assets` does: both arrive as 403, one is fixed by re-running
    SSO and the other never resolves without an in-game role change.

    Exists because `my_corp_assets` CANNOT answer whether a corp-held blueprint
    can be installed. There a blueprint is a type_id and a quantity, so a stack
    of ten is either ten originals that run forever or one ten-run copy. `runs`
    is the distinction: -1 an ORIGINAL, a positive count a COPY with that many
    runs left. `quantity` encodes its own: -1 a singleton, -2 a copy, positive
    a stack of that many.

    Disjoint from `my_blueprints`: a blueprint in a corp hangar is corp-owned
    and never appears in the character's own list, so the two can be summed
    without double-counting.

    The envelope carries `corporation_id` as well as `character_id`, because a
    corp snapshot must key on the corporation rather than on whichever
    character happened to hold the role.
    """
    try:
        token, cid, scopes = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    if CORP_BLUEPRINTS_SCOPE not in scopes:
        return {
            "error": "missing_scope",
            "message": (
                f"Character {cid} has no {CORP_BLUEPRINTS_SCOPE} scope. "
                f"Existing tokens do not gain new scopes on refresh — run "
                f"sso_login (or sso_login_start / sso_login_finish) for this "
                f"character to re-authorize with it."
            ),
            "required_scope": CORP_BLUEPRINTS_SCOPE,
            "character_id": cid,
        }
    # Public endpoint, no auth and no scope — the corporation a character
    # belongs to is not privileged information.
    profile = await get_client().get_json(f"/characters/{cid}/")
    corp_id = int(profile["corporation_id"])
    rows = await get_client().get_all_pages(
        f"/corporations/{corp_id}/blueprints/", auth_token=token
    )
    return {**_rows(rows, cid, limit, complete), "corporation_id": corp_id}


async def my_blueprints(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Blueprints the character owns (requires esi-characters.read_blueprints.v1).

    Exists because the ASSET list cannot answer the only question that matters
    about a blueprint: whether it can be installed again tomorrow. `/assets/`
    reports a blueprint as a type_id and a quantity and stops there, so ten
    rows of one formula could be ten originals that run forever or a single
    ten-run copy stack that is gone once consumed. Anything gating "can I start
    this job" on the asset count is guessing.

    This endpoint carries the distinction ESI encodes in two fields:

      * `runs` = -1 means an ORIGINAL (BPO): infinite runs, and one job at a
        time per copy of it.
      * `runs` > 0 means a COPY (BPC) with exactly that many runs left.
      * `quantity` = -1 marks a single stacked-as-one item, -2 marks a BPC;
        a positive quantity is a stack of that many.

    `material_efficiency` / `time_efficiency` come along because a caller
    costing a job needs the ME of the blueprint it will actually install, not
    the ME it assumed.

    Missing SCOPE is returned as a structured error BEFORE any request, for
    the reason my_corp_assets spells out: the token already tells us, and
    spending a 403 against ESI's error budget to rediscover it is waste. Note
    that adding this scope to the default set does NOT help an existing token
    — `_refresh` preserves the stored scope string, so every character issued
    a token before this tool existed must re-run SSO to acquire it.
    """
    try:
        token, cid, scopes = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    if BLUEPRINTS_SCOPE not in scopes:
        return {
            "error": "missing_scope",
            "message": (
                f"Character {cid} has no {BLUEPRINTS_SCOPE} scope. Existing "
                f"tokens do not gain new scopes on refresh — run sso_login "
                f"(or sso_login_start / sso_login_finish) for this character "
                f"to re-authorize with it."
            ),
            "required_scope": BLUEPRINTS_SCOPE,
            "character_id": cid,
        }
    rows = await get_client().get_all_pages(
        f"/characters/{cid}/blueprints/", auth_token=token
    )
    return _rows(rows, cid, limit, complete)


async def my_open_orders(
    character: int | str | None = None,
    limit: int | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Open market orders (requires esi-markets.read_character_orders.v1)."""
    try:
        token, cid, _scopes = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_json(f"/characters/{cid}/orders/", auth_token=token)
    return _rows(rows, cid, limit, complete)


async def my_skills(character: int | str | None = None) -> dict[str, Any]:
    """Trained skills (requires esi-skills.read_skills.v1). Filter Accounting /
    Broker Relations / hauling skills to plug into arbitrage math."""
    try:
        token, cid, _scopes = await _auth(character)
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
        token, cid, _scopes = await _auth(character)
    except CharacterSelectionError as e:
        return e.detail
    rows = await get_client().get_json(
        f"/characters/{cid}/industry/jobs/",
        params={"include_completed": str(include_completed).lower()},
        auth_token=token,
    )
    return _rows(rows, cid, limit, complete)
