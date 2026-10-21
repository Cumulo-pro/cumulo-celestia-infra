from __future__ import annotations

from datetime import UTC, datetime

from fibre_bot.reports.format import (
    NA,
    data_since_line,
    dig,
    esc,
    fmt_bytes,
    fmt_int,
    fmt_pct,
    fmt_ratio_pct,
    fmt_seconds_as_ms,
    fmt_tia,
    short_addr,
    split_message,
)

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
SINCE = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)


def test_none_is_never_zero() -> None:
    for fn in (fmt_int, fmt_pct, fmt_ratio_pct, fmt_tia, fmt_bytes, fmt_seconds_as_ms):
        assert fn(None) == NA
    assert fmt_int(0) == "0"
    assert fmt_ratio_pct(0) == "0.00%"


def test_bools_are_not_numbers() -> None:
    assert fmt_int(True) == NA


def test_tia_and_bytes() -> None:
    assert fmt_tia(30_186_015_000) == "30,186.02 TIA"
    assert fmt_tia(695_000) == "0.6950 TIA"
    assert fmt_bytes(143_630_532_608) == "133.8 GiB"
    assert fmt_bytes(512) == "512 B"


def test_ratio_vs_pct_scales() -> None:
    assert fmt_ratio_pct(0.9747) == "97.47%"
    assert fmt_pct(97.47) == "97.47%"


def test_escape_and_short_addr() -> None:
    assert esc("<b>&") == "&lt;b&gt;&amp;"
    assert short_addr("celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx") == (
        "celestiavalcons14s…lz0pfx"
    )


def test_dig_handles_missing_and_null() -> None:
    data = {"a": {"b": None, "c": [1, 2]}}
    assert dig(data, "a", "b", default="x") == "x"
    assert dig(data, "a", "c", 1) == 2
    assert dig(data, "nope", "deeper") is None


def test_data_since_line() -> None:
    assert data_since_line(24, SINCE, NOW) is None
    assert data_since_line(720, SINCE, NOW) is not None
    assert "2026-09-25" in (data_since_line(720, SINCE, NOW) or "")
    assert data_since_line(None, SINCE, NOW) == "Data since 2026-09-25"
    assert data_since_line(720, None, NOW) is None


def test_split_message_respects_limit_and_paragraphs() -> None:
    para = "line\n" * 50
    text = "\n\n".join([para.strip()] * 30)
    chunks = split_message(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_split_message_short_is_single() -> None:
    assert split_message("hello") == ["hello"]
