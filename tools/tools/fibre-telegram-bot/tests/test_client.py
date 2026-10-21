from __future__ import annotations

import logging

import httpx
import pytest
import respx

from fibre_bot.api.client import (
    AmbiguousError,
    FibreClient,
    NotFoundError,
    TokenBucket,
    WarmingUpError,
)
from tests.conftest import CUMULO, load_json, no_sleep

BASE = "https://fibre-api.test"
API = f"{BASE}/v1/mocha-5"


def make_client(sleeps: list[float] | None = None) -> FibreClient:
    async def record(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return FibreClient(
        BASE,
        "mocha-5",
        http=httpx.AsyncClient(),
        max_retries=2,
        sleep=record,
        general_limiter=TokenBucket(10_000, 1000),
        search_limiter=TokenBucket(10_000, 1000),
    )


@respx.mock
async def test_summary_ok() -> None:
    respx.get(f"{API}/summary").respond(json=load_json("summary.json"))
    data = await make_client().summary()
    assert data["readiness"]["ready_validators"] == 77


@respx.mock
async def test_429_waits_retry_after_then_succeeds() -> None:
    route = respx.get(f"{API}/summary")
    route.side_effect = [
        httpx.Response(429, json={"error": "rate_limited"}, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"ok": True}),
    ]
    sleeps: list[float] = []
    data = await make_client(sleeps).summary()
    assert data == {"ok": True}
    assert 7.0 in sleeps


@respx.mock
async def test_503_warming_up_retries_then_raises() -> None:
    respx.get(f"{API}/summary").respond(503, json={"error": "warming_up"})
    with pytest.raises(WarmingUpError):
        await make_client().summary()
    assert respx.calls.call_count == 3


@respx.mock
async def test_409_ambiguous_returns_candidates() -> None:
    respx.get(f"{API}/validator/node/status").respond(
        409,
        json={
            "error": "ambiguous",
            "candidates": [
                {"valcons": "celestiavalcons1a", "moniker": "Node"},
                {"valcons": "celestiavalcons1b", "moniker": "node"},
            ],
        },
    )
    with pytest.raises(AmbiguousError) as info:
        await make_client().validator_status("node")
    assert [c["valcons"] for c in info.value.candidates] == [
        "celestiavalcons1a",
        "celestiavalcons1b",
    ]


@respx.mock
async def test_status_503_is_data() -> None:
    respx.get(f"{API}/validator/{CUMULO}/status").respond(
        503, json={"reason": "not_ready", "ready": False, "moniker": "Cumulo"}
    )
    data = await make_client().validator_status(CUMULO)
    assert data["reason"] == "not_ready"
    assert respx.calls.call_count == 1


@respx.mock
async def test_404_not_found() -> None:
    respx.get(f"{API}/validator/x/blobs").respond(404, json={"error": "not_found"})
    with pytest.raises(NotFoundError):
        await make_client().validator_blobs("x")


@respx.mock
async def test_param_adjusted_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(f"{API}/publishers").respond(
        json={"period": "30d", "period_requested": "90d", "period_applied": "30d"},
        headers={"X-Fibre-Param-Adjusted": "period=30d"},
    )
    with caplog.at_level(logging.WARNING):
        await make_client().publishers("90d")
    assert "param_adjusted" in caplog.text


@respx.mock
async def test_transport_error_retries() -> None:
    route = respx.get(f"{API}/summary")
    route.side_effect = [httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    assert await make_client().summary() == {"ok": 1}


async def test_token_bucket_waits_when_empty() -> None:
    t = [0.0]
    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)
        t[0] += s

    bucket = TokenBucket(per_minute=60, burst=1, clock=lambda: t[0])
    await bucket.acquire(fake_sleep)
    await bucket.acquire(fake_sleep)
    assert waits and abs(waits[0] - 1.0) < 1e-6


def test_no_sleep_helper_is_awaitable() -> None:
    assert callable(no_sleep)
