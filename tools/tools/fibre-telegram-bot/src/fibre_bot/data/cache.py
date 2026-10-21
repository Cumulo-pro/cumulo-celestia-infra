"""Shared data layer, one per chain: a background poller for /summary, the full /metrics only
every 30 minutes (validator directory), and a small TTL cache for on-demand endpoints. Every
button press reads from here, so API load does not grow with the number of users."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

from fibre_bot.api.client import FibreAPIError, FibreClient
from fibre_bot.api.metrics import MetricsSnapshot

log = logging.getLogger(__name__)

T = TypeVar("T")


class TTLCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_fetch(
        self, key: str, fetch: Callable[[], Awaitable[T]], ttl: float | None = None
    ) -> T:
        hit = self._data.get(key)
        if hit and hit[0] > self._clock():
            value: T = hit[1]
            return value
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # one fetch per key even if many users press at once
            hit = self._data.get(key)
            if hit and hit[0] > self._clock():
                cached: T = hit[1]
                return cached
            result = await fetch()
            self._data[key] = (self._clock() + (ttl if ttl is not None else self._ttl), result)
            if len(self._data) > 500:
                self._evict()
            return result

    def _evict(self) -> None:
        now = self._clock()
        for key in [k for k, (exp, _) in self._data.items() if exp <= now]:
            self._data.pop(key, None)
            self._locks.pop(key, None)


class DataStore:
    """Per chain data.

    Every poll: /summary only (network figures and the data timestamp).
    Every `directory_every` seconds: the full /metrics (about 830 KB with every validator), only
    to know monikers and valcons for name lookups and inline search.
    On demand: /metrics?validator=<valcons> for one validator's report, cached for a minute.
    """

    def __init__(
        self,
        client: FibreClient,
        *,
        poll_interval: float = 60,
        stale_after: float = 600,
        on_demand_ttl: float = 300,
        directory_every: float = 1800,
        validator_ttl: float = 60,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.directory_every = directory_every
        self.validator_ttl = validator_ttl
        self.cache = TTLCache(on_demand_ttl)
        self._wall = wall_clock
        self.summary: dict[str, Any] | None = None
        self.metrics: MetricsSnapshot | None = None  # directory: full /metrics, refreshed rarely
        self.directory_at: float | None = None
        self.last_refresh_at: float | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._refresh_lock = asyncio.Lock()
        self.cycle_listeners: list[Callable[[DataStore], Awaitable[None]]] = []

    # State ------------------------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self.summary is not None and self.metrics is not None

    def generated_at(self) -> float | None:
        """When the aggregator computed the figures: summary first, directory as fallback."""
        meta = (self.summary or {}).get("meta") or {}
        raw = meta.get("generated_at") if isinstance(meta, dict) else None
        if isinstance(raw, str):
            with contextlib.suppress(ValueError):
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        return self.metrics.generated_at if self.metrics else None

    def data_age(self) -> float | None:
        """Seconds since the aggregator computed the figures (not since we fetched them)."""
        generated = self.generated_at()
        if generated is None:
            return None
        return max(0.0, self._wall() - generated)

    def is_stale(self) -> bool:
        age = self.data_age()
        return age is None or age > self.stale_after

    def directory_due(self) -> bool:
        return (
            self.metrics is None
            or self.directory_at is None
            or self._wall() - self.directory_at >= self.directory_every
        )

    # Polling ----------------------------------------------------------------------------

    async def refresh(self, *, only_if_missing: bool = False) -> None:
        async with self._refresh_lock:
            if only_if_missing and self.loaded:
                return  # another request loaded it while we waited for the lock
            started = time.monotonic()
            want_directory = self.directory_due()
            try:
                if want_directory:
                    summary, metrics_text = await asyncio.gather(
                        self.client.summary(), self.client.metrics_text()
                    )
                else:
                    summary, metrics_text = await self.client.summary(), None
            except FibreAPIError as exc:
                self.last_error = str(exc)
                log.warning(
                    "poll_failed chain=%s error=%s keeping_previous=%s",
                    self.client.chain,
                    exc,
                    self.loaded,
                )
                raise
            self.summary = summary
            if metrics_text is not None:
                # ~3,000 series take a few hundred ms to parse: keep the event loop free.
                self.metrics = await asyncio.to_thread(MetricsSnapshot.parse, metrics_text)
                self.directory_at = self._wall()
            self.last_refresh_at = self._wall()
            self.last_error = None
            age = self.data_age()
            log.info(
                "health chain=%s poll_ms=%d data_age_s=%s stale=%s directory=%s validators=%d",
                self.client.chain,
                (time.monotonic() - started) * 1000,
                f"{age:.0f}" if age is not None else "n/a",
                self.is_stale(),
                "refreshed" if metrics_text is not None else "cached",
                len(self.metrics.validators()) if self.metrics else 0,
            )

    async def ensure_loaded(self) -> None:
        if not self.loaded:
            await self.refresh(only_if_missing=True)

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(FibreAPIError):
                await self.refresh()
                for listener in self.cycle_listeners:
                    try:
                        await listener(self)
                    except Exception:
                        log.exception("cycle_listener_failed")
            await asyncio.sleep(self.poll_interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name=f"poller-{self.client.chain}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # On-demand fetches ------------------------------------------------------------------

    async def blob_stats(self, period: str) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"blobstats:{period}", lambda: self.client.blob_stats(period)
        )

    async def publishers(self, period: str) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"publishers:{period}", lambda: self.client.publishers(period)
        )

    async def activity_series(self, range_: str) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"activity:{range_}", lambda: self.client.activity_series(range_)
        )

    async def timeseries(self, range_: str) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"timeseries:{range_}", lambda: self.client.timeseries(range_)
        )

    async def validator_blobs(self, valcons: str) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"blobs:{valcons}", lambda: self.client.validator_blobs(valcons, limit=1)
        )

    async def validator_metrics(self, valcons: str) -> MetricsSnapshot:
        """Fresh metrics for one validator (a fraction of the full /metrics)."""

        async def fetch() -> MetricsSnapshot:
            text = await self.client.metrics_text(valcons)
            return await asyncio.to_thread(MetricsSnapshot.parse, text)

        return await self.cache.get_or_fetch(f"vmetrics:{valcons}", fetch, ttl=self.validator_ttl)

    async def validator_status(self, valcons: str) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"status:{valcons}", lambda: self.client.validator_status(valcons), ttl=60
        )

    async def report(self, period: str, validator: str | None = None) -> dict[str, Any]:
        return await self.cache.get_or_fetch(
            f"report:{period}:{validator or ''}",
            lambda: self.client.report(period, validator),
        )

    async def search(self, query: str) -> dict[str, Any]:
        q = query.strip().lower()
        return await self.cache.get_or_fetch(f"search:{q}", lambda: self.client.search(q))
