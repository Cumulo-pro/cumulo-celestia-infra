"""Settings loaded from the environment (and an optional .env file)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CUMULO_VALCONS = "celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    fibre_api_base: str = "https://fibre-api.cumulo.com.es"
    # Chains come from the API's /v1/chains (fibre_active decides when a chain starts).
    # DEFAULT_CHAIN: what users get unless they choose another network (mainnet).
    # FIBRE_CHAIN: the chain of v0.1 (old buttons without a chain go there); also used when the
    # chain list cannot be read. FIBRE_CHAINS limits the bot to some chains, empty = all.
    default_chain: str = "celestia"
    fibre_chain: str = "mocha-5"
    fibre_chains: str = ""
    # Public pages per chain, "key=url,key=url". A chain without one gets no link.
    chain_pages: str = (
        "mocha-5=https://cumulo.pro/services/celestia_mocha/fibre.php,"
        "celestia=https://cumulo.pro/services/celestia/fibre.php"
    )
    # Report tab per chain (takes ?period= and ?validator=), used for card and validator links.
    # A chain without one links only to its general page (mainnet until its report is confirmed).
    chain_report_pages: str = "mocha-5=https://cumulo.pro/services/celestia_mocha/fibre-report"
    # Default validator: valcons per chain ("key=valcons,...") or, if missing, this moniker.
    default_valcons: str = CUMULO_VALCONS  # v0.1 setting, applies to FIBRE_CHAIN
    default_validators: str = ""
    default_moniker: str = "Cumulo"
    weekly_chains: str = ""  # empty = every chain
    report_channel_id: int | None = None
    # Weekly post to REPORT_CHANNEL_ID (day: mon..sun, time HH:MM in TZ)
    weekly_day: str = "mon"
    weekly_time: str = "09:00"
    # Chat where the bot uploads card images once to get reusable file_ids for inline mode.
    # A private channel with the bot as admin is best; empty = first ADMIN_CHAT_IDS entry,
    # deleting each upload right away.
    storage_chat_id: int | None = None
    state_path: str = "fibre_bot_state.json"
    admin_chat_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    db_path: str = "fibre_bot.sqlite3"  # unused, kept so older .env files still load
    tz: str = "Europe/Madrid"
    poll_interval_seconds: int = 60
    stale_after_seconds: int = 600
    log_level: str = "INFO"

    @field_validator("admin_chat_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("report_channel_id", "storage_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("fibre_api_base")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")
