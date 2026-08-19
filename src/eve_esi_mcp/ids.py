from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from .esi_client import get_client


# PLEX and other account-services items trade on a single cross-cluster market rather
# than per-region. Asking for them in The Forge returns an empty order book and a stale
# history stub, which reads as "illiquid here" rather than "you asked the wrong region".
GLOBAL_MARKET_REGION_ID = 19000001

# type_ids known to trade only on the global market.
GLOBAL_MARKET_TYPE_IDS = frozenset({44992})  # PLEX


def is_global_market_type(type_id: int | None) -> bool:
    return type_id is not None and type_id in GLOBAL_MARKET_TYPE_IDS


@lru_cache(maxsize=1)
def load_regions() -> dict[str, int]:
    with resources.files("eve_esi_mcp.data").joinpath("regions.json").open("r") as f:
        regions: dict[str, int] = json.load(f)["regions"]
        regions.setdefault("Global", GLOBAL_MARKET_REGION_ID)
        return regions


@lru_cache(maxsize=1)
def load_hubs() -> dict[str, dict[str, Any]]:
    with resources.files("eve_esi_mcp.data").joinpath("hubs.json").open("r") as f:
        return json.load(f)["hubs"]


def region_id(region: str | int) -> int:
    if isinstance(region, int):
        return region
    if region.isdigit():
        return int(region)
    regions = load_regions()
    if region in regions:
        return regions[region]
    lower = {k.lower(): v for k, v in regions.items()}
    if region.lower() in lower:
        return lower[region.lower()]
    # Traders think in hubs ("Jita"), not regions ("The Forge"). Accept either.
    hubs = load_hubs()
    if region.lower() in hubs:
        return int(hubs[region.lower()]["region_id"])
    raise ValueError(
        f"Unknown region '{region}'. Known: {sorted(regions)} — or call resolve_ids(['{region}'])."
    )


def hub(name: str) -> dict[str, Any]:
    hubs = load_hubs()
    key = name.lower()
    if key not in hubs:
        raise ValueError(f"Unknown hub '{name}'. Known: {sorted(hubs)}")
    return hubs[key]


async def resolve_ids_bulk(names: list[str]) -> dict[str, Any]:
    """POST /universe/ids/ — returns {categories: [{id, name}, ...]}.

    Input names must be exact (case-sensitive in ESI). No batching needed under 500 names.
    """
    client = get_client()
    # ESI returns 400 on empty list; short-circuit.
    if not names:
        return {}
    return await client.post_json("/universe/ids/", json=names)


async def resolve_names_bulk(ids: list[int]) -> list[dict[str, Any]]:
    """POST /universe/names/ — returns [{id, name, category}, ...]. Batches at 1000."""
    client = get_client()
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), 1000):
        chunk = ids[i : i + 1000]
        if not chunk:
            continue
        out.extend(await client.post_json("/universe/names/", json=chunk))
    return out
