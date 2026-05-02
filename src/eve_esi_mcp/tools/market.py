from __future__ import annotations

from typing import Any, Literal

from ..esi_client import get_client
from ..ids import region_id as _region_id


async def market_orders(
    region: str | int,
    type_id: int | None = None,
    order_type: Literal["buy", "sell", "all"] = "all",
    location_id: int | None = None,
    page_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch regional market orders from /markets/{region_id}/orders/.

    Parameters
    ----------
    region : region name (e.g. "The Forge") or numeric region_id.
    type_id : optional; if provided, filters to one item (much cheaper).
    order_type : buy, sell, or all.
    location_id : optional station/structure filter applied client-side.
    page_limit : cap on pages fetched (safety net for region-wide scans).
    """
    rid = _region_id(region)
    params: dict[str, Any] = {"order_type": order_type}
    if type_id is not None:
        params["type_id"] = type_id

    client = get_client()
    path = f"/markets/{rid}/orders/"
    if type_id is not None:
        # Per-type calls are small; single paginated fetch is fine.
        orders = await client.get_all_pages(path, params=params)
    else:
        if page_limit is None:
            page_limit = 3  # region-wide scans are huge; require explicit opt-in
        # Manually walk pages with cap.
        orders = []
        for page in range(1, page_limit + 1):
            p = dict(params)
            p["page"] = page
            resp = await client.get(path, params=p)
            chunk = resp.json()
            orders.extend(chunk)
            total = int(resp.headers.get("X-Pages", "1"))
            if page >= total:
                break

    if location_id is not None:
        orders = [o for o in orders if o.get("location_id") == location_id]
    return orders


async def market_history(region: str | int, type_id: int) -> list[dict[str, Any]]:
    """Daily market history (up to ~400 days) for a type in a region.

    Each entry: {date, average, highest, lowest, order_count, volume}.
    """
    rid = _region_id(region)
    return await get_client().get_json(
        f"/markets/{rid}/history/", params={"type_id": type_id}
    )


async def market_prices() -> list[dict[str, Any]]:
    """Global average + adjusted prices used for industry cost calculations.

    Returns list of {type_id, average_price, adjusted_price}. Adjusted price is the
    EIV input — feed it into build_cost_estimate.
    """
    return await get_client().get_json("/markets/prices/")


async def best_bid_ask(region: str | int, type_id: int) -> dict[str, Any]:
    """Summarise best bid, best ask, spread, and recent liquidity.

    Convenience for the common profit question "should I buy here or sell here?".
    """
    rid = _region_id(region)
    orders = await market_orders(region=rid, type_id=type_id, order_type="all")
    buys = [o for o in orders if o.get("is_buy_order")]
    sells = [o for o in orders if not o.get("is_buy_order")]
    best_bid = max((o["price"] for o in buys), default=None)
    best_ask = min((o["price"] for o in sells), default=None)

    # 7-day average volume from history.
    hist = await market_history(region=rid, type_id=type_id)
    recent = hist[-7:] if len(hist) >= 7 else hist
    avg_daily_vol = sum(h["volume"] for h in recent) / max(1, len(recent))
    avg_daily_isk = sum(h["volume"] * h["average"] for h in recent) / max(1, len(recent))

    spread_abs = (best_ask - best_bid) if (best_bid and best_ask) else None
    spread_pct = (spread_abs / best_bid * 100) if (spread_abs and best_bid) else None

    return {
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
    }


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
