"""Player structures: the one authenticated lookup of a *universe* object.

Every other endpoint hands back a bare `location_id` for a citadel, and
`resolve_names` leaves it as a hole, because a player structure's identity is
visible only to pilots its owner lets dock. This tool fills the hole.

It has three outcomes where the corp tools have two, and the tests below pin
each one, because a caller sweeping thousands of unknown location_ids acts
differently on all three:

  * missing SCOPE -- fixable by re-running SSO, reported before any request;
  * no ACCESS -- a 403 past the scope check, permanent, must not be retried;
  * anything else -- still raises, because it is neither predictable nor final.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

STRUCTURES_SCOPE = "esi-universe.read_structures.v1"


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


def _add(char_id, name, scope=STRUCTURES_SCOPE):
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
async def test_a_visible_structure_comes_back_keyed_on_the_id_asked_for():
    """The caller looked this up to fill in a location_id it already had, so the
    id it asked with must come back alongside the name -- ESI's own body does
    not repeat it, and matching results back to the sweep is the whole job."""
    from eve_esi_mcp.tools.character import structure_info

    _add(95000101, "Docked Alice")
    respx.get("https://esi.evetech.net/universe/structures/1035466617946/").mock(
        return_value=httpx.Response(200, json={
            "name": "4-HWWF - WinterCo. Central Station",
            "owner_id": 98599770,
            "solar_system_id": 30000240,
            "type_id": 35834,
        }))

    out = await structure_info(1035466617946, character=95000101)

    assert out["structure_id"] == 1035466617946
    assert out["character_id"] == 95000101
    assert out["name"] == "4-HWWF - WinterCo. Central Station"
    assert out["solar_system_id"] == 30000240


@respx.mock
async def test_a_missing_scope_is_reported_without_calling_esi():
    """The token already says the scope is absent. Spending a 403 to rediscover
    it wastes ESI's error budget -- and that 403 is indistinguishable from the
    permanent no-docking one, so the caller would stop retrying a structure a
    fresh login would have opened."""
    from eve_esi_mcp.tools.character import structure_info

    _add(95000102, "Scopeless Bob", scope="esi-assets.read_assets.v1")
    route = respx.get(
        "https://esi.evetech.net/universe/structures/1035466617946/").mock(
        return_value=httpx.Response(200, json={"name": "should not be fetched"}))

    out = await structure_info(1035466617946, character=95000102)

    assert out["error"] == "missing_scope"
    assert out["required_scope"] == STRUCTURES_SCOPE
    assert not route.called


@respx.mock
async def test_a_403_past_the_scope_check_is_a_structured_permanent_no_access():
    """Past the scope check a 403 can only mean this character cannot dock here,
    which is a property of the structure's ACL and will not change on retry.
    Raising it would leave a sweep unable to tell 'stop asking' from a transient
    failure, and every retry burns error budget for a guaranteed 403."""
    from eve_esi_mcp.tools.character import structure_info

    _add(95000103, "Unwelcome Carol")
    respx.get("https://esi.evetech.net/universe/structures/1035466617946/").mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"}))

    out = await structure_info(1035466617946, character=95000103)

    assert out["error"] == "no_access"
    assert out["character_id"] == 95000103
    assert out["structure_id"] == 1035466617946
    # The distinction the caller branches on: this one is not the fixable kind.
    assert "required_scope" not in out


@respx.mock
async def test_a_non_403_failure_still_raises():
    """A structure that no longer exists (404) and a server fault are neither
    predictable from the token nor permanent, so they keep ESIError's normal
    meaning. Swallowing them into `no_access` would tell a caller to stop
    asking about a structure that is merely unreachable right now."""
    from eve_esi_mcp.esi_client import ESIError
    from eve_esi_mcp.tools.character import structure_info

    _add(95000104, "Stale Dave")
    respx.get("https://esi.evetech.net/universe/structures/1035466617946/").mock(
        return_value=httpx.Response(404, json={"error": "Structure not found"}))

    with pytest.raises(ESIError) as excinfo:
        await structure_info(1035466617946, character=95000104)

    assert excinfo.value.status == 404


@respx.mock
async def test_the_ids_the_caller_passed_win_over_anything_in_esis_body():
    """The success envelope is `{**info, structure_id, character_id}` in that
    order on purpose. The caller matches results back to the location_id it
    swept with, so if ESI ever returned a key of either name the values here
    must still be the ones asked with, not whatever the body carried."""
    from eve_esi_mcp.tools.character import structure_info

    _add(95000105, "Collision Erin")
    respx.get("https://esi.evetech.net/universe/structures/1044752365771/").mock(
        return_value=httpx.Response(200, json={
            "name": "Structure", "solar_system_id": 31001394,
            "owner_id": 98000001, "type_id": 35832,
            "structure_id": 999, "character_id": 999,
        }))

    out = await structure_info(1044752365771, character=95000105)

    assert out["structure_id"] == 1044752365771
    assert out["character_id"] == 95000105


# ---- wiring --------------------------------------------------------------


def test_the_default_scope_set_requests_structure_reads():
    """Without this in the DEFAULT set, the tool's own `missing_scope` message
    is a dead end: it tells the operator to run `sso_login` to acquire the
    scope, and a bare `sso_login()` reads this string, so they would
    re-authorize and get `missing_scope` again."""
    from eve_esi_mcp.config import get_settings

    assert "esi-universe.read_structures.v1" in get_settings().sso_scopes


async def test_the_structure_tool_is_registered_on_the_server():
    """A tool that exists but is never registered is unreachable over stdio,
    which is the only way eve-indy talks to this server."""
    from eve_esi_mcp.server import build_server

    tool = await build_server().get_tool("structure_info")

    assert tool.name == "structure_info"
