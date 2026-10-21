"""Mocha-5 plus Celestia mainnet ("celestia" in the API).

Mainnet is pending until /v1/chains says fibre_active true (expected with the v10 upgrade at
height 15116658). Mainnet data does not exist yet, so these tests reuse the mocha fixtures under
the mainnet path: they check the wiring (which chain is called, labels, links, activation), not
real mainnet figures.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import respx
from aiogram.methods import AnswerInlineQuery, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, InlineQuery, InlineQueryResultArticle, Message, Update

from fibre_bot import USER_AGENT, __version__
from fibre_bot.api.client import FibreNotActiveError
from fibre_bot.bot.keyboards import CardCB, ChainCB, ReportCB
from fibre_bot.bot.publish import CardPublisher
from fibre_bot.chains import ChainRegistry, parse_mapping
from fibre_bot.reports.card import card_svg
from fibre_bot.scheduler import StateFile, WeeklyReport
from tests.conftest import (
    API_BASE,
    CUMULO,
    MAINNET_PAGE,
    MOCHA_REPORT,
    load_json,
    load_text,
    make_registry,
)
from tests.test_bot_flow import CHAT, USER, callback, last_text, setup, text_msg

MAINNET = ("celestia", "mainnet")
MOCHA = ("mocha-5", "testnet")
PENDING_MAINNET = ("celestia", "mainnet", False)
MONDAY = dt.datetime(2026, 10, 5, 8, 0, tzinfo=dt.UTC)

NOT_ACTIVE = {
    "error": "fibre_not_active",
    "chain": "celestia",
    "network": "mainnet",
    "app_version": "9.0.8",
    "activation": {
        "height": 15116658,
        "current_height": 15104658,
        "blocks_left": 12000,
        "block_time_s": 6,
        "eta": "2026-10-20T03:44:30Z",
    },
    "message": "Fibre is not active on this chain yet",
}


def chains_listing(mainnet_active: bool) -> dict[str, Any]:
    return {
        "chains": [
            {
                "key": "mocha-5",
                "chain_id": "mocha-5",
                "network": "testnet",
                "available": True,
                "fibre_active": True,
                "app_version": "10.4.0-mocha",
            },
            {
                "key": "celestia",
                "chain_id": "celestia",
                "network": "mainnet",
                "available": mainnet_active,
                "fibre_active": mainnet_active,
                "app_version": "10.4.0" if mainnet_active else "9.0.8",
            },
        ]
    }


def mock_chain(key: str) -> dict[str, Any]:
    api = f"{API_BASE}/v1/{key}"
    return {
        "summary": respx.get(f"{api}/summary").respond(json=load_json("summary.json")),
        # Validator-specific metrics first: a route without params matches any query string.
        "vmetrics": respx.get(f"{api}/metrics", params={"validator": CUMULO}).respond(
            text=load_text("metrics_validator.txt")
        ),
        "metrics": respx.get(f"{api}/metrics").respond(text=load_text("metrics_validator.txt")),
        "timeseries": respx.get(f"{api}/timeseries").respond(json=load_json("timeseries_7d.json")),
        "status": respx.get(f"{api}/validator/{CUMULO}/status").respond(
            json=load_json("status.json")
        ),
        "blobs": respx.get(f"{api}/validator/{CUMULO}/blobs").respond(
            json=load_json("blobs_counts.json")
        ),
        "report": respx.get(f"{api}/report").respond(json=load_json("report_2026-W40_cumulo.json")),
    }


def mock_pending_mainnet() -> Any:
    return respx.get(url__startswith=f"{API_BASE}/v1/celestia/").respond(
        503, json=NOT_ACTIVE, headers={"Retry-After": "600"}
    )


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# The 503 fibre_not_active answer ------------------------------------------------------


@respx.mock
async def test_not_active_503_is_not_retried_and_keeps_activation_info() -> None:
    route = mock_pending_mainnet()
    registry = make_registry(chains=(MOCHA, PENDING_MAINNET))
    client = registry.known()[0].store.client
    assert client.chain == "celestia"
    with pytest.raises(FibreNotActiveError) as info:
        await client.summary()
    assert route.call_count == 1  # no retries, unlike a "warming up" 503
    assert info.value.retry_after == 600
    assert info.value.activation["blocks_left"] == 12000
    assert info.value.app_version == "9.0.8"


# Discovery and activation ---------------------------------------------------------------


@respx.mock
async def test_pending_mainnet_then_activation_without_restart() -> None:
    listing = respx.get(f"{API_BASE}/v1/chains").respond(json=chains_listing(False))
    probe = mock_pending_mainnet()
    clock = FakeClock()
    registry = make_registry(chains=(), monotonic=clock)

    await registry.discover()
    assert [c.key for c in registry.all()] == ["mocha-5"]  # only active chains have data
    assert registry.primary.key == "mocha-5"  # default (mainnet) not active yet
    mainnet = registry.by_alias("mainnet")
    assert mainnet is not None and not mainnet.active and mainnet.code == "m"
    assert probe.call_count == 1
    assert mainnet.not_active_text() == (
        "Fibre is not active on Mainnet yet: 12,000 blocks left, "
        "about 2026-10-20 03:44 UTC, an estimate."
    )

    clock.t += 300  # inside Retry-After: the pending chain is not asked again
    await registry.discover()
    assert probe.call_count == 1
    clock.t += 301
    await registry.discover()
    assert probe.call_count == 2

    listing.respond(json=chains_listing(True))  # height 15116658 reached
    added = await registry.discover()
    assert [c.key for c in added] == ["celestia"]
    assert registry.primary.key == "celestia"  # mainnet becomes the default by itself
    assert registry.get("t").key == "mocha-5"  # codes never change
    assert probe.call_count == 2  # an active chain is not probed


@respx.mock
async def test_discovery_falls_back_to_mocha_when_the_list_fails() -> None:
    respx.get(f"{API_BASE}/v1/chains").respond(500, json={"error": "x"})
    registry = make_registry(chains=())
    await registry.discover()
    assert [c.key for c in registry.all()] == ["mocha-5"]


@respx.mock
async def test_filter_only_some_chains() -> None:
    respx.get(f"{API_BASE}/v1/chains").respond(json=chains_listing(True))
    registry = make_registry(chains=())
    registry.only = ["mocha-5"]
    await registry.discover()
    assert [c.key for c in registry.known()] == ["mocha-5"]


def test_parse_mapping() -> None:
    assert parse_mapping("mocha-5=https://a, celestia=https://b,bad,=x") == {
        "mocha-5": "https://a",
        "celestia": "https://b",
    }


@respx.mock
async def test_user_agent_is_the_versioned_one() -> None:
    route = respx.get(f"{API_BASE}/v1/chains").respond(json=chains_listing(False))
    mock_pending_mainnet()
    registry = ChainRegistry(API_BASE)  # real client factory and shared HTTP client
    try:
        await registry.discover()
    finally:
        await registry.stop()
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert f"fibre-telegram-bot/{__version__} (+https://cumulo.pro)" == USER_AGENT


# Data layer: less to download ---------------------------------------------------------


@respx.mock
async def test_polls_summary_and_full_metrics_only_every_30_minutes() -> None:
    routes = mock_chain("mocha-5")
    wall = [1791105300.0]
    registry = make_registry(chains=(MOCHA,))
    store = registry.all()[0].store
    store._wall = lambda: wall[0]
    await store.refresh()
    assert routes["summary"].call_count == 1 and routes["metrics"].call_count == 1
    wall[0] += 60
    await store.refresh()
    wall[0] += 60
    await store.refresh()
    assert routes["summary"].call_count == 3
    assert routes["metrics"].call_count == 1  # the directory is kept
    wall[0] += 1800
    await store.refresh()
    assert routes["metrics"].call_count == 2


@respx.mock
async def test_network_from_summary_and_validator_from_its_own_metrics() -> None:
    routes = mock_chain("mocha-5")
    registry = make_registry(chains=(MOCHA,))
    service = registry.all()[0].service
    from fibre_bot.data.models import Period

    text = await service.network(Period.H24)
    assert "Ready stake meets 2/3" in text and "· Mocha testnet" in text
    assert not routes["vmetrics"].called

    text = await service.validator(CUMULO, Period.D7)
    assert routes["vmetrics"].call_count == 1  # only this validator's series
    assert "Cumulo</b> · Mocha testnet" in text
    assert f"{MOCHA_REPORT}?validator=Cumulo" in text
    await service.validator(CUMULO, Period.H24)
    assert routes["vmetrics"].call_count == 1  # cached for a minute


# Bot flow --------------------------------------------------------------------------------


@respx.mock
async def test_mocha_only_shows_the_network_and_nothing_else_changes() -> None:
    mock_chain("mocha-5")
    dp, bot, session, _ = setup()
    await dp.feed_update(bot, text_msg("/fibre"))
    text = last_text(session)
    assert text.startswith("<b>Fibre reports</b> · Mocha testnet")
    assert "Switch to" not in str([c for c in session.calls if isinstance(c, SendMessage)][-1])


@respx.mock
async def test_mainnet_is_the_default_with_a_switch_button() -> None:
    mocha, main = mock_chain("mocha-5"), mock_chain("celestia")
    dp, bot, session, memory = setup(make_registry(chains=(MOCHA, MAINNET)))

    await dp.feed_update(bot, text_msg("/fibre"))
    sent = [c for c in session.calls if isinstance(c, SendMessage)][-1]
    assert sent.text.startswith("<b>Fibre reports</b> · Mainnet")
    labels = [b.text for row in sent.reply_markup.inline_keyboard for b in row]  # type: ignore[union-attr]
    assert "🔁 Switch to Mocha testnet" in labels

    await dp.feed_update(bot, callback(ReportCB(kind="n", period="24h", c="m").pack(), 3))
    assert main["summary"].called and not mocha["summary"].called
    text = last_text(session)
    assert "· Mainnet" in text and MAINNET_PAGE in text

    await dp.feed_update(bot, callback(ChainCB(c="t").pack(), 4))
    assert last_text(session).startswith("<b>Fibre reports</b> · Mocha testnet")
    assert memory.chain[42] == "t"
    await dp.feed_update(bot, text_msg("/fibre", 5))  # remembers the choice
    assert last_text(session).startswith("<b>Fibre reports</b> · Mocha testnet")


@respx.mock
async def test_buttons_sent_by_v01_still_work_and_go_to_mocha() -> None:
    mocha, main = mock_chain("mocha-5"), mock_chain("celestia")
    dp, bot, session, _ = setup(make_registry(chains=(MOCHA, MAINNET)))
    # Exactly what v0.1 put in its buttons: no chain field at all.
    msg = Message(message_id=100, date=dt.datetime.now(dt.UTC), chat=CHAT, text="menu")
    old = Update(
        update_id=7,
        callback_query=CallbackQuery(
            id="7", from_user=USER, chat_instance="x", message=msg, data="r:n:24h:"
        ),
    )
    await dp.feed_update(bot, old)
    assert mocha["summary"].called and not main["summary"].called
    assert "· Mocha testnet" in last_text(session)


@respx.mock
async def test_pending_mainnet_answers_with_the_countdown() -> None:
    mock_chain("mocha-5")
    probe = mock_pending_mainnet()
    respx.get(f"{API_BASE}/v1/chains").respond(json=chains_listing(False))
    registry = make_registry(chains=())
    await registry.discover()
    dp, bot, session, _ = setup(registry)
    calls_before = probe.call_count

    for i, cmd in enumerate(("/fibre mainnet", "/status mainnet cumulo", "/card mainnet")):
        await dp.feed_update(bot, text_msg(cmd, 10 + i))
        assert last_text(session) == (
            "Fibre is not active on Mainnet yet: 12,000 blocks left, "
            "about 2026-10-20 03:44 UTC, an estimate."
        )
    assert probe.call_count == calls_before  # answered from memory, no API call

    await dp.feed_update(bot, text_msg("/fibre", 20))  # default falls back to mocha meanwhile
    assert last_text(session).startswith("<b>Fibre reports</b> · Mocha testnet")
    assert "Not active" not in last_text(session)


@respx.mock
async def test_status_always_names_the_network() -> None:
    mock_chain("mocha-5")
    respx.get(f"{API_BASE}/v1/mocha-5/validator/cumulo/status").respond(
        json=load_json("status.json")
    )
    dp, bot, session, _ = setup()
    await dp.feed_update(bot, text_msg("/status cumulo"))
    assert last_text(session).startswith("[Mocha testnet] <b>Cumulo</b>")


@respx.mock
async def test_mainnet_card_label_and_links() -> None:
    mock_chain("celestia")
    mock_chain("mocha-5")
    dp, bot, session, _ = setup(make_registry(chains=(MOCHA, MAINNET)))

    await dp.feed_update(bot, callback(CardCB(period="pw", c="m").pack()))
    photo = [c for c in session.calls if isinstance(c, SendPhoto)][-1]
    assert photo.caption and photo.caption.startswith("<b>Celestia Fibre on mainnet")
    # Mainnet report tab not confirmed: link to the general page, not the API's report URL.
    assert f'href="{MAINNET_PAGE}"' in photo.caption
    assert "fibre-report" not in photo.caption

    await dp.feed_update(bot, callback(CardCB(period="pw", c="t").pack(), 3))
    photo = [c for c in session.calls if isinstance(c, SendPhoto)][-1]
    assert photo.caption and "Celestia Fibre on mocha-5" in photo.caption
    assert "celestia_mocha/fibre-report?period=2026-W40" in photo.caption

    assert "Celestia Fibre on mainnet" in card_svg(
        load_json("report_2026-W40_cumulo.json"), "mainnet"
    )


@respx.mock
async def test_no_links_for_a_chain_without_pages() -> None:
    mock_chain("celestia")
    registry = make_registry(chains=(MAINNET,), pages={}, report_pages={})
    from fibre_bot.data.models import Period

    text = await registry.primary.service.network(Period.H24)
    assert "<a href" not in text and "Powered by Cumulo Fibre Monitor" in text


@respx.mock
async def test_default_validator_per_chain() -> None:
    for key in ("mocha-5", "celestia"):
        mock_chain(key)
    registry = make_registry(chains=(MOCHA, MAINNET))
    for chain in registry.all():
        await chain.store.ensure_loaded()
    assert registry.default_validator(registry.get("t")).valcons == CUMULO  # configured
    assert registry.default_validator(registry.primary).moniker == "Cumulo"  # by moniker


# Inline and weekly ---------------------------------------------------------------------


def build_publisher(tmp_path: Path, registry: ChainRegistry) -> Any:
    from fibre_bot.bot.inline import build_inline_router

    dp, bot, session, memory = setup(registry)
    publisher = CardPublisher(bot, registry, memory, 555, delete_uploads=True)
    weekly = WeeklyReport(
        publisher, -100777, StateFile(str(tmp_path / "state.json")), clock=lambda: MONDAY
    )
    dp.include_router(build_inline_router(publisher, weekly, [USER.id]))
    return dp, bot, session, weekly


def inline(text: str, uid: int) -> Update:
    return Update(
        update_id=uid,
        inline_query=InlineQuery(id=str(uid), from_user=USER, query=text, offset=""),
    )


@respx.mock
async def test_inline_defaults_to_mainnet_and_takes_a_network_word(tmp_path: Path) -> None:
    mocha, main = mock_chain("mocha-5"), mock_chain("celestia")
    registry = make_registry(chains=(MOCHA, MAINNET), wall=MONDAY.timestamp(), now=MONDAY)
    dp, bot, session, _ = build_publisher(tmp_path, registry)

    await dp.feed_update(bot, inline("", 1))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert answer.results and all(r.title.startswith("Mainnet · ") for r in answer.results)
    assert main["report"].called and not mocha["report"].called

    await dp.feed_update(bot, inline("mocha cumu", 2))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert len(answer.results) == 3
    assert all(r.title.startswith("Mocha testnet · Cumulo") for r in answer.results)


@respx.mock
async def test_inline_pending_mainnet(tmp_path: Path) -> None:
    mock_chain("mocha-5")
    mock_pending_mainnet()
    respx.get(f"{API_BASE}/v1/chains").respond(json=chains_listing(False))
    registry = make_registry(chains=(), wall=MONDAY.timestamp(), now=MONDAY)
    await registry.discover()
    dp, bot, session, _ = build_publisher(tmp_path, registry)
    await dp.feed_update(bot, inline("mainnet cumulo", 1))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert len(answer.results) == 1 and isinstance(answer.results[0], InlineQueryResultArticle)
    assert "12,000 blocks left" in (answer.results[0].description or "")


@respx.mock
async def test_weekly_posts_active_chains_and_reads_old_state(tmp_path: Path) -> None:
    mock_chain("mocha-5")
    mock_chain("celestia")
    registry = make_registry(chains=(MOCHA, MAINNET), wall=MONDAY.timestamp(), now=MONDAY)
    _, _, session, weekly = build_publisher(tmp_path, registry)
    weekly.state.save({"last_week": "2026-W40"})  # v0.1 format: mocha already posted
    status = await weekly.post()
    assert status == "Mainnet: posted week 2026-W40; Mocha testnet: week 2026-W40 already posted"
    assert sum(isinstance(c, SendPhoto) and c.chat_id == -100777 for c in session.calls) == 1
    assert weekly.state.load()["weeks"] == {"mocha-5": "2026-W40", "celestia": "2026-W40"}
