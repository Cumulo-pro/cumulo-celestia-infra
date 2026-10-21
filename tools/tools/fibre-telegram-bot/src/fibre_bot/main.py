"""Entry point: long polling bot plus the shared background poller."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from fibre_bot.bot.handlers import ChatMemory, build_router
from fibre_bot.bot.inline import build_inline_router
from fibre_bot.bot.publish import CardPublisher
from fibre_bot.bot.throttle import install as install_throttle
from fibre_bot.chains import ChainRegistry, parse_mapping
from fibre_bot.config import Settings
from fibre_bot.scheduler import StateFile, WeeklyReport

log = logging.getLogger("fibre_bot")

COMMANDS = [
    BotCommand(command="fibre", description="Open the Fibre report menu"),
    BotCommand(command="status", description="Quick readiness check for a validator"),
    BotCommand(command="card", description="Report card image for last week"),
    BotCommand(command="help", description="How this bot works"),
]

WARM_EVERY_SECONDS = 600


async def keep_cards_warm(publisher: CardPublisher) -> None:
    """Upload fresh network cards regularly so inline answers never wait for a render."""
    while True:
        try:
            for chain in publisher.registry.all():
                await chain.store.ensure_loaded()
            ready = await publisher.warm()
            log.info("cards_warm ready=%d", ready)
        except Exception:
            log.exception("cards_warm_failed")
        await asyncio.sleep(WARM_EVERY_SECONDS)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
        stream=sys.stdout,
    )
    # httpx logs full URLs at INFO; the Telegram one would contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    token = settings.telegram_bot_token.get_secret_value()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see .env.example)")

    default_validators = parse_mapping(settings.default_validators)
    if settings.default_valcons:
        default_validators.setdefault(settings.fibre_chain, settings.default_valcons)
    registry = ChainRegistry(
        settings.fibre_api_base,
        default_chain=settings.default_chain,
        legacy_chain=settings.fibre_chain,
        only=[c.strip() for c in settings.fibre_chains.split(",")],
        pages=parse_mapping(settings.chain_pages),
        report_pages=parse_mapping(settings.chain_report_pages),
        default_validators=default_validators,
        default_moniker=settings.default_moniker,
        poll_interval=settings.poll_interval_seconds,
        stale_after=settings.stale_after_seconds,
    )
    memory = ChatMemory()

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    storage = settings.storage_chat_id
    if storage is None and settings.admin_chat_ids:
        storage = settings.admin_chat_ids[0]
    publisher = CardPublisher(
        bot, registry, memory, storage, delete_uploads=settings.storage_chat_id is None
    )
    weekly = WeeklyReport(
        publisher,
        settings.report_channel_id,
        StateFile(settings.state_path),
        day=settings.weekly_day,
        time=settings.weekly_time,
        tz=settings.tz,
        only=[c.strip() for c in settings.weekly_chains.split(",")],
        legacy_chain=settings.fibre_chain,
    )
    dp.include_router(build_inline_router(publisher, weekly, settings.admin_chat_ids))
    dp.include_router(build_router(registry, memory))
    install_throttle(dp, interval=1.0)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await bot.set_my_commands(COMMANDS)
    await registry.start()
    log.info(
        "started api=%s chains=%s",
        settings.fibre_api_base,
        ",".join(f"{c.key}({c.code})" for c in registry.all()),
    )
    weekly.start()
    background: list[asyncio.Task[object]] = []
    if publisher.can_upload:
        background.append(asyncio.create_task(keep_cards_warm(publisher)))
    else:
        log.info("inline_images_disabled reason=no STORAGE_CHAT_ID or ADMIN_CHAT_IDS")

    async def catch_up() -> None:
        try:
            for chain in registry.all():
                await chain.store.ensure_loaded()
            status = await weekly.catch_up()
        except Exception:
            log.exception("weekly_catch_up_failed")
            return
        if status:
            log.info("weekly_catch_up %s", status)

    background.append(asyncio.create_task(catch_up()))

    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({polling, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        log.info("shutting_down")
        weekly.stop()
        for task in background:
            task.cancel()
        with contextlib.suppress(RuntimeError):
            await dp.stop_polling()
        polling.cancel()
        stopper.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await polling
        await registry.stop()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
