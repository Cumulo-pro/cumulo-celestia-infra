"""The chains the bot serves, read from the API's /v1/chains (never hard coded).

- `fibre_active: true`: the chain works like mocha-5, with its own poller and cache.
- `fibre_active: false`: the chain is known but pending. Its endpoints answer 503
  `fibre_not_active`; that is not an error. The bot keeps the activation info (blocks left, ETA)
  and asks again at most every 10 minutes (Retry-After). When /v1/chains says
  `fibre_active: true`, the chain starts on its own, without a restart.

The HTTP client and the rate limiters are shared by every chain (the API limits are per IP).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from fibre_bot import USER_AGENT
from fibre_bot.api.client import FibreAPIError, FibreClient, FibreNotActiveError, TokenBucket
from fibre_bot.bot.service import ReportService
from fibre_bot.data.cache import DataStore
from fibre_bot.data.models import ValidatorRef
from fibre_bot.reports.format import parse_time

log = logging.getLogger(__name__)

DISCOVER_EVERY_SECONDS = 600
SPARE_CODES = "abcdefghijklnopqrsuvwxyz"  # m (mainnet) and t (testnet) are preferred codes


@dataclass
class Chain:
    key: str  # path segment in the API: /v1/<key>/...
    chain_id: str
    network: str  # "mainnet" or "testnet", as the API says
    code: str  # one character, used in callback data
    store: DataStore
    service: ReportService
    page: str | None  # public page on cumulo.pro (None: not published, no link)
    report_page: str | None  # report tab, accepts ?period= and ?validator=
    active: bool = True
    app_version: str = ""
    activation: dict[str, Any] = field(default_factory=dict)
    next_probe: float = 0.0  # monotonic time before which a pending chain is not asked again

    @property
    def is_mainnet(self) -> bool:
        return self.network.lower() == "mainnet"

    @property
    def label(self) -> str:
        return network_label(self.key, self.network)

    @property
    def aliases(self) -> set[str]:
        words = {self.key.lower(), self.chain_id.lower(), self.network.lower()}
        words.add(self.key.split("-")[0].lower())
        return {w for w in words if w}

    def not_active_text(self) -> str:
        """User-facing line for a pending chain (plain text, no em dash)."""
        left = self.activation.get("blocks_left")
        eta = parse_time(self.activation.get("eta"))
        detail = []
        if isinstance(left, int | float) and not isinstance(left, bool):
            detail.append(f"{int(left):,} blocks left")
        if eta is not None:
            detail.append(f"about {eta.astimezone(UTC):%Y-%m-%d %H:%M} UTC, an estimate")
        text = f"Fibre is not active on {self.label} yet"
        return text + (": " + ", ".join(detail) if detail else "") + "."


def network_label(key: str, network: str) -> str:
    """'Mainnet' or 'Mocha testnet' (shown on every message)."""
    if network.lower() == "mainnet":
        return "Mainnet"
    name = key.split("-")[0].capitalize()
    return f"{name} {network.lower()}" if network else name


def parse_mapping(raw: str) -> dict[str, str]:
    """'mocha-5=https://a,celestia=https://b' -> {key: value}."""
    out: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


class ChainRegistry:
    def __init__(
        self,
        base_url: str,
        *,
        default_chain: str = "celestia",
        legacy_chain: str = "mocha-5",
        only: list[str] | None = None,
        pages: dict[str, str] | None = None,
        report_pages: dict[str, str] | None = None,
        default_validators: dict[str, str] | None = None,
        default_moniker: str = "Cumulo",
        poll_interval: float = 60,
        stale_after: float = 600,
        directory_every: float = 1800,
        http: httpx.AsyncClient | None = None,
        client_factory: Callable[[str], FibreClient] | None = None,
        wall_clock: Callable[[], float] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_chain = default_chain
        self.legacy_chain = legacy_chain  # what v0.1 buttons (no chain code) refer to
        self.only = [c for c in (only or []) if c]
        self.pages = pages or {}
        self.report_pages = report_pages or {}
        self.default_validators = default_validators or {}
        self.default_moniker = default_moniker
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.directory_every = directory_every
        self._own_http = http is None and client_factory is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0), headers={"User-Agent": USER_AGENT}
        )
        self.general = TokenBucket(per_minute=100, burst=30)
        self.search = TokenBucket(per_minute=15, burst=5)
        self._client_factory = client_factory or self._make_client
        self._wall_clock = wall_clock
        self._clock = clock
        self._monotonic = monotonic
        self._chains: dict[str, Chain] = {}
        self._started = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def _make_client(self, key: str) -> FibreClient:
        return FibreClient(
            self.base_url,
            key,
            http=self.http,
            general_limiter=self.general,
            search_limiter=self.search,
        )

    # Lookup ------------------------------------------------------------------------------

    def _ordered(self, chains: list[Chain]) -> list[Chain]:
        """Default chain first, then mainnet, then the rest in the order they were added."""
        return sorted(chains, key=lambda c: (c.key != self.default_chain, not c.is_mainnet))

    def all(self) -> list[Chain]:
        """Active chains only (the ones with data)."""
        return self._ordered([c for c in self._chains.values() if c.active])

    def known(self) -> list[Chain]:
        """Active and pending chains."""
        return self._ordered(list(self._chains.values()))

    @property
    def multi(self) -> bool:
        return len(self.all()) > 1

    @property
    def primary(self) -> Chain:
        """DEFAULT_CHAIN when it is active (mainnet), otherwise the first active chain."""
        chains = self.all()
        if not chains:
            raise RuntimeError("no active chain yet")
        return chains[0]

    def get(self, code: str | None) -> Chain:
        """Chain by code. None: the default chain. Empty code (a button sent by v0.1, which
        only knew mocha-5): the legacy chain."""
        if code and code in self._chains and self._chains[code].active:
            return self._chains[code]
        if code == "":
            for chain in self.all():
                if chain.key == self.legacy_chain:
                    return chain
        return self.primary

    def by_alias(self, word: str) -> Chain | None:
        """Any known chain, pending included, so 'mainnet' can answer 'not active yet'."""
        w = word.lower().strip()
        for chain in self.known():
            if w in chain.aliases:
                return chain
        return None

    def others(self, code: str) -> list[tuple[str, str]]:
        return [(c.code, c.label) for c in self.all() if c.code != code]

    def default_validator(self, chain: Chain) -> ValidatorRef | None:
        """Configured valcons for that chain if the monitor knows it, else the default moniker
        (each chain has its own valcons for the same operator)."""
        metrics = chain.store.metrics
        valcons = self.default_validators.get(chain.key)
        if valcons and (metrics is None or metrics.validator(valcons).exists):
            moniker = metrics.validator(valcons).moniker if metrics else None
            return ValidatorRef(valcons, moniker or valcons)
        if metrics is not None and self.default_moniker:
            found = metrics.find_valcons(self.default_moniker)
            if found:
                return ValidatorRef(found, metrics.validator(found).moniker or found)
        return None

    # Discovery ---------------------------------------------------------------------------

    def _code_for(self, network: str) -> str:
        used = set(self._chains)
        preferred = "m" if network.lower() == "mainnet" else "t"
        if preferred not in used:
            return preferred
        return next(ch for ch in SPARE_CODES if ch not in used)

    def add(
        self,
        key: str,
        chain_id: str | None = None,
        network: str = "",
        *,
        active: bool = True,
        app_version: str = "",
    ) -> Chain:
        for chain in self._chains.values():
            if chain.key == key:
                return chain
        client = self._client_factory(key)
        store = DataStore(
            client,
            poll_interval=self.poll_interval,
            stale_after=self.stale_after,
            directory_every=self.directory_every,
            wall_clock=self._wall_clock or time.time,
        )
        page = self.pages.get(key)
        report_page = self.report_pages.get(key)
        chain_id = chain_id or key
        service = ReportService(
            store,
            self._clock,
            dashboard=page,
            report_page=report_page,
            chain_name="mainnet" if network.lower() == "mainnet" else chain_id,
            label=network_label(key, network),
        )
        code = self._code_for(network)
        chain = Chain(
            key,
            chain_id,
            network,
            code,
            store,
            service,
            page,
            report_page,
            active=active,
            app_version=app_version,
        )
        self._chains[code] = chain
        if self._started and active:
            store.start()
        log.info(
            "chain_added key=%s network=%s code=%s active=%s page=%s",
            key,
            network or "?",
            code,
            active,
            "yes" if page else "none",
        )
        return chain

    def _activate(self, chain: Chain) -> None:
        chain.active = True
        chain.activation = {}
        if self._started:
            chain.store.start()
        log.info("chain_activated key=%s", chain.key)

    async def _probe(self, chain: Chain) -> None:
        """Ask a pending chain (at most every Retry-After) for its activation info."""
        if self._monotonic() < chain.next_probe:
            return
        wait = float(DISCOVER_EVERY_SECONDS)
        try:
            await chain.store.client.summary()
        except FibreNotActiveError as exc:
            if exc.activation.get("blocks_left") != chain.activation.get("blocks_left"):
                log.info(
                    "chain_pending key=%s blocks_left=%s eta=%s",
                    chain.key,
                    exc.activation.get("blocks_left"),
                    exc.activation.get("eta"),
                )
            chain.activation = exc.activation
            chain.app_version = exc.app_version or chain.app_version
            wait = max(exc.retry_after, wait)
        except FibreAPIError:
            pass
        chain.next_probe = self._monotonic() + wait

    async def discover(self) -> list[Chain]:
        """Sync with /v1/chains. Returns the chains that became usable this time."""
        async with self._lock:
            ready_before = {c.key for c in self.all()}
            listing: list[dict[str, Any]] = []
            try:
                listing = await self._client_factory(self.legacy_chain).chains()
            except FibreAPIError as exc:
                log.warning("chains_discovery_failed error=%s", exc)
            for item in listing:
                key = str(item.get("key") or item.get("chain_id") or "")
                if not key or (self.only and key not in self.only):
                    continue
                if "fibre_active" in item:
                    active = item.get("fibre_active") is True
                else:  # an answer without the field: fall back to `available`
                    active = item.get("available") is not False
                existing = next((c for c in self._chains.values() if c.key == key), None)
                if existing is None:
                    self.add(
                        key,
                        str(item.get("chain_id") or key),
                        str(item.get("network") or ""),
                        active=active,
                        app_version=str(item.get("app_version") or ""),
                    )
                else:
                    existing.app_version = str(item.get("app_version") or existing.app_version)
                    if active and not existing.active:
                        self._activate(existing)
            if not self.all():  # API down at start: serve the legacy chain anyway
                self.add(self.legacy_chain, network="testnet")
            for chain in self.known():
                if not chain.active:
                    await self._probe(chain)
            return [c for c in self.all() if c.key not in ready_before]

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(DISCOVER_EVERY_SECONDS)
            with contextlib.suppress(Exception):
                await self.discover()

    async def start(self) -> None:
        await self.discover()
        self._started = True
        for chain in self.all():
            chain.store.start()
        self._task = asyncio.create_task(self._loop(), name="chain-discovery")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for chain in self._chains.values():
            await chain.store.stop()
        if self._own_http:
            await self.http.aclose()
