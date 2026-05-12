import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from trading_system.store.db import init_db
from trading_system.store.models import Base, SignalRecord, TradeRecord


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return engine


def test_signal_insert(db):
    with Session(db) as s:
        rec = SignalRecord(
            ticker="AAPL", direction="long", price=150.0,
            donchian_high=148.0, donchian_low=140.0, ema_50=145.0, atr=2.5,
            status="pending", timestamp=datetime.now(timezone.utc),
        )
        s.add(rec)
        s.commit()
        assert rec.id is not None


def test_trade_links_to_signal(db):
    with Session(db) as s:
        sig = SignalRecord(
            ticker="NVDA", direction="long", price=600.0,
            donchian_high=598.0, donchian_low=570.0, ema_50=580.0, atr=8.0,
            status="approved", timestamp=datetime.now(timezone.utc),
        )
        s.add(sig)
        s.flush()
        trade = TradeRecord(
            signal_id=sig.id, ticker="NVDA", side="buy", qty=5.0,
            fill_price=601.0, stop_loss=589.0, take_profit=625.0,
            status="filled", filled_at=datetime.now(timezone.utc),
        )
        s.add(trade)
        s.commit()
        assert trade.signal_id == sig.id


def test_signal_status_values(db):
    """Valid statuses: pending, approved, rejected, expired."""
    with Session(db) as s:
        for status in ("pending", "approved", "rejected", "expired"):
            rec = SignalRecord(
                ticker="MSFT", direction="long", price=300.0,
                donchian_high=295.0, donchian_low=280.0, ema_50=290.0, atr=3.0,
                status=status, timestamp=datetime.now(timezone.utc),
            )
            s.add(rec)
        s.commit()
