from __future__ import annotations

from typing import Any

from ..esi_client import get_client
from ..limits import capped, tool_limit


async def _system_kills_raw() -> list[dict[str, Any]]:
    return await get_client().get_json("/universe/system_kills/")


async def _system_jumps_raw() -> list[dict[str, Any]]:
    return await get_client().get_json("/universe/system_jumps/")


async def system_kills(limit: int | None = None) -> dict[str, Any]:
    """Ship/pod/NPC kills per system in the last hour (danger heat map).

    Covers every system with activity. For "where is it hottest", prefer
    hottest_systems(), which ranks first and returns only the top N.
    """
    return capped(await _system_kills_raw(), limit=tool_limit(limit))


async def system_jumps(limit: int | None = None) -> dict[str, Any]:
    """Gate jumps per system in the last hour (traffic heat map).

    For "where is it busiest", prefer hottest_systems('ship_jumps').
    """
    return capped(await _system_jumps_raw(), limit=tool_limit(limit))


async def sovereignty_systems(limit: int | None = None) -> dict[str, Any]:
    """Sovereignty claims per system (alliance/corp/faction).

    Replaces the old /sovereignty/map/, which ESI removed at compatibility date
    2026-05-19 in favour of the unified /sovereignty/systems/.
    """
    payload = await get_client().get_json("/sovereignty/systems/")
    rows = payload.get("solar_systems", []) if isinstance(payload, dict) else payload
    return capped(rows, limit=tool_limit(limit))


async def sovereignty_campaigns() -> list[dict[str, Any]]:
    """Active sovereignty structure campaigns (attack/defend timers)."""
    return await get_client().get_json("/sovereignty/campaigns/")


async def hottest_systems(kind: str = "kills", limit: int = 20) -> dict[str, Any]:
    """Top-N systems by ship_kills, pod_kills, npc_kills, or ship_jumps.

    Ranks across every system before truncating, so the top N is the true top N.
    """
    if kind == "ship_jumps":
        data = await _system_jumps_raw()
        key = "ship_jumps"
    else:
        data = await _system_kills_raw()
        key = {
            "kills": "ship_kills",
            "ship_kills": "ship_kills",
            "pod_kills": "pod_kills",
            "npc_kills": "npc_kills",
        }.get(kind, "ship_kills")

    ranked = sorted(data, key=lambda r: r.get(key, 0), reverse=True)
    return capped(ranked, limit=tool_limit(limit), extra={"ranked_by": key})
