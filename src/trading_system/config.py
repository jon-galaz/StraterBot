from typing import Any
from pydantic import field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource, DotEnvSettingsSource


_CSV_FIELDS = {"universe", "trader_user_ids"}


def _universe_prepare(self, field_name: str, field: Any, value: Any, value_is_complex: bool) -> Any:
    if field_name in _CSV_FIELDS and isinstance(value, str):
        return value
    return super(type(self), self).prepare_field_value(field_name, field, value, value_is_complex)


class UniverseEnvSource(EnvSettingsSource):
    prepare_field_value = _universe_prepare


class UniverseDotEnvSource(DotEnvSettingsSource):
    prepare_field_value = _universe_prepare


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            UniverseEnvSource(settings_cls),
            UniverseDotEnvSource(settings_cls, env_file=".env", env_file_encoding="utf-8"),
            file_secret_settings,
        )

    # Alpaca
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_paper: bool = True

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Database
    database_url: str = "sqlite:///./trading.db"

    # Trading — 30-name universe spanning all 11 GICS sectors for breadth.
    # Per active-management law (IR ∝ √N), more independent names = smoother edge.
    universe: list[str] = Field(
        default=[
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
    )
    approval_timeout_minutes: int = 15
    max_concurrent_positions: int = 5
    daily_max_loss_pct: float = 2.0
    # Telegram user IDs allowed to approve/reject trades and run admin commands.
    # Everyone else in the group can read signals but cannot interact.
    # Get your ID by messaging @userinfobot on Telegram.
    trader_user_ids: list[int] = Field(default=[])

    @field_validator("universe", mode="before")
    @classmethod
    def parse_universe(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    @field_validator("trader_user_ids", mode="before")
    @classmethod
    def parse_trader_ids(cls, v: str | list) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v
