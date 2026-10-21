"""Small shared types. The API payloads themselves stay as plain dicts and are read with
the defensive helpers in reports/format.py, so a missing field renders as n/a."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Public report page of the mocha-5 Fibre monitor (other chains configure their own).
DASHBOARD_URL = "https://cumulo.pro/services/celestia_mocha/fibre-report"


class Period(StrEnum):
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"
    ALL = "all"

    @property
    def hours(self) -> int | None:
        return {"24h": 24, "7d": 168, "30d": 720}.get(self.value)

    @property
    def label(self) -> str:
        return "since start" if self is Period.ALL else self.value

    @property
    def window_key(self) -> str | None:
        """Key used in summary.meta.windows."""
        return {"24h": "h24", "7d": "d7", "30d": "d30"}.get(self.value)

    @property
    def metric_label(self) -> str | None:
        """Value of the Prometheus `period` label, when one exists."""
        return None if self is Period.ALL else self.value

    @property
    def series_range(self) -> str:
        """Range for /timeseries and /activity/series. 'all' is clamped server side."""
        return "30d" if self is Period.ALL else self.value


class CardPeriod(StrEnum):
    """Calendar periods of the /report endpoint, as offered in the bot."""

    THIS_WEEK = "w"
    LAST_WEEK = "pw"
    THIS_MONTH = "m"
    LAST_MONTH = "pm"
    THIS_YEAR = "y"

    @property
    def label(self) -> str:
        return {
            "w": "This week",
            "pw": "Last week",
            "m": "This month",
            "pm": "Last month",
            "y": "This year",
        }[self.value]

    @property
    def text_period(self) -> Period:
        """Closest rolling window for the text reports."""
        if self in (CardPeriod.THIS_WEEK, CardPeriod.LAST_WEEK):
            return Period.D7
        return Period.ALL if self is CardPeriod.THIS_YEAR else Period.D30

    def api_key(self, now: datetime) -> str:
        """Value for /report?period=. Closed periods use explicit keys (ISO week, YYYY-MM)."""
        if self is CardPeriod.THIS_WEEK:
            return "week"
        if self is CardPeriod.THIS_MONTH:
            return "month"
        if self is CardPeriod.THIS_YEAR:
            return "year"
        if self is CardPeriod.LAST_WEEK:
            year, week, _ = (now - timedelta(days=7)).isocalendar()
            return f"{year}-W{week:02d}"
        first = now.replace(day=1)
        last_month = first - timedelta(days=1)
        return f"{last_month.year}-{last_month.month:02d}"


class ReportKind(StrEnum):
    NETWORK = "n"
    VALIDATOR = "v"
    MARKET = "m"


@dataclass(frozen=True)
class ValidatorRef:
    valcons: str
    moniker: str


@dataclass(frozen=True)
class ReportContext:
    """What every renderer needs besides the data: the clock and the staleness state."""

    now: datetime
    stale: bool = False
    data_age: float | None = None
    dashboard: str | None = DASHBOARD_URL  # general page; None: not published, no link
    report_page: str | None = None  # report tab, takes ?validator= (validator links)
    label: str | None = None  # "Mainnet" / "Mocha testnet", shown on every report
