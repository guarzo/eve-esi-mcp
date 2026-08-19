"""Endpoints that changed shape at newer ESI compatibility dates.

At compatibility_date 2026-08-18:
  - GET  /route/{o}/{d}/  -> 404; replaced by POST returning {"route": [...]}
  - GET  /sovereignty/map/ -> 404; unified into /sovereignty/systems/
Both verified against live ESI before this change.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")


@pytest.fixture(autouse=True)
def _fresh_client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_ESI_MCP_CONTACT", "test@example.com")
    monkeypatch.setenv("EVE_ESI_MCP_CACHE_DIR", str(tmp_path / "cache"))
    import eve_esi_mcp.config as cfg
    import eve_esi_mcp.esi_client as ec

    cfg._settings = None
    ec._client = None
    yield
    cfg._settings = None
    ec._client = None


async def test_route_uses_post_and_unwraps_the_route_key():
    from eve_esi_mcp.tools.universe import route

    with respx.mock(base_url="https://esi.evetech.net") as router:
        posted = router.post("/route/30000142/30002187/").mock(
            return_value=httpx.Response(200, json={"route": [30000142, 30000138, 30002187]})
        )
        result = await route(30000142, 30002187)

    assert posted.called
    assert result == [30000142, 30000138, 30002187]


async def test_jumps_between_counts_hops_not_systems():
    from eve_esi_mcp.tools.universe import jumps_between

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.post("/route/30000142/30002187/").mock(
            return_value=httpx.Response(200, json={"route": [1, 2, 3, 4]})
        )
        assert await jumps_between(30000142, 30002187) == 3


async def test_route_sends_the_wire_names_esi_actually_expects():
    """ESI silently returns a null route for unknown body keys, so these names
    must be exact: `avoid_systems` (not `avoid`) and `preference` (not `flag`)."""
    from eve_esi_mcp.tools.universe import route

    captured = {}

    def _capture(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"route": [1, 2, 3]})

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.post("/route/30000142/30002187/").mock(side_effect=_capture)
        await route(30000142, 30002187, flag="secure", avoid=[30000138])

    assert captured == {"preference": "Safer", "avoid_systems": [30000138]}


async def test_route_preference_names_are_mapped():
    from eve_esi_mcp.tools.universe import route

    for friendly, wire in [
        ("shortest", "Shorter"),
        ("secure", "Safer"),
        ("insecure", "LessSecure"),
    ]:
        captured = {}

        def _capture(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"route": [1, 2]})

        with respx.mock(base_url="https://esi.evetech.net") as router:
            router.post("/route/1/2/").mock(side_effect=_capture)
            await route(1, 2, flag=friendly)
        assert captured["preference"] == wire


async def test_route_raises_rather_than_reporting_zero_jumps_on_a_null_route():
    """A null route used to fall through to jumps_between returning 0 — a wrong
    answer that looks like a real one."""
    from eve_esi_mcp.esi_client import ESIError
    from eve_esi_mcp.tools.universe import jumps_between

    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.post("/route/1/2/").mock(
            return_value=httpx.Response(200, json={"route": None})
        )
        with pytest.raises(ESIError):
            await jumps_between(1, 2)


async def test_sovereignty_systems_replaces_sovereignty_map():
    from eve_esi_mcp.tools.activity import sovereignty_systems

    rows = [{"solar_system_id": i, "claim": {}} for i in range(200)]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/sovereignty/systems/").mock(
            return_value=httpx.Response(200, json={"solar_systems": rows})
        )
        result = await sovereignty_systems(limit=10)

    assert result["returned"] == 10
    assert result["total"] == 200
    assert result["truncated"] is True


async def test_sovereignty_map_is_gone():
    """The old name must not linger as a broken tool."""
    from eve_esi_mcp.tools import activity

    assert not hasattr(activity, "sovereignty_map")
