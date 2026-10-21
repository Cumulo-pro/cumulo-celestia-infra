"""Inline mode (@CumuloFibreBot in any chat) and the admin /weekly command."""

from __future__ import annotations

import asyncio
import hashlib
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatMemberUpdated,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultUnion,
    InputTextMessageContent,
    Message,
)

from fibre_bot.api.client import FibreAPIError
from fibre_bot.bot.publish import CardPublisher, share_keyboard
from fibre_bot.bot.service import Card
from fibre_bot.data.models import CardPeriod
from fibre_bot.reports.format import esc
from fibre_bot.scheduler import WeeklyReport

log = logging.getLogger(__name__)

NETWORK_PERIODS = list(CardPeriod)
VALIDATOR_PERIODS = [CardPeriod.LAST_WEEK, CardPeriod.THIS_MONTH, CardPeriod.THIS_YEAR]
INLINE_CACHE_SECONDS = 300


def _result_id(card: Card) -> str:
    return hashlib.sha1(card.cache_key.encode()).hexdigest()[:32]


def build_inline_router(
    publisher: CardPublisher, weekly: WeeklyReport, admin_ids: list[int]
) -> Router:
    router = Router(name="inline")
    registry = publisher.registry

    async def to_result(card: Card, label: str) -> InlineQueryResultUnion:
        markup = share_keyboard(card, await publisher.bot_username())
        file_id = await publisher.file_id(card)
        title = f"{label} · {card.title}" if label else card.title
        if file_id:
            return InlineQueryResultCachedPhoto(
                id=_result_id(card),
                photo_file_id=file_id,
                title=title,
                caption=card.caption,
                parse_mode="HTML",
                reply_markup=markup,
            )
        # No storage chat configured: share the summary as text instead of the image.
        return InlineQueryResultArticle(
            id=_result_id(card),
            title=title,
            description="Fibre report summary",
            input_message_content=InputTextMessageContent(
                message_text=card.caption, parse_mode="HTML", link_preview_options=None
            ),
            reply_markup=markup,
        )

    @router.inline_query()
    async def on_inline(query: InlineQuery) -> None:
        text = query.query.strip()
        valcons: str | None = None
        # "@CumuloFibreBot mocha cumulo": an optional network word, then a validator name.
        words = text.split()
        chain = registry.by_alias(words[0]) if words else None
        if chain is not None:
            text = " ".join(words[1:])
        else:
            chain = registry.primary
        if not chain.active:
            note = chain.not_active_text()
            await query.answer(
                [
                    InlineQueryResultArticle(
                        id=f"pending-{chain.key}"[:64],
                        title=f"{chain.label}: Fibre not active yet",
                        description=note,
                        input_message_content=InputTextMessageContent(message_text=esc(note)),
                    )
                ],
                cache_time=60,
                is_personal=False,
            )
            return
        service = chain.service
        label = chain.label
        if text:
            try:
                await service.store.ensure_loaded()
            except FibreAPIError:
                await query.answer([], cache_time=5)
                return
            matches = service.match_validators(text, limit=1)
            if not matches:
                log.info("inline_no_match query=%r", text[:40])
                await query.answer(
                    [],
                    cache_time=30,
                    is_personal=False,
                )
                return
            valcons = matches[0].valcons
        periods = VALIDATOR_PERIODS if valcons else NETWORK_PERIODS
        cards = await asyncio.gather(
            *(service.card(p, valcons) for p in periods), return_exceptions=True
        )
        results: list[InlineQueryResultUnion] = []
        for card in cards:
            if isinstance(card, Card):
                results.append(await to_result(card, label))
            elif not isinstance(card, FibreAPIError):
                raise card  # a bug, not an API hiccup
        await query.answer(results, cache_time=INLINE_CACHE_SECONDS, is_personal=False)
        log.info(
            "inline_answered chain=%s query=%r validator=%s results=%d images=%s",
            chain.key,
            text[:40],
            valcons or "-",
            len(results),
            publisher.can_upload,
        )

    @router.message(Command("weekly"))
    async def cmd_weekly(message: Message, command: CommandObject) -> None:
        user = message.from_user
        if user is None or user.id not in admin_ids:
            await message.answer("This command is for the bot admins.")
            return
        arg = (command.args or "").strip().lower()
        if arg == "preview":
            status = await weekly.post(message.chat.id, force=True)
        elif arg == "post":
            status = await weekly.post(force=True)
        else:
            weeks = weekly.posted_weeks()
            chains = ", ".join(c.key for c in weekly.chains()) or "none"
            last = ", ".join(f"{k} {v}" for k, v in weeks.items()) or "never"
            await message.answer(
                "<b>Weekly report</b>\n"
                f"Channel: {esc(weekly.channel_id or 'not set (REPORT_CHANNEL_ID)')}\n"
                f"Schedule: {esc(weekly.day)} {weekly.hour:02d}:{weekly.minute:02d} "
                f"({esc(weekly.tz)})\n"
                f"Chains: {esc(chains)}\n"
                f"Last posted: {esc(last)}\n\n"
                "/weekly preview: send last week's card here\n"
                "/weekly post: post it to the channel now"
            )
            return
        await message.answer(esc(status))

    @router.message(Command("id"))
    async def cmd_id(message: Message) -> None:
        """Setup helper: the ids to put in ADMIN_CHAT_IDS / STORAGE_CHAT_ID."""
        user = message.from_user
        await message.answer(
            f"Your user id: <code>{user.id if user else 'n/a'}</code>\n"
            f"This chat id: <code>{message.chat.id}</code>"
        )

    @router.my_chat_member()
    async def on_added(update: ChatMemberUpdated) -> None:
        """Log the id of any channel or group the bot is added to (for REPORT_CHANNEL_ID)."""
        log.info(
            "bot_membership chat_id=%s type=%s title=%r status=%s",
            update.chat.id,
            update.chat.type,
            update.chat.title,
            update.new_chat_member.status,
        )

    return router
