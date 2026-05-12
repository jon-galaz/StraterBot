import pandas as pd
import numpy as np
import pytest
from trading_system.backtest.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    cagr,
    calmar_ratio,
    win_rate,
)


@pytest.fixture
def equity_rising():
    """Steadily rising equity — high Sharpe, low drawdown."""
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    return pd.Series(10_000 * (1 + np.arange(252) * 0.001), index=idx)


@pytest.fixture
def equity_flat():
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    return pd.Series(10_000.0, index=idx)


def test_sharpe_positive_for_rising_equity(equity_rising):
    sr = sharpe_ratio(equity_rising, freq="daily")
    assert sr > 0


def test_sharpe_zero_for_flat_equity(equity_flat):
    sr = sharpe_ratio(equity_flat, freq="daily")
    assert sr == 0.0


def test_max_drawdown_zero_for_rising(equity_rising):
    dd = max_drawdown(equity_rising)
    assert dd == 0.0


def test_max_drawdown_negative_for_falling():
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    equity = pd.Series([10_000, 9_000, 8_000, 9_500, 10_000], index=idx)
    dd = max_drawdown(equity)
    assert dd < 0


def test_cagr_positive_for_rising(equity_rising):
    c = cagr(equity_rising)
    assert c > 0


def test_calmar_positive_for_rising(equity_rising):
    c = calmar_ratio(equity_rising)
    assert c > 0


def test_sortino_positive_for_rising(equity_rising):
    s = sortino_ratio(equity_rising, freq="daily")
    assert s > 0


def test_win_rate_basic():
    pnl = pd.Series([100, -50, 200, -30, 50])
    assert win_rate(pnl) == pytest.approx(0.6)


def test_win_rate_empty():
    assert win_rate(pd.Series([], dtype=float)) == 0.0
