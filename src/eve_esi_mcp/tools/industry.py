from __future__ import annotations

from typing import Any, Literal

from ..esi_client import get_client
from .market import market_prices
from .universe import jumps_between

Activity = Literal[
    "manufacturing",
    "researching_time_efficiency",
    "researching_material_efficiency",
    "copying",
    "invention",
    "reaction",
]


async def industry_systems(activity: Activity | None = None) -> list[dict[str, Any]]:
    """Cost indices per system from /industry/systems/.

    Each entry: {solar_system_id, cost_indices: [{activity, cost_index}, ...]}.
    If `activity` is given, rows are reshaped to {system_id, cost_index}.
    """
    raw = await get_client().get_json("/industry/systems/")
    if activity is None:
        return raw
    out = []
    for row in raw:
        for ci in row.get("cost_indices", []):
            if ci["activity"] == activity:
                out.append(
                    {
                        "solar_system_id": row["solar_system_id"],
                        "cost_index": ci["cost_index"],
                    }
                )
                break
    out.sort(key=lambda r: r["cost_index"])
    return out


async def cheapest_system(
    activity: Activity,
    limit: int = 25,
    nearest_to: int | None = None,
) -> list[dict[str, Any]]:
    """Rank systems by cost_index (ascending) for a given activity.

    If `nearest_to` is a system_id, results include jumps from that system (slow —
    ESI route calls fan out; be sensible with limits).
    """
    rows = await industry_systems(activity=activity)
    top = rows[: max(limit, 1)]
    if nearest_to is None:
        return top
    enriched = []
    for r in top:
        try:
            j = await jumps_between(nearest_to, r["solar_system_id"])
        except Exception:
            j = None
        enriched.append({**r, "jumps_from_origin": j})
    return enriched


async def build_cost_estimate(
    product_type_id: int,
    materials: list[dict[str, int]],
    system_id: int,
    runs: int = 1,
    me_pct: float = 10.0,
    job_tax_pct: float = 1.5,
    facility_bonus_pct: float = 1.0,
) -> dict[str, Any]:
    """EIV-style manufacturing cost estimate.

    materials: list of {"type_id": int, "quantity_per_run": int}.
    me_pct: material-efficiency research on the BPO/BPC (0-10).
    job_tax_pct: NPC-station 1.5%, player structures usually configurable lower.
    facility_bonus_pct: 1.0 = NPC station. Rigged engineering complex can be <1.

    Returns estimated install cost + materials cost at /markets/prices/ adjusted prices.
    The "adjusted_price" from ESI is specifically what CCP uses for EIV.
    """
    prices = {p["type_id"]: p for p in await market_prices()}
    material_cost = 0.0
    rows = []
    me_mult = (1 - me_pct / 100) * facility_bonus_pct
    for m in materials:
        qty = max(1, round(m["quantity_per_run"] * runs * me_mult))
        adj = prices.get(m["type_id"], {}).get("adjusted_price", 0.0) or 0.0
        avg = prices.get(m["type_id"], {}).get("average_price", 0.0) or 0.0
        line = adj * qty
        material_cost += line
        rows.append(
            {
                "type_id": m["type_id"],
                "qty": qty,
                "adjusted_price": adj,
                "average_price": avg,
                "line_cost_eiv": line,
            }
        )

    # Install cost = EIV * cost_index * (1 + system_tax + facility_tax)
    idx_rows = await industry_systems(activity="manufacturing")
    ci = next((r["cost_index"] for r in idx_rows if r["solar_system_id"] == system_id), 0.0)
    eiv = material_cost
    install_cost = eiv * ci * (1 + job_tax_pct / 100)

    return {
        "product_type_id": product_type_id,
        "system_id": system_id,
        "cost_index": ci,
        "eiv_isk": eiv,
        "install_cost_isk": install_cost,
        "total_estimated_cost_isk": eiv + install_cost,
        "materials": rows,
        "notes": (
            "EIV uses adjusted_price (CCP's input); your actual material spend is "
            "average_price or better. Compare product's best_bid_ask to this total "
            "cost minus your actual material cost to see margin."
        ),
    }
