from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

from fibre_bot.api.metrics import MetricsSnapshot
from fibre_bot.data.models import Period, ReportContext
from fibre_bot.reports.format import parse_time
from fibre_bot.reports.market import render_market
from fibre_bot.reports.network import render_network
from fibre_bot.reports.validator import render_status_line, render_validator
from tests.conftest import CUMULO, NOW, load_json

DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash


def assert_clean(text: str) -> None:
    assert not any(d in text for d in DASHES), "em/en dash in output"
    assert "Powered by" in text


# Network ---------------------------------------------------------------------------------


def test_network_meets_threshold(
    summary: dict[str, Any], metrics: MetricsSnapshot, ctx: ReportContext
) -> None:
    text = render_network(summary, metrics, Period.H24, ctx)
    assert "✅ Ready stake meets 2/3" in text
    assert "77/83" in text
    assert "AS ok" in text and "203 ms" in text
    assert "Data since" not in text
    assert_clean(text)


def test_network_below_threshold_uses_flag_not_percentage(
    summary: dict[str, Any], metrics_text: str, ctx: ReportContext
) -> None:
    # 66.67% rounded looks like "meets", but the exact server flag says it does not.
    text = metrics_text.replace(
        'fibre_ready_meets_threshold{chain="mocha-5"} 1',
        'fibre_ready_meets_threshold{chain="mocha-5"} 0',
    ).replace(
        'fibre_validators_needed_to_threshold{chain="mocha-5"} 0',
        'fibre_validators_needed_to_threshold{chain="mocha-5"} 1',
    )
    s = copy.deepcopy(summary)
    s["readiness"]["ready_stake_pct"] = 66.67
    out = render_network(s, MetricsSnapshot.parse(text), Period.H24, ctx)
    assert "❌ Ready stake is below 2/3: 1 more ready validators needed" in out


def test_network_falls_back_to_summary_flag_without_metrics(
    summary: dict[str, Any], ctx: ReportContext
) -> None:
    out = render_network(summary, None, Period.H24, ctx)
    assert "✅ Ready stake meets 2/3" in out


def test_network_nulls_render_na(summary: dict[str, Any], ctx: ReportContext) -> None:
    s = copy.deepcopy(summary)
    s["readiness"] = {"ready_validators": None, "bonded_validators": None}
    s["network_uptime_24h"] = None
    s["retention_24h"] = {}
    out = render_network(s, MetricsSnapshot.parse(""), Period.H24, ctx)
    assert "2/3 status: n/a" in out
    assert "Ready: n/a/n/a" in out
    assert "Network uptime: n/a" in out
    assert "0.00%" not in out


def test_network_30d_says_data_since(
    summary: dict[str, Any], metrics: MetricsSnapshot, ctx: ReportContext
) -> None:
    ts = load_json("timeseries_7d.json")
    out = render_network(summary, metrics, Period.D30, ctx, ts)
    assert "Data since 2026-09-25" in out
    assert "Ready stake: min 92.55%" in out
    out_all = render_network(summary, metrics, Period.ALL, ctx, ts)
    assert "Period: <b>since start</b>" in out_all
    assert "Data since 2026-09-25" in out_all


def test_network_quorum_break_is_listed(
    summary: dict[str, Any], metrics: MetricsSnapshot, ctx: ReportContext
) -> None:
    s = copy.deepcopy(summary)
    s["concentration"]["by_provider"][0]["quorum_survives_loss"] = False
    out = render_network(s, metrics, Period.H24, ctx)
    assert "<b>Providers</b>: Provider A" in out


def test_network_stale_banner(summary: dict[str, Any], metrics: MetricsSnapshot) -> None:
    out = render_network(
        summary, metrics, Period.H24, ReportContext(now=NOW, stale=True, data_age=1500)
    )
    assert out.startswith("⚠️ <b>Monitor data is stale</b> (25 min old)")


# Validator -------------------------------------------------------------------------------


def test_validator_report(
    summary: dict[str, Any], metrics: MetricsSnapshot, ctx: ReportContext
) -> None:
    out = render_validator(
        CUMULO,
        metrics,
        Period.D7,
        ctx,
        status=load_json("status.json"),
        blobs=load_json("blobs_counts.json"),
        history_since=parse_time(summary["history_since"]),
    )
    assert "<b>Cumulo</b>" in out
    assert "✅ Ready (4/4 regions)" in out
    assert "Signed: 90.13% (7,667 of 8,507)" in out
    assert "Below 100% is normal" in out
    assert "Retention: 100.00% (159 checks" in out
    assert "Not ready 0" in out
    assert "whole tracked history" in out
    assert "Typical RTT: 106 ms" in out
    assert "days left" in out
    assert_clean(out)


def test_validator_retention_needs_five_checks(metrics_text: str, ctx: ReportContext) -> None:
    text = metrics_text.replace(
        f'fibre_validator_obligation_checks{{chain="mocha-5",valcons="{CUMULO}",moniker="Cumulo",'
        'period="24h"} 41',
        f'fibre_validator_obligation_checks{{chain="mocha-5",valcons="{CUMULO}",moniker="Cumulo",'
        'period="24h"} 3',
    )
    out = render_validator(CUMULO, MetricsSnapshot.parse(text), Period.H24, ctx)
    assert "Retention: not enough data (3 checks)" in out


def test_validator_absent_series_are_na(metrics_text: str, ctx: ReportContext) -> None:
    keep = [
        line
        for line in metrics_text.splitlines()
        if "fibre_validator_info" in line
        or "fibre_validator_ready" in line
        or line.startswith("# TYPE fibre_validator_info")
        or not line.startswith("fibre_validator")
    ]
    out = render_validator(CUMULO, MetricsSnapshot.parse("\n".join(keep) + "\n"), Period.D7, ctx)
    assert "Uptime: n/a" in out
    assert "Signed: n/a" in out
    assert "Retention: not enough data (0 checks)" in out
    assert "Typical RTT: n/a" in out
    assert "Certificate: n/a" in out
    assert "Blobs (whole tracked history)</b>\nn/a" in out


def test_validator_not_ready_with_reason(metrics_text: str, ctx: ReportContext) -> None:
    text = metrics_text.replace(
        f'fibre_validator_ready{{chain="mocha-5",valcons="{CUMULO}",moniker="Cumulo"}} 1',
        f'fibre_validator_ready{{chain="mocha-5",valcons="{CUMULO}",moniker="Cumulo"}} 0',
    )
    out = render_validator(
        CUMULO,
        MetricsSnapshot.parse(text),
        Period.H24,
        ctx,
        status={"reason": "not_ready", "ready": False},
    )
    assert "❌ Not ready: host not ready" in out


def test_validator_unknown(metrics: MetricsSnapshot, ctx: ReportContext) -> None:
    out = render_validator(
        "celestiavalcons1unknownxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", metrics, Period.H24, ctx
    )
    assert "not in the bonded set" in out


def test_validator_cert_warning(metrics: MetricsSnapshot, ctx: ReportContext) -> None:
    expiry = metrics.validator(CUMULO).get("fibre_validator_cert_expiry_timestamp_seconds")
    assert expiry is not None
    near = ReportContext(now=NOW.fromtimestamp(expiry - 5 * 86400, tz=NOW.tzinfo))
    out = render_validator(CUMULO, metrics, Period.H24, near)
    assert "Certificate: ⚠️ 5 days left" in out


def test_status_line() -> None:
    line = render_status_line(load_json("status.json"))
    assert line.startswith("<b>Cumulo</b>: ✅ Ready (4/4 regions)")
    assert "typical RTT 111 ms" in line
    unknown = render_status_line({"reason": "monitor_unknown", "moniker": "X", "ready": False})
    assert "monitor has no result yet" in unknown
    assert "Not ready" not in unknown


# Market ----------------------------------------------------------------------------------


def test_market_report(ctx: ReportContext, summary: dict[str, Any]) -> None:
    out = render_market(
        Period.D7,
        ctx,
        stats=load_json("blobs_stats_7d.json"),
        publishers=load_json("publishers_7d.json"),
        series=load_json("activity_series_7d.json"),
        history_since=summary["history_since"],
    )
    assert "Blobs: 8,508" in out
    assert "Fees: 30,186.02 TIA" in out
    assert "Average price: 0.2204 TIA per MiB" in out
    assert "✅ Fee check: 8,508/8,508 blobs paid the expected fee" in out
    assert "4-16 MiB" in out and "99.6%" in out
    assert "262144" not in out  # no raw bucket bounds
    assert "celestia1las83…ee9snr" in out
    assert "celestia14a6ku" not in out  # publishers with 0 blobs in the period are hidden
    assert "Held: 20,921.17 TIA in 10 accounts" in out
    assert_clean(out)


def test_market_fee_mismatch_warning(ctx: ReportContext) -> None:
    stats = load_json("blobs_stats_7d.json")
    stats["fee_check"]["mismatches"] = [{"txhash": "AA"}]
    stats["fee_check"]["matching"] = 8507
    out = render_market(Period.D7, ctx, stats=stats, publishers=None)
    assert "⚠️ Fee check: 1 blob did not pay" in out


def test_market_empty_is_na(ctx: ReportContext) -> None:
    out = render_market(Period.H24, ctx, stats=None, publishers=None)
    assert "Blobs: n/a" in out
    assert "Fees: n/a" in out
    assert "Fee check: n/a" in out
    assert "Payment promises timed out: n/a" in out


def test_market_since_start(ctx: ReportContext, summary: dict[str, Any]) -> None:
    out = render_market(
        Period.ALL, ctx, stats=None, publishers=None, history_since=summary["history_since"]
    )
    assert "Period: <b>since start</b>" in out
    assert "Data since 2026-09-25" in out


def test_data_since_not_shown_when_history_covers(ctx: ReportContext) -> None:
    later = ReportContext(now=NOW + timedelta(days=40))
    out = render_market(
        Period.D30, later, stats=None, publishers=None, history_since="2026-09-25T15:00:00.000Z"
    )
    assert "Data since" not in out


def test_concentration_separates_countries_and_providers() -> None:
    import json
    from pathlib import Path

    from fibre_bot.reports.network import concentration_section

    s = json.loads(Path("fixtures/summary.json").read_text())
    s["readiness"]["ready_stake_pct"] = 68.6
    s["concentration"]["by_country"][0].update(label="France", quorum_survives_loss=False)
    s["concentration"]["by_provider"][0].update(label="host.example", quorum_survives_loss=False)
    out = concentration_section(s) or ""
    assert "just 1.93 points above 2/3" in out
    assert "<b>Countries</b>: France" in out
    assert "<b>Providers</b>: host.⁠example" in out  # no Telegram autolink
    assert "\u2014" not in out


def test_tiny_pct_and_plurals() -> None:
    from fibre_bot.reports.format import fmt_pct, fmt_size_range, plural

    assert fmt_pct(0.001) == "<0.01%"
    assert fmt_pct(0) == "0.00%"
    assert plural(1, "account") == "1 account"
    assert plural(10, "account") == "10 accounts"
    assert fmt_size_range(16 * 2**20, 64 * 2**20) == "16-64 MiB"


def test_sub_millisecond_rtt() -> None:
    from fibre_bot.reports.format import fmt_ms, fmt_seconds_as_ms

    assert fmt_seconds_as_ms(0.0002) == "<1 ms"
    assert fmt_ms(92.4) == "92 ms"


def test_registered_not_ready_tiny_stake() -> None:
    from fibre_bot.reports.network import threshold_section

    s = load_json("summary.json")
    r = s["readiness"]
    r["tiers"] = {"registered_not_ready": {"validators": 1, "stake_pct": 0.0}}
    r["registered_tokens"] = r["ready_tokens"] + 1000
    out = threshold_section(s, None)
    assert "Registered, not ready: 1 (&lt;0.01%)" in out
    assert "<0" not in out  # a raw "<" makes Telegram reject the whole message
