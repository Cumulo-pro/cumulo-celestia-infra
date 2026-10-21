"""Per-user flood protection: at most one update per user every `interval` seconds.

The data layer already keeps API load constant; this stops one user (or a script) from
making the bot render or upload in a tight loop, and keeps us inside Telegram's own limits.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, interval: float = 1.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.interval = interval
        self._clock = clock
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        now = self._clock()
        last = self._last.get(user.id)
        if last is not None and now - last < self.interval:
            if isinstance(event, CallbackQuery):
                await event.answer("One moment…")
            return None
        self._last[user.id] = now
        if len(self._last) > 10_000:
            cutoff = now - 60
            self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return await handler(event, data)


def install(dispatcher: Any, interval: float = 1.0) -> ThrottleMiddleware:
    mw = ThrottleMiddleware(interval)
    dispatcher.message.outer_middleware(mw)
    dispatcher.callback_query.outer_middleware(mw)
    return mw


__all__ = ["ThrottleMiddleware", "install"]
