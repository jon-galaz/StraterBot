"""
Safety tests for AlpacaExecutor — idempotency, atomic claim, kill-switch guard,
position cap and duplicate protection. Uses a fake broker client (no network).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from trading_system.executor.alpaca_executor import AlpacaExecutor
from trading_system.store.db import init_db, make_engine, make_session_factory
from trading_system.store.models import SignalRecord, TradeRecord


class FakeAccount:
    equity = "10000"
    last_equity = "10000"
    cash = "10000"


class FakeTradingClient:
    """Records submitted orders; rejects a re-used client_order_id like Alpaca."""

    def __init__(self):
        self.submitted = []
        self._seen_keys = set()

    def get_account(self):
        return FakeAccount()

    def submit_order(self, req):
        if req.client_order_id in self._seen_keys:
            raise RuntimeError(f"duplicate client_order_id {req.client_order_id}")
        self._seen_keys.add(req.client_order_id)
        self.submitted.append(req)
        return SimpleNamespace(id=f"ord-{req.client_order_id}")


class FakeKillSwitch:
    def __init__(self, triggered=False):
        self.triggered = triggered


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    return engine, f"sqlite:///{tmp_path}/t.db"


def _make_executor(db, max_positions=5, kill_switch=None):
    engine, url = db
    sf = make_session_factory(engine)
    ex = AlpacaExecutor("k", "s", True, sf, url, max_concurrent_positions=max_positions)
    ex.client = FakeTradingClient()
    ex.kill_switch = kill_switch
    return ex, sf


def _add_signal(sf, ticker="AAPL", status="pending") -> int:
    with sf() as s:
        rec = SignalRecord(
            ticker=ticker, direction="long", price=150.0,
            donchian_high=148.0, donchian_low=140.0, ema_50=145.0, atr=2.5,
            status=status, timestamp=datetime.now(timezone.utc),
        )
        s.add(rec)
        s.commit()
        return rec.id


def _add_open_trade(sf, ticker):
    with sf() as s:
        sig = SignalRecord(
            ticker=ticker, direction="long", price=10.0,
            donchian_high=9, donchian_low=8, ema_50=9, atr=1,
            status="executed", timestamp=datetime.now(timezone.utc),
        )
        s.add(sig)
        s.flush()
        s.add(TradeRecord(signal_id=sig.id, ticker=ticker, side="buy",
                          qty=1.0, status="open"))
        s.commit()


def test_execute_places_bracket_with_deterministic_key(db):
    ex, sf = _make_executor(db)
    sid = _add_signal(sf)
    order_id = ex.execute(sid)

    assert order_id == f"ord-sig-{sid}"
    assert len(ex.client.submitted) == 1
    assert ex.client.submitted[0].client_order_id == f"sig-{sid}"
    with sf() as s:
        sig = s.get(SignalRecord, sid)
        assert sig.status == "executed"
        trade = s.execute(select(TradeRecord).where(TradeRecord.signal_id == sid)).scalar_one()
        assert trade.status == "open"
        assert trade.client_order_id == f"sig-{sid}"


def test_second_execute_is_rejected_no_duplicate_order(db):
    ex, sf = _make_executor(db)
    sid = _add_signal(sf)
    ex.execute(sid)
    with pytest.raises(ValueError):
        ex.execute(sid)  # already executed
    assert len(ex.client.submitted) == 1  # never a second order


def test_kill_switch_blocks_entry(db):
    ex, sf = _make_executor(db, kill_switch=FakeKillSwitch(triggered=True))
    sid = _add_signal(sf)
    with pytest.raises(RuntimeError):
        ex.execute(sid)
    assert ex.client.submitted == []
    with sf() as s:
        assert s.get(SignalRecord, sid).status == "pending"  # untouched


def test_position_cap_blocks_and_releases_claim(db):
    ex, sf = _make_executor(db, max_positions=1)
    _add_open_trade(sf, "NVDA")  # already at cap of 1
    sid = _add_signal(sf, ticker="AAPL")
    with pytest.raises(RuntimeError):
        ex.execute(sid)
    assert ex.client.submitted == []
    with sf() as s:
        # Claim released so the trader can retry after closing a position.
        assert s.get(SignalRecord, sid).status == "pending"


def test_duplicate_ticker_blocked(db):
    ex, sf = _make_executor(db)
    _add_open_trade(sf, "AAPL")
    sid = _add_signal(sf, ticker="AAPL")
    with pytest.raises(RuntimeError):
        ex.execute(sid)
    assert ex.client.submitted == []
    with sf() as s:
        assert s.get(SignalRecord, sid).status == "pending"
