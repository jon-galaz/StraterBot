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
    monkeypatch.setenv("UNIVERSE", "AAPL,MSFT,GOOGL")
    settings = Settings()
    assert settings.universe == ["AAPL", "MSFT", "GOOGL"]
