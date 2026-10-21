"""Glue between the data layer and the pure renderers. No Telegram code here, so it can
be tested without a bot."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fibre_bot.api.client import FibreAPIError
from fibre_bot.api.metrics import MetricsSnapshot
from fibre_bot.data.cache import DataStore
from fibre_bot.data.models import (
    DASHBOARD_URL,
    CardPeriod,
    Period,
    ReportContext,
    ValidatorRef,
)
from fibre_bot.reports.card import card_caption, card_svg, render_details, render_png
from fibre_bot.reports.format import parse_time
from fibre_bot.reports.market import render_market
from fibre_bot.reports.network import render_network
from fibre_bot.reports.validator import render_status_line, render_validator

log = logging.getLogger(__name__)

VALCONS_PREFIX = "celestiavalcons"


def _ok(result: object) -> dict[str, Any] | None:
    if isinstance(result, BaseException):
        if not isinstance(result, FibreAPIError):
            raise result
        log.warning("optional_fetch_failed error=%s", result)
        return None
    return result if isinstance(result, dict) else None


@dataclass(frozen=True)
class Card:
    png: bytes
    caption: str
    period_key: str
    filename: str
    cache_key: str
    title: str
    url: str
    closed: bool


class ReportService:
    def __init__(
        self,
        store: DataStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        dashboard: str | None = DASHBOARD_URL,
        report_page: str | None = None,
        chain_name: str | None = None,
        label: str | None = None,
    ) -> None:
        self.store = store
        self._clock = clock
        self.dashboard = dashboard  # general public page (None: not published)
        self.report_page = report_page  # report tab, used for validator links
        self.chain_name = chain_name  # shown on cards: "mainnet" or the chain id
        self.label = label  # "Mainnet" / "Mocha testnet", shown on every report

    def context(self) -> ReportContext:
        return ReportContext(
            now=self._clock(),
            stale=self.store.is_stale(),
            data_age=self.store.data_age(),
            dashboard=self.dashboard,
            report_page=self.report_page,
            label=self.label,
        )

    async def _loaded(self) -> tuple[dict[str, Any], MetricsSnapshot]:
        await self.store.ensure_loaded()
        assert self.store.summary is not None and self.store.metrics is not None
        return self.store.summary, self.store.metrics

    async def network(self, period: Period) -> str:
        # Everything the network report needs is in /summary (polled every minute); the
        # directory snapshot can be up to 30 minutes old, so it is not used here.
        summary, _ = await self._loaded()
        timeseries = None
        if period is not Period.H24:
            try:
                timeseries = await self.store.timeseries(period.series_range)
            except FibreAPIError as exc:
                log.warning("timeseries_failed error=%s", exc)
        return render_network(summary, None, period, self.context(), timeseries)

    async def validator(self, valcons: str, period: Period) -> str:
        summary, directory = await self._loaded()
        status: object
        blobs: object
        fresh: object
        status, blobs, fresh = await asyncio.gather(
            self.store.validator_status(valcons),
            self.store.validator_blobs(valcons),
            self.store.validator_metrics(valcons),
            return_exceptions=True,
        )
        metrics = directory
        if isinstance(fresh, MetricsSnapshot):
            metrics = fresh
        elif isinstance(fresh, BaseException) and not isinstance(fresh, FibreAPIError):
            raise fresh
        else:
            log.warning("validator_metrics_failed valcons=%s using=directory", valcons)
        return render_validator(
            valcons,
            metrics,
            period,
            self.context(),
            status=_ok(status),
            blobs=_ok(blobs),
            history_since=parse_time(summary.get("history_since")),
        )

    async def market(self, period: Period) -> str:
        summary, _ = await self._loaded()
        stats: object
        publishers: object
        series: object
        stats, publishers, series = await asyncio.gather(
            self.store.blob_stats(period.value),
            self.store.publishers(period.value),
            self.store.activity_series(period.series_range),
            return_exceptions=True,
        )
        stats_d, pubs_d = _ok(stats), _ok(publishers)
        if stats_d is None and pubs_d is None:
            raise FibreAPIError("market data unavailable")
        return render_market(
            period,
            self.context(),
            stats=stats_d,
            publishers=pubs_d,
            series=_ok(series),
            history_since=summary.get("history_since"),
        )

    async def _report(self, period: CardPeriod, valcons: str | None) -> dict[str, Any]:
        report = await self.store.report(period.api_key(self._clock()), valcons or None)
        # Links: the API's report URL only when this chain's report page is confirmed in the
        # config; otherwise the general page; otherwise none (page not published yet).
        linked = dict(report)
        url = report.get("url") if self.report_page else None
        linked["url"] = url or self.dashboard or None
        return linked

    async def card(self, period: CardPeriod, valcons: str | None = None) -> Card:
        report = await self._report(period, valcons)
        key = str((report.get("period") or {}).get("key", period.value))

        async def build() -> bytes:
            return await asyncio.to_thread(render_png, card_svg(report, self.chain_name))

        period_info = report.get("period") or {}
        closed = period_info.get("status") == "closed"
        # A closed period never changes: one image (and one Telegram upload) for good.
        version = "closed" if closed else str(report.get("generated_at", ""))
        cache_key = f"card:{key}:{valcons or ''}:{version}"
        png = await self.store.cache.get_or_fetch(cache_key, build)
        moniker = ""
        if isinstance(report.get("validator"), dict):
            moniker = str(report["validator"].get("moniker") or valcons or "")
        who = f"-{moniker.replace(' ', '_')}" if moniker else ""
        label = str(period_info.get("label") or key)
        title = f"{moniker} · {label}" if moniker else f"Fibre network · {label}"
        if period_info.get("status") == "in_progress":
            title += " (so far)"
        return Card(
            png=png,
            caption=card_caption(report, self.chain_name),
            period_key=key,
            filename=f"fibre-report-{key}{who}.png",
            cache_key=cache_key,
            title=title,
            url=str(report.get("url") or ""),
            closed=closed,
        )

    def match_validators(self, query: str, limit: int = 5) -> list[ValidatorRef]:
        """Local, API-free lookup for inline mode (runs on every keystroke)."""
        q = query.strip().lower()
        metrics = self.store.metrics
        if not q or metrics is None:
            return []
        exact, prefix, contains = [], [], []
        for valcons, info in metrics.validators().items():
            name = info["moniker"]
            low = name.lower()
            ref = ValidatorRef(valcons, name or valcons)
            if q in (low, valcons.lower(), info["operator_address"].lower()):
                exact.append(ref)
            elif low.startswith(q):
                prefix.append(ref)
            elif q in low:
                contains.append(ref)
        ordered = (
            exact
            + sorted(prefix, key=lambda r: r.moniker)
            + sorted(contains, key=lambda r: r.moniker)
        )
        return ordered[:limit]

    async def card_details(self, period: CardPeriod, valcons: str | None = None) -> str:
        return render_details(await self._report(period, valcons), self.label)

    async def status_line(self, ident: str) -> str:
        """May raise AmbiguousError (409) so the handler can offer candidates."""
        status = await self.store.validator_status(ident)
        return render_status_line(status)

    def known_moniker(self, valcons: str) -> str | None:
        if self.store.metrics is None:
            return None
        return self.store.metrics.validator(valcons).moniker

    async def resolve(self, text: str) -> list[ValidatorRef]:
        """Turn user input into validators: valcons, valoper or moniker. 0, 1 or many."""
        query = text.strip()
        if not query:
            return []
        await self.store.ensure_loaded()
        metrics = self.store.metrics
        if query.startswith(VALCONS_PREFIX):
            return [ValidatorRef(query, self.known_moniker(query) or query)]
        if metrics is not None:
            local = metrics.find_valcons(query)
            if local:
                return [ValidatorRef(local, self.known_moniker(local) or local)]
        result = await self.store.search(query)
        refs = []
        for v in result.get("validators") or []:
            if isinstance(v, dict) and isinstance(v.get("valcons"), str):
                refs.append(ValidatorRef(v["valcons"], str(v.get("moniker") or v["valcons"])))
        exact = [r for r in refs if r.moniker.lower() == query.lower()]
        return exact if len(exact) == 1 else refs
