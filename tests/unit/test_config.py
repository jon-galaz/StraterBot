import pytest
from trading_system.config import Settings


def test_settings_loads_defaults(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    settings = Settings()
    assert settings.alpaca_paper is True
    assert settings.approval_timeout_minutes == 15
    assert settings.max_concurrent_positions == 5
    assert "AAPL" in settings.universe


def test_settings_universe_is_list(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("UNIVERSE", "aapl, msft ,googl")
    settings = Settings()
    assert settings.universe == ["AAPL", "MSFT", "GOOGL"]


def _base_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")


def test_trader_user_ids_parsed_from_csv(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADER_USER_IDS", "123456789, 987654321")
    settings = Settings(_env_file=None)
    assert settings.trader_user_ids == [123456789, 987654321]


def test_trader_user_ids_defaults_empty_fail_closed(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("TRADER_USER_IDS", raising=False)
    # _env_file=None isolates the test from any local .env on disk.
    settings = Settings(_env_file=None)
    # Empty by default — authorisation is fail-closed (no one) unless configured.
    assert settings.trader_user_ids == []
