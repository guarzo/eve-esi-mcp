from __future__ import annotations

from typing import Any, Literal

from ..esi_client import ESIError, get_client
from ..ids import resolve_ids_bulk, resolve_names_bulk

# ESI renamed the route security options when GET became POST. The friendly names are
# kept on this tool's surface; these are the wire values it now expects.
_ROUTE_PREFERENCE = {
    "shortest": "Shorter",
    "secure": "Safer",
    "insecure": "LessSecure",
}


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
    # ESI replaced GET /route/{o}/{d}/ with POST at compatibility date 2025-09-30. The
    # GET form 404s at any newer date; options moved from query params into the body
    # under new names, and the response is now {"route": [...]} not a bare array.
    #
    # Unknown body keys are NOT rejected — ESI returns a null route instead — so the
    # names here have to be exactly right or this fails silently.
    body: dict[str, Any] = {"preference": _ROUTE_PREFERENCE[flag]}
    if avoid:
        body["avoid_systems"] = list(avoid)

    payload = await get_client().post_json(
        f"/route/{origin}/{destination}/",
        json=body,
    )
    route_systems = payload.get("route") if isinstance(payload, dict) else payload
    if not route_systems:
        raise ESIError(
            200,
            f"ESI returned no route from {origin} to {destination} "
            f"(preference={body['preference']}, avoid={len(avoid or [])} systems)",
            f"/route/{origin}/{destination}/",
        )
    return route_systems


async def jumps_between(origin: int, destination: int) -> int:
    """Convenience: number of gate jumps between two systems (shortest path)."""
    path = await route(origin, destination)
    return max(0, len(path) - 1)
