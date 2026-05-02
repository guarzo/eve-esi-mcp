from __future__ import annotations

from typing import Any, Literal

from ..esi_client import get_client
from ..ids import resolve_ids_bulk, resolve_names_bulk


async def resolve_names(ids: list[int]) -> list[dict[str, Any]]:
    """Resolve a list of numeric EVE IDs to names/categories.

    Covers characters, corporations, alliances, constellations, systems, regions,
    stations, factions, inventory types. Batched at 1000/req internally.
    """
    return await resolve_names_bulk(ids)


async def resolve_ids(names: list[str]) -> dict[str, Any]:
    """Resolve a list of EVE names (case-sensitive) to IDs grouped by category."""
    return await resolve_ids_bulk(names)


async def region_info(region_id: int) -> dict[str, Any]:
    return await get_client().get_json(f"/universe/regions/{region_id}/")


async def constellation_info(constellation_id: int) -> dict[str, Any]:
    return await get_client().get_json(f"/universe/constellations/{constellation_id}/")


async def system_info(system_id: int) -> dict[str, Any]:
    return await get_client().get_json(f"/universe/systems/{system_id}/")


async def type_info(type_id: int) -> dict[str, Any]:
    return await get_client().get_json(f"/universe/types/{type_id}/")


async def list_regions() -> list[int]:
    return await get_client().get_json("/universe/regions/")


async def route(
    origin: int,
    destination: int,
    flag: Literal["shortest", "secure", "insecure"] = "shortest",
    avoid: list[int] | None = None,
) -> list[int]:
    """Compute a k-space jump route between two solar systems.

    Returns the ordered list of system IDs from origin to destination (inclusive).
    `flag=secure` prefers hi-sec; `insecure` prefers low/null. Wormholes are NOT
    considered — ESI only knows gate connections.
    """
    params: dict[str, Any] = {"flag": flag}
    if avoid:
        params["avoid"] = ",".join(str(a) for a in avoid)
    return await get_client().get_json(
        f"/route/{origin}/{destination}/",
        params=params,
    )


async def jumps_between(origin: int, destination: int) -> int:
    """Convenience: number of gate jumps between two systems (shortest path)."""
    path = await route(origin, destination)
    return max(0, len(path) - 1)
