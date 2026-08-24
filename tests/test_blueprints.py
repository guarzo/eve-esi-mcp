"""Character blueprints: the BPO/BPC distinction the asset list cannot make.

`/assets/` reports a blueprint as a type_id and a quantity, so ten rows of one
formula could be ten originals that run forever or a single ten-run copy stack
that is gone once consumed. eve-indy gates "can I start this job" on that
difference, so these tests pin the two properties it depends on:

  * `runs` survives the envelope intact, since -1 (original) versus a positive
    count (copy) IS the distinction;
  * a token issued before this scope existed reports `missing_scope` WITHOUT
    spending a request, because re-running SSO is the fix and a 403 would look
    like something else.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

BP_SCOPE = "esi-characters.read_blueprints.v1"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_ESI_MCP_CONTACT", "test@example.com")
    monkeypatch.setenv("EVE_ESI_MCP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("EVE_ESI_MCP_DATA_DIR", str(tmp_path / "data"))
    import eve_esi_mcp.config as cfg
    import eve_esi_mcp.esi_client as ec

    cfg._settings = None
    ec._client = None
    yield
    cfg._settings = None
    ec._client = None


def _add(char_id, name, scope=BP_SCOPE):
    from eve_esi_mcp import sso

    sso.save_character(
        character_id=char_id,
        character_name=name,
        tokens={
            "access_token": f"token-{char_id}",
            "refresh_token": f"refresh-{char_id}",
            "expires_in": 1200,
        },
        scope=scope,
    )


async def test_runs_distinguishes_an_original_from_a_copy():
    """-1 runs is a BPO and installs forever; 10 runs is a BPC that depletes.
    Both arrive as the same type_id, which is exactly why assets cannot tell
    them apart."""
    from eve_esi_mcp.tools.character import my_blueprints

    _add(1001, "Alice")
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/blueprints/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "item_id": 1,
                        "type_id": 57499,
                        "quantity": -1,
                        "runs": -1,
                        "material_efficiency": 10,
                        "time_efficiency": 20,
                        "location_id": 60003760,
                        "location_flag": "Hangar",
                    },
                    {
                        "item_id": 2,
                        "type_id": 57499,
                        "quantity": -2,
                        "runs": 10,
                        "material_efficiency": 2,
                        "time_efficiency": 4,
                        "location_id": 60003760,
                        "location_flag": "Hangar",
                    },
                ],
            )
        )
        result = await my_blueprints(complete=True)

    rows = result["items"]
    assert [r["runs"] for r in rows] == [-1, 10]
    assert [r["material_efficiency"] for r in rows] == [10, 2]
    assert result["character_id"] == 1001


async def test_missing_scope_is_reported_without_spending_a_request():
    """A token issued before this scope existed cannot gain it on refresh, so
    the fix is re-running SSO -- and saying so beats a 403 the caller would
    have to classify from text."""
    from eve_esi_mcp.tools.character import my_blueprints

    _add(1002, "Bob", scope="esi-assets.read_assets.v1")
    # assert_all_called=False because the POINT of this test is that the route
    # is never reached; respx's default would fail it for the passing case.
    with respx.mock(base_url="https://esi.evetech.net",
                    assert_all_called=False) as router:
        route = router.get("/characters/1002/blueprints/").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await my_blueprints(complete=True)

    assert result["error"] == "missing_scope"
    assert result["required_scope"] == BP_SCOPE
    assert result["character_id"] == 1002
    assert not route.called


async def test_the_walk_is_paginated():
    """A long-running industrialist holds more than one page of blueprints."""
    from eve_esi_mcp.tools.character import my_blueprints

    _add(1003, "Cass")
    page1 = [
        {"item_id": i, "type_id": 46165, "quantity": -1, "runs": -1}
        for i in range(1000)
    ]
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1003/blueprints/", params={"page": "1"}).mock(
            return_value=httpx.Response(200, json=page1, headers={"X-Pages": "2"})
        )
        router.get("/characters/1003/blueprints/", params={"page": "2"}).mock(
            return_value=httpx.Response(
                200,
                json=[{"item_id": 9999, "type_id": 46175, "quantity": -1, "runs": -1}],
                headers={"X-Pages": "2"},
            )
        )
        result = await my_blueprints(complete=True)

    assert len(result["items"]) == 1001
