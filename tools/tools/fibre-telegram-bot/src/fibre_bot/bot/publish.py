"""Turn report cards into Telegram file_ids and post them.

Inline mode can only show a photo that Telegram already has (or a public URL), so each card is
uploaded once to a storage chat and then referenced by file_id. Closed periods never change, so
their file_id is reused for good; open periods get a new one at most every few minutes.
"""

from __future__ import annotations

import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from fibre_bot.api.client import FibreAPIError
from fibre_bot.bot.handlers import ChatMemory
from fibre_bot.bot.service import Card
from fibre_bot.chains import ChainRegistry
from fibre_bot.data.models import CardPeriod

log = logging.getLogger(__name__)


def share_keyboard(card: Card, bot_username: str | None) -> InlineKeyboardMarkup | None:
    """URL buttons only: they work in channels, groups and inline messages alike."""
    row = []
    if card.url:  # a chain without a public page yet gets only the bot button
        row.append(InlineKeyboardButton(text="📊 Full report", url=card.url))
    if bot_username:
        row.append(InlineKeyboardButton(text="🤖 Open the bot", url=f"https://t.me/{bot_username}"))
    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


class CardPublisher:
    def __init__(
        self,
        bot: Bot,
        registry: ChainRegistry,
        memory: ChatMemory,
        storage_chat_id: int | None,
        *,
        delete_uploads: bool = False,
    ) -> None:
        self.bot = bot
        self.registry = registry
        self.memory = memory
        self.storage_chat_id = storage_chat_id
        self.delete_uploads = delete_uploads
        self.username: str | None = None

    @property
    def can_upload(self) -> bool:
        return self.storage_chat_id is not None

    async def bot_username(self) -> str | None:
        if self.username is None:
            with contextlib.suppress(TelegramAPIError):
                self.username = (await self.bot.me()).username
        return self.username

    async def file_id(self, card: Card) -> str | None:
        known = self.memory.photo_ids.get(card.cache_key)
        if known or self.storage_chat_id is None:
            return known
        try:
            msg = await self.bot.send_photo(
                self.storage_chat_id,
                BufferedInputFile(card.png, filename=card.filename),
                caption=card.title,
                disable_notification=True,
            )
        except TelegramAPIError as exc:
            log.warning("storage_upload_failed chat=%s error=%s", self.storage_chat_id, exc)
            return None
        if not msg.photo:
            return None
        file_id = msg.photo[-1].file_id
        self.memory.remember_photo(card.cache_key, file_id)
        if self.delete_uploads:  # the file_id stays valid after the message is gone
            with contextlib.suppress(TelegramAPIError):
                await self.bot.delete_message(self.storage_chat_id, msg.message_id)
        return file_id

    async def warm(self) -> int:
        """Pre-render and upload the network cards of every chain so inline answers are instant."""
        ready = 0
        for chain in self.registry.all():
            for period in CardPeriod:
                try:
                    card = await chain.service.card(period)
                except FibreAPIError as exc:
                    log.warning("warm_failed chain=%s period=%s error=%s", chain.key, period, exc)
                    continue
                if await self.file_id(card):
                    ready += 1
        return ready

    async def post(self, chat_id: int, card: Card) -> Message:
        known = self.memory.photo_ids.get(card.cache_key)
        photo: str | BufferedInputFile = known or BufferedInputFile(
            card.png, filename=card.filename
        )
        msg = await self.bot.send_photo(
            chat_id,
            photo,
            caption=card.caption,
            reply_markup=share_keyboard(card, await self.bot_username()),
        )
        if known is None and msg.photo:
            self.memory.remember_photo(card.cache_key, msg.photo[-1].file_id)
        return msg
