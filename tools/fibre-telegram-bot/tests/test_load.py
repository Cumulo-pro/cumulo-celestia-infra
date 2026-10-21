"""Many users at once: API traffic must not grow with users, and nothing may fail."""

from __future__ import annotations

import asyncio
import time

import respx
from aiogram.methods import SendPhoto

from fibre_bot.bot.keyboards import CardCB, ReportCB
from fibre_bot.bot.throttle import ThrottleMiddleware
from tests.conftest import CUMULO, load_json
from tests.test_bot_flow import API, callback, last_text, mock_api, setup

USERS = 500


@respx.mock
async def test_500_users_pressing_at_once() -> None:
    mock_api()
    report = respx.get(f"{API}/report").respond(json=load_json("report_2026-W40_cumulo.json"))
    dp, bot, session, _ = setup()
    kinds = [
        ReportCB(kind="n", period="7d").pack(),
        ReportCB(kind="m", period="7d").pack(),
        ReportCB(kind="v", period="7d", valcons=CUMULO).pack(),
        CardCB(period="pw").pack(),
    ]
    started = time.perf_counter()
    await asyncio.gather(
        *(dp.feed_update(bot, callback(kinds[i % 4], 1000 + i)) for i in range(USERS))
    )
    elapsed = time.perf_counter() - started

    api_calls = sum(1 for route in respx.mock.routes for _ in route.calls)
    # One fetch per distinct resource (single flight + cache), not one per user.
    assert api_calls <= 12, api_calls
    assert report.call_count == 1
    photos = [c for c in session.calls if isinstance(c, SendPhoto)]
    assert len(photos) == USERS // 4
    # The first card is uploaded; every later one reuses Telegram's file_id.
    assert sum(isinstance(p.photo, str) for p in photos) >= len(photos) - 5
    assert "Powered by" in last_text(session) or photos
    print(f"\n{USERS} concurrent requests in {elapsed:.2f}s, {api_calls} API calls")


async def test_throttle_blocks_rapid_clicks() -> None:
    t = [0.0]
    mw = ThrottleMiddleware(interval=1.0, clock=lambda: t[0])
    seen: list[int] = []

    async def handler(event: object, data: dict[str, object]) -> None:
        seen.append(1)

    class User:
        id = 7

    class Event:
        from_user = User()

    await mw(handler, Event(), {})  # type: ignore[arg-type]
    await mw(handler, Event(), {})  # type: ignore[arg-type]
    t[0] = 1.5
    await mw(handler, Event(), {})  # type: ignore[arg-type]
    assert len(seen) == 2
