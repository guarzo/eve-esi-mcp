"""Corporation blueprints: the corp-scoped counterpart to `my_blueprints`.

The eve-indy blueprint gate asks "can this line be installed?", and the
operator holds formulas in corp hangars. `/corporations/{id}/assets/` cannot
answer it -- there a blueprint is a type_id and a quantity, so a stack of ten
is either ten originals or one ten-run copy. Only this endpoint carries `runs`.

Three properties the sync layer depends on:

  * the envelope carries `corporation_id`, so the snapshot keys on the
    corporation rather than on whichever character had the role;
  * a missing SCOPE is a structured error returned BEFORE any request, so the
    caller can tell "re-run SSO" (fixable) from a 403 "no Director" (not);
  * the walk is paginated, because a corp hangar holds more than one page.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

CORP_BP_SCOPE = "esi-corporations.read_blueprints.v1"


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


def _add(char_id, name, scope=CORP_BP_SCOPE):
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


@respx.mock
async def test_envelope_carries_the_corporation_id_it_resolved():
    """The caller keys its snapshot on the corporation, so the corporation id
    must come back -- a snapshot keyed on the fetching character would be
    erased by the next character who pulls the same corp."""
    from eve_esi_mcp.tools.character import my_corp_blueprints

    _add(95000001, "Director Alice")
    respx.get("https://esi.evetech.net/characters/95000001/").mock(
        return_value=httpx.Response(200, json={"corporation_id": 98000001}))
    respx.get("https://esi.evetech.net/corporations/98000001/blueprints/").mock(
        return_value=httpx.Response(200, json=[
            {"item_id": 1, "type_id": 46175, "quantity": -1, "runs": -1,
             "material_efficiency": 10, "time_efficiency": 20,
             "location_id": 60003760, "location_flag": "CorpSAG1"},
        ], headers={"X-Pages": "1"}))

    out = await my_corp_blueprints(character=95000001, complete=True)

    assert out["corporation_id"] == 98000001
    assert out["character_id"] == 95000001
    assert [r["item_id"] for r in out["items"]] == [1]


@respx.mock
async def test_a_missing_scope_is_reported_without_calling_esi():
    """The token already says the scope is absent, so spending a 403 to
    rediscover it wastes ESI's error budget -- and the caller would read that
    403 as a missing Director role, writing the corporation off permanently
    when re-running SSO is all it needs."""
    from eve_esi_mcp.tools.character import my_corp_blueprints

    _add(95000002, "Scopeless Bob", scope="esi-assets.read_assets.v1")
    route = respx.get(
        "https://esi.evetech.net/corporations/98000001/blueprints/").mock(
        return_value=httpx.Response(200, json=[]))

    out = await my_corp_blueprints(character=95000002, complete=True)

    assert out["error"] == "missing_scope"
    assert out["required_scope"] == CORP_BP_SCOPE
    assert out["character_id"] == 95000002
    assert not route.called


@respx.mock
async def test_the_runs_field_survives_the_walk():
    """`runs` is the whole reason this tool exists: -1 is an original that
    installs forever, a positive count is a copy with that many runs left.
    Dropping it would leave the gate guessing exactly as the asset feed does."""
    from eve_esi_mcp.tools.character import my_corp_blueprints

    _add(95000003, "Director Carol")
    respx.get("https://esi.evetech.net/characters/95000003/").mock(
        return_value=httpx.Response(200, json={"corporation_id": 98000002}))
    respx.get("https://esi.evetech.net/corporations/98000002/blueprints/").mock(
        return_value=httpx.Response(200, json=[
            {"item_id": 10, "type_id": 46175, "quantity": -1, "runs": -1,
             "material_efficiency": 10, "time_efficiency": 20,
             "location_id": 60003760, "location_flag": "CorpSAG1"},
            {"item_id": 11, "type_id": 46176, "quantity": -2, "runs": 7,
             "material_efficiency": 2, "time_efficiency": 4,
             "location_id": 60003760, "location_flag": "CorpSAG2"},
        ], headers={"X-Pages": "1"}))

    out = await my_corp_blueprints(character=95000003, complete=True)

    assert {r["item_id"]: r["runs"] for r in out["items"]} == {10: -1, 11: 7}


@respx.mock
async def test_every_page_is_walked():
    """A corp hangar holds more than one page of blueprints, and a caller that
    gates installs on this must never see a partial set: a formula missing
    from an unwalked page reads as 'you cannot run this'."""
    from eve_esi_mcp.tools.character import my_corp_blueprints

    _add(95000004, "Director Dave")
    respx.get("https://esi.evetech.net/characters/95000004/").mock(
        return_value=httpx.Response(200, json={"corporation_id": 98000003}))

    def _page(request):
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(200, headers={"X-Pages": "2"}, json=[
                {"item_id": 20, "type_id": 46175, "quantity": -1, "runs": -1,
                 "material_efficiency": 0, "time_efficiency": 0,
                 "location_id": 1, "location_flag": "CorpSAG1"}])
        return httpx.Response(200, headers={"X-Pages": "2"}, json=[
            {"item_id": 21, "type_id": 46176, "quantity": -1, "runs": -1,
             "material_efficiency": 0, "time_efficiency": 0,
             "location_id": 1, "location_flag": "CorpSAG1"}])

    respx.get(
        "https://esi.evetech.net/corporations/98000003/blueprints/"
    ).mock(side_effect=_page)

    out = await my_corp_blueprints(character=95000004, complete=True)

    assert sorted(r["item_id"] for r in out["items"]) == [20, 21]
