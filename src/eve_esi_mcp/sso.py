from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import httpx
import structlog

from .config import get_settings

log = structlog.get_logger(__name__)

# Module-level state shared between sso_login_start and sso_login_finish.
_login_state: dict[str, Any] = {}

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
VERIFY_URL = "https://login.eveonline.com/oauth/verify"


class CharacterSelectionError(RuntimeError):
    """Raised when the caller's `character` argument cannot be resolved.

    Carries a structured `detail` so tools can return the available choices to the
    model rather than surfacing a stack trace it cannot act on.
    """

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(detail.get("message", detail.get("error", "character error")))
        self.detail = detail


def _store_path() -> Path:
    return get_settings().data_dir / "sso_tokens.json"


def _legacy_path() -> Path:
    """Pre-multi-character single-token file. Migrated on first use."""
    return get_settings().data_dir / "sso_token.json"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def load_store() -> dict[str, dict[str, Any]]:
    """All logged-in characters, keyed by character_id as a string."""
    path = _store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("characters", {})
    except (json.JSONDecodeError, OSError):
        log.warning("sso.store_unreadable", path=str(path))
        return {}


def save_store(store: dict[str, dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps({"characters": store}, indent=2))
    path.chmod(0o600)


def save_character(
    character_id: int,
    character_name: str,
    tokens: dict[str, Any],
    scope: str = "",
) -> None:
    """Add or replace one character. Other characters are left untouched."""
    store = load_store()
    store[str(character_id)] = {
        **tokens,
        "character_id": int(character_id),
        "character_name": character_name,
        "scope": scope or tokens.get("scope", ""),
        "saved_at": tokens.get("saved_at") or int(time.time()),
    }
    save_store(store)


def clear_tokens(character_id: int | None = None) -> int:
    """Log out one character, or all of them. Returns how many were removed."""
    store = load_store()
    if character_id is None:
        removed = len(store)
        save_store({})
        _legacy_path().unlink(missing_ok=True)
        return removed
    if store.pop(str(character_id), None) is None:
        return 0
    save_store(store)
    return 1


def migrate_legacy_token(character_id: int, character_name: str) -> bool:
    """Fold a pre-multi-character sso_token.json into the store.

    Identity isn't in the token file, so the caller supplies it (from _verify).
    Returns True if a migration happened.
    """
    legacy = _legacy_path()
    if not legacy.exists():
        return False
    try:
        tokens = json.loads(legacy.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("sso.legacy_unreadable", path=str(legacy))
        legacy.unlink(missing_ok=True)
        return False
    save_character(
        character_id=character_id,
        character_name=character_name,
        tokens=tokens,
        scope=tokens.get("scope", ""),
    )
    legacy.unlink(missing_ok=True)
    log.info("sso.migrated_legacy_token", character_id=character_id)
    return True


async def _adopt_legacy_if_present() -> None:
    """Best-effort migration: identify the legacy token, then fold it in."""
    legacy = _legacy_path()
    if not legacy.exists():
        return
    try:
        tokens = json.loads(legacy.read_text())
    except (json.JSONDecodeError, OSError):
        legacy.unlink(missing_ok=True)
        return
    access = tokens.get("access_token")
    verified = await _verify(access) if access else {"error": "no access token"}
    char_id = verified.get("CharacterID")
    if not char_id:
        # Access token expired; try the refresh token before giving up.
        refreshed = await _refresh(tokens)
        if refreshed:
            verified = await _verify(refreshed["access_token"])
            char_id = verified.get("CharacterID")
            tokens = refreshed
    if not char_id:
        log.warning("sso.legacy_migration_failed", note="re-run sso_login")
        legacy.unlink(missing_ok=True)
        return
    migrate_legacy_token(int(char_id), verified.get("CharacterName", "unknown"))


class _CodeHandler(BaseHTTPRequestHandler):
    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ("code", "state", "error", "error_description"):
            v = qs.get(key, [""])[0]
            if v:
                _CodeHandler.captured[key] = v
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if _CodeHandler.captured.get("code"):
            self.wfile.write(
                b"<h1>EVE SSO login complete</h1>"
                b"<p>You can close this tab and return to your MCP client.</p>"
            )
        else:
            err = _CodeHandler.captured.get("error", "unknown")
            desc = _CodeHandler.captured.get("error_description", "")
            self.wfile.write(
                f"<h1>EVE SSO login failed</h1><p><b>{err}</b>: {desc}</p>".encode()
            )

    def log_message(self, *_: Any) -> None:
        return


def _stop_listener() -> None:
    server = _login_state.pop("server", None)
    thread = _login_state.pop("thread", None)
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001
            pass
    if thread is not None:
        try:
            thread.join(timeout=2)
        except Exception:  # noqa: BLE001
            pass


async def sso_login_start(scopes: str | None = None) -> dict[str, Any]:
    """Begin the EVE SSO PKCE flow. Returns immediately with the authorize URL.

    Side effects:
    - Starts a local HTTP listener on `EVE_SSO_CALLBACK_PORT` (default 8765) to
      catch EVE's redirect.
    - Attempts to open the URL in your default browser via `webbrowser.open`.
    - Also prints the URL to the server's stderr — visible in Claude Desktop's
      MCP log panel — as a fallback for headless / no-browser environments.

    After you sign in to EVE and approve the app, call `sso_login_finish` to
    exchange the authorization code for tokens. Tokens are persisted at
    `$XDG_DATA_HOME/eve-esi-mcp/sso_token.json` (0600).
    """
    settings = get_settings()
    if not settings.sso_client_id:
        return {
            "error": (
                "EVE_SSO_CLIENT_ID is not set. Register a developer app at "
                "https://developers.eveonline.com/applications (Authentication Only "
                "or Authentication & API Access, with PKCE enabled), and set the "
                f"callback URL to http://localhost:{settings.sso_callback_port}/callback. "
                "Then add EVE_SSO_CLIENT_ID to the `env` block in claude_desktop_config.json."
            )
        }

    # Tear down any previous half-finished attempt.
    _stop_listener()
    _CodeHandler.captured = {}

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://localhost:{settings.sso_callback_port}/callback"
    scope_str = scopes or settings.sso_scopes

    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": settings.sso_client_id,
        "scope": scope_str,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    authorize_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    try:
        server = HTTPServer(("localhost", settings.sso_callback_port), _CodeHandler)
    except OSError as exc:
        return {
            "error": (
                f"could not bind localhost:{settings.sso_callback_port} ({exc}). "
                "Another process is using the port — change EVE_SSO_CALLBACK_PORT "
                "in claude_desktop_config.json, and update your EVE app's callback URL to match."
            )
        }
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    _login_state.update(
        {
            "verifier": verifier,
            "state": state,
            "scope_str": scope_str,
            "redirect_uri": redirect_uri,
            "server": server,
            "thread": thread,
            "started_at": time.time(),
        }
    )

    # Best-effort browser open. Returns False on headless or if no browser registered.
    browser_opened = False
    try:
        browser_opened = webbrowser.open(authorize_url, new=1, autoraise=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("sso.webbrowser_open_failed", error=str(exc))

    # Also dump the URL to stderr so users on headless boxes can copy it from
    # Claude Desktop's MCP log panel.
    print(f"\n[eve-esi-mcp] EVE SSO authorize URL:\n{authorize_url}\n", file=sys.stderr, flush=True)
    log.info("sso.awaiting_callback", browser_opened=browser_opened)

    return {
        "authorize_url": authorize_url,
        "browser_opened": browser_opened,
        "callback_listener": redirect_uri,
        "next_step": (
            "Open the authorize_url in a browser if it didn't open automatically, "
            "sign in to EVE, approve the scopes, then call sso_login_finish."
        ),
    }


async def sso_login_finish(timeout_seconds: int = 300) -> dict[str, Any]:
    """Wait for the EVE SSO callback (after the user authorises in their browser),
    exchange the code for access+refresh tokens, and persist them.

    Call `sso_login_start` first. Default wait is 5 minutes.
    """
    if not _login_state.get("server"):
        return {
            "error": (
                "no login in progress — call sso_login_start first to get the authorize URL."
            )
        }

    settings = get_settings()
    deadline = time.time() + max(10, timeout_seconds)
    try:
        while time.time() < deadline:
            if _CodeHandler.captured.get("code") or _CodeHandler.captured.get("error"):
                break
            await asyncio.sleep(0.5)
    finally:
        _stop_listener()

    if _CodeHandler.captured.get("error"):
        return {
            "error": f"EVE returned error: {_CodeHandler.captured.get('error')} "
            f"({_CodeHandler.captured.get('error_description', '')})"
        }

    code = _CodeHandler.captured.get("code")
    returned_state = _CodeHandler.captured.get("state")
    if not code:
        return {
            "error": (
                f"no authorization code received within {timeout_seconds}s. "
                "Click the authorize_url returned by sso_login_start and complete sign-in, "
                "then call sso_login_finish again."
            )
        }
    if returned_state != _login_state.get("state"):
        return {"error": "state mismatch — possible CSRF; aborting"}

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.sso_client_id,
                "code_verifier": _login_state["verifier"],
                "redirect_uri": _login_state["redirect_uri"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        return {"error": f"token exchange failed: {r.status_code} {r.text}"}

    tokens = r.json()
    scope_str = _login_state.get("scope_str", "")
    tokens["scope"] = scope_str
    verified = await _verify(tokens["access_token"])
    _login_state.clear()

    char_id = verified.get("CharacterID")
    if not char_id:
        return {"error": f"could not identify character: {verified}"}

    # Add this character alongside any already logged in, rather than replacing.
    save_character(
        character_id=int(char_id),
        character_name=verified.get("CharacterName", "unknown"),
        tokens=tokens,
        scope=scope_str,
    )
    store = load_store()
    return {
        "status": "logged_in",
        "character_id": int(char_id),
        "character_name": verified.get("CharacterName", "unknown"),
        "character": verified,
        "scopes": scope_str.split(),
        "logged_in_characters": len(store),
    }


async def sso_login(scopes: str | None = None) -> dict[str, Any]:
    """One-shot wrapper: start + wait for callback in a single tool call.

    Useful from a CLI but awkward in MCP clients because Claude can't show you
    the URL during the wait. Prefer sso_login_start + sso_login_finish.
    """
    started = await sso_login_start(scopes=scopes)
    if "error" in started:
        return started
    finished = await sso_login_finish(timeout_seconds=300)
    finished["authorize_url"] = started["authorize_url"]
    finished["browser_opened"] = started["browser_opened"]
    return finished


async def _verify(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(VERIFY_URL, headers={"Authorization": f"Bearer {access_token}"})
    return r.json() if r.status_code == 200 else {"error": r.text}


async def sso_status() -> dict[str, Any]:
    """Every logged-in character, with scopes and token expiry."""
    await _adopt_legacy_if_present()
    store = load_store()
    if not store:
        return {"logged_in": False, "characters": []}
    now = int(time.time())
    return {
        "logged_in": True,
        "character_count": len(store),
        "characters": [
            {
                "character_id": rec["character_id"],
                "character_name": rec.get("character_name", "unknown"),
                "scopes": (rec.get("scope") or "").split(),
                "token_expires_in_seconds": max(
                    0, rec.get("saved_at", 0) + rec.get("expires_in", 0) - now
                ),
            }
            for rec in store.values()
        ],
    }


async def sso_logout(character_id: int | None = None) -> dict[str, Any]:
    """Log out one character, or every character when called with no argument."""
    removed = clear_tokens(character_id=character_id)
    return {
        "status": "logged_out",
        "removed": removed,
        "scope": "one" if character_id is not None else "all",
        "remaining": len(load_store()),
    }


async def list_characters() -> list[dict[str, Any]]:
    """Logged-in characters available to the `character` argument on other tools."""
    await _adopt_legacy_if_present()
    return [
        {
            "character_id": rec["character_id"],
            "character_name": rec.get("character_name", "unknown"),
            "scopes": (rec.get("scope") or "").split(),
        }
        for rec in load_store().values()
    ]


def _summarise(store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "character_id": r["character_id"],
            "character_name": r.get("character_name", "unknown"),
        }
        for r in store.values()
    ]


async def resolve_character(character: int | str | None = None) -> dict[str, Any]:
    """Pick which logged-in character a call applies to.

    None + exactly one character logged in -> that character. None + several ->
    CharacterSelectionError listing the choices, so the model can pick rather than
    silently getting whichever happens to be first.
    """
    await _adopt_legacy_if_present()
    store = load_store()
    if not store:
        raise CharacterSelectionError(
            {
                "error": "not_logged_in",
                "message": "No characters are logged in. Run sso_login_start / sso_login_finish.",
                "available_characters": [],
            }
        )

    if character is None:
        if len(store) == 1:
            return next(iter(store.values()))
        raise CharacterSelectionError(
            {
                "error": "ambiguous_character",
                "message": (
                    f"{len(store)} characters are logged in. Pass `character` "
                    f"(id or name) to choose one."
                ),
                "available_characters": _summarise(store),
            }
        )

    key = str(character)
    if key in store:
        return store[key]
    wanted = key.casefold()
    for rec in store.values():
        if str(rec.get("character_name", "")).casefold() == wanted:
            return rec
    raise CharacterSelectionError(
        {
            "error": "unknown_character",
            "message": f"No logged-in character matches {character!r}.",
            "available_characters": _summarise(store),
        }
    )


async def _refresh(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Exchange a refresh token for a fresh access token. None if it failed."""
    refresh = rec.get("refresh_token")
    if not refresh:
        return None
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": get_settings().sso_client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        log.warning("sso.refresh_failed", status=r.status_code, body=r.text[:200])
        return None
    new_tokens = r.json()
    new_tokens["scope"] = rec.get("scope", "")
    new_tokens["saved_at"] = int(time.time())
    return new_tokens


async def get_valid_access_token(character: int | str | None = None) -> str | None:
    """Access token for the selected character, refreshing if near expiry."""
    try:
        rec = await resolve_character(character)
    except CharacterSelectionError:
        return None

    if time.time() < rec.get("saved_at", 0) + rec.get("expires_in", 1200) - 60:
        return rec.get("access_token")

    new_tokens = await _refresh(rec)
    if not new_tokens:
        return None
    save_character(
        character_id=rec["character_id"],
        character_name=rec.get("character_name", "unknown"),
        tokens=new_tokens,
        scope=rec.get("scope", ""),
    )
    return new_tokens.get("access_token")


async def character_id(character: int | str | None = None) -> int | None:
    """character_id of the selected character, without a network round-trip.

    The identity is recorded at login, so this no longer calls the deprecated
    /oauth/verify on every authenticated tool call.
    """
    try:
        return int((await resolve_character(character))["character_id"])
    except (CharacterSelectionError, KeyError, TypeError, ValueError):
        return None
