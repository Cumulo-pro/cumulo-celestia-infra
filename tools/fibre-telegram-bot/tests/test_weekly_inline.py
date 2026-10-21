from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import respx
from aiogram.methods import AnswerInlineQuery, DeleteMessage, SendMessage, SendPhoto
from aiogram.types import (
    Chat,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    Message,
    Update,
    User,
)

from fibre_bot.bot.inline import build_inline_router
from fibre_bot.bot.publish import CardPublisher
from fibre_bot.scheduler import StateFile, WeeklyReport
from tests.conftest import load_json, make_registry
from tests.test_bot_flow import API, USER, mock_api, setup, text_msg

# Monday 5 Oct 2026, 10:00 in Madrid: week 40 has just closed.
MONDAY = dt.datetime(2026, 10, 5, 8, 0, tzinfo=dt.UTC)
CHANNEL = -1001234567890
ADMIN = USER.id


def build(
    tmp_path: Path,
    *,
    storage: int | None = 555,
    clock: dt.datetime = MONDAY,
    chains: tuple[tuple[str, str], ...] = (("mocha-5", "testnet"),),
) -> Any:
    # Monday clock so "last week" is week 40, and data that is not stale at that time.
    registry = make_registry(wall=clock.timestamp(), now=clock, chains=chains)
    dp, bot, session, memory = setup(registry)
    publisher = CardPublisher(bot, registry, memory, storage, delete_uploads=True)
    weekly = WeeklyReport(
        publisher, CHANNEL, StateFile(str(tmp_path / "state.json")), clock=lambda: clock
    )
    dp.include_router(build_inline_router(publisher, weekly, [ADMIN]))
    return dp, bot, session, publisher, weekly


def inline_update(query: str, update_id: int = 50) -> Update:
    return Update(
        update_id=update_id,
        inline_query=InlineQuery(id=str(update_id), from_user=USER, query=query, offset=""),
    )


def mock_report() -> Any:
    return respx.get(f"{API}/report").respond(json=load_json("report_2026-W40_cumulo.json"))


# Weekly --------------------------------------------------------------------------------


def test_schedule_math(tmp_path: Path) -> None:
    _, _, _, _, weekly = build(tmp_path)
    assert weekly.due_week_key() == "2026-W40"
    # Monday 09:00 Madrid is 07:00 UTC in October (CEST).
    assert weekly.last_scheduled_run() == dt.datetime(2026, 10, 5, 7, 0, tzinfo=dt.UTC)
    _, _, _, _, early = build(tmp_path, clock=dt.datetime(2026, 10, 5, 6, 0, tzinfo=dt.UTC))
    assert early.last_scheduled_run() == dt.datetime(2026, 9, 28, 7, 0, tzinfo=dt.UTC)
    with pytest.raises(ValueError):
        WeeklyReport(weekly.publisher, None, weekly.state, day="monday")


@respx.mock
async def test_weekly_posts_once(tmp_path: Path) -> None:
    mock_api()
    mock_report()
    _, _, session, _, weekly = build(tmp_path)
    assert await weekly.post() == "posted week 2026-W40"
    assert await weekly.post() == "week 2026-W40 already posted"
    photos = [c for c in session.calls if isinstance(c, SendPhoto)]
    assert [p.chat_id for p in photos] == [CHANNEL]
    assert photos[0].caption and "Week 40, 2026" in photos[0].caption
    buttons = photos[0].reply_markup.inline_keyboard[0]  # type: ignore[union-attr]
    assert buttons[0].url and "period=2026-W40" in buttons[0].url
    assert buttons[1].url == "https://t.me/CumuloFibreBot"
    assert weekly.state.load()["weeks"] == {"mocha-5": "2026-W40"}


@respx.mock
async def test_weekly_skips_open_week_and_preview_does_not_count(tmp_path: Path) -> None:
    mock_api()
    report = load_json("report_2026-W40_cumulo.json")
    report["period"]["status"] = "in_progress"
    respx.get(f"{API}/report").respond(json=report)
    _, _, _session, _, weekly = build(tmp_path)
    assert "not closed yet" in await weekly.post()
    assert await weekly.post(ADMIN, force=True) == "posted week 2026-W40"
    assert weekly.state.load() == {}


@respx.mock
async def test_catch_up_only_after_a_first_post(tmp_path: Path) -> None:
    mock_api()
    mock_report()
    _, _, _session, _, weekly = build(tmp_path)
    assert await weekly.catch_up() is None  # first install: never post by surprise
    weekly.state.save({"last_week": "2026-W39"})  # v0.1 state format keeps working
    assert await weekly.catch_up() == "posted week 2026-W40"
    assert await weekly.catch_up() is None


@respx.mock
async def test_weekly_command_admin_only(tmp_path: Path) -> None:
    mock_api()
    mock_report()
    dp, bot, session, _, _ = build(tmp_path)
    stranger = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=dt.datetime.now(dt.UTC),
            chat=Chat(id=999, type="private"),
            from_user=User(id=999, is_bot=False, first_name="X"),
            text="/weekly",
        ),
    )
    await dp.feed_update(bot, stranger)
    assert "for the bot admins" in [c for c in session.calls if isinstance(c, SendMessage)][-1].text
    await dp.feed_update(bot, text_msg("/weekly", 2))
    assert "Last posted: never" in [c for c in session.calls if isinstance(c, SendMessage)][-1].text
    await dp.feed_update(bot, text_msg("/weekly preview", 3))
    assert any(isinstance(c, SendPhoto) and c.chat_id == 42 for c in session.calls)


# Inline --------------------------------------------------------------------------------


@respx.mock
async def test_inline_network_cards_uploaded_once(tmp_path: Path) -> None:
    mock_api()
    mock_report()
    dp, bot, session, _publisher, _ = build(tmp_path)
    await dp.feed_update(bot, inline_update(""))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert all(isinstance(r, InlineQueryResultCachedPhoto) for r in answer.results)
    assert answer.results and answer.cache_time == 300
    uploads = sum(isinstance(c, SendPhoto) and c.chat_id == 555 for c in session.calls)
    deletes = sum(isinstance(c, DeleteMessage) for c in session.calls)
    assert uploads == deletes >= 1  # storage uploads are removed right away

    await dp.feed_update(bot, inline_update("", 51))
    again = sum(isinstance(c, SendPhoto) and c.chat_id == 555 for c in session.calls)
    assert again == uploads  # second query reuses the file_ids


@respx.mock
async def test_inline_validator_search_and_unknown(tmp_path: Path) -> None:
    mock_api()
    route = mock_report()
    dp, bot, session, _, _ = build(tmp_path)
    await dp.feed_update(bot, inline_update("cumu"))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert len(answer.results) == 3
    assert route.calls.last.request.url.params["validator"].startswith("celestiavalcons14s4")

    await dp.feed_update(bot, inline_update("nobody-like-this", 52))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert answer.results == []


@respx.mock
async def test_inline_without_storage_shares_text(tmp_path: Path) -> None:
    mock_api()
    mock_report()
    dp, bot, session, _, _ = build(tmp_path, storage=None)
    await dp.feed_update(bot, inline_update(""))
    answer = [c for c in session.calls if isinstance(c, AnswerInlineQuery)][-1]
    assert answer.results and all(isinstance(r, InlineQueryResultArticle) for r in answer.results)
    assert not any(isinstance(c, SendPhoto) for c in session.calls)
