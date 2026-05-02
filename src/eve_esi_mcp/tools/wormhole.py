from __future__ import annotations

import csv
from functools import lru_cache
from importlib import resources
from typing import Any

from ..esi_client import get_client
from ..ids import resolve_ids_bulk


@lru_cache(maxsize=1)
def _load_statics() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    path = resources.files("eve_esi_mcp.data").joinpath("jspace_statics.csv")
    with path.open("r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            system = row["system"].strip()
            if not system:
                continue
            statics = [s.strip() for s in row.get("statics", "").split(",") if s.strip()]
            out[system] = {
                "system": system,
                "class": row.get("class", "").strip(),
                "effect": row.get("effect", "").strip() or None,
                "statics": statics,
                "notes": row.get("notes", "").strip() or None,
            }
    return out


async def jspace_info(system: str) -> dict[str, Any]:
    """Lookup a wormhole (J-space) system by name.

    Returns ESI universe/systems data plus bundled static-wormhole metadata
    (class, environmental effect, static connections). Live wormhole connections
    are *not* in ESI — maintain those in Pathfinder/Tripwire.
    """
    statics = _load_statics()
    static_meta = statics.get(system)

    ids = await resolve_ids_bulk([system])
    sys_list = ids.get("systems") or []
    if not sys_list:
        return {
            "system": system,
            "error": "system not found via /universe/ids/ (names are case-sensitive)",
            "static_meta": static_meta,
        }
    sys_id = sys_list[0]["id"]
    sys_data = await get_client().get_json(f"/universe/systems/{sys_id}/")
    return {
        "system": system,
        "system_id": sys_id,
        "security_status": sys_data.get("security_status"),
        "constellation_id": sys_data.get("constellation_id"),
        "planets": sys_data.get("planets"),
        "static_meta": static_meta
        or {
            "warning": (
                "No bundled static-wormhole data for this system. The shipped "
                "jspace_statics.csv is a sample — replace with an SDE-derived dump "
                "for full coverage (see README)."
            )
        },
    }


def lookup_statics(wh_class: str) -> list[dict[str, Any]]:
    """List every J-system in the bundled dataset matching a class (e.g. 'C5')."""
    statics = _load_statics()
    wh_class = wh_class.upper().strip()
    return [v for v in statics.values() if v["class"].upper() == wh_class]
