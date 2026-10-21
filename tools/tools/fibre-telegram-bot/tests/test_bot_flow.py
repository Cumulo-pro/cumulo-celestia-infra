"""Drive the real router through aiogram with a fake Telegram session (no network)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator
from typing import Any

import respx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageMedia,
    EditMessageText,
    GetMe,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from fibre_bot.bot.handlers import ChatMemory, build_router
from fibre_bot.bot.keyboards import CardCB, CardMenuCB, DetailCB, MenuCB, PickCB, ReportCB
from tests.conftest import CUMULO, load_json, load_text, make_registry

API = "https://fibre-api.test/v1/mocha-5"
CHAT = Chat(id=42, type="private")
USER = User(id=7, is_bot=False, first_name="Mon")


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            return Message(
                message_id=100, date=dt.datetime.now(dt.UTC), chat=CHAT, text=method.text
            )
        if isinstance(method, SendPhoto | EditMessageMedia):
            return photo_message()
        if isinstance(method, GetMe):
            return User(id=123456789, is_bot=True, first_name="Fibre", username="CumuloFibreBot")
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""


def photo_message() -> Message:
    return Message(
        message_id=101,
        date=dt.datetime.now(dt.UTC),
        chat=CHAT,
        caption="card",
        photo=[PhotoSize(file_id="f", file_unique_id="u", width=1200, height=630)],
    )


def setup(registry: Any = None) -> tuple[Dispatcher, Bot, FakeSession, ChatMemory]:
    registry = registry or make_registry()
    memory = ChatMemory()
    dp = Dispatcher()
    dp.include_router(build_router(registry, memory))
    session = FakeSession()
    bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return dp, bot, session, memory


def text_msg(text: str, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id, date=dt.datetime.now(dt.UTC), chat=CHAT, from_user=USER, text=text
        ),
    )


def callback(data: str, update_id: int = 2) -> Update:
    msg = Message(message_id=100, date=dt.datetime.now(dt.UTC), chat=CHAT, text="menu")
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id), from_user=USER, chat_instance="x", message=msg, data=data
        ),
    )


def mock_api() -> None:
    respx.get(f"{API}/summary").respond(json=load_json("summary.json"))
    respx.get(f"{API}/metrics").respond(text=load_text("metrics_validator.txt"))
    respx.get(f"{API}/timeseries").respond(json=load_json("timeseries_7d.json"))
    respx.get(f"{API}/validator/{CUMULO}/status").respond(json=load_json("status.json"))
    respx.get(f"{API}/validator/{CUMULO}/blobs").respond(json=load_json("blobs_counts.json"))
    respx.get(f"{API}/blobs/stats").respond(json=load_json("blobs_stats_7d.json"))
    respx.get(f"{API}/publishers").respond(json=load_json("publishers_7d.json"))
    respx.get(f"{API}/activity/series").respond(json=load_json("activity_series_7d.json"))
    respx.get(f"{API}/search").respond(json=load_json("search.json"))


def last_text(session: FakeSession) -> str:
    for call in reversed(session.calls):
        if isinstance(call, SendMessage | EditMessageText):
            return call.text
    raise AssertionError("no message sent")


@respx.mock
async def test_full_button_paths() -> None:
    mock_api()
    dp, bot, session, memory = setup()

    await dp.feed_update(bot, text_msg("/fibre"))
    assert "What do you want to see?" in last_text(session)

    for kind in ("n", "m"):
        for period in ("24h", "7d", "30d", "all"):
            await dp.feed_update(bot, callback(ReportCB(kind=kind, period=period).pack()))
            assert "Powered by" in last_text(session), (kind, period)
    for period in ("24h", "7d", "30d"):
        await dp.feed_update(
            bot, callback(ReportCB(kind="v", period=period, valcons=CUMULO).pack())
        )
        assert "<b>Cumulo</b>" in last_text(session)

    reports = [
        c for c in session.calls if isinstance(c, EditMessageText) and "Powered by" in c.text
    ]
    assert len(reports) == 11  # 4 network + 4 market + 3 validator, none failed

    # Validator picker remembers Cumulo, and typed names resolve through search.
    await dp.feed_update(bot, callback(MenuCB(target="v").pack()))
    await dp.feed_update(bot, callback(MenuCB(target="vask").pack()))
    assert 42 in memory.awaiting_name
    await dp.feed_update(bot, text_msg("celestiavaloper16yv2p64nzps0lqcealnumrsqc2rcszkd0uzlp8", 9))
    assert "Choose a period" in last_text(session)
    assert memory.last_validator[(42, "t")].valcons == CUMULO  # per chat and chain

    await dp.feed_update(bot, callback(PickCB(valcons=CUMULO).pack()))
    assert "Choose a period" in last_text(session)
    assert all(
        isinstance(c, SendMessage | EditMessageText | AnswerCallbackQuery) for c in session.calls
    )


@respx.mock
async def test_status_command_and_api_down() -> None:
    mock_api()
    respx.get(f"{API}/validator/cumulo/status").respond(json=load_json("status.json"))
    dp, bot, session, _ = setup()
    await dp.feed_update(bot, text_msg("/status cumulo"))
    assert "✅ Ready" in last_text(session)

    respx.get(f"{API}/validator/broken/status").respond(500, json={"error": "x"})
    await dp.feed_update(bot, text_msg("/status broken", 3))
    assert "not reachable" in last_text(session)


@respx.mock
async def test_free_text_is_ignored_unless_asked() -> None:
    mock_api()
    dp, bot, session, _ = setup()
    await dp.feed_update(bot, text_msg("hello"))
    assert session.calls == []


@respx.mock
async def test_card_flow() -> None:
    mock_api()
    report = respx.get(f"{API}/report").respond(json=load_json("report_2026-W40_cumulo.json"))
    dp, bot, session, _ = setup()

    await dp.feed_update(bot, callback(CardMenuCB(valcons=CUMULO).pack()))
    assert "Report card" in last_text(session)

    # From a text menu: the menu is replaced by a photo with caption and buttons.
    await dp.feed_update(bot, callback(CardCB(period="pw", valcons=CUMULO).pack(), 3))
    photos = [c for c in session.calls if isinstance(c, SendPhoto)]
    assert len(photos) == 1
    assert photos[0].caption is not None and "They kept 99.996%" in photos[0].caption
    assert any(isinstance(c, DeleteMessage) for c in session.calls)
    assert report.calls.last.request.url.params["period"] == "2026-W39"  # NOW is Sun 4 Oct
    assert report.calls.last.request.url.params["validator"] == CUMULO

    # Refresh on the photo edits it in place.
    photo_cb = Update(
        update_id=4,
        callback_query=CallbackQuery(
            id="4",
            from_user=USER,
            chat_instance="x",
            message=photo_message(),
            data=CardCB(period="pw", valcons=CUMULO).pack(),
        ),
    )
    await dp.feed_update(bot, photo_cb)
    assert any(isinstance(c, EditMessageMedia) for c in session.calls)

    # Details arrive as a new text message; the card stays.
    deletes_before = sum(isinstance(c, DeleteMessage) for c in session.calls)
    details = Update(
        update_id=5,
        callback_query=CallbackQuery(
            id="5",
            from_user=USER,
            chat_instance="x",
            message=photo_message(),
            data=DetailCB(period="pw", valcons=CUMULO).pack(),
        ),
    )
    await dp.feed_update(bot, details)
    assert "Kept their data: 99.996%" in last_text(session)
    assert sum(isinstance(c, DeleteMessage) for c in session.calls) == deletes_before

    # Back from the photo returns to a text menu (photo deleted, new message sent).
    back = Update(
        update_id=6,
        callback_query=CallbackQuery(
            id="6",
            from_user=USER,
            chat_instance="x",
            message=photo_message(),
            data=CardMenuCB(valcons=CUMULO).pack(),
        ),
    )
    await dp.feed_update(bot, back)
    assert "Report card" in last_text(session)


@respx.mock
async def test_card_command_and_api_error() -> None:
    mock_api()
    respx.get(f"{API}/report").respond(json=load_json("report_2026-W40_cumulo.json"))
    dp, bot, session, _ = setup()
    await dp.feed_update(bot, text_msg("/card"))
    assert any(isinstance(c, SendPhoto) for c in session.calls)

    respx.get(f"{API}/report").respond(500, json={"error": "x"})
    dp, bot, session, _ = setup()
    await dp.feed_update(bot, text_msg("/card", 2))
    assert "not reachable" in last_text(session)
