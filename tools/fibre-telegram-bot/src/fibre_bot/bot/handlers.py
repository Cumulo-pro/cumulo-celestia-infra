"""Telegram commands and callback queries: on-demand reports and report cards, per chain."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from fibre_bot.api.client import (
    AmbiguousError,
    FibreAPIError,
    FibreNotActiveError,
    NotFoundError,
    WarmingUpError,
)
from fibre_bot.bot.keyboards import (
    CardCB,
    CardMenuCB,
    ChainCB,
    DetailCB,
    MenuCB,
    PickCB,
    ReportCB,
    candidates_menu,
    card_actions,
    card_period_menu,
    main_menu,
    period_menu,
    report_actions,
    validator_menu,
)
from fibre_bot.data.models import CardPeriod, Period, ReportKind, ValidatorRef
from fibre_bot.reports.format import esc, short_addr, split_message

if TYPE_CHECKING:
    from fibre_bot.chains import Chain, ChainRegistry

log = logging.getLogger(__name__)

API_DOWN = "The Fibre API is not reachable right now. Please try again in a minute."
WARMING_UP = "The monitor is warming up (data not ready yet). Please try again in a minute."


def help_text(registry: ChainRegistry) -> str:
    active = ", ".join(c.label for c in registry.all())
    pending = [c for c in registry.known() if not c.active]
    soon = "".join(f"\n{esc(c.not_active_text())}" for c in pending) + "\n" if pending else ""
    example = registry.all()[-1].key.split("-")[0] if registry.multi else ""
    networks = (
        f"Default network: {esc(registry.primary.label)}. Add the network to commands and inline "
        f"for another one, for example <code>/status {esc(example)} cumulo</code> or "
        f"<code>@CumuloFibreBot {esc(example)} cumulo</code>.\n\n"
        if registry.multi
        else ""
    )
    return (
        "<b>Fibre Network Monitor bot</b>\n"
        f"Status reports for Celestia Fibre: {esc(active)}.{soon}\n"
        "/fibre: open the report menu (network, a validator, market, report card)\n"
        "/card: report card image for last week (add a moniker for a validator card)\n"
        "/status &lt;moniker or valcons&gt;: quick readiness check\n"
        "/help: this message\n\n"
        f"{networks}"
        "Share a card in any chat: type @CumuloFibreBot in the message box, or "
        "@CumuloFibreBot cumulo for a validator. If a group blocks inline bots, open the card "
        "here and forward it.\n\n"
        "Figures come from Cumulo's Fibre Network Monitor and refresh every minute. "
        '"n/a" means there is no data yet, never zero.\n\n'
        "<i>Powered by Cumulo Fibre Monitor · cumulo.pro</i>"
    )


@dataclass
class ChatMemory:
    """Per-chat state kept in memory (lost on restart, which only resets defaults)."""

    last_validator: dict[tuple[int, str], ValidatorRef] = field(default_factory=dict)
    chain: dict[int, str] = field(default_factory=dict)
    awaiting_name: dict[int, str] = field(default_factory=dict)  # chat -> chain code
    photo_ids: dict[str, str] = field(default_factory=dict)

    def remember_photo(self, key: str, file_id: str, limit: int = 200) -> None:
        if len(self.photo_ids) >= limit:
            self.photo_ids.pop(next(iter(self.photo_ids)))
        self.photo_ids[key] = file_id


def api_error_text(exc: FibreAPIError) -> str:
    if isinstance(exc, FibreNotActiveError):
        return "Fibre is not active on this network yet."
    if isinstance(exc, WarmingUpError):
        return WARMING_UP
    if isinstance(exc, NotFoundError):
        return "No validator found with that name."
    return API_DOWN


async def show(
    target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Edit the menu message in place for callbacks, send a new message for commands.
    Long reports are split; the keyboard goes on the last part."""
    chunks = split_message(text)
    if isinstance(target, CallbackQuery):
        message = target.message
        if not isinstance(message, Message):
            await target.answer()
            return
        if message.photo:
            # A photo cannot become a text message: replace it, keeping the chat tidy.
            with contextlib.suppress(TelegramBadRequest):
                await message.delete()
            for i, chunk in enumerate(chunks):
                await message.answer(
                    chunk,
                    reply_markup=markup if i == len(chunks) - 1 else None,
                    disable_web_page_preview=True,
                )
            await target.answer()
            return
        first_markup = markup if len(chunks) == 1 else None
        try:
            await message.edit_text(
                chunks[0], reply_markup=first_markup, disable_web_page_preview=True
            )
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                raise
            await target.answer("Already up to date")
            return
        for i, chunk in enumerate(chunks[1:], start=1):
            await message.answer(
                chunk,
                reply_markup=markup if i == len(chunks) - 1 else None,
                disable_web_page_preview=True,
            )
        await target.answer()
    else:
        for i, chunk in enumerate(chunks):
            await target.answer(
                chunk,
                reply_markup=markup if i == len(chunks) - 1 else None,
                disable_web_page_preview=True,
            )


def build_router(registry: ChainRegistry, memory: ChatMemory) -> Router:
    router = Router(name="reports")

    def chat_id(target: Message | CallbackQuery) -> int:
        msg = target.message if isinstance(target, CallbackQuery) else target
        return msg.chat.id if msg is not None else 0

    def chain_of(target: Message | CallbackQuery, code: str | None = None) -> Chain:
        """A callback's code when there is one ("" = old v0.1 button, mocha), else the chat's
        last choice, else the default chain (mainnet)."""
        chain = registry.get(code if code is not None else memory.chain.get(chat_id(target)))
        memory.chain[chat_id(target)] = chain.code
        return chain

    def title(text: str, chain: Chain) -> str:
        """Every message says which network it is about."""
        return f"<b>{text}</b> · {esc(chain.label)}"

    def split_chain_arg(target: Message, args: str) -> tuple[Chain, str]:
        """'/card mocha cumulo' -> (mocha chain, 'cumulo'); no network word -> chat chain.
        The returned chain can be pending (not active): callers check `chain.active`."""
        words = args.split()
        if words:
            named = registry.by_alias(words[0])
            if named is not None:
                if named.active:
                    memory.chain[target.chat.id] = named.code
                return named, " ".join(words[1:])
        return chain_of(target), args

    async def pending(message: Message, chain: Chain) -> bool:
        """Answer for a network that is not active yet. True when it answered."""
        if chain.active:
            return False
        await message.answer(esc(chain.not_active_text()))
        return True

    def last_or_default(cid: int, chain: Chain) -> ValidatorRef | None:
        return memory.last_validator.get((cid, chain.code)) or registry.default_validator(chain)

    def known_moniker(chain: Chain, valcons: str) -> str:
        return chain.service.known_moniker(valcons) or short_addr(valcons)

    async def root_menu(target: Message | CallbackQuery, chain: Chain) -> None:
        await show(
            target,
            f"{title('Fibre reports', chain)}\nWhat do you want to see?",
            main_menu(chain.code, registry.others(chain.code)),
        )

    async def open_validator(
        target: Message | CallbackQuery, chain: Chain, ref: ValidatorRef
    ) -> None:
        memory.last_validator[(chat_id(target), chain.code)] = ref
        await show(
            target,
            f"{title(esc(ref.moniker), chain)}\n<code>{esc(short_addr(ref.valcons))}</code>\n"
            "Choose a period:",
            period_menu(ReportKind.VALIDATOR, ref.valcons, chain.code),
        )

    async def offer(
        target: Message | CallbackQuery, chain: Chain, refs: list[ValidatorRef]
    ) -> None:
        if not refs:
            await show(
                target,
                "No validator matches that name. Try another moniker or a valcons.",
                validator_menu(None, chain.code),
            )
        elif len(refs) == 1:
            await open_validator(target, chain, refs[0])
        else:
            await show(
                target, "Several validators match. Pick one:", candidates_menu(refs, chain.code)
            )

    # Commands ----------------------------------------------------------------------------

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(help_text(registry), disable_web_page_preview=True)

    @router.message(Command("fibre"))
    async def cmd_fibre(message: Message, command: CommandObject) -> None:
        """Straight to the menu of the chat's network (default: mainnet); the menu has a
        button to switch network. '/fibre mocha' opens another one."""
        memory.awaiting_name.pop(message.chat.id, None)
        chain, _ = split_chain_arg(message, (command.args or "").strip())
        if await pending(message, chain):
            return
        memory.chain[message.chat.id] = chain.code
        await root_menu(message, chain)

    @router.message(Command("status"))
    async def cmd_status(message: Message, command: CommandObject) -> None:
        chain, ident = split_chain_arg(message, (command.args or "").strip())
        if await pending(message, chain):
            return
        if not ident:
            ref = last_or_default(message.chat.id, chain)
            if ref is None:
                await message.answer("Usage: /status &lt;moniker or valcons&gt;")
                return
            ident = ref.valcons
        try:
            text = await chain.service.status_line(ident)
        except AmbiguousError as exc:
            refs = [
                ValidatorRef(str(c.get("valcons")), str(c.get("moniker") or c.get("valcons")))
                for c in exc.candidates
                if c.get("valcons")
            ]
            await message.answer(
                "Several validators match. Pick one:",
                reply_markup=candidates_menu(refs, chain.code),
            )
            return
        except FibreAPIError as exc:
            await message.answer(api_error_text(exc))
            return
        await message.answer(f"[{esc(chain.label)}] {text}", disable_web_page_preview=True)

    # Menus -------------------------------------------------------------------------------

    @router.callback_query(ChainCB.filter())
    async def on_chain(query: CallbackQuery, callback_data: ChainCB) -> None:
        chain = registry.get(callback_data.c)
        memory.chain[chat_id(query)] = chain.code
        await root_menu(query, chain)

    @router.callback_query(MenuCB.filter())
    async def on_menu(query: CallbackQuery, callback_data: MenuCB) -> None:
        cid = chat_id(query)
        chain = chain_of(query, callback_data.c)
        target = callback_data.target
        if target == "root":
            memory.awaiting_name.pop(cid, None)
            await root_menu(query, chain)
        elif target == ReportKind.NETWORK.value:
            await show(
                query,
                f"{title('Network report', chain)}\nChoose a period:",
                period_menu(ReportKind.NETWORK, c=chain.code),
            )
        elif target == ReportKind.MARKET.value:
            await show(
                query,
                f"{title('Market report', chain)}\nChoose a period:",
                period_menu(ReportKind.MARKET, c=chain.code),
            )
        elif target == ReportKind.VALIDATOR.value:
            await show(
                query,
                title("Validator report", chain),
                validator_menu(last_or_default(cid, chain), chain.code),
            )
        elif target == "vask":
            memory.awaiting_name[cid] = chain.code
            await show(query, "Send the validator moniker, valoper or valcons as a message.", None)
        else:
            await query.answer()

    @router.callback_query(PickCB.filter())
    async def on_pick(query: CallbackQuery, callback_data: PickCB) -> None:
        chain = chain_of(query, callback_data.c)
        ref = ValidatorRef(callback_data.valcons, known_moniker(chain, callback_data.valcons))
        await open_validator(query, chain, ref)

    @router.callback_query(ReportCB.filter())
    async def on_report(query: CallbackQuery, callback_data: ReportCB) -> None:
        chain = chain_of(query, callback_data.c)
        try:
            kind = ReportKind(callback_data.kind)
            period = Period(callback_data.period)
        except ValueError:
            await query.answer("Unknown report")
            return
        try:
            if kind is ReportKind.NETWORK:
                text = await chain.service.network(period)
            elif kind is ReportKind.MARKET:
                text = await chain.service.market(period)
            else:
                text = await chain.service.validator(callback_data.valcons, period)
        except FibreAPIError as exc:
            log.warning(
                "report_failed chain=%s kind=%s period=%s error=%s", chain.key, kind, period, exc
            )
            await query.answer(api_error_text(exc), show_alert=True)
            return
        await show(query, text, report_actions(kind, period, callback_data.valcons, chain.code))

    # Report cards ------------------------------------------------------------------------

    async def send_card(
        target: Message | CallbackQuery,
        chain: Chain,
        period: CardPeriod,
        valcons: str,
        *,
        refresh: bool,
    ) -> None:
        if isinstance(target, CallbackQuery):
            await target.answer("Drawing the card…")
        message = target.message if isinstance(target, CallbackQuery) else target
        if not isinstance(message, Message):
            return
        try:
            card = await chain.service.card(period, valcons or None)
        except FibreAPIError as exc:
            log.warning("card_failed chain=%s period=%s error=%s", chain.key, period, exc)
            await message.answer(api_error_text(exc))
            return
        # Once Telegram has the image, resend it by file_id: no re-upload for the next users.
        known = memory.photo_ids.get(card.cache_key)
        photo: str | BufferedInputFile = known or BufferedInputFile(
            card.png, filename=card.filename
        )
        markup = card_actions(period, valcons, chain.code)
        sent: Message | bool | None = None
        if isinstance(target, CallbackQuery) and refresh and message.photo:
            with contextlib.suppress(TelegramBadRequest):  # "not modified" is fine
                sent = await message.edit_media(
                    InputMediaPhoto(media=photo, caption=card.caption), reply_markup=markup
                )
        else:
            if isinstance(target, CallbackQuery):
                with contextlib.suppress(TelegramBadRequest):
                    await message.delete()
            sent = await message.answer_photo(photo, caption=card.caption, reply_markup=markup)
        if known is None and isinstance(sent, Message) and sent.photo:
            memory.remember_photo(card.cache_key, sent.photo[-1].file_id)

    @router.message(Command("card"))
    async def cmd_card(message: Message, command: CommandObject) -> None:
        chain, ident = split_chain_arg(message, (command.args or "").strip())
        if await pending(message, chain):
            return
        valcons = ""
        if ident:
            try:
                refs = await chain.service.resolve(ident)
            except FibreAPIError as exc:
                await message.answer(api_error_text(exc))
                return
            if len(refs) != 1:
                await offer(message, chain, refs)
                return
            valcons = refs[0].valcons
        await send_card(message, chain, CardPeriod.LAST_WEEK, valcons, refresh=False)

    @router.callback_query(CardMenuCB.filter())
    async def on_card_menu(query: CallbackQuery, callback_data: CardMenuCB) -> None:
        chain = chain_of(query, callback_data.c)
        who = ""
        if callback_data.valcons:
            who = f" for <b>{esc(known_moniker(chain, callback_data.valcons))}</b>"
        await show(
            query,
            f"🖼 {title('Report card', chain)}{who}\nCalendar periods in UTC, the same as the "
            "cumulo.pro report. Choose one:",
            card_period_menu(callback_data.valcons, chain.code),
        )

    @router.callback_query(CardCB.filter())
    async def on_card(query: CallbackQuery, callback_data: CardCB) -> None:
        chain = chain_of(query, callback_data.c)
        try:
            period = CardPeriod(callback_data.period)
        except ValueError:
            await query.answer("Unknown period")
            return
        await send_card(query, chain, period, callback_data.valcons, refresh=True)

    @router.callback_query(DetailCB.filter())
    async def on_details(query: CallbackQuery, callback_data: DetailCB) -> None:
        chain = chain_of(query, callback_data.c)
        try:
            period = CardPeriod(callback_data.period)
            text = await chain.service.card_details(period, callback_data.valcons or None)
        except ValueError:
            await query.answer("Unknown period")
            return
        except FibreAPIError as exc:
            await query.answer(api_error_text(exc), show_alert=True)
            return
        message = query.message
        if isinstance(message, Message):
            # Keep the card (it is the shareable part) and send the details below it.
            for chunk in split_message(text):
                await message.answer(chunk, disable_web_page_preview=True)
        await query.answer()

    # Free text: only when we asked for a validator name ----------------------------------

    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        cid = message.chat.id
        code = memory.awaiting_name.pop(cid, None)
        if code is None:
            return
        chain = chain_of(message, code)
        try:
            refs = await chain.service.resolve(message.text or "")
        except FibreAPIError as exc:
            await message.answer(api_error_text(exc))
            return
        await offer(message, chain, refs)

    return router
