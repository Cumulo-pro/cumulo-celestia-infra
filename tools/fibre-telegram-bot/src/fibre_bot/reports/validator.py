"""Validator report and the one-line /status answer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fibre_bot.api.metrics import MetricsSnapshot
from fibre_bot.data.models import Period, ReportContext
from fibre_bot.reports.format import (
    NA,
    data_since_line,
    esc,
    fmt_int,
    fmt_ms,
    fmt_ratio_pct,
    fmt_seconds_as_ms,
    footer,
    join_sections,
    num,
    pre_table,
    short_addr,
    stale_banner,
)

MIN_RETENTION_CHECKS = 5
REGION_ORDER = ["US", "EU", "CA", "AS"]  # same order as the quorum table of the network report

REASON_TEXT = {
    "ready": "ready",
    "not_ready": "host not ready",
    "no_host": "no Fibre host registered",
    "monitor_unknown": "monitor has no result yet",
    "warming_up": "monitor warming up",
}


def readiness_line(
    ready: float | None,
    reason: str | None,
    regions_online: float | None,
    regions_reporting: float | None,
) -> str:
    regions = ""
    if regions_online is not None and regions_reporting is not None:
        regions = f" ({fmt_int(regions_online)}/{fmt_int(regions_reporting)} regions)"
    if reason in ("monitor_unknown", "warming_up"):
        return f"❔ {esc(REASON_TEXT[reason])}"
    if ready is None and reason is None:
        return "❔ Readiness: n/a"
    if (ready is not None and ready >= 1) or reason == "ready":
        return f"✅ Ready{regions}"
    text = REASON_TEXT.get(reason or "", reason or "not ready")
    return f"❌ Not ready: {esc(text)}{regions}"


def retention_line(served: float | None, checks: float | None) -> str:
    if checks is None or checks < MIN_RETENTION_CHECKS:
        evidence = fmt_int(checks) if checks is not None else "0"
        return f"Retention: not enough data ({evidence} checks)"
    return f"Retention: {fmt_ratio_pct(served)} ({fmt_int(checks)} checks of signed blobs)"


def cert_line(expiry_ts: float | None, now: datetime) -> str:
    if expiry_ts is None:
        return f"Certificate: {NA}"
    days = (expiry_ts - now.timestamp()) / 86400
    if days < 0:
        return "Certificate: ⚠️ expired"
    warn = "⚠️ " if days < 14 else ""
    return f"Certificate: {warn}{days:.0f} days left"


def render_validator(
    valcons: str,
    metrics: MetricsSnapshot,
    period: Period,
    ctx: ReportContext,
    *,
    status: dict[str, Any] | None = None,
    blobs: dict[str, Any] | None = None,
    history_since: datetime | None = None,
) -> str:
    v = metrics.validator(valcons)
    label = period.metric_label or "30d"
    moniker = v.moniker or (status or {}).get("moniker") or short_addr(valcons)

    head = [
        f"⚡ <b>{esc(moniker)}</b>" + (f" · {esc(ctx.label)}" if ctx.label else ""),
        f"<code>{esc(short_addr(valcons))}</code>",
        f"Period: <b>{esc(period.label)}</b>",
    ]
    since = data_since_line(period.hours, history_since, ctx.now)
    if since:
        head.append(f"<i>{esc(since)}</i>")
    if ctx.stale:
        head.insert(0, stale_banner(ctx.data_age) + "\n")
    if not v.exists:
        head.append("This validator is not in the bonded set seen by the monitor.")
        return join_sections("\n".join(head), footer(base=ctx.dashboard))

    reason = (status or {}).get("reason")
    stake = v.get("fibre_validator_stake_ratio")
    head.append(
        readiness_line(
            v.get("fibre_validator_ready"),
            reason if isinstance(reason, str) else None,
            v.get("fibre_validator_regions_online"),
            v.get("fibre_validator_regions_reporting"),
        )
    )
    head.append(f"Stake: {fmt_ratio_pct(stake)} of total")

    signed_ratio = v.get("fibre_validator_signed_ratio", label)
    signed = v.get("fibre_validator_signed_blobs", label)
    signable = v.get("fibre_validator_signable_blobs", label)
    signed_detail = ""
    if signed is not None and signable is not None:
        signed_detail = f" ({fmt_int(signed)} of {fmt_int(signable)})"
    perf = [
        f"📊 <b>Performance ({esc(label)})</b>",
        f"Uptime: {fmt_ratio_pct(v.get('fibre_validator_online_ratio', label))}",
        f"Signed: {fmt_ratio_pct(signed_ratio)}{signed_detail}",
        *(
            ["<i>Below 100% is normal: publishers stop collecting signatures at 2/3 of stake.</i>"]
            if signed_ratio is not None and signed_ratio < 1
            else []
        ),
        retention_line(
            v.get("fibre_validator_obligations_served_ratio", label),
            v.get("fibre_validator_obligation_checks", label),
        ),
        f"Rows verified: {fmt_ratio_pct(v.get('fibre_validator_rows_verified_ratio', label))}",
        f"Unreachable checks: {fmt_int(v.get('fibre_validator_unreachable_checks', label))}",
    ]

    counts = (blobs or {}).get("counts") if blobs else None
    if isinstance(counts, dict):
        blob_section = (
            "🧾 <b>Blobs (whole tracked history)</b>\n"
            f"Signed {fmt_int(counts.get('signed'))} · "
            f"Not collected {fmt_int(counts.get('not_collected'))} · "
            f"<b>Not ready {fmt_int(counts.get('not_ready'))}</b>\n"
            '<i>Only "not ready" is a failure: the host could not sign.</i>'
        )
    else:
        blob_section = "🧾 <b>Blobs (whole tracked history)</b>\nn/a"

    rtt = v.by_region("fibre_validator_rtt_seconds")
    up = v.by_region("fibre_validator_region_up")
    net = [
        "🌐 <b>Network</b>",
        f"Typical RTT: {esc(fmt_seconds_as_ms(v.get('fibre_validator_typical_rtt_seconds')))}",
    ]
    if rtt or up:
        rows = [["Region", "RTT", "Up"]]
        order = {name: i for i, name in enumerate(REGION_ORDER)}
        for region in sorted(set(rtt) | set(up), key=lambda r: (order.get(r, 99), r)):
            state = up.get(region)
            rows.append(
                [
                    region,
                    fmt_seconds_as_ms(rtt.get(region)),
                    NA if state is None else ("yes" if state >= 1 else "no"),
                ]
            )
        net.append(pre_table(rows))
    net.append(cert_line(v.get("fibre_validator_cert_expiry_timestamp_seconds"), ctx.now))
    identity = v.get("fibre_validator_identity_ok")
    if identity is not None and identity < 1:
        net.append("⚠️ TLS identity does not match the consensus key")
    warnings = v.get("fibre_validator_warnings")
    net.append(f"Active warnings: {fmt_int(warnings)}")
    status_warnings = (status or {}).get("warnings")
    if isinstance(status_warnings, list):
        for w in status_warnings[:3]:
            text = w.get("text") if isinstance(w, dict) else w
            net.append(f"• {esc(text)}")

    return join_sections(
        "\n".join(head),
        "\n".join(perf),
        blob_section,
        "\n".join(net),
        # Validator link goes to the report tab (it takes ?validator=); else the general page.
        footer(v.moniker or valcons, base=ctx.report_page)
        if ctx.report_page
        else footer(base=ctx.dashboard),
    )


def render_status_line(status: dict[str, Any]) -> str:
    """Quick answer for /status <validator>, from the /status endpoint."""
    moniker = status.get("moniker") or short_addr(str(status.get("valcons", "?")))
    reason = status.get("reason")
    raw_regions = status.get("regions")
    regions: dict[str, Any] = raw_regions if isinstance(raw_regions, dict) else {}
    online = len(
        [r for r in regions.values() if isinstance(r, dict) and r.get("status") == "online"]
    )
    line = readiness_line(
        1.0 if status.get("ready") is True else (0.0 if status.get("ready") is False else None),
        reason if isinstance(reason, str) else None,
        float(online) if regions else None,
        float(len(regions)) if regions else None,
    )
    extra = []
    typical = num(status.get("typical_rtt_ms"))
    if typical is not None:
        extra.append(f"typical RTT {esc(fmt_ms(typical))}")
    uptime_map = status.get("uptime")
    uptime = num(uptime_map.get("h24")) if isinstance(uptime_map, dict) else None
    if uptime is not None:
        extra.append(f"uptime 24h {uptime:.2f}%")
    suffix = f" · {', '.join(extra)}" if extra else ""
    return f"<b>{esc(moniker)}</b>: {line}{suffix}"
