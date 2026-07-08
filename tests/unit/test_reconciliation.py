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
    def __init__(self, positions, open_orders=None):
        self._positions = positions
        self._open_orders = open_orders or []

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(q)) for s, q in self._positions.items()]

    def get_orders(self, filter=None):
        return [SimpleNamespace(symbol=s) for s in self._open_orders]


@pytest.fixture
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    return make_session_factory(engine)


def _open_trade(sf, ticker, qty, fill_price=None):
    with sf() as s:
        sig = SignalRecord(ticker=ticker, direction="long", price=10.0,
                           donchian_high=9, donchian_low=8, ema_50=9, atr=1,
                           status="executed", timestamp=datetime.now(timezone.utc))
        s.add(sig)
        s.flush()
        s.add(TradeRecord(signal_id=sig.id, ticker=ticker, side="buy", qty=qty,
                          status="open", fill_price=fill_price))
        s.commit()


def test_qty_mismatch_detected(sf):
    _open_trade(sf, "AAPL", 5.0)
    recon = Reconciliation(FakeClient({"AAPL": 3}), sf)  # broker has 3, local 5
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("QTY mismatch" in m and "AAPL" in m for m in bot.messages)


def test_matching_state_is_ok(sf):
    _open_trade(sf, "AAPL", 5.0, fill_price=10.0)
    recon = Reconciliation(FakeClient({"AAPL": 5}), sf)
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("matches Alpaca" in m for m in bot.messages)


def test_unfilled_order_with_pending_alpaca_order_is_not_a_mismatch(sf):
    # Approved after close: order accepted by Alpaca, queued for next open, no
    # position yet, no local fill. Must be reported as pending, not a mismatch.
    _open_trade(sf, "AAPL", 5.0, fill_price=None)
    recon = Reconciliation(FakeClient({}, open_orders=["AAPL"]), sf)
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("matches Alpaca" in m for m in bot.messages)
    assert not any("mismatch" in m.lower() for m in bot.messages)
    assert any("awaiting fill" in m and "AAPL" in m for m in bot.messages)


def test_unfilled_order_with_no_alpaca_order_is_a_mismatch(sf):
    # No position AND no accepted order → the order is truly gone: real drift.
    _open_trade(sf, "AAPL", 5.0, fill_price=None)
    recon = Reconciliation(FakeClient({}, open_orders=[]), sf)
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("not found in Alpaca" in m and "AAPL" in m for m in bot.messages)


def test_filled_trade_missing_position_is_a_mismatch(sf):
    # We think it's filled/open but Alpaca has no position and no order → drift,
    # even if some unrelated order is pending.
    _open_trade(sf, "AAPL", 5.0, fill_price=10.0)
    recon = Reconciliation(FakeClient({}, open_orders=["AAPL"]), sf)
    bot = FakeBot()
    asyncio.run(recon.run(bot, 1))
    assert any("not found in Alpaca" in m and "AAPL" in m for m in bot.messages)
