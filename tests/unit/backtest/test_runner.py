import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch
from trading_system.backtest.runner import run_backtest, BacktestResult


@pytest.fixture
def uptrend_df():
    """300 bars of uptrend with volume pattern that passes 1.5x confirmation."""
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.arange(n) * 2.0, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.2
    volume = pd.Series(5_000_000.0, index=idx)
    volume.iloc[-1] = 10_000_000.0
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_run_backtest_returns_result(uptrend_df):
    with patch("trading_system.backtest.runner.fetch_bars", return_value=uptrend_df):
        result = run_backtest("AAPL", "2020-01-01", "2021-06-01")
    assert isinstance(result, BacktestResult)


def test_backtest_result_has_metrics(uptrend_df):
    with patch("trading_system.backtest.runner.fetch_bars", return_value=uptrend_df):
        result = run_backtest("AAPL", "2020-01-01", "2021-06-01")
    assert result.ticker == "AAPL"
    assert isinstance(result.sharpe_ratio, float)
    assert isinstance(result.max_drawdown_pct, float)
    assert isinstance(result.win_rate, float)
    assert result.n_trades >= 0


def test_backtest_equity_curve_is_series(uptrend_df):
    with patch("trading_system.backtest.runner.fetch_bars", return_value=uptrend_df):
        result = run_backtest("AAPL", "2020-01-01", "2021-06-01")
    assert isinstance(result.equity_curve, pd.Series)
    assert len(result.equity_curve) > 0
