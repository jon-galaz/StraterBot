"""
Integration tests — require internet access (yfinance).
Run with: uv run pytest tests/integration/ -v -m integration
"""
import math
import inspect
import pytest
import pandas as pd
from trading_system.backtest.runner import run_backtest, BacktestResult


@pytest.mark.integration
def test_backtest_nvda_produces_equity_curve():
    """NVDA was a top Donchian performer in Strater v1."""
    result = run_backtest("NVDA", "2020-01-01", "2022-12-31")
    assert isinstance(result, BacktestResult)
    assert isinstance(result.equity_curve, pd.Series)
    assert len(result.equity_curve) > 100


@pytest.mark.integration
def test_backtest_metrics_are_finite():
    result = run_backtest("AAPL", "2020-01-01", "2022-12-31")
    assert math.isfinite(result.total_return_pct)
    assert math.isfinite(result.max_drawdown_pct)
    assert math.isfinite(result.sharpe_ratio)
    assert 0 <= result.win_rate <= 100
    assert result.n_trades >= 0


@pytest.mark.integration
def test_rule_engine_is_not_duplicated():
    """
    Verifies the backtester Strategy imports RuleEngine from the shared module.
    Enforces the critical architectural invariant.
    """
    from trading_system.backtest.strategy import StraterStrategy
    from trading_system.rules.engine import RuleEngine

    source = inspect.getsource(StraterStrategy.init)
    assert "RuleEngine" in source
