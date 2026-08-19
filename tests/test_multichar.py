"""Multi-character SSO: several logged-in characters, selectable per call."""

from __future__ import annotations

import json
import os

import httpx
import pytest
import respx

os.environ.setdefault("EVE_ESI_MCP_CONTACT", "test@example.com")


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


def _add(char_id, name, scope="esi-wallet.read_character_wallet.v1"):
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


# ---- store ---------------------------------------------------------------


def test_saving_two_characters_keeps_both():
    from eve_esi_mcp import sso

    _add(1001, "Alice")
    _add(1002, "Bob")
    store = sso.load_store()
    assert set(store) == {"1001", "1002"}
    assert store["1001"]["character_name"] == "Alice"


def test_token_store_is_not_world_readable():
    from eve_esi_mcp import sso

    _add(1001, "Alice")
    mode = sso._store_path().stat().st_mode & 0o777
    assert mode & 0o077 == 0, f"token store mode {oct(mode)} exposes refresh tokens"


def test_legacy_single_character_file_is_migrated(tmp_path):
    """An existing sso_token.json must not silently strand the user's login."""
    from eve_esi_mcp import sso

    legacy = sso._legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "access_token": "legacy-access",
                "refresh_token": "legacy-refresh",
                "expires_in": 1200,
                "saved_at": 9_999_999_999,
                "scope": "esi-wallet.read_character_wallet.v1",
            }
        )
    )

    migrated = sso.migrate_legacy_token(character_id=1001, character_name="Alice")
    assert migrated is True
    store = sso.load_store()
    assert store["1001"]["refresh_token"] == "legacy-refresh"
    assert not legacy.exists(), "legacy file should be consumed, not left behind"


def test_logout_removes_one_character_and_leaves_the_rest():
    from eve_esi_mcp import sso

    _add(1001, "Alice")
    _add(1002, "Bob")
    sso.clear_tokens(character_id=1001)
    assert set(sso.load_store()) == {"1002"}


def test_logout_without_argument_removes_all():
    from eve_esi_mcp import sso

    _add(1001, "Alice")
    _add(1002, "Bob")
    sso.clear_tokens()
    assert sso.load_store() == {}


# ---- selection -----------------------------------------------------------


async def test_single_character_is_the_implicit_default():
    from eve_esi_mcp.sso import resolve_character

    _add(1001, "Alice")
    rec = await resolve_character(None)
    assert rec["character_id"] == 1001


async def test_selection_by_id_and_by_name():
    from eve_esi_mcp.sso import resolve_character

    _add(1001, "Alice")
    _add(1002, "Bob")
    assert (await resolve_character(1002))["character_name"] == "Bob"
    assert (await resolve_character("Bob"))["character_id"] == 1002
    # Names are how humans refer to characters; matching should not be case-picky.
    assert (await resolve_character("alice"))["character_id"] == 1001


async def test_ambiguous_default_lists_the_available_characters():
    from eve_esi_mcp.sso import CharacterSelectionError, resolve_character

    _add(1001, "Alice")
    _add(1002, "Bob")
    with pytest.raises(CharacterSelectionError) as exc:
        await resolve_character(None)
    detail = exc.value.detail
    assert detail["error"] == "ambiguous_character"
    names = {c["character_name"] for c in detail["available_characters"]}
    assert names == {"Alice", "Bob"}


async def test_unknown_character_names_the_ones_that_exist():
    from eve_esi_mcp.sso import CharacterSelectionError, resolve_character

    _add(1001, "Alice")
    with pytest.raises(CharacterSelectionError) as exc:
        await resolve_character("Nobody")
    assert exc.value.detail["error"] == "unknown_character"
    assert "Alice" in str(exc.value.detail["available_characters"])


async def test_no_characters_logged_in_is_its_own_error():
    from eve_esi_mcp.sso import CharacterSelectionError, resolve_character

    with pytest.raises(CharacterSelectionError) as exc:
        await resolve_character(None)
    assert exc.value.detail["error"] == "not_logged_in"


# ---- tools ---------------------------------------------------------------


async def test_character_tools_select_the_named_character():
    from eve_esi_mcp.tools.character import my_wallet

    _add(1001, "Alice")
    _add(1002, "Bob")
    with respx.mock(base_url="https://esi.evetech.net") as router:
        route = router.get("/characters/1002/wallet/").mock(
            return_value=httpx.Response(200, json=555.5)
        )
        result = await my_wallet(character="Bob")

    assert route.called
    assert result["character_id"] == 1002
    assert result["balance_isk"] == 555.5


async def test_character_tools_return_the_structured_error_rather_than_raising():
    """The model needs to see the choices, not a stack trace."""
    from eve_esi_mcp.tools.character import my_wallet

    _add(1001, "Alice")
    _add(1002, "Bob")
    result = await my_wallet()
    assert result["error"] == "ambiguous_character"
    assert len(result["available_characters"]) == 2


async def test_list_characters_reports_everyone_logged_in():
    from eve_esi_mcp.sso import list_characters

    _add(1001, "Alice")
    _add(1002, "Bob")
    rows = await list_characters()
    assert {r["character_name"] for r in rows} == {"Alice", "Bob"}
    assert all("scopes" in r for r in rows)
