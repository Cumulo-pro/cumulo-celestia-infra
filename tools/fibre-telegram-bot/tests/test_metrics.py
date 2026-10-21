from __future__ import annotations

from fibre_bot.api.metrics import MetricsSnapshot
from tests.conftest import CUMULO


def test_network_values(metrics: MetricsSnapshot) -> None:
    assert metrics.get("fibre_ready_meets_threshold") == 1
    assert metrics.get("fibre_validators_needed_to_threshold") == 0
    assert metrics.generated_at == 1791105286
    assert metrics.probes_up == 4
    assert metrics.by_label("fibre_quorum_rtt_seconds", "region")["AS"] == 0.204


def test_validator_view(metrics: MetricsSnapshot) -> None:
    v = metrics.validator(CUMULO)
    assert v.exists
    assert v.moniker == "Cumulo"
    assert v.get("fibre_validator_signed_ratio", "7d") == 0.9012577877
    assert v.get("fibre_validator_obligation_checks", "30d") == 171
    assert v.by_region("fibre_validator_rtt_seconds")["EU"] == 0.106


def test_absent_series_is_none_not_zero(metrics: MetricsSnapshot) -> None:
    v = metrics.validator(CUMULO)
    assert v.get("fibre_validator_deleted_after_window_ratio", "24h") is None
    assert metrics.get("fibre_metric_that_does_not_exist") is None
    assert metrics.probes_up is not None
    assert MetricsSnapshot.parse("").probes_up is None


def test_unknown_validator(metrics: MetricsSnapshot) -> None:
    v = metrics.validator("celestiavalcons1nothere")
    assert not v.exists
    assert v.get("fibre_validator_ready") is None


def test_find_valcons(metrics: MetricsSnapshot) -> None:
    assert metrics.find_valcons(CUMULO) == CUMULO
    assert metrics.find_valcons("cumulo") == CUMULO
    assert metrics.find_valcons("celestiavaloper16yv2p64nzps0lqcealnumrsqc2rcszkd0uzlp8") == CUMULO
    assert metrics.find_valcons("nobody") is None


def test_nan_is_dropped() -> None:
    snap = MetricsSnapshot.parse('# TYPE x gauge\nx{chain="mocha-5"} NaN\n')
    assert snap.get("x") is None
