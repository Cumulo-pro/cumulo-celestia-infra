"""Opt-in checks against the real API: pytest -m live"""

from __future__ import annotations

import pytest

from fibre_bot.api.client import FibreClient
from fibre_bot.api.metrics import MetricsSnapshot
from fibre_bot.bot.service import ReportService
from fibre_bot.data.cache import DataStore
from fibre_bot.data.models import CardPeriod, Period
from tests.conftest import CUMULO

pytestmark = pytest.mark.live
BASE = "https://fibre-api.cumulo.com.es"


async def test_live_reports_render() -> None:
    client = FibreClient(BASE, "mocha-5")
    try:
        service = ReportService(DataStore(client), chain_name="mocha-5")
        for period in Period:
            assert "2/3 threshold" in await service.network(period)
            assert "Activity" in await service.market(period)
        for period in (Period.H24, Period.D7, Period.D30):
            assert "Performance" in await service.validator(CUMULO, period)
        assert "Cumulo" in await service.status_line(CUMULO)
        for card_period in CardPeriod:
            card = await service.card(card_period, CUMULO)
            assert card.png[:4] == b"\x89PNG" and "Celestia Fibre" in card.caption
            assert "Network" in await service.card_details(card_period)
        assert (await service.resolve("cumulo"))[0].valcons == CUMULO
    finally:
        await client.aclose()


async def test_live_metrics_have_expected_series() -> None:
    client = FibreClient(BASE, "mocha-5")
    try:
        snap = MetricsSnapshot.parse(await client.metrics_text())
    finally:
        await client.aclose()
    assert snap.get("fibre_ready_meets_threshold") is not None
    assert snap.validator(CUMULO).exists
    assert len(snap.validators()) > 10
