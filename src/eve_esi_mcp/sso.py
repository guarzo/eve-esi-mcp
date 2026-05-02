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


def _token_path() -> Path:
    return get_settings().data_dir / "sso_token.json"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def save_tokens(tokens: dict[str, Any]) -> None:
    tokens = {**tokens, "saved_at": int(time.time())}
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens))
    path.chmod(0o600)


def load_tokens() -> dict[str, Any] | None:
    path = _token_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())


def clear_tokens() -> None:
    path = _token_path()
    if path.exists():
        path.unlink()


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
    save_tokens(tokens)
    verified = await _verify(tokens["access_token"])
    _login_state.clear()
    return {
        "status": "logged_in",
        "character": verified,
        "scopes": scope_str.split(),
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
    tokens = load_tokens()
    if not tokens:
        return {"logged_in": False}
    access = await get_valid_access_token()
    if not access:
        return {"logged_in": False, "note": "refresh failed; run sso_login again"}
    verified = await _verify(access)
    return {"logged_in": True, "character": verified, "scopes": tokens.get("scope", "").split()}


async def sso_logout() -> dict[str, Any]:
    clear_tokens()
    return {"status": "logged_out"}


async def get_valid_access_token() -> str | None:
    """Return an access_token, refreshing if needed. None if not logged in."""
    settings = get_settings()
    tokens = load_tokens()
    if not tokens:
        return None

    expires_in = tokens.get("expires_in", 1200)
    saved_at = tokens.get("saved_at", 0)
    if time.time() < saved_at + expires_in - 60:
        return tokens.get("access_token")

    refresh = tokens.get("refresh_token")
    if not refresh:
        return None
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": settings.sso_client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        log.warning("sso.refresh_failed", status=r.status_code, body=r.text[:200])
        return None
    new_tokens = r.json()
    new_tokens["scope"] = tokens.get("scope")
    save_tokens(new_tokens)
    return new_tokens.get("access_token")


async def character_id() -> int | None:
    access = await get_valid_access_token()
    if not access:
        return None
    data = await _verify(access)
    char_id = data.get("CharacterID")
    return int(char_id) if char_id else None
