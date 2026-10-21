"""Weekly post: the card of the week that just closed, every Monday morning, in the channel.

Restart safety: the last posted week is kept in a small JSON state file, so a restart never
posts the same week twice, and a bot that was down at post time catches up when it starts
(only if it has posted before, so a first install never posts by surprise).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from fibre_bot.api.client import FibreAPIError
from fibre_bot.bot.publish import CardPublisher
from fibre_bot.chains import Chain
from fibre_bot.data.models import CardPeriod

log = logging.getLogger(__name__)

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class StateFile:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)  # atomic: a crash never leaves a half-written file


class WeeklyReport:
    def __init__(
        self,
        publisher: CardPublisher,
        channel_id: int | None,
        state: StateFile,
        *,
        day: str = "mon",
        time: str = "09:00",
        tz: str = "Europe/Madrid",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        only: list[str] | None = None,
        legacy_chain: str = "mocha-5",
    ) -> None:
        if day not in DAYS:
            raise ValueError(f"WEEKLY_DAY must be one of {', '.join(DAYS)}")
        hour, minute = (int(x) for x in time.split(":"))
        self.publisher = publisher
        self.channel_id = channel_id
        self.state = state
        self.day, self.hour, self.minute = day, hour, minute
        self.tz = ZoneInfo(tz)
        self.clock = clock
        self.only = [c for c in (only or []) if c]
        self.legacy_chain = legacy_chain  # chain the v0.1 state file refers to
        self.scheduler: AsyncIOScheduler | None = None

    @property
    def enabled(self) -> bool:
        return self.channel_id is not None

    def due_week_key(self) -> str:
        return CardPeriod.LAST_WEEK.api_key(self.clock())

    def last_scheduled_run(self) -> datetime:
        """Most recent scheduled post time at or before now, in UTC."""
        local = self.clock().astimezone(self.tz)
        target = DAYS.index(self.day)
        days_back = (local.weekday() - target) % 7
        run = (local - timedelta(days=days_back)).replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        if run > local:
            run -= timedelta(days=7)
        return run.astimezone(UTC)

    def posted_weeks(self, state: dict[str, Any] | None = None) -> dict[str, str]:
        """{chain key: last posted week}. Reads the single-chain format of v0.1 too."""
        state = self.state.load() if state is None else state
        weeks = dict(state.get("weeks") or {})
        if isinstance(state.get("last_week"), str) and self.legacy_chain not in weeks:
            weeks[self.legacy_chain] = state["last_week"]
        return weeks

    def chains(self) -> list[Chain]:
        chains = self.publisher.registry.all()
        if self.only:
            chains = [c for c in chains if c.key in self.only]
        return chains

    async def post(self, chat_id: int | None = None, *, force: bool = False) -> str:
        """Post last week's card of every chain. Returns a short status for logs and /weekly."""
        target = chat_id if chat_id is not None else self.channel_id
        if target is None:
            return "no channel configured (REPORT_CHANNEL_ID)"
        state = self.state.load()
        weeks = self.posted_weeks(state)
        before = dict(weeks)
        statuses = []
        for chain in self.chains():
            statuses.append(await self._post_chain(chain, target, chat_id, force, weeks))
        if chat_id is None and weeks != before:  # only real channel posts count
            state.pop("last_week", None)
            state["weeks"] = weeks
            state["posted_at"] = self.clock().isoformat()
            self.state.save(state)
        return "; ".join(statuses) or "no chains"

    async def _post_chain(
        self, chain: Chain, target: int, chat_id: int | None, force: bool, weeks: dict[str, str]
    ) -> str:
        name = chain.label if len(self.chains()) > 1 else ""
        prefix = f"{name}: " if name else ""
        try:
            card = await chain.service.card(CardPeriod.LAST_WEEK)
        except FibreAPIError as exc:
            log.warning("weekly_failed chain=%s error=%s", chain.key, exc)
            return f"{prefix}API error: {exc}"
        if not force and chat_id is None and weeks.get(chain.key) == card.period_key:
            return f"{prefix}week {card.period_key} already posted"
        if not card.closed and not force:
            return f"{prefix}week {card.period_key} is not closed yet"
        await self.publisher.post(target, card)
        if chat_id is None:
            weeks[chain.key] = card.period_key
        log.info("weekly_posted chain=%s week=%s chat=%s", chain.key, card.period_key, target)
        return f"{prefix}posted week {card.period_key}"

    async def catch_up(self) -> str | None:
        """After downtime: post if the scheduled time passed and a week is still missing.
        Only when the bot has posted before, so a first install never posts by surprise."""
        weeks = self.posted_weeks()
        if not self.enabled or not weeks:
            return None
        due = self.due_week_key()
        if all(weeks.get(c.key) == due for c in self.chains()):
            return None
        if self.clock() < self.last_scheduled_run():
            return None
        return await self.post()

    def start(self) -> None:
        if not self.enabled:
            log.info("weekly_disabled reason=no REPORT_CHANNEL_ID")
            return
        self.scheduler = AsyncIOScheduler(timezone=self.tz)
        self.scheduler.add_job(
            self.post,
            CronTrigger(day_of_week=self.day, hour=self.hour, minute=self.minute, timezone=self.tz),
            id="weekly",
            misfire_grace_time=6 * 3600,
            coalesce=True,
        )
        self.scheduler.start()
        log.info(
            "weekly_scheduled day=%s time=%02d:%02d tz=%s",
            self.day,
            self.hour,
            self.minute,
            self.tz,
        )

    def stop(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
