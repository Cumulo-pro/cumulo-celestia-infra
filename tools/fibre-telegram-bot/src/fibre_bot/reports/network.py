"""Network report: 2/3 status, quorum per region, liveness, concentration, retention."""

from __future__ import annotations

from typing import Any

from fibre_bot.api.metrics import MetricsSnapshot
from fibre_bot.data.models import Period, ReportContext
from fibre_bot.reports.format import (
    NA,
    data_since_line,
    dig,
    esc,
    fmt_int,
    fmt_ms,
    fmt_ratio_pct,
    footer,
    join_sections,
    no_autolink,
    num,
    parse_time,
    pre_table,
    stake_bar,
    stale_banner,
)
from fibre_bot.reports.format import (
    fmt_pct as _fmt_pct_text,
)


def fmt_pct(pct: Any, digits: int = 2) -> str:
    """Percentage escaped for HTML ("<0.01%" would otherwise break Telegram's parser)."""
    return esc(_fmt_pct_text(pct, digits))


def header_lines(summary: dict[str, Any], title: str, period: Period, ctx: ReportContext) -> str:
    meta = dig(summary, "meta", default={})
    app_version = dig(summary, "payment_formula", "app_version", default=NA)
    lines = [
        f"🛰 <b>{esc(title)}</b> · {esc(ctx.label)}"
        if ctx.label
        else f"🛰 <b>{esc(title)}</b> · {esc(meta.get('chain', NA))} "
        f"({esc(meta.get('network', NA))})",
        f"App {esc(app_version)} · height {fmt_int(meta.get('height'))}",
        f"Period: <b>{esc(period.label)}</b>",
    ]
    since = data_since_line(period.hours, parse_time(summary.get("history_since")), ctx.now)
    if since:
        lines.append(f"<i>{esc(since)}</i>")
    if ctx.stale:
        lines.insert(0, stale_banner(ctx.data_age) + "\n")
    return "\n".join(lines)


def threshold_section(summary: dict[str, Any], metrics: MetricsSnapshot | None) -> str:
    readiness = dig(summary, "readiness", default={})
    # Exact integer comparison is done server side: use the flag, never a rounded percentage.
    meets = metrics.get("fibre_ready_meets_threshold") if metrics else None
    if meets is None and isinstance(readiness.get("ready_meets_threshold"), bool):
        meets = 1.0 if readiness["ready_meets_threshold"] else 0.0
    needed = metrics.get("fibre_validators_needed_to_threshold") if metrics else None
    if needed is None:
        needed = num(readiness.get("validators_needed_to_threshold"))

    if meets is None:
        status = "❔ 2/3 status: n/a"
    elif meets >= 1:
        status = "✅ Ready stake meets 2/3"
    else:
        more = fmt_int(needed) if needed is not None else NA
        status = f"❌ Ready stake is below 2/3: {more} more ready validators needed"

    bonded = readiness.get("bonded_validators")
    tiers = readiness.get("tiers") or {}
    no_host = tiers.get("no_host") or {}
    not_ready = tiers.get("registered_not_ready") or {}
    lines = [
        "🛡 <b>2/3 threshold</b>",
        status,
        f"<code>{stake_bar(readiness.get('ready_stake_pct'))}</code> "
        f"{fmt_pct(readiness.get('ready_stake_pct'))} ready",
        f"Ready: {fmt_int(readiness.get('ready_validators'))}/{fmt_int(bonded)} validators, "
        f"{fmt_pct(readiness.get('ready_stake_pct'))} of stake",
        f"Registered: {fmt_int(readiness.get('registered_validators'))}/{fmt_int(bonded)}, "
        f"{fmt_pct(readiness.get('registered_stake_pct'))} of stake",
    ]
    if not_ready:
        pct = num(not_ready.get("stake_pct"))
        if pct == 0 and (num(not_ready.get("validators")) or 0) > 0:
            # The API rounds to 2 decimals; a validator with a little stake is not 0%.
            reg, rdy = num(readiness.get("registered_tokens")), num(readiness.get("ready_tokens"))
            total = num(readiness.get("bonded_tokens"))
            pct = (reg - rdy) / total * 100 if reg and rdy and total else 0.001
            pct = pct or 0.001
        lines.append(
            f"Registered, not ready: {fmt_int(not_ready.get('validators'))} ({fmt_pct(pct)})"
        )
    if no_host:
        lines.append(
            f"No host: {fmt_int(no_host.get('validators'))} ({fmt_pct(no_host.get('stake_pct'))})"
        )
    return "\n".join(lines)


def quorum_section(summary: dict[str, Any]) -> str | None:
    regions = summary.get("quorum_by_region") or []
    if not regions:
        return None
    rows = [["Region", "Quorum RTT", "Needed"]]
    for r in regions:
        reached = r.get("reached")
        flag = "" if reached is None else (" ok" if reached else " NO")
        rows.append(
            [
                str(r.get("region", "?")) + flag,
                fmt_ms(r.get("quorum_rtt_ms")),
                fmt_int(r.get("validators_needed")),
            ]
        )
    return "🌍 <b>Quorum by probe region</b>\n" + pre_table(rows)


def liveness_section(summary: dict[str, Any]) -> str | None:
    lc = summary.get("liveness_coefficient")
    if not isinstance(lc, dict):
        return None
    return (
        "🧩 <b>Liveness coefficient</b>\n"
        "<i>How many would have to go offline together to drop below 2/3</i>\n"
        f"Validators {fmt_int(lc.get('validators'))} · Providers {fmt_int(lc.get('providers'))}"
        f" · Countries {fmt_int(lc.get('countries'))}"
    )


MAX_LISTED = 6


def _risk_list(entries: list[dict[str, Any]]) -> str:
    shown = [
        f"{esc(no_autolink(str(e.get('label') or e.get('key'))))} "
        f"{fmt_pct(e.get('ready_stake_pct'))}"
        for e in entries[:MAX_LISTED]
    ]
    more = len(entries) - MAX_LISTED
    return " · ".join(shown) + (f" · and {more} more" if more > 0 else "")


def concentration_section(summary: dict[str, Any]) -> str | None:
    readiness = dig(summary, "readiness", default={})
    conc = summary.get("concentration")
    if not isinstance(conc, dict):
        return None
    by_country = conc.get("by_country") or []
    by_provider = conc.get("by_provider") or []

    def at_risk(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        risky = [e for e in entries if e.get("quorum_survives_loss") is False]
        return sorted(risky, key=lambda e: -(num(e.get("ready_stake_pct")) or 0))

    countries, providers = at_risk(by_country), at_risk(by_provider)
    lines = [
        "🏢 <b>Concentration</b>",
        f"{fmt_int(readiness.get('countries'))} countries · "
        f"{fmt_int(readiness.get('providers'))} hosting providers",
    ]
    if countries or providers:
        ready = num(readiness.get("ready_stake_pct"))
        threshold = num(readiness.get("threshold_pct")) or 200 / 3
        if ready is not None:
            lines.append(
                f"Ready stake is {fmt_pct(ready)} of total stake, "
                f"just {ready - threshold:.2f} points above 2/3. "
                "If any one of these goes fully offline, Fibre drops below 2/3 "
                "(share of total stake):"
            )
        else:
            lines.append(
                "If any one of these goes fully offline, Fibre drops below 2/3 "
                "(share of total stake):"
            )
        if countries:
            lines.append(f"❌ <b>Countries</b>: {_risk_list(countries)}")
        if providers:
            lines.append(f"❌ <b>Providers</b>: {_risk_list(providers)}")
        return "\n".join(lines)
    if by_country or by_provider:
        lines.append("✅ Losing any single country or provider keeps Fibre above 2/3")
    top = [
        f"{kind} {_risk_list(e[:1])}"
        for kind, e in (("country", by_country), ("provider", by_provider))
        if e
    ]
    if top:
        lines.append("Largest: " + " · ".join(top))
    return "\n".join(lines)


def retention_section(summary: dict[str, Any], metrics: MetricsSnapshot | None) -> str:
    ret = summary.get("retention_24h") or {}
    obligations = ret.get("obligations") or {}
    served = metrics.get("fibre_retention_obligations_served_ratio_24h") if metrics else None
    served_txt = fmt_ratio_pct(served) if served is not None else fmt_pct(obligations.get("pct"))
    verified = metrics.get("fibre_rows_verified_ratio_24h") if metrics else None
    verified_txt = (
        fmt_ratio_pct(verified) if verified is not None else fmt_pct(dig(ret, "verified", "pct"))
    )
    avail = ret.get("availability") or {}
    lines = [
        "💾 <b>Retention and uptime (24h)</b>",
        f"Obligations served: {served_txt} ({fmt_int(obligations.get('checked'))} checks)",
        f"Rows verified: {verified_txt}",
        f"Network uptime: {fmt_pct(dig(summary, 'network_uptime_24h', 'pct'))}",
    ]
    if avail:
        lines.append(
            f"Blobs reconstructable by the monitor: {fmt_int(avail.get('reconstructable'))}"
            f"/{fmt_int(avail.get('blobs'))}"
        )
    return "\n".join(lines)


def period_section(timeseries: dict[str, Any] | None, period: Period) -> str | None:
    """Ready stake over the period from /timeseries (hourly points)."""
    if period is Period.H24 or not timeseries:
        return None
    values = [
        v for p in timeseries.get("points") or [] if (v := num(p.get("ready_pct"))) is not None
    ]
    ready = [v for p in timeseries.get("points") or [] if (v := num(p.get("ready"))) is not None]
    if not values:
        return f"📈 <b>Over {esc(period.label)}</b>\nReady stake history: n/a"
    lines = [
        f"📈 <b>Over {esc(period.label)}</b> (hourly)",
        f"Ready stake: min {fmt_pct(min(values))} · avg {fmt_pct(sum(values) / len(values))}"
        f" · max {fmt_pct(max(values))}",
    ]
    if ready:
        lines.append(f"Ready validators: {fmt_int(min(ready))} to {fmt_int(max(ready))}")
    return "\n".join(lines)


def render_network(
    summary: dict[str, Any],
    metrics: MetricsSnapshot | None,
    period: Period,
    ctx: ReportContext,
    timeseries: dict[str, Any] | None = None,
) -> str:
    return join_sections(
        header_lines(summary, "Fibre network", period, ctx),
        threshold_section(summary, metrics),
        quorum_section(summary),
        liveness_section(summary),
        concentration_section(summary),
        period_section(timeseries, period),
        retention_section(summary, metrics),
        footer(base=ctx.dashboard),
    )
