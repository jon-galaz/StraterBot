from typing import Annotated

from pydantic import field_validator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Canonical 30-name universe spanning all 11 GICS sectors for breadth.
# Per active-management law (IR ∝ √N), more independent names = smoother edge.
# Single source of truth — the backtest scripts import this so they can never
# drift from what the live scanner trades.
DEFAULT_UNIVERSE: list[str] = [
    # Tech
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD",
    # Communication services
    "GOOGL", "META", "NFLX",
    # Consumer discretionary
    "AMZN", "TSLA", "HD", "LULU",
    # Consumer staples
    "COST", "KO", "WMT",
    # Financials
    "JPM", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "LLY",
    # Industrials
    "CAT", "URI", "AXON",
    # Energy
    "XOM", "CVX",
    # Materials / Utilities / Real estate
    "FCX", "NEE", "AMT",
    # Mid-cap winner from Phase-3 backtest
    "ELF",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Alpaca
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_paper: bool = True
    # Historical data feed for the live scanner. "iex" is free but reports only
    # IEX-venue volume (a fraction of consolidated tape) — the 1.5×SMA volume
    # filter is NOT comparable to the yfinance-based backtest under IEX. Set to
    # "sip" once a paid subscription is active so live volume matches backtest.
    alpaca_data_feed: str = "iex"

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Database
    database_url: str = "sqlite:///./trading.db"

    # Trading — NoDecode keeps pydantic-settings from JSON-decoding the env value
    # so the validators below can parse a plain comma-separated string.
    universe: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    approval_timeout_minutes: int = 15
    max_concurrent_positions: int = 5
    daily_max_loss_pct: float = 2.0
    # Telegram user IDs allowed to approve/reject trades and run admin commands.
    # Everyone else in the group can read signals but cannot interact.
    # SAFETY: an empty list means NO ONE is authorised (fail closed) — set this
    # before going live. Get your ID by messaging @userinfobot on Telegram.
    trader_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    @field_validator("universe", mode="before")
    @classmethod
    def parse_universe(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [t.strip().upper() for t in v.split(",") if t.strip()]
        return v

    @field_validator("trader_user_ids", mode="before")
    @classmethod
    def parse_trader_ids(cls, v: str | list) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v
