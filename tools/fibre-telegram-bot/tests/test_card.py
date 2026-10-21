from __future__ import annotations

import copy
import struct
from datetime import UTC, datetime
from typing import Any

import pytest

from fibre_bot.data.models import CardPeriod
from fibre_bot.reports.card import (
    CAPTION_LIMIT,
    card_caption,
    card_svg,
    pct_no100,
    ratio_pct,
    render_details,
    render_png,
)
from tests.conftest import load_json

DASHES = (chr(0x2014), chr(0x2013))


@pytest.fixture
def report() -> dict[str, Any]:
    data: dict[str, Any] = load_json("report_2026-W40_cumulo.json")
    return data


def test_pct_no100_never_rounds_up_to_100() -> None:
    assert pct_no100(99.9957, 1) == "99.996%"
    assert pct_no100(100.0, 1) == "100.0%"
    assert pct_no100(98.81, 1) == "98.8%"
    assert pct_no100(None) == "-"


def test_ratio_pct_uses_counts_not_rounded_pct() -> None:
    assert ratio_pct({"served": 23080, "checks": 23081, "pct": 100}) == pytest.approx(99.99567)
    assert ratio_pct({"ready": 10, "checks": 10, "pct": 100}) == 100
    assert ratio_pct({"pct": 42}) == 42
    assert ratio_pct({"served": 0, "checks": 0, "pct": None}) is None
    assert ratio_pct(None) is None


def test_card_svg_has_the_figures(report: dict[str, Any]) -> None:
    svg = card_svg(report)
    for expected in (
        "Celestia Fibre on mocha-5",
        "Week 40, 2026 (28 Sep to 4 Oct)",
        "97.5%",
        "8,554",
        "30,414 TIA",
        "77 / 78",
        "98.8%",
        "What breaks 2/3: 3 hosting companies failing together",
        "Cumulo: ready in 100.0% of checks, signed 7,714 of 8,554 blobs, kept 100.0% of its data",
        "134.84 GiB of data · 99.996% of signed data kept",
    ):
        assert expected in svg, expected
    assert "so far" not in svg
    assert not any(d in svg for d in DASHES)


def test_card_svg_network_only_and_in_progress(report: dict[str, Any]) -> None:
    r = copy.deepcopy(report)
    r.pop("validator")
    r["period"]["status"] = "in_progress"
    svg = card_svg(r)
    assert "· so far" in svg
    assert "Cumulo:" not in svg


def test_card_svg_survives_missing_data() -> None:
    svg = card_svg({"chain_id": "mocha-5", "period": {"label": "x"}})
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert ">-<" in svg  # missing values render as "-", never 0


def test_card_escapes_text(report: dict[str, Any]) -> None:
    r = copy.deepcopy(report)
    r["validator"]["moniker"] = "<script>&"
    assert "&lt;script&gt;&amp;" in card_svg(r)


def test_render_png_is_1200x630(report: dict[str, Any]) -> None:
    png = render_png(card_svg(report))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (1200, 630)


def test_caption_matches_card_and_fits(report: dict[str, Any]) -> None:
    cap = card_caption(report)
    assert cap.startswith("<b>Celestia Fibre on mocha-5 · Week 40, 2026 (28 Sep to 4 Oct)</b>")
    assert "2/3 is needed (up from 92.9%)" in cap
    assert "They kept 99.996% of the data they signed for." in cap  # API text says 100.0%
    assert "14 servers joined, 1 left." in cap
    assert 'href="https://cumulo.pro/services/celestia_mocha/fibre-report?period=2026-W40' in cap
    assert len(cap) <= CAPTION_LIMIT


def test_caption_is_trimmed_when_too_long(report: dict[str, Any]) -> None:
    r = copy.deepcopy(report)
    r["validator"]["moniker"] = "x" * 1200
    cap = card_caption(r)
    assert len(cap) <= CAPTION_LIMIT
    assert "…" in cap and "Full report on cumulo.pro" in cap


def test_details(report: dict[str, Any]) -> None:
    text = render_details(report)
    assert "Stake ready: 92.86% → <b>97.47%</b>" in text
    assert "Lowest: 85.64% on 2026-10-01 18:00 UTC" in text
    assert "Online: 98.81%, -0.83 pts vs previous · 740,264 of 749,176 checks" in text
    assert "Kept their data: 99.996% (23,080 of 23,081 downloads)" in text
    assert "Fees paid: 30,413.87 TIA" in text
    assert "<b>not ready 0</b>" in text
    assert "Kept its data: 100.00% (359 checks)" in text
    assert not any(d in text for d in DASHES)


def test_details_without_validator_or_data() -> None:
    text = render_details({"period": {"label": "Week 41", "status": "in_progress"}})
    assert "(so far)" in text
    assert "Blobs: n/a" in text


@pytest.mark.parametrize(
    ("now", "period", "key"),
    [
        (datetime(2026, 10, 5, 13, tzinfo=UTC), CardPeriod.LAST_WEEK, "2026-W40"),
        (datetime(2026, 10, 5, 13, tzinfo=UTC), CardPeriod.LAST_MONTH, "2026-09"),
        (datetime(2026, 10, 5, 13, tzinfo=UTC), CardPeriod.THIS_WEEK, "week"),
        (datetime(2026, 10, 5, 13, tzinfo=UTC), CardPeriod.THIS_MONTH, "month"),
        (datetime(2026, 1, 5, 9, tzinfo=UTC), CardPeriod.LAST_WEEK, "2026-W01"),
        (datetime(2026, 1, 1, 9, tzinfo=UTC), CardPeriod.LAST_WEEK, "2025-W52"),
        (datetime(2026, 1, 31, 9, tzinfo=UTC), CardPeriod.LAST_MONTH, "2025-12"),
    ],
)
def test_card_period_keys(now: datetime, period: CardPeriod, key: str) -> None:
    assert period.api_key(now) == key


def test_this_year_key() -> None:
    assert CardPeriod.THIS_YEAR.api_key(datetime(2026, 10, 5, tzinfo=UTC)) == "year"
    assert CardPeriod.THIS_YEAR.label == "This year"


def test_links_point_to_dashboard_not_api(report: dict[str, Any]) -> None:
    from fibre_bot.reports.format import dashboard_url, footer

    assert "fibre-api" not in footer() and "fibre-report" in footer()
    assert dashboard_url("Cumulo") == (
        "https://cumulo.pro/services/celestia_mocha/fibre-report?validator=Cumulo"
    )
    assert "validator=P-OPS+Team" in footer("P-OPS Team")
    details = render_details(report)
    assert "Open this report on the dashboard" in details
    assert "period=2026-W40&amp;validator=Cumulo" in details


def test_year_card_chart_and_caption() -> None:
    from fibre_bot.reports.card import daily_chart

    y = load_json("report_2026_year.json")
    svg = card_svg(y)
    assert "Stake ready at the end of each day" in svg
    assert "low 81.5%" in svg  # null days before tracking started are skipped
    assert "25 Sep to 5 Oct" in svg
    assert "Provider A, Provider B, Provider C" in svg
    cap = card_caption(y)
    assert "(Provider A, Provider B, Provider C) failing together." in cap
    assert "held there now." in cap
    assert "up from" not in cap  # no previous period for the year
    assert daily_chart([{"day": "2026-10-05", "ready_pct": 97.0}], 0, 0, 10, 10) == ""
    assert daily_chart(None, 0, 0, 10, 10) == ""


def test_closed_period_caption_says_at_the_end(report: dict[str, Any]) -> None:
    assert "held there at the end." in card_caption(report)


def test_breaks_line_single_provider() -> None:
    from fibre_bot.reports.card import breaks_line

    one = {"providers": 1, "sets": {"providers": [{"label": "Provider A"}]}}
    assert breaks_line(one) == "What breaks 2/3: Provider A alone going offline"
