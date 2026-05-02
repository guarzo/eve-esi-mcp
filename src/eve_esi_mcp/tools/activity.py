from __future__ import annotations

from typing import Any

from ..esi_client import get_client


async def system_kills() -> list[dict[str, Any]]:
    """Ship/pod/NPC kills per system in the last hour (danger/activity heat map)."""
    return await get_client().get_json("/universe/system_kills/")


async def system_jumps() -> list[dict[str, Any]]:
    """Gate jumps per system in the last hour (traffic heat map)."""
    return await get_client().get_json("/universe/system_jumps/")


async def sovereignty_map() -> list[dict[str, Any]]:
    """Null-sec sovereignty holders per system (alliance/corp/faction)."""
    return await get_client().get_json("/sovereignty/map/")


async def sovereignty_campaigns() -> list[dict[str, Any]]:
    """Active sovereignty structure campaigns (attack/defend timers)."""
    return await get_client().get_json("/sovereignty/campaigns/")


async def hottest_systems(kind: str = "kills", limit: int = 20) -> list[dict[str, Any]]:
    """Shorthand: top-N systems by ship_kills, pod_kills, npc_kills, or ship_jumps."""
    if kind == "ship_jumps":
        data = await system_jumps()
        key = "ship_jumps"
    else:
        data = await system_kills()
        key = {
            "kills": "ship_kills",
            "ship_kills": "ship_kills",
            "pod_kills": "pod_kills",
            "npc_kills": "npc_kills",
        }.get(kind, "ship_kills")

    sorted_rows = sorted(data, key=lambda r: r.get(key, 0), reverse=True)
    return sorted_rows[:limit]
