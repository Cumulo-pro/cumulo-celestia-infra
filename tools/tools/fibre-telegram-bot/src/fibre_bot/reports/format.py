"""Formatting helpers shared by every report.

Rules: HTML parse mode, every dynamic string escaped, `None` always rendered as "n/a"
(never as 0), no em or en dashes.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from fibre_bot.data.models import DASHBOARD_URL

__all__ = ["DASHBOARD_URL"]  # re-exported for callers that import it from here

NA = "n/a"
UTIA_PER_TIA = 1_000_000
TELEGRAM_LIMIT = 4096


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def no_autolink(text: str) -> str:
    """Stop Telegram from turning names like 'host.example' into links (word joiner after dots)."""
    return text.replace(".", ".\u2060")


def dig(data: Any, *path: str | int, default: Any = None) -> Any:
    """Safe nested lookup: dig(summary, "readiness", "ready_validators")."""
    cur = data
    for key in path:
        if isinstance(cur, Mapping):
            if key not in cur:
                return default
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int) and -len(cur) <= key < len(cur):
            cur = cur[key]
        else:
            return default
    return default if cur is None else cur


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_int(value: Any) -> str:
    n = num(value)
    return NA if n is None else f"{round(n):,}"


def fmt_ratio_pct(ratio: Any, digits: int = 2) -> str:
    """Prometheus ratio (0 to 1) as a percentage."""
    n = num(ratio)
    return NA if n is None else f"{n * 100:.{digits}f}%"


def fmt_pct(pct: Any, digits: int = 2) -> str:
    """JSON percentage (0 to 100)."""
    n = num(pct)
    if n is None:
        return NA
    if 0 < abs(n) < 0.5 * 10**-digits:  # tiny but not zero: never print 0.00%
        return f"<{10**-digits:.{digits}f}%"
    return f"{n:.{digits}f}%"


def plural(count: Any, word: str) -> str:
    """'1 account', '2 accounts', 'n/a accounts'."""
    return f"{fmt_int(count)} {word}" + ("" if num(count) == 1 else "s")


def fmt_tia(utia: Any, digits: int = 2) -> str:
    n = num(utia)
    if n is None:
        return NA
    # utia are exact integers: use Decimal so 30,186.015 TIA rounds half up, not to .01
    try:
        tia = Decimal(str(utia if isinstance(utia, int | str) else n)) / UTIA_PER_TIA
    except InvalidOperation:
        return NA
    if 0 < abs(tia) < 1 and digits < 4:
        digits = 4
    rounded = tia.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{rounded:,.{digits}f} TIA"


def fmt_bytes(value: Any) -> str:
    n = num(value)
    if n is None:
        return NA
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    i = 0
    while abs(n) >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.0f} {units[i]}" if i == 0 else f"{n:.1f} {units[i]}"


def fmt_size_range(lo: Any, hi: Any) -> str:
    def short(v: Any) -> str:
        n = num(v)
        if n is None:
            return "?"
        if n == 0:
            return "0"
        return fmt_bytes(n).replace(".0 ", " ")

    a, b = short(lo), short(hi)
    if a.endswith(b[-4:]) and " " in a:  # same unit: "16-64 MiB"
        return f"{a.split(' ')[0]}-{b}"
    return f"{a}-{b}"


def fmt_seconds_as_ms(seconds: Any) -> str:
    n = num(seconds)
    return NA if n is None else fmt_ms(n * 1000)


def fmt_ms(ms: Any) -> str:
    n = num(ms)
    if n is None:
        return NA
    return "<1 ms" if 0 <= n < 0.5 else f"{n:.0f} ms"  # a probe next to the host: not 0 ms


def short_addr(addr: str, head: int = 18, tail: int = 6) -> str:
    if len(addr) <= head + tail + 1:
        return addr
    return f"{addr[:head]}…{addr[-tail:]}"


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_date(dt: datetime | None) -> str:
    return NA if dt is None else dt.astimezone(UTC).strftime("%Y-%m-%d")


def data_since_line(
    period_hours: int | None, history_since: datetime | None, now: datetime
) -> str | None:
    """'Data since <date>' when the period reaches back before the tracked history.

    period_hours=None means "since start", which always gets the line.
    """
    if history_since is None:
        return None
    since = fmt_date(history_since)
    if period_hours is None:
        return f"Data since {since}"
    if now - timedelta(hours=period_hours) < history_since:
        return f"Data since {since} (tracked history is shorter than the period)"
    return None


def stale_banner(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "⚠️ <b>Monitor data unavailable.</b> Figures may be out of date."
    minutes = int(age_seconds // 60)
    return f"⚠️ <b>Monitor data is stale</b> ({minutes} min old). Figures may be out of date."


def stake_bar(pct: Any, threshold: float = 200 / 3, width: int = 12) -> str:
    """Text gauge of ready stake, with the 2/3 mark: ▰▰▰▰▰▰▰▰┃▰▰▱"""
    n = num(pct)
    if n is None:
        return ""
    filled = max(0, min(width, round(n / 100 * width)))
    mark = round(threshold / 100 * width)
    cells = ["▰" if i < filled else "▱" for i in range(width)]
    cells.insert(mark, "┃")
    return "".join(cells)


def dashboard_url(
    validator: str | None = None, period: str | None = None, base: str = DASHBOARD_URL
) -> str:
    """Link to the public report page on cumulo.pro (meant for users, not the API docs)."""
    params = {k: v for k, v in (("period", period), ("validator", validator)) if v}
    return f"{base}?{urlencode(params)}" if params else base


def footer(validator: str | None = None, base: str | None = DASHBOARD_URL) -> str:
    """Dashboard link (when the chain has one) and the credit line."""
    credit = "<i>Powered by Cumulo Fibre Monitor · cumulo.pro</i>"
    if not base:
        return credit
    url = html.escape(dashboard_url(validator, base=base), quote=True)
    label = "Open this validator on the dashboard" if validator else "Open the Fibre dashboard"
    return f'📊 <a href="{url}">{label}</a>\n{credit}'


def pre_table(rows: list[list[str]]) -> str:
    """Small aligned monospace table. Keep it narrow: it must fit a phone screen."""
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in rows]
    return "<pre>" + esc("\n".join(lines)) + "</pre>"


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph boundaries (blank lines), then on lines, never inside <pre>."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(block) <= limit:
            current = block
            continue
        # A single huge block: cut it by lines (tables are short, so this is plain text).
        current = ""
        for line in block.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = line[:limit]
    if current:
        chunks.append(current)
    return chunks


def join_sections(*sections: str | None) -> str:
    return "\n\n".join(s for s in sections if s)
