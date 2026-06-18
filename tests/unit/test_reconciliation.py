"""Reconciliation must detect quantity drift, not just presence drift."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trading_system.safety.reconciliation import Reconciliation
from trading_system.store.db import init_db, make_engine, make_session_factory
from trading_system.store.models import SignalRecord, TradeRecord


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)


class FakeClient:
    def __init__(self, positions):
        self._positions = positions

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(q)) for s, q in self._positions.items()]


@pytest.fixture
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    return make_session_factory(engine)


def _open_trade(sf, ticker, qty):
    with sf() as s:
        sig = SignalRecord(ticker=ticker, direction="long", price=10.0,
                           donchian_high=9, donchian_low=8, ema_50=9, atr=1,
                           status="executed", timestamp=datetime.now(timezone.utc))
        s.add(sig)
        s.flush()
        s.add(TradeRecord(signal_id=sig.id, ticker=ticker, side="buy", qty=qty, status="open"))
        s.commit()


def test_qty_mismatch_detected(sf):
    _open_trade(sf, "AAPL", 5.0)
    recon = Reconciliation(FakeClient({"AAPL": 3}), sf)  # broker has 3, local 5
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("QTY mismatch" in m and "AAPL" in m for m in bot.messages)


def test_matching_state_is_ok(sf):
    _open_trade(sf, "AAPL", 5.0)
    recon = Reconciliation(FakeClient({"AAPL": 5}), sf)
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("matches Alpaca" in m for m in bot.messages)
