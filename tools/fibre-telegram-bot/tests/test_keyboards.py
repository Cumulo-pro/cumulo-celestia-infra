from __future__ import annotations

from fibre_bot.bot.handlers import ChatMemory, build_router
from fibre_bot.bot.keyboards import (
    PickCB,
    ReportCB,
    candidates_menu,
    card_actions,
    card_period_menu,
    main_menu,
    period_menu,
    report_actions,
)
from fibre_bot.data.models import CardPeriod, Period, ReportKind, ValidatorRef
from tests.conftest import CUMULO


def all_callback_data(markup: object) -> list[str]:
    rows = markup.inline_keyboard  # type: ignore[attr-defined]
    return [b.callback_data for row in rows for b in row if b.callback_data]


def test_callback_data_fits_telegram_limit() -> None:
    markups = [
        main_menu(),
        period_menu(ReportKind.VALIDATOR, CUMULO),
        period_menu(ReportKind.NETWORK),
        report_actions(ReportKind.VALIDATOR, Period.D30, CUMULO),
        candidates_menu([ValidatorRef(CUMULO, "Cumulo")] * 10),
        # With the chain code every multi-chain button carries (worst case: longest period).
        period_menu(ReportKind.VALIDATOR, CUMULO, c="m"),
        report_actions(ReportKind.VALIDATOR, Period.D30, CUMULO, c="m"),
        card_period_menu(CUMULO, c="m"),
        card_actions(CardPeriod.LAST_MONTH, CUMULO, c="m"),
        candidates_menu([ValidatorRef(CUMULO, "Cumulo")], c="m"),
    ]
    for m in markups:
        for data in all_callback_data(m):
            assert len(data.encode()) <= 64, data


def test_validator_has_no_since_start_but_network_does() -> None:
    val = all_callback_data(period_menu(ReportKind.VALIDATOR, CUMULO))
    net = all_callback_data(period_menu(ReportKind.NETWORK))
    assert not any(":all" in d for d in val)
    assert any(":all" in d for d in net)


def test_callback_roundtrip() -> None:
    packed = ReportCB(kind="v", period="7d", valcons=CUMULO).pack()
    cb = ReportCB.unpack(packed)
    assert cb.valcons == CUMULO and cb.period == "7d"
    assert PickCB.unpack(PickCB(valcons=CUMULO).pack()).valcons == CUMULO


def test_candidates_are_capped() -> None:
    refs = [ValidatorRef(f"celestiavalcons1x{i}", f"n{i}") for i in range(20)]
    picks = [d for d in all_callback_data(candidates_menu(refs)) if d.startswith("pk:")]
    assert len(picks) == 8


def test_router_builds() -> None:
    from tests.conftest import make_registry

    router = build_router(make_registry(), ChatMemory())
    assert router.name == "reports"
