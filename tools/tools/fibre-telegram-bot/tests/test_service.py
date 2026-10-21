from __future__ import annotations

import httpx
import pytest
import respx

from fibre_bot.api.client import FibreAPIError, FibreClient, TokenBucket
from fibre_bot.bot.service import ReportService
from fibre_bot.data.cache import DataStore, TTLCache
from fibre_bot.data.models import Period
from tests.conftest import CUMULO, NOW, load_json, load_text, no_sleep

BASE = "https://fibre-api.test"
API = f"{BASE}/v1/mocha-5"


def make_service(wall: float = 1791105300.0) -> ReportService:
    client = FibreClient(
        BASE,
        "mocha-5",
        http=httpx.AsyncClient(),
        max_retries=0,
        sleep=no_sleep,
        general_limiter=TokenBucket(10_000, 1000),
        search_limiter=TokenBucket(10_000, 1000),
    )
    store = DataStore(client, wall_clock=lambda: wall)
    return ReportService(store, clock=lambda: NOW)


def mock_poll() -> None:
    respx.get(f"{API}/summary").respond(json=load_json("summary.json"))
    respx.get(f"{API}/metrics").respond(text=load_text("metrics_validator.txt"))


@respx.mock
async def test_resolve_paths() -> None:
    mock_poll()
    search = respx.get(f"{API}/search")
    svc = make_service()

    refs = await svc.resolve(CUMULO)
    assert refs[0].valcons == CUMULO and refs[0].moniker == "Cumulo"

    refs = await svc.resolve("CUMULO")  # local, case-insensitive, no API call
    assert refs[0].valcons == CUMULO
    assert search.call_count == 0

    search.respond(
        json={
            "validators": [
                {"valcons": "celestiavalcons1a", "moniker": "Alpha Node"},
                {"valcons": "celestiavalcons1b", "moniker": "Alpha"},
            ]
        }
    )
    refs = await svc.resolve("alpha")
    assert [r.valcons for r in refs] == ["celestiavalcons1b"]  # exact moniker wins

    search.respond(json=load_json("search_empty.json"))
    assert await svc.resolve("zzzz") == []


@respx.mock
async def test_network_report_end_to_end() -> None:
    mock_poll()
    respx.get(f"{API}/timeseries").respond(json=load_json("timeseries_7d.json"))
    text = await make_service().network(Period.D7)
    assert "✅ Ready stake meets 2/3" in text
    assert "Over 7d" in text


@respx.mock
async def test_network_survives_timeseries_failure() -> None:
    mock_poll()
    respx.get(f"{API}/timeseries").respond(500, json={"error": "boom"})
    text = await make_service().network(Period.D30)
    assert "Over 30d" not in text
    assert "2/3 threshold" in text


@respx.mock
async def test_validator_report_end_to_end() -> None:
    mock_poll()
    respx.get(f"{API}/validator/{CUMULO}/status").respond(json=load_json("status.json"))
    respx.get(f"{API}/validator/{CUMULO}/blobs").respond(json=load_json("blobs_counts.json"))
    text = await make_service().validator(CUMULO, Period.D30)
    assert "Signed 7,820" in text
    assert "Data since 2026-09-25" in text


@respx.mock
async def test_validator_report_without_optional_endpoints() -> None:
    mock_poll()
    respx.get(f"{API}/validator/{CUMULO}/status").respond(500, json={"error": "x"})
    respx.get(f"{API}/validator/{CUMULO}/blobs").respond(500, json={"error": "x"})
    text = await make_service().validator(CUMULO, Period.H24)
    assert "✅ Ready" in text
    assert "Blobs (whole tracked history)</b>\nn/a" in text


@respx.mock
async def test_market_end_to_end_and_failure() -> None:
    mock_poll()
    respx.get(f"{API}/blobs/stats").respond(json=load_json("blobs_stats_7d.json"))
    respx.get(f"{API}/publishers").respond(json=load_json("publishers_7d.json"))
    respx.get(f"{API}/activity/series").respond(json=load_json("activity_series_7d.json"))
    text = await make_service().market(Period.D7)
    assert "Blobs: 8,508" in text

    respx.get(f"{API}/blobs/stats").respond(500, json={"error": "x"})
    respx.get(f"{API}/publishers").respond(500, json={"error": "x"})
    with pytest.raises(FibreAPIError):
        await make_service().market(Period.H24)


@respx.mock
async def test_stale_data_shows_banner() -> None:
    mock_poll()
    # The fixture was generated at 1791105286; 20 minutes later it is stale.
    svc = make_service(wall=1791105286 + 1200)
    await svc.store.ensure_loaded()
    assert svc.store.is_stale()
    respx.get(f"{API}/timeseries").respond(json=load_json("timeseries_7d.json"))
    text = await svc.network(Period.H24)
    assert "Monitor data is stale" in text


@respx.mock
async def test_poll_failure_keeps_previous_data() -> None:
    mock_poll()
    svc = make_service()
    await svc.store.refresh()
    respx.get(f"{API}/summary").respond(500, json={"error": "x"})
    with pytest.raises(FibreAPIError):
        await svc.store.refresh()
    assert svc.store.loaded


async def test_ttl_cache_single_flight() -> None:
    calls = 0
    t = [0.0]

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return calls

    cache = TTLCache(60, clock=lambda: t[0])
    assert await cache.get_or_fetch("k", fetch) == 1
    assert await cache.get_or_fetch("k", fetch) == 1
    t[0] = 61
    assert await cache.get_or_fetch("k", fetch) == 2
