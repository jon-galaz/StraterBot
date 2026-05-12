import pandas as pd
import numpy as np
import pytest
from trading_system.rules.engine import RuleEngine, Signal


def _make_bars(n: int, trend: str = "up") -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    if trend == "up":
        close = pd.Series(100 + np.arange(n) * 2.0, index=idx)   # step=2.0 so close breaks above prev high+1
    else:
        close = pd.Series(200 - np.arange(n) * 2.0, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.2
    # Background volume low, last bar high — so last bar passes 1.5x confirmation
    volume = pd.Series(5_000_000.0, index=idx)
    volume.iloc[-1] = 10_000_000.0   # 10M > 1.5 * 5M = 7.5M ✓
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_no_signal_before_warmup():
    engine = RuleEngine()
    assert engine.evaluate(_make_bars(50, "up")) is None


def test_signal_on_breakout_in_uptrend():
    engine = RuleEngine(ticker="AAPL")
    result = engine.evaluate(_make_bars(300, "up"))
    assert isinstance(result, Signal)
    assert result.direction == "long"
    assert result.ticker == "AAPL"


def test_no_signal_in_downtrend():
    engine = RuleEngine()
    result = engine.evaluate(_make_bars(300, "down"))
    assert result is None


def test_signal_has_required_fields():
    engine = RuleEngine(ticker="NVDA")
    result = engine.evaluate(_make_bars(300, "up"))
    assert result is not None
    assert result.price > 0
    assert result.donchian_high > 0
    assert result.donchian_low > 0
    assert result.ema_50 > 0
    assert result.atr > 0


def test_no_signal_on_low_volume():
    engine = RuleEngine()
    bars = _make_bars(300, "up")
    bars.loc[bars.index[-1], "Volume"] = 100.0
    assert engine.evaluate(bars) is None


def test_is_exit_on_breakdown():
    engine = RuleEngine()
    bars = _make_bars(300, "down")
    # In a downtrend close < donchian low → should signal exit
    assert engine.is_exit(bars) is True


def test_is_exit_false_in_uptrend():
    engine = RuleEngine()
    bars = _make_bars(300, "up")
    assert engine.is_exit(bars) is False
