"""
Tests for the kill switch: durable (restart-surviving) trigger state and
alert-independent-of-liquidation behaviour. Uses a fake broker + bot.
"""
import asyncio
from types import SimpleNamespace

import pytest

from trading_system.config import Settings
from trading_system.safety.kill_switch import KillSwitch


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)


class FakeClient:
    def __init__(self, equity, last_equity, close_raises=False, close_results=None):
        self._equity = equity
        self._last_equity = last_equity
        self.close_raises = close_raises
        self.close_results = close_results or []
        self.closed = False

    def get_account(self):
        return SimpleNamespace(equity=str(self._equity), last_equity=str(self._last_equity))

    def close_all_positions(self, cancel_orders=True):
        self.closed = True
        if self.close_raises:
            raise RuntimeError("alpaca down")
        return self.close_results


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        alpaca_api_key="k", alpaca_secret_key="s",
        telegram_bot_token="t", telegram_chat_id="1",
        database_url=f"sqlite:///{tmp_path}/trading.db",
        daily_max_loss_pct=2.0,
    )


def test_trigger_persists_across_restart(tmp_path):
    settings = _settings(tmp_path)
    client = FakeClient(equity=9700, last_equity=10000)  # -3% < -2%
    ks = KillSwitch(client, settings)
    bot = FakeBot()

    asyncio.run(ks.check(bot, 1))
    assert ks.triggered is True
    assert client.closed is True
    assert any("KILL SWITCH" in m for m in bot.messages)

    # A fresh instance (simulating a process restart) must still be triggered.
    ks2 = KillSwitch(FakeClient(equity=9700, last_equity=10000), settings)
    assert ks2.triggered is True


def test_reset_clears_persisted_state(tmp_path):
    settings = _settings(tmp_path)
    ks = KillSwitch(FakeClient(9700, 10000), settings)
    asyncio.run(ks.check(FakeBot(), 1))
    assert ks.triggered is True
    ks.reset()
    assert ks.triggered is False
    assert KillSwitch(FakeClient(9700, 10000), settings).triggered is False


def test_no_trigger_within_loss_limit(tmp_path):
    settings = _settings(tmp_path)
    ks = KillSwitch(FakeClient(equity=9900, last_equity=10000), settings)  # -1%
    asyncio.run(ks.check(FakeBot(), 1))
    assert ks.triggered is False


def test_alert_fires_even_if_liquidation_raises(tmp_path):
    settings = _settings(tmp_path)
    client = FakeClient(equity=9000, last_equity=10000, close_raises=True)
    ks = KillSwitch(client, settings)
    bot = FakeBot()
    asyncio.run(ks.check(bot, 1))
    # Halt latched, operator alerted, and the failure surfaced loudly.
    assert ks.triggered is True
    assert any("KILL SWITCH" in m for m in bot.messages)
    assert any("LIQUIDATION FAILED" in m for m in bot.messages)
