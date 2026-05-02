from __future__ import annotations

import asyncio
import random
from typing import Any

import hishel
import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import Settings, get_settings

log = structlog.get_logger(__name__)


class ESIError(RuntimeError):
    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"{status} {message} [{url}]")
        self.status = status
        self.url = url


class ESIClient:
    """Thin async wrapper over ESI. Honours cache, error-limit, pagination.

    One shared instance per server process — created lazily via `get_client()`.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        storage = hishel.AsyncFileStorage(base_path=settings.cache_dir / "http")
        controller = hishel.Controller(
            cacheable_methods=["GET"],
            cacheable_status_codes=[200, 203, 300, 301, 304, 308],
            allow_heuristics=False,
            allow_stale=False,
        )
        transport = hishel.AsyncCacheTransport(
            transport=httpx.AsyncHTTPTransport(http2=True, retries=0),
            storage=storage,
            controller=controller,
        )
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            transport=transport,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": settings.user_agent(),
                "Accept": "application/json",
            },
        )
        # Guards concurrent error-limit waiters so we don't stampede.
        self._error_limit_lock = asyncio.Lock()
        self._error_limit_wait_until: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- core request ------------------------------------------------------

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> httpx.Response:
        await self._respect_error_limit()

        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        async for attempt in AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(4),
            wait=wait_exponential_jitter(initial=0.5, max=15.0),
            retry=retry_if_exception(_is_retryable),
        ):
            with attempt:
                resp = await self._client.get(path, params=params, headers=headers)
                self._update_error_limit(resp)
                if resp.status_code >= 500 or resp.status_code == 420:
                    raise ESIError(resp.status_code, resp.text[:200], str(resp.request.url))
                if resp.status_code >= 400:
                    # 4xx other than 420 aren't retryable — surface immediately.
                    raise ESIError(resp.status_code, resp.text[:500], str(resp.request.url))
                return resp
        raise RuntimeError("unreachable")

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> Any:
        resp = await self.get(path, params=params, auth_token=auth_token)
        return resp.json()

    async def post_json(
        self,
        path: str,
        *,
        json: Any,
        params: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> Any:
        await self._respect_error_limit()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        resp = await self._client.post(path, params=params, json=json, headers=headers)
        self._update_error_limit(resp)
        if resp.status_code >= 400:
            raise ESIError(resp.status_code, resp.text[:500], str(resp.request.url))
        return resp.json()

    # ---- pagination --------------------------------------------------------

    async def get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> list[Any]:
        """Fetch all pages of a paginated ESI endpoint.

        ESI pagination uses the `X-Pages` response header and a `page` query param.
        Page 1 is fetched first to learn the page count; remaining pages run with a
        concurrency cap.
        """
        base_params = dict(params or {})
        base_params["page"] = 1
        first = await self.get(path, params=base_params, auth_token=auth_token)
        items: list[Any] = list(first.json())
        total_pages = int(first.headers.get("X-Pages", "1"))
        if total_pages <= 1:
            return items

        sem = asyncio.Semaphore(self.settings.page_concurrency)

        async def fetch(page: int) -> list[Any]:
            async with sem:
                await asyncio.sleep(random.uniform(0, 0.15))  # jitter
                p = dict(params or {})
                p["page"] = page
                r = await self.get(path, params=p, auth_token=auth_token)
                return list(r.json())

        results = await asyncio.gather(*(fetch(p) for p in range(2, total_pages + 1)))
        for chunk in results:
            items.extend(chunk)
        return items

    # ---- error limit -------------------------------------------------------

    def _update_error_limit(self, resp: httpx.Response) -> None:
        remain = resp.headers.get("X-ESI-Error-Limit-Remain")
        reset = resp.headers.get("X-ESI-Error-Limit-Reset")
        if remain is None or reset is None:
            return
        try:
            r = int(remain)
            t = int(reset)
        except ValueError:
            return
        if r <= self.settings.error_limit_floor:
            loop = asyncio.get_event_loop()
            self._error_limit_wait_until = max(self._error_limit_wait_until, loop.time() + t + 1)
            log.warning(
                "esi.error_limit_low",
                remain=r,
                reset_seconds=t,
                url=str(resp.request.url),
            )

    async def _respect_error_limit(self) -> None:
        loop = asyncio.get_event_loop()
        now = loop.time()
        if now < self._error_limit_wait_until:
            async with self._error_limit_lock:
                wait = self._error_limit_wait_until - loop.time()
                if wait > 0:
                    log.warning("esi.error_limit_sleep", seconds=round(wait, 1))
                    await asyncio.sleep(wait)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ESIError):
        return exc.status >= 500 or exc.status == 420
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return True
    return False


_client: ESIClient | None = None


def get_client() -> ESIClient:
    global _client
    if _client is None:
        _client = ESIClient(get_settings())
    return _client
