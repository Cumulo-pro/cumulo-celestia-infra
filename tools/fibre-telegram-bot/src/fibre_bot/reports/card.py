"""Report card: the 1200 x 630 image the cumulo.pro Fibre report page offers as "Download image",
built from the API /report endpoint, plus the caption and a details text for the same period.

The SVG layout is a port of reportCardSvg() from cumulo.pro/services/celestia_mocha/fibre-report.
One deliberate difference: percentages are computed from the raw counts (served / checks) when
they exist, because the API rounds `pct` and can say 100 when one check in 23,081 failed.
"""

from __future__ import annotations

import html
from functools import lru_cache
from importlib import resources
from typing import Any

from fibre_bot.reports.format import (
    NA,
    esc,
    fmt_bytes,
    fmt_int,
    fmt_tia,
    join_sections,
    num,
    parse_time,
    short_addr,
)

CARD_W, CARD_H = 1200, 630
CAPTION_LIMIT = 1024
INK, MUTED, ACCENT, BG, BORDER = "#321A0C", "#6E4A1C", "#A86A24", "#FDF8F2", "#E8D9C4"
FONT = "Karla, Raleway, Segoe UI, Arial, sans-serif"
DASHES = {"\u2014": ",", "\u2013": "-"}  # em dash, en dash


# Numbers ---------------------------------------------------------------------------------


def ratio_pct(obj: Any) -> float | None:
    """Exact percentage from {served|ready, checks}; falls back to the rounded `pct`."""
    if not isinstance(obj, dict):
        return None
    checks = num(obj.get("checks"))
    part = num(obj.get("served", obj.get("ready")))
    if checks and part is not None:
        return part / checks * 100
    return num(obj.get("pct"))


def pct_no100(x: float | None, digits: int = 1) -> str:
    """Never show 100% for something below 100 (99.996 with 1 digit becomes 99.996%)."""
    if x is None:
        return "-"
    d = digits
    while x < 100 and f"{x:.{d}f}" == f"{100:.{d}f}" and d < 6:
        d += 1
    return f"{x:.{d}f}%"


def bytes_short(b: Any) -> str:
    n = num(b)
    if n is None:
        return "-"
    if n >= 1073741824:
        return f"{n / 1073741824:.2f} GiB"
    if n >= 1048576:
        return f"{n / 1048576:.{1 if n >= 10485760 else 2}f} MiB"
    if n >= 1024:
        return f"{round(n / 1024)} KiB"
    return f"{int(n)} B"


def _n(x: Any) -> str:
    v = num(x)
    return "-" if v is None else f"{round(v):,}"


def _count(x: Any, word: str) -> str:
    return f"{_n(x)} {word}" + ("" if num(x) == 1 else "s")


def _clean(text: str) -> str:
    for dash, repl in DASHES.items():
        text = text.replace(dash, repl)
    return text


# Card ------------------------------------------------------------------------------------


def _t(x: int, y: int, s: str, size: int, weight: int, fill: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}" font-family="{FONT}">{html.escape(_clean(str(s)))}</text>'
    )


def _validator_line(v: dict[str, Any]) -> str:
    blobs = v.get("blobs") or {}
    total = sum(int(num(blobs.get(k)) or 0) for k in ("signed", "not_collected", "not_ready"))
    parts = []
    online = ratio_pct(v.get("online"))
    if online is not None:
        parts.append(f"ready in {pct_no100(online, 1)} of checks")
    if total:
        parts.append(f"signed {_n(blobs.get('signed'))} of {_n(total)} blobs")
    kept = ratio_pct(v.get("kept"))
    if kept is not None:
        parts.append(f"kept {pct_no100(kept, 1)} of its data")
    name = v.get("moniker") or short_addr(str(v.get("valcons", "")))
    return f"{name}: " + ", ".join(parts)


def breaks_line(liveness: Any) -> str:
    """'What breaks 2/3: 3 hosting companies (A, B, C) failing together', or ''."""
    if not isinstance(liveness, dict) or not liveness.get("providers"):
        return ""
    providers = liveness["providers"]
    sets = (liveness.get("sets") or {}).get("providers") or []
    names = [str(g.get("label")) for g in sets if isinstance(g, dict) and g.get("label")]
    if providers == 1:  # "1 hosting company failing together" reads oddly
        return "What breaks 2/3: " + (
            f"{names[0]} alone going offline" if names else "1 hosting company going offline"
        )
    line = f"What breaks 2/3: {providers} hosting companies"
    line += f" ({', '.join(names)})" if names else ""
    return line + " failing together"


def _short_day(day: str) -> str:
    """2026-09-25 -> 25 Sep"""
    dt = parse_time(f"{day}T00:00:00Z")
    return f"{dt.day} {dt:%b}" if dt else day


def daily_chart(days: Any, x0: int, y0: int, x1: int, y1: int, threshold: float = 200 / 3) -> str:
    """Stake ready at the end of each day against the 2/3 line. Empty with fewer than 2 days."""
    if not isinstance(days, list):
        return ""
    pts = [(d.get("day"), num(d.get("ready_pct"))) for d in days if isinstance(d, dict)]
    values = [(day, v) for day, v in pts if v is not None]
    if len(values) < 2:
        return ""
    lo = min(60.0, min(v for _, v in values) - 5)
    hi = 100.0

    def y(v: float) -> float:
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    step = (x1 - x0) / (len(values) - 1)
    xy = [(x0 + i * step, y(v)) for i, (_, v) in enumerate(values)]
    line = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in xy)
    area = f"{x0},{y1} {line} {x1},{y1}"
    ty = y(threshold)
    out = f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#fff" rx="8"/>'
    out += f'<polygon points="{area}" fill="{ACCENT}" fill-opacity="0.14"/>'
    out += (
        f'<line x1="{x0}" y1="{ty:.1f}" x2="{x1}" y2="{ty:.1f}" stroke="{MUTED}" '
        'stroke-width="1.5" stroke-dasharray="6 5"/>'
    )
    out += _t(x1 - 8, int(ty) - 6, "2/3", 14, 700, MUTED, "end")
    out += (
        f'<polyline points="{line}" fill="none" stroke="{ACCENT}" stroke-width="3" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    out += _t(x0, y0 - 12, "Stake ready at the end of each day", 16, 700, MUTED)
    first, last = values[0][0], values[-1][0]
    if first and last:
        out += _t(
            x1,
            y1 + 22,
            f"{_short_day(str(first))} to {_short_day(str(last))}",
            14,
            400,
            MUTED,
            "end",
        )
    low_i = min(range(len(values)), key=lambda i: values[i][1])
    for i in {low_i, len(values) - 1}:
        x, yy = xy[i]
        out += f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="4.5" fill="{ACCENT}"/>'
    lx, ly = xy[low_i]
    if low_i != len(values) - 1:
        anchor = "start" if low_i < len(values) / 2 else "end"
        dx = 8 if anchor == "start" else -8
        out += _t(
            int(lx + dx),
            int(min(ly + 18, y1 - 4)),
            f"low {values[low_i][1]:.1f}%",
            14,
            700,
            INK,
            anchor,
        )
    return out


def card_svg(r: dict[str, Any], chain_name: str | None = None) -> str:
    network = r.get("network") or {}
    end = network.get("end") or {}
    usage = r.get("usage") or {}
    v = r.get("validator")
    liveness = network.get("liveness") or {}
    period = r.get("period") or {}

    online = ratio_pct(network.get("online"))
    fees = num(usage.get("fees_utia"))
    cells = [
        ("Blobs", _n(usage.get("blobs"))),
        ("Fees paid", f"{_n(fees / 1e6)} TIA" if fees is not None else "-"),
        (
            "Servers ready",
            f"{end['ready']} / {end['registered']}"
            if end.get("ready") is not None and end.get("registered") is not None
            else "-",
        ),
        ("Online", pct_no100(online, 1)),
    ]

    line2 = breaks_line(liveness)

    label = str(period.get("label", ""))
    if period.get("status") == "in_progress":
        label += " · so far"

    ready_pct = num(end.get("ready_pct"))
    s = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_W}" height="{CARD_H}" '
        f'viewBox="0 0 {CARD_W} {CARD_H}"><rect width="{CARD_W}" height="{CARD_H}" fill="{BG}"/>'
        f'<rect x="0" y="0" width="{CARD_W}" height="10" fill="{ACCENT}"/>'
    )
    s += _t(64, 92, f"Celestia Fibre on {chain_name or r.get('chain_id', '')}", 30, 700, INK)
    s += _t(64, 134, label, 26, 400, MUTED)
    s += _t(64, 250, pct_no100(ready_pct, 1), 104, 700, ACCENT)
    s += _t(64, 296, "of the stake is ready to sign Fibre payments (2/3 is needed)", 28, 400, INK)
    for i, (title, value) in enumerate(cells):
        x = 64 + i * 272
        s += (
            f'<rect x="{x}" y="340" width="252" height="120" rx="14" fill="#fff" '
            f'stroke="{BORDER}"/>'
        )
        s += _t(x + 22, 380, title, 20, 400, MUTED) + _t(x + 22, 432, value, 40, 700, INK)
    # Day by day chart in the free space right of the big figure (above the tagline).
    s += daily_chart(r.get("days"), 640, 168, 1136, 258)
    if line2:
        s += _t(64, 510, line2, 22, 400, INK)
    if isinstance(v, dict):
        s += _t(64, 546, _validator_line(v), 22, 700, INK)
    kept = ratio_pct(network.get("kept"))
    right = f"{bytes_short(usage.get('bytes'))} of data"
    if kept is not None:
        right += f" · {pct_no100(kept, 2)} of signed data kept"
    s += _t(64, 598, "Fibre Network Monitor by Cumulo · cumulo.pro", 20, 400, MUTED)
    s += _t(1136, 598, right, 20, 400, MUTED, "end")
    return s + "</svg>"


@lru_cache(maxsize=1)
def _font_path() -> str:
    return str(resources.files("fibre_bot.assets").joinpath("Karla.ttf"))


def render_png(svg: str) -> bytes:
    """SVG to PNG with the bundled Karla font only, so output is identical on every host."""
    import resvg_py

    data = resvg_py.svg_to_bytes(svg_string=svg, font_files=[_font_path()], skip_system_fonts=True)
    return bytes(data)


# Caption and details ---------------------------------------------------------------------


def card_caption(r: dict[str, Any], chain_name: str | None = None) -> str:
    """Share text in the same words as the cumulo.pro report, built from the same JSON as the
    card so both always agree (the API `text` uses the rounded pct). Max 1024 characters."""
    network = r.get("network") or {}
    end = network.get("end") or {}
    usage = r.get("usage") or {}
    escrow = usage.get("escrow") or {}
    events = usage.get("events") or {}
    period = r.get("period") or {}
    prev_end = ((r.get("previous") or {}).get("network") or {}).get("end") or {}

    title = f"Celestia Fibre on {chain_name or r.get('chain_id', '')} · {period.get('label', '')}"
    if period.get("status") == "in_progress":
        title += ", so far"
    lines = []
    ready = num(end.get("ready_pct"))
    if ready is not None:
        change = ""
        prev_ready = num(prev_end.get("ready_pct"))
        if prev_ready is not None and round(prev_ready, 1) != round(ready, 1):
            word = "up" if ready > prev_ready else "down"
            change = f" ({word} from {pct_no100(prev_ready, 1)})"
        lines.append(
            f"{pct_no100(ready, 1)} of the stake is ready to sign Fibre payments; "
            f"2/3 is needed{change}."
        )
    if end.get("ready") is not None:
        lines.append(
            f"{_n(end.get('ready'))} of {_n(end.get('registered'))} registered Fibre servers "
            f"ready, out of {_n(end.get('bonded'))} validators."
        )
    online = ratio_pct(network.get("online"))
    if online is not None:
        lines.append(f"Servers answered ready in {pct_no100(online, 1)} of our checks.")
    kept = ratio_pct(network.get("kept"))
    if kept is not None:
        lines.append(f"They kept {pct_no100(kept, 1)} of the data they signed for.")
    breaks = breaks_line(network.get("liveness"))
    if breaks:
        lines.append(breaks + ".")
    fees = num(usage.get("fees_utia"))
    lines.append(
        f"{_count(usage.get('blobs'), 'blob')}, {fmt_bytes(usage.get('bytes'))} of data, "
        f"{_n(fees / 1e6) if fees is not None else '-'} TIA in fees, "
        f"{_count(usage.get('payers'), 'publisher')}."
    )
    deposited = num(escrow.get("deposited_utia"))
    held = num(escrow.get("balance_end_utia"))
    if deposited is not None and held is not None:
        lines.append(
            f"{_n(deposited / 1e6)} TIA deposited into the Fibre escrow; "
            f"{_n(held / 1e6)} TIA held there "
            f"{'now' if period.get('status') == 'in_progress' else 'at the end'}."
        )
    if events.get("registered") is not None:
        lines.append(
            f"{_count(events.get('registered'), 'server')} joined, {_n(events.get('left'))} left."
        )
    v = r.get("validator")
    if isinstance(v, dict):
        lines.append(_validator_line(v) + ".")

    url = str(r.get("url") or "")
    tail = f'\n📊 <a href="{esc(url)}">Full report on cumulo.pro</a>' if url else ""
    body = [f"• {esc(_clean(ln))}" for ln in lines]
    head = f"<b>{esc(_clean(title))}</b>"
    text = "\n".join([head, *body]) + tail
    while len(text) > CAPTION_LIMIT and body:
        body.pop()
        text = "\n".join([head, *body, "…"]) + tail
    return text


def _delta(cur: float | None, prev: float | None, unit: str = " pts") -> str:
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < 0.005:
        return ", no change"
    return f", {'+' if d > 0 else ''}{d:.2f}{unit} vs previous"


def render_details(r: dict[str, Any], label: str | None = None) -> str:
    """Text with the figures behind the card, for the same calendar period."""
    period = r.get("period") or {}
    network = r.get("network") or {}
    start, end = network.get("start") or {}, network.get("end") or {}
    usage = r.get("usage") or {}
    prev = r.get("previous") or {}
    prev_net = prev.get("network") or {}
    v = r.get("validator")

    status = " (so far)" if period.get("status") == "in_progress" else ""
    chain_prefix = f"{esc(label)} · " if label else ""
    head = f"📋 <b>{chain_prefix}{esc(period.get('label', NA))}</b>{status}"

    online = network.get("online") or {}
    kept = network.get("kept") or {}
    lowest = network.get("lowest_ready") or {}
    low_at = parse_time(lowest.get("at"))
    settle = network.get("settlement_open") or {}
    net = [
        "🛡 <b>Network</b>",
        f"Stake ready: {pct_no100(num(start.get('ready_pct')), 2)} → "
        f"<b>{pct_no100(num(end.get('ready_pct')), 2)}</b>",
        f"Lowest: {pct_no100(num(lowest.get('ready_pct')), 2)}"
        + (f" on {low_at:%Y-%m-%d %H:%M} UTC" if low_at else ""),
        f"Payments could settle: {pct_no100(num(settle.get('pct')), 1)} of the time",
        f"Servers ready: {fmt_int(end.get('ready'))} of {fmt_int(end.get('registered'))} "
        f"registered, {fmt_int(end.get('bonded'))} validators",
        f"Online: {pct_no100(ratio_pct(online), 2)}"
        + _delta(ratio_pct(online), ratio_pct(prev_net.get("online")))
        + f" · {fmt_int(online.get('ready'))} of {fmt_int(online.get('checks'))} checks",
        f"Kept their data: {pct_no100(ratio_pct(kept), 3)} ({fmt_int(kept.get('served'))} of "
        f"{fmt_int(kept.get('checks'))} downloads)",
    ]

    escrow = usage.get("escrow") or {}
    top = usage.get("top_payer") or {}
    busiest = usage.get("busiest_day") or {}
    use = [
        "📦 <b>Use and TIA</b>",
        f"Blobs: {fmt_int(usage.get('blobs'))} · Data: {fmt_bytes(usage.get('bytes'))}",
        f"Fees paid: {fmt_tia(usage.get('fees_utia'))} · "
        f"{fmt_tia(usage.get('price_utia_per_mib'), digits=4)} per MiB",
        f"Publishers: {fmt_int(usage.get('payers'))}"
        + (
            f" · largest paid {pct_no100(num(top.get('share_of_fees_pct')), 2)} of fees"
            if top
            else ""
        ),
        f"Escrow: {fmt_tia(escrow.get('deposited_utia'))} deposited, "
        f"{fmt_tia(escrow.get('balance_end_utia'))} held at the end",
    ]
    if busiest.get("day"):
        use.append(f"Busiest day: {esc(busiest['day'])} ({fmt_int(busiest.get('blobs'))} blobs)")

    ev = usage.get("events") or {}
    events = (
        "🗓 <b>What happened</b>\n"
        f"{fmt_int(ev.get('registered'))} servers registered · {fmt_int(ev.get('left'))} left · "
        f"{fmt_int(ev.get('host_changed'))} moved\n"
        f"{fmt_int(ev.get('status_not_ready'))} times a server stopped being ready · "
        f"{fmt_int(ev.get('status_back_ready'))} times one came back"
    )

    val = None
    if isinstance(v, dict):
        blobs = v.get("blobs") or {}
        vprev = v.get("previous") or {}
        name = v.get("moniker") or short_addr(str(v.get("valcons", "")))
        vk = v.get("kept") or {}
        kept_line = (
            f"Kept its data: {pct_no100(ratio_pct(vk), 2)} ({fmt_int(vk.get('checks'))} checks)"
            if (num(vk.get("checks")) or 0) >= 5
            else f"Kept its data: not enough data ({fmt_int(vk.get('checks'))} checks)"
        )
        val = "\n".join(
            [
                f"⚡ <b>{esc(name)}</b>",
                f"Ready in {pct_no100(ratio_pct(v.get('online')), 2)} of checks"
                + _delta(ratio_pct(v.get("online")), ratio_pct(vprev.get("online"))),
                f"Signed {fmt_int(blobs.get('signed'))} · not collected "
                f"{fmt_int(blobs.get('not_collected'))} · <b>not ready "
                f"{fmt_int(blobs.get('not_ready'))}</b>",
                kept_line,
                f"Average RTT: {fmt_int(v.get('avg_rtt_ms'))} ms"
                if v.get("avg_rtt_ms") is not None
                else f"Average RTT: {NA}",
            ]
        )

    note = "<i>Calendar period in UTC, same figures as the cumulo.pro report.</i>"
    link = ""
    if r.get("url"):
        link = f'📊 <a href="{esc(str(r["url"]))}">Open this report on the dashboard</a>\n'
    tail = link + "<i>Powered by Cumulo Fibre Monitor · cumulo.pro</i>"
    return join_sections(head, "\n".join(net), "\n".join(use), events, val, note, tail)
