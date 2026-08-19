from __future__ import annotations

from typing import Any, Literal

from ..esi_client import get_client
from ..ids import GLOBAL_MARKET_REGION_ID, is_global_market_type
from ..ids import region_id as _region_id
from ..limits import capped, tool_limit

# A region-wide order book is ~414 pages in The Forge, ~59k tokens per page. One page
# is enough to show the model the shape; anything more must be asked for explicitly.
DEFAULT_REGION_PAGE_LIMIT = 1
MAX_REGION_PAGE_LIMIT = 20


def _resolve_market_region(
    region: str | int, type_id: int | None
) -> tuple[int, int | None]:
    """Return (region_to_query, redirected_from) for a market lookup."""
    rid = _region_id(region)
    if is_global_market_type(type_id) and rid != GLOBAL_MARKET_REGION_ID:
        return GLOBAL_MARKET_REGION_ID, rid
    return rid, None


def _redirect_note(type_id: int | None) -> str:
    return (
        f"type_id {type_id} trades only on the global market (region "
        f"{GLOBAL_MARKET_REGION_ID}), not per-region. The requested region was ignored "
        f"and the global market queried instead."
    )


async def market_orders(
    region: str | int,
    type_id: int | None = None,
    order_type: Literal["buy", "sell", "all"] = "all",
    location_id: int | None = None,
    page_limit: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Regional market orders from /markets/{region_id}/orders/.

    region : region name (e.g. "The Forge") or numeric region_id.
    type_id : optional, but strongly preferred — filtering to one item costs one page,
        while an unfiltered region scan is hundreds.
    order_type : buy, sell, or all.
    location_id : optional station/structure filter, applied client-side.
    page_limit : pages to fetch on an unfiltered region scan (default 1).
    limit : max rows returned. Truncation is always reported.

    Globally-traded types (PLEX) are redirected to the global market automatically.
    """
    rid, redirected_from = _resolve_market_region(region, type_id)
    params: dict[str, Any] = {"order_type": order_type}
    if type_id is not None:
        params["type_id"] = type_id

    client = get_client()
    path = f"/markets/{rid}/orders/"
    pages_available = 1
    pages_fetched = 1

    if type_id is not None:
        # Per-type books are one page in practice; fetch them whole.
        orders = await client.get_all_pages(path, params=params)
    else:
        cap = DEFAULT_REGION_PAGE_LIMIT if page_limit is None else page_limit
        cap = min(max(cap, 1), MAX_REGION_PAGE_LIMIT)
        orders = []
        pages_fetched = 0
        for page in range(1, cap + 1):
            p = dict(params)
            p["page"] = page
            resp = await client.get(path, params=p)
            orders.extend(resp.json())
            pages_fetched += 1
            pages_available = int(resp.headers.get("X-Pages", "1"))
            if page >= pages_available:
                break

    if location_id is not None:
        orders = [o for o in orders if o.get("location_id") == location_id]

    extra: dict[str, Any] = {"region_id": rid, "type_id": type_id}
    if type_id is None:
        # Only meaningful for a capped region scan; a per-type book is fetched whole,
        # so reporting a page count there would be misleading rather than informative.
        extra["pages_available"] = pages_available
        extra["pages_fetched"] = pages_fetched
    note = None
    if redirected_from is not None:
        extra["region_redirected_from"] = redirected_from
        note = _redirect_note(type_id)

    # A per-type book is small and worth returning whole; an unfiltered region scan
    # is ~59k tokens per page even after the page cap, so it gets a row cap too.
    row_limit = limit if type_id is not None else tool_limit(limit)
    result = capped(orders, limit=row_limit, extra=extra, note=note)
    if pages_fetched < pages_available:
        result["truncated"] = True
        page_note = (
            f"Fetched {pages_fetched} of {pages_available} pages — a small slice of the "
            f"region's order book. Pass a type_id for a complete book on one item, or "
            f"raise page_limit (max {MAX_REGION_PAGE_LIMIT})."
        )
        result["note"] = (
            f"{result['note']} {page_note}" if result.get("note") else page_note
        )
    return result


async def market_history(region: str | int, type_id: int) -> dict[str, Any]:
    """Daily market history (up to ~400 days) for a type in a region.

    Each entry: {date, average, highest, lowest, order_count, volume}.
    Globally-traded types are redirected to the global market automatically.
    """
    rid, redirected_from = _resolve_market_region(region, type_id)
    rows = await get_client().get_json(
        f"/markets/{rid}/history/", params={"type_id": type_id}
    )
    extra: dict[str, Any] = {"region_id": rid, "type_id": type_id}
    note = None
    if redirected_from is not None:
        extra["region_redirected_from"] = redirected_from
        note = _redirect_note(type_id)
    # ~400 small rows; keep it whole but still report the shape.
    return capped(rows, limit=None, extra=extra, note=note)


async def _all_market_prices() -> list[dict[str, Any]]:
    """Unbounded /markets/prices/ for internal lookups. Not exposed as a tool."""
    return await get_client().get_json("/markets/prices/")


async def market_prices(
    type_ids: list[int] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Global average + adjusted prices, used for industry cost calculations.

    Returns {type_id, average_price, adjusted_price}. `adjusted_price` is the EIV
    input CCP uses — feed it into build_cost_estimate.

    The full dataset is ~14,000 rows and does not fit in context. Pass `type_ids` to
    price specific items exactly; otherwise the result is truncated and says so.
    """
    rows = await _all_market_prices()
    if type_ids:
        wanted = set(type_ids)
        rows = [r for r in rows if r.get("type_id") in wanted]
        return capped(rows, limit=None, extra={"filtered_to_type_ids": len(wanted)})
    return capped(
        rows,
        limit=tool_limit(limit),
        note="Pass type_ids=[...] to price specific items exactly instead of sampling.",
    )


async def best_bid_ask(region: str | int, type_id: int) -> dict[str, Any]:
    """Best bid, best ask, spread, and recent liquidity for one item in one region.

    The cheap answer to "should I buy here or sell here?" — a few hundred bytes rather
    than the raw order array. Globally-traded types are redirected automatically.
    """
    rid, redirected_from = _resolve_market_region(region, type_id)
    book = await market_orders(region=rid, type_id=type_id, order_type="all", limit=None)
    orders = book["items"]
    buys = [o for o in orders if o.get("is_buy_order")]
    sells = [o for o in orders if not o.get("is_buy_order")]
    best_bid = max((o["price"] for o in buys), default=None)
    best_ask = min((o["price"] for o in sells), default=None)

    hist = (await market_history(region=rid, type_id=type_id))["items"]
    recent = hist[-7:] if len(hist) >= 7 else hist
    avg_daily_vol = sum(h["volume"] for h in recent) / max(1, len(recent))
    avg_daily_isk = sum(h["volume"] * h["average"] for h in recent) / max(1, len(recent))

    spread_abs = (best_ask - best_bid) if (best_bid and best_ask) else None
    spread_pct = (spread_abs / best_bid * 100) if (spread_abs and best_bid) else None

    out: dict[str, Any] = {
        "region_id": rid,
        "type_id": type_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_isk": spread_abs,
        "spread_pct": spread_pct,
        "buy_order_count": len(buys),
        "sell_order_count": len(sells),
        "avg_daily_volume_7d": avg_daily_vol,
        "avg_daily_isk_7d": avg_daily_isk,
        "no_orders": not orders,
    }
    if redirected_from is not None:
        out["region_redirected_from"] = redirected_from
        out["note"] = _redirect_note(type_id)
    if not orders:
        empty_note = (
            f"No orders at all for type_id {type_id} in region {rid}. Null prices here "
            f"mean 'nothing on the book', not 'cheap' — check the type_id and region "
            f"before drawing any conclusion."
        )
        out["note"] = f"{out['note']} {empty_note}" if out.get("note") else empty_note
    return out


async def top_traded(
    region: str | int,
    type_ids: list[int],
    days: int = 7,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Rank a candidate list of type_ids by recent daily ISK turnover in a region.

    ESI has no "all movers" endpoint, so callers must pass the universe of items
    they care about (e.g. all T2 modules, or the top-100 by guess). This uses
    /markets/{region}/history/ per type — be mindful of fan-out.
    """
    rid = _region_id(region)
    client = get_client()
    results: list[dict[str, Any]] = []
    for tid in type_ids:
        try:
            hist = await client.get_json(
                f"/markets/{rid}/history/", params={"type_id": tid}
            )
        except Exception:
            continue
        if not hist:
            continue
        recent = hist[-days:]
        avg_vol = sum(h["volume"] for h in recent) / max(1, len(recent))
        avg_isk = sum(h["volume"] * h["average"] for h in recent) / max(1, len(recent))
        results.append(
            {
                "type_id": tid,
                "avg_daily_volume": avg_vol,
                "avg_daily_isk": avg_isk,
            }
        )
    results.sort(key=lambda r: r["avg_daily_isk"], reverse=True)
    return results[:limit]
