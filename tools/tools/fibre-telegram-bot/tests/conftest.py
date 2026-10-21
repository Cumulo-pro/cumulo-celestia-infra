from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fibre_bot.api.metrics import MetricsSnapshot
from fibre_bot.data.models import ReportContext

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CUMULO = "celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx"
NOW = datetime(2026, 10, 4, 9, 20, tzinfo=UTC)


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def summary() -> dict[str, Any]:
    data: dict[str, Any] = load_json("summary.json")
    return data


@pytest.fixture
def metrics_text() -> str:
    return load_text("metrics_validator.txt")


@pytest.fixture
def metrics(metrics_text: str) -> MetricsSnapshot:
    return MetricsSnapshot.parse(metrics_text)


@pytest.fixture
def ctx() -> ReportContext:
    return ReportContext(now=NOW)


async def no_sleep(_: float) -> None:
    return None


API_BASE = "https://fibre-api.test"


MOCHA_PAGE = "https://cumulo.pro/services/celestia_mocha/fibre.php"
MOCHA_REPORT = "https://cumulo.pro/services/celestia_mocha/fibre-report"
MAINNET_PAGE = "https://cumulo.pro/services/celestia/fibre.php"


def make_registry(
    *,
    wall: float = 1791105300.0,
    now: datetime = NOW,
    chains: tuple[tuple[Any, ...], ...] = (("mocha-5", "testnet"),),
    pages: dict[str, str] | None = None,
    report_pages: dict[str, str] | None = None,
    monotonic: Any = None,
) -> Any:
    """Registry with the given (key, network[, active]) chains, no network calls, no pollers."""
    import time

    import httpx

    from fibre_bot.api.client import FibreClient, TokenBucket
    from fibre_bot.chains import ChainRegistry

    http = httpx.AsyncClient()

    def factory(key: str) -> FibreClient:
        return FibreClient(
            API_BASE,
            key,
            http=http,
            max_retries=0,
            sleep=no_sleep,
            general_limiter=TokenBucket(10_000, 1000),
            search_limiter=TokenBucket(10_000, 1000),
        )

    registry = ChainRegistry(
        API_BASE,
        default_chain="celestia",
        legacy_chain="mocha-5",
        pages=pages if pages is not None else {"mocha-5": MOCHA_PAGE, "celestia": MAINNET_PAGE},
        report_pages=report_pages if report_pages is not None else {"mocha-5": MOCHA_REPORT},
        default_validators={"mocha-5": CUMULO},
        http=http,
        client_factory=factory,
        wall_clock=lambda: wall,
        clock=lambda: now,
        monotonic=monotonic or time.monotonic,
    )
    for entry in chains:
        key, network = entry[0], entry[1]
        active = entry[2] if len(entry) > 2 else True
        registry.add(key, key, network, active=active)
    return registry
