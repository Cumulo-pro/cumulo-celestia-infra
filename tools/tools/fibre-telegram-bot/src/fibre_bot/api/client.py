"""Async client for the Fibre Network Monitor API.

Handles the API conventions in one place: per-IP rate limits (client-side token buckets),
429 with Retry-After, 503 while the aggregator warms up, 409 for ambiguous monikers and the
X-Fibre-Param-Adjusted header for clamped parameters.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import quote

import httpx

from fibre_bot import USER_AGENT

log = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]


class FibreAPIError(Exception):
    """Any failure talking to the API (transport error or unexpected status)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotFoundError(FibreAPIError):
    pass


class WarmingUpError(FibreAPIError):
    """503 outside /status: the aggregator has not finished its first cycle."""


class RateLimitedError(FibreAPIError):
    pass


class FibreNotActiveError(FibreAPIError):
    """503 `fibre_not_active`: the chain exists but Fibre is not activated yet. Not an outage:
    never retried, never alerted on. Carries the activation info the API sends."""

    def __init__(self, body: dict[str, Any], retry_after: float) -> None:
        super().__init__("fibre not active yet", status=503)
        self.chain = str(body.get("chain") or "")
        self.network = str(body.get("network") or "")
        self.app_version = str(body.get("app_version") or "")
        activation = body.get("activation")
        self.activation: dict[str, Any] = activation if isinstance(activation, dict) else {}
        self.retry_after = retry_after


class AmbiguousError(FibreAPIError):
    """409: a moniker matched several validators."""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        super().__init__("ambiguous validator name", status=409)
        self.candidates = candidates


class TokenBucket:
    """Simple async token bucket: `rate` tokens per minute, up to `burst` stored."""

    def __init__(self, per_minute: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self._rate = per_minute / 60.0
        self._burst = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, sleep: SleepFn = asyncio.sleep) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._tokens = min(self._burst, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await sleep((1 - self._tokens) / self._rate)


def _normalize_candidates(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                out.append({"valcons": item, "moniker": item})
    return out


class FibreClient:
    def __init__(
        self,
        base_url: str,
        chain: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        sleep: SleepFn = asyncio.sleep,
        general_limiter: TokenBucket | None = None,
        search_limiter: TokenBucket | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chain = chain
        self._own_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
        )
        self._max_retries = max_retries
        self._sleep = sleep
        # The API allows 120/min (burst 60) and 20/min for /search: stay well below.
        self._general = general_limiter or TokenBucket(per_minute=100, burst=30)
        self._search = search_limiter or TokenBucket(per_minute=15, burst=5)

    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v1/{self.chain}{path}"

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        accept_status: Iterable[int] = (),
        search: bool = False,
        root: bool = False,
    ) -> httpx.Response:
        accept = set(accept_status)
        url = f"{self.base_url}/v1{path}" if root else self._url(path)
        last_error: FibreAPIError | None = None
        for attempt in range(self._max_retries + 1):
            await self._general.acquire(self._sleep)
            if search:
                await self._search.acquire(self._sleep)
            try:
                resp = await self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = FibreAPIError(f"transport error: {exc.__class__.__name__}")
                await self._sleep(min(2**attempt, 10))
                continue

            adjusted = resp.headers.get("X-Fibre-Param-Adjusted")
            if adjusted:
                log.warning("param_adjusted path=%s params=%s header=%s", path, params, adjusted)

            status = resp.status_code
            if status == 503:
                body = _safe_json(resp)
                if body.get("error") == "fibre_not_active":
                    raise FibreNotActiveError(body, _retry_after(resp, default=600.0))
            if status < 400 or status in accept:
                return resp
            if status == 429:
                wait = min(_retry_after(resp), 120.0)
                log.warning("rate_limited path=%s retry_after=%s", path, wait)
                last_error = RateLimitedError("rate limited", status=429)
                await self._sleep(wait)
                continue
            if status == 503:
                last_error = WarmingUpError("data warming up", status=503)
                await self._sleep(min(2**attempt, 10))
                continue
            body = _safe_json(resp)
            if status == 409:
                raise AmbiguousError(_normalize_candidates(body.get("candidates")))
            if status == 404:
                raise NotFoundError(str(body.get("error", "not found")), status=404)
            if status >= 500:
                last_error = FibreAPIError(f"server error {status}", status=status)
                await self._sleep(min(2**attempt, 10))
                continue
            raise FibreAPIError(str(body.get("error", f"HTTP {status}")), status=status)
        assert last_error is not None
        raise last_error

    async def _json(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        resp = await self._get(path, params, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise FibreAPIError("invalid JSON in response", status=resp.status_code) from exc

    # Endpoints ---------------------------------------------------------------------------

    async def chains(self) -> list[dict[str, Any]]:
        """GET /v1/chains: [{key, chain_id, network, available, ...}]. Not chain specific."""
        data = await self._json("/chains", root=True)
        items = data.get("chains") if isinstance(data, dict) else None
        return [c for c in items or [] if isinstance(c, dict)]

    async def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._json("/summary")
        return result

    async def metrics_text(self, validator: str | None = None) -> str:
        params = {"validator": validator} if validator else None
        resp = await self._get("/metrics", params)
        return resp.text

    async def validator_status(self, ident: str) -> dict[str, Any]:
        """/status answers 503 when the host is not ready: that is data, not an error."""
        resp = await self._get(
            f"/validator/{quote(ident, safe='')}/status",
            {"unknown": "ok"},
            accept_status=(503,),
        )
        body = _safe_json(resp)
        if "reason" not in body:
            raise WarmingUpError("data warming up", status=resp.status_code)
        return body

    async def validator_blobs(self, ident: str, limit: int = 1) -> dict[str, Any]:
        result: dict[str, Any] = await self._json(
            f"/validator/{quote(ident, safe='')}/blobs", {"limit": limit}
        )
        return result

    async def validator_history(self, valcons: str, days: int) -> dict[str, Any]:
        result: dict[str, Any] = await self._json(
            f"/validator/{quote(valcons, safe='')}/history", {"days": days}
        )
        return result

    async def timeseries(self, range_: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._json("/timeseries", {"range": range_})
        return result

    async def publishers(self, period: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._json("/publishers", {"period": period})
        return result

    async def blob_stats(self, period: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._json("/blobs/stats", {"period": period})
        return result

    async def activity_series(self, range_: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._json("/activity/series", {"range": range_})
        return result

    async def events(self, *, types: Iterable[str] = (), limit: int = 50) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        types = list(types)
        if types:
            params["type"] = ",".join(types)
        result: dict[str, Any] = await self._json("/events", params)
        return result

    async def report(self, period: str, validator: str | None = None) -> dict[str, Any]:
        """Calendar report: period is week, month, year or a key like 2026-W40, 2026-09, 2026."""
        params: dict[str, Any] = {"period": period}
        if validator:
            params["validator"] = validator
        result: dict[str, Any] = await self._json("/report", params)
        return result

    async def search(self, query: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._json("/search", {"q": query}, search=True)
        return result


def _retry_after(resp: httpx.Response, default: float = 5.0) -> float:
    raw = resp.headers.get("Retry-After", "")
    try:
        return max(1.0, min(float(raw), 3600.0))
    except ValueError:
        return default


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
