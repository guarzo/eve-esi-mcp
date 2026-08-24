"""Corporation assets: the corp-scoped counterpart to `my_assets`.

Three properties the eve-indy sync layer depends on, none of which the
character-scoped tools already guarantee:

  * the envelope carries `corporation_id`, so a caller can key a snapshot on
    the corporation rather than on whichever character happened to fetch it;
  * a 403 (no Director role) surfaces as an error carrying the STATUS, because
    the MCP stdio boundary flattens exceptions to text and the caller
    classifies "uncovered corp" from that text;
  * the walk is paginated, since a corp running structures holds far more than
    one page of assets.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")

CORP_SCOPE = "esi-assets.read_corporation_assets.v1"


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


def _add(char_id, name, scope=CORP_SCOPE):
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


async def test_envelope_carries_the_corporation_id_it_resolved():
    """The caller keys its snapshot on the corp, so the corp must come back."""
    from eve_esi_mcp.tools.character import my_corp_assets

    _add(1001, "Alice")
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/").mock(
            return_value=httpx.Response(200, json={"corporation_id": 98000001})
        )
        router.get("/corporations/98000001/assets/").mock(
            return_value=httpx.Response(
                200, json=[{"item_id": 5, "type_id": 34, "quantity": 7}]
            )
        )
        result = await my_corp_assets(complete=True)

    assert result["corporation_id"] == 98000001
    assert result["character_id"] == 1001
    assert [r["item_id"] for r in result["items"]] == [5]


async def test_a_403_raises_with_the_status_in_the_message():
    """eve-indy classifies "no Director" by string-matching the error text.

    `MCP.call` on the eve-indy side renders a tool failure as plain text, so a
    status that lives only on the exception object never crosses the boundary.
    If the status stops appearing in str(exc), a corp the operator simply lacks
    Director in becomes indistinguishable from a 500 — and eve-indy's nightly
    refresh fails instead of degrading.
    """
    from eve_esi_mcp.esi_client import ESIError
    from eve_esi_mcp.tools.character import my_corp_assets

    _add(1001, "Alice")
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/").mock(
            return_value=httpx.Response(200, json={"corporation_id": 98000001})
        )
        router.get("/corporations/98000001/assets/").mock(
            return_value=httpx.Response(403, text="character does not have required role")
        )
        with pytest.raises(ESIError) as excinfo:
            await my_corp_assets(complete=True)

    assert excinfo.value.status == 403
    assert "403" in str(excinfo.value)


async def test_every_page_of_a_multi_page_corp_hangar_is_returned():
    """A corp running structures holds more than one page; a partial walk would
    silently understate held stock and under-order the difference."""
    from eve_esi_mcp.tools.character import my_corp_assets

    _add(1001, "Alice")
    with respx.mock(base_url="https://esi.evetech.net") as router:
        router.get("/characters/1001/").mock(
            return_value=httpx.Response(200, json={"corporation_id": 98000001})
        )

        def paged(request):
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(
                    200, json=[{"item_id": 1}], headers={"X-Pages": "2"}
                )
            return httpx.Response(200, json=[{"item_id": 2}], headers={"X-Pages": "2"})

        router.get("/corporations/98000001/assets/").mock(side_effect=paged)
        result = await my_corp_assets(complete=True)

    assert sorted(r["item_id"] for r in result["items"]) == [1, 2]


async def test_it_returns_the_structured_error_when_the_character_is_ambiguous():
    """Same contract as the other character tools: the model sees the choices."""
    from eve_esi_mcp.tools.character import my_corp_assets

    _add(1001, "Alice")
    _add(1002, "Bob")
    result = await my_corp_assets()

    assert result["error"] == "ambiguous_character"


# ---- wiring --------------------------------------------------------------


def test_the_default_scope_set_requests_corporation_assets():
    """Without this in the DEFAULT set, `sso_login` grants a token that can
    never read corp assets, and the 403 looks like a missing Director role
    rather than a missing scope."""
    from eve_esi_mcp.config import get_settings

    assert "esi-assets.read_corporation_assets.v1" in get_settings().sso_scopes


async def test_the_corp_assets_tool_is_registered_on_the_server():
    """A tool that exists but is never registered is unreachable over stdio,
    which is the only way eve-indy talks to this server."""
    from eve_esi_mcp.server import build_server

    tool = await build_server().get_tool("my_corp_assets")

    assert tool.name == "my_corp_assets"
