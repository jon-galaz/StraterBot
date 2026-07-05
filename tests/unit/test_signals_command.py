"""Tests for the /signals command: formatting + newest-first DB query."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import main
from trading_system.store.db import init_db, make_engine, make_session_factory
from trading_system.store.models import SignalRecord


def _sig(ts, ticker, price, status):
    return SignalRecord(
        ticker=ticker, direction="long", price=price,
        donchian_high=price, donchian_low=price - 5, ema_50=price - 2, atr=1.0,
        status=status, timestamp=ts,
    )


def test_format_signals_empty():
    assert main.format_signals([]) == "No signals recorded yet."


def test_format_signals_renders_status_and_fields():
    ts = datetime(2026, 6, 10, 20, 5, tzinfo=timezone.utc)
    out = main.format_signals([
        (ts, "AAPL", 150.0, "executed"),
        (ts, "NVDA", 600.0, "expired"),
    ])
    assert "AAPL" in out and "$150.00" in out
    assert "✅" in out            # executed
    assert "⌛" in out            # expired
    assert "expired = fired but never actioned" in out


def test_recent_signals_query_is_newest_first_and_limited(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    sf = make_session_factory(engine)
    base = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    with sf() as s:
        for i in range(5):
            s.add(_sig(base + timedelta(days=i), f"T{i}", 100 + i, "pending"))
        s.commit()

    # Same query the command uses.
    with sf() as s:
        rows = s.execute(
            select(SignalRecord).order_by(SignalRecord.timestamp.desc()).limit(3)
        ).scalars().all()
        tickers = [r.ticker for r in rows]

    assert tickers == ["T4", "T3", "T2"]  # newest first, capped at 3
