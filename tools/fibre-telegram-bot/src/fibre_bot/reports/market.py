"""Market report: blobs, bytes, fees, price per MiB, size distribution, publishers, escrow."""

from __future__ import annotations

from typing import Any

from fibre_bot.data.models import Period, ReportContext
from fibre_bot.reports.format import (
    NA,
    data_since_line,
    esc,
    fmt_bytes,
    fmt_int,
    fmt_size_range,
    fmt_tia,
    footer,
    join_sections,
    num,
    parse_time,
    plural,
    pre_table,
    short_addr,
    stale_banner,
)

TOP_PUBLISHERS = 5


def render_market(
    period: Period,
    ctx: ReportContext,
    *,
    stats: dict[str, Any] | None,
    publishers: dict[str, Any] | None,
    series: dict[str, Any] | None = None,
    history_since: str | None = None,
) -> str:
    net = f" · {esc(ctx.label)}" if ctx.label else ""
    head = [f"📦 <b>Fibre market</b>{net}", f"Period: <b>{esc(period.label)}</b>"]
    since = data_since_line(period.hours, parse_time(history_since), ctx.now)
    if since:
        head.append(f"<i>{esc(since)}</i>")
    if ctx.stale:
        head.insert(0, stale_banner(ctx.data_age) + "\n")

    stats = stats or {}
    totals = (publishers or {}).get("totals") or {}

    blobs = stats.get("settlements", totals.get("blobs"))
    bytes_ = stats.get("bytes", totals.get("bytes"))
    fees = stats.get("fees_utia", totals.get("fees_utia"))
    price = totals.get("price_utia_per_mib")
    timeouts = None
    if series and isinstance(series.get("points"), list):
        values = [num(p.get("timeouts")) for p in series["points"]]
        known = [v for v in values if v is not None]
        timeouts = sum(known) if known else None

    activity = [
        "💸 <b>Activity</b>",
        f"Blobs: {fmt_int(blobs)} · Data: {fmt_bytes(bytes_)}",
        f"Fees: {fmt_tia(fees)}",
        f"Publishers: {fmt_int(totals.get('publishers'))}",
        f"Average price: {fmt_tia(price, digits=4)} per MiB",
        f"Payment promises timed out: {fmt_int(timeouts)}",
    ]

    fee_check = stats.get("fee_check") if isinstance(stats.get("fee_check"), dict) else None
    if fee_check is None:
        activity.append(f"Fee check: {NA}")
    else:
        mismatches = fee_check.get("mismatches") or []
        checked = fmt_int(fee_check.get("checked"))
        matching = fmt_int(fee_check.get("matching"))
        if mismatches:
            activity.append(
                f"⚠️ Fee check: {plural(len(mismatches), 'blob')} did not pay the expected fee "
                f"({matching}/{checked} match)"
            )
        else:
            activity.append(f"✅ Fee check: {matching}/{checked} blobs paid the expected fee")

    size_section = None
    buckets = [b for b in stats.get("size_buckets") or [] if (num(b.get("blobs")) or 0) > 0]
    total_blobs = sum(num(b.get("blobs")) or 0 for b in buckets)
    if buckets and total_blobs:
        rows = [["Size", "Blobs", "Share"]]
        for b in buckets:
            count = num(b.get("blobs")) or 0
            rows.append(
                [
                    fmt_size_range(b.get("from_bytes"), b.get("to_bytes")),
                    fmt_int(count),
                    f"{count / total_blobs * 100:.1f}%",
                ]
            )
        size_section = "📏 <b>Blob sizes</b>\n" + pre_table(rows)

    pub_section = None
    pubs = [p for p in (publishers or {}).get("publishers") or [] if isinstance(p, dict)]
    pubs = [p for p in pubs if (num(p.get("blobs")) or 0) > 0]
    pubs.sort(key=lambda p: num(p.get("fees_utia")) or 0, reverse=True)
    if pubs:
        lines = ["🏆 <b>Top publishers</b> (share of fees)"]
        for p in pubs[:TOP_PUBLISHERS]:
            share = num(p.get("share_fees_pct"))
            share_txt = NA if share is None else f"{share:.2f}%"
            lines.append(
                f"<code>{esc(short_addr(str(p.get('account', '?')), 14, 6))}</code> "
                f"{share_txt} · {plural(p.get('blobs'), 'blob')}"
            )
        pub_section = "\n".join(lines)

    escrow = (
        "🏦 <b>Escrow</b> (TIA that publishers deposit to pay for blobs)\n"
        f"Held: {fmt_tia(totals.get('escrow_held_utia'))} in "
        f"{plural(totals.get('escrow_accounts'), 'account')}"
    )
    queued = num(totals.get("queued_out_utia"))
    if queued:
        escrow += f"\nQueued withdrawals: {fmt_tia(queued)}"

    return join_sections(
        "\n".join(head),
        "\n".join(activity),
        size_section,
        pub_section,
        escrow,
        footer(base=ctx.dashboard),
    )
