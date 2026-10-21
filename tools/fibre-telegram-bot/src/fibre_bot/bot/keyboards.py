"""Inline keyboards and their callback data.

Callback data is limited to 64 bytes. Every callback carries the chain as a one character code
(`c`, see chains.py). The longest one is a validator report: "r:v:30d:" plus a 54 character
valcons plus ":m" = 64 bytes, exactly the limit (every valcons has the same length, and
test_keyboards checks it). An empty code means the primary chain, so buttons sent by older
versions keep working.
"""

from __future__ import annotations

from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fibre_bot.data.models import CardPeriod, Period, ReportKind, ValidatorRef

MAX_CANDIDATES = 8


class _AcceptOlder:
    """Buttons sent by v0.1 carry one field less (no chain code). Pad missing trailing fields
    with empty values so they still unpack ("" chain code = mocha-5) instead of being ignored."""

    @classmethod
    def unpack(cls, value: str) -> Any:
        sep: str = cls.__separator__  # type: ignore[attr-defined]
        expected = len(cls.model_fields)  # type: ignore[attr-defined]
        given = len(value.split(sep)) - 1
        if given < expected:
            value += sep * (expected - given)
        return super().unpack(value)  # type: ignore[misc]


class MenuCB(_AcceptOlder, CallbackData, prefix="mn"):
    target: str  # "root", "n", "m", "v" (validator picker), "vask" (ask for a name), "net"
    c: str = ""


class ChainCB(CallbackData, prefix="ch"):
    c: str


class ReportCB(_AcceptOlder, CallbackData, prefix="r"):
    kind: str
    period: str
    valcons: str = ""
    c: str = ""


class PickCB(_AcceptOlder, CallbackData, prefix="pk"):
    valcons: str
    c: str = ""


class CardMenuCB(_AcceptOlder, CallbackData, prefix="cm"):
    valcons: str = ""
    c: str = ""


class CardCB(_AcceptOlder, CallbackData, prefix="cd"):
    period: str
    valcons: str = ""
    c: str = ""


class DetailCB(_AcceptOlder, CallbackData, prefix="dt"):
    period: str
    valcons: str = ""
    c: str = ""


def chain_menu(chains: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """chains: (code, label) pairs, primary first."""
    kb = InlineKeyboardBuilder()
    for code, label in chains:
        kb.button(text=label, callback_data=ChainCB(c=code))
    kb.adjust(len(chains))
    return kb.as_markup()


def main_menu(c: str = "", others: list[tuple[str, str]] | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🛡 Network", callback_data=MenuCB(target=ReportKind.NETWORK.value, c=c))
    kb.button(text="⚡ A validator", callback_data=MenuCB(target=ReportKind.VALIDATOR.value, c=c))
    kb.button(text="📦 Market", callback_data=MenuCB(target=ReportKind.MARKET.value, c=c))
    kb.button(text="🖼 Report card (image)", callback_data=CardMenuCB(c=c))
    rows = [3, 1]
    for code, label in others or []:
        kb.button(text=f"🔁 Switch to {label}", callback_data=ChainCB(c=code))
        rows.append(1)
    kb.adjust(*rows)
    return kb.as_markup()


def card_period_menu(valcons: str = "", c: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in CardPeriod:
        kb.button(text=p.label, callback_data=CardCB(period=p.value, valcons=valcons, c=c))
    back = PickCB(valcons=valcons, c=c) if valcons else MenuCB(target="root", c=c)
    kb.button(text="« Back", callback_data=back)
    kb.adjust(2, 2, 1, 1)
    return kb.as_markup()


def card_actions(period: CardPeriod, valcons: str = "", c: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Refresh", callback_data=CardCB(period=period.value, valcons=valcons, c=c))
    kb.button(text="📋 Details", callback_data=DetailCB(period=period.value, valcons=valcons, c=c))
    kb.button(text="« Back", callback_data=CardMenuCB(valcons=valcons, c=c))
    kb.adjust(2, 1)
    return kb.as_markup()


def period_menu(kind: ReportKind, valcons: str = "", c: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    periods = [Period.H24, Period.D7, Period.D30]
    if kind is not ReportKind.VALIDATOR:
        periods.append(Period.ALL)
    for p in periods:
        text = "Since start" if p is Period.ALL else p.value
        kb.button(
            text=text,
            callback_data=ReportCB(kind=kind.value, period=p.value, valcons=valcons, c=c),
        )
    rows = [len(periods)]
    if kind is ReportKind.VALIDATOR:
        kb.button(text="🖼 Report card (image)", callback_data=CardMenuCB(valcons=valcons, c=c))
        rows.append(1)
    back = MenuCB(
        target=ReportKind.VALIDATOR.value if kind is ReportKind.VALIDATOR else "root", c=c
    )
    kb.button(text="« Back", callback_data=back)
    kb.adjust(*rows, 1)
    return kb.as_markup()


def report_actions(
    kind: ReportKind, period: Period, valcons: str = "", c: str = ""
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data=ReportCB(
                        kind=kind.value, period=period.value, valcons=valcons, c=c
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="« Back",
                    callback_data=(
                        PickCB(valcons=valcons, c=c).pack()
                        if kind is ReportKind.VALIDATOR
                        else MenuCB(target=kind.value, c=c).pack()
                    ),
                ),
            ]
        ]
    )


def validator_menu(last: ValidatorRef | None, c: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if last is not None:
        kb.button(text=f"Use {last.moniker[:30]}", callback_data=PickCB(valcons=last.valcons, c=c))
    kb.button(text="Type a moniker or valcons", callback_data=MenuCB(target="vask", c=c))
    kb.button(text="« Back", callback_data=MenuCB(target="root", c=c))
    kb.adjust(1)
    return kb.as_markup()


def candidates_menu(candidates: list[ValidatorRef], c: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cand in candidates[:MAX_CANDIDATES]:
        kb.button(
            text=cand.moniker[:40] or cand.valcons[:20],
            callback_data=PickCB(valcons=cand.valcons, c=c),
        )
    kb.button(text="« Back", callback_data=MenuCB(target="root", c=c))
    kb.adjust(1)
    return kb.as_markup()
