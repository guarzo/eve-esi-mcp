from __future__ import annotations

from typing import Any

from ..ids import hub as hub_info
from .market import best_bid_ask
from .universe import jumps_between


async def compare_hubs(
    type_id: int,
    hubs: list[str] | None = None,
) -> dict[str, Any]:
    """Side-by-side best-bid/best-ask for an item across major trade hubs.

    Surfaces the richest and thinnest markets at a glance, plus the jump distance
    between the cheapest seller and richest buyer.
    """
    hubs = hubs or ["jita", "amarr", "dodixie", "rens", "hek"]
    rows: list[dict[str, Any]] = []
    for h in hubs:
        meta = hub_info(h)
        snap = await best_bid_ask(region=meta["region_id"], type_id=type_id)
        rows.append(
            {
                "hub": h,
                "system_id": meta["system_id"],
                "region": meta["region_name"],
                **snap,
            }
        )

    sells = [r for r in rows if r["best_ask"] is not None]
    buys = [r for r in rows if r["best_bid"] is not None]
    cheapest = min(sells, key=lambda r: r["best_ask"]) if sells else None
    richest = max(buys, key=lambda r: r["best_bid"]) if buys else None

    spread = None
    spread_pct = None
    jumps = None
    if cheapest and richest and cheapest["hub"] != richest["hub"]:
        spread = richest["best_bid"] - cheapest["best_ask"]
        spread_pct = spread / cheapest["best_ask"] * 100
        try:
            jumps = await jumps_between(cheapest["system_id"], richest["system_id"])
        except Exception:
            jumps = None

    return {
        "type_id": type_id,
        "rows": rows,
        "cheapest_sell_hub": cheapest["hub"] if cheapest else None,
        "richest_buy_hub": richest["hub"] if richest else None,
        "gross_spread_isk": spread,
        "gross_spread_pct": spread_pct,
        "jumps_cheapest_to_richest": jumps,
    }


async def find_spreads(
    src_hub: str,
    dst_hub: str,
    type_ids: list[int],
    min_margin_pct: float = 10.0,
    min_daily_isk: float = 5e8,
) -> list[dict[str, Any]]:
    """Rank candidate items by profitable spread between two hubs.

    Strategy: for each type_id, pull best_bid_ask at src and dst. Keep items where
    the ask at `src` is meaningfully below the bid at `dst` and both hubs have
    enough daily ISK turnover for the strategy to matter.
    """
    src = hub_info(src_hub)
    dst = hub_info(dst_hub)

    results: list[dict[str, Any]] = []
    for tid in type_ids:
        try:
            a = await best_bid_ask(region=src["region_id"], type_id=tid)
            b = await best_bid_ask(region=dst["region_id"], type_id=tid)
        except Exception:
            continue
        if a["best_ask"] is None or b["best_bid"] is None:
            continue
        margin = b["best_bid"] - a["best_ask"]
        if margin <= 0:
            continue
        margin_pct = margin / a["best_ask"] * 100
        if margin_pct < min_margin_pct:
            continue
        dst_isk = b["avg_daily_isk_7d"]
        if dst_isk < min_daily_isk:
            continue
        results.append(
            {
                "type_id": tid,
                "src_ask": a["best_ask"],
                "dst_bid": b["best_bid"],
                "margin_isk": margin,
                "margin_pct": margin_pct,
                "dst_avg_daily_isk_7d": dst_isk,
                "src_avg_daily_volume_7d": a["avg_daily_volume_7d"],
            }
        )

    jumps: int | None
    try:
        jumps = await jumps_between(src["system_id"], dst["system_id"])
    except Exception:
        jumps = None

    results.sort(key=lambda r: r["margin_pct"], reverse=True)
    return [{"jumps": jumps, **r} for r in results]


def freight_cost(
    m3: float,
    collateral: float,
    rate_per_m3: float = 1000.0,
    collateral_pct: float = 1.0,
    minimum_fee: float = 500_000.0,
) -> dict[str, float]:
    """Typical freight-service pricing: max(min_fee, m3*rate + collateral*pct%)."""
    fee = m3 * rate_per_m3 + collateral * (collateral_pct / 100.0)
    fee = max(fee, minimum_fee)
    return {
        "fee_isk": fee,
        "per_m3_component": m3 * rate_per_m3,
        "collateral_component": collateral * (collateral_pct / 100.0),
        "minimum_applied": fee == minimum_fee,
    }


def profit_after_fees(
    buy_price: float,
    sell_price: float,
    qty: int,
    sales_tax_pct: float = 3.6,
    broker_fee_pct_buy: float = 3.0,
    broker_fee_pct_sell: float = 3.0,
    freight_isk: float = 0.0,
) -> dict[str, float]:
    """Net profit on a hauling trade after sales tax + broker fees + freight.

    Defaults approximate unskilled rates; tune with user's actual Accounting /
    Broker Relations levels.
    """
    cost_basis = buy_price * qty * (1 + broker_fee_pct_buy / 100)
    gross = sell_price * qty
    sell_fees = gross * (sales_tax_pct + broker_fee_pct_sell) / 100
    net = gross - sell_fees - cost_basis - freight_isk
    return {
        "cost_basis_isk": cost_basis,
        "gross_revenue_isk": gross,
        "sell_fees_isk": sell_fees,
        "freight_isk": freight_isk,
        "net_profit_isk": net,
        "roi_pct": net / cost_basis * 100 if cost_basis else 0.0,
    }
