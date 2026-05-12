"""
Pure pandas/numpy metrics module for performance statistics.
No Backtrader dependencies — used by backtester's runner to compute performance.
Consistent between backtesting and live trading.
"""

from __future__ import annotations

import math
import pandas as pd
import numpy as np


def _annualization_factor(freq: str) -> float:
    """
    Get the annualization factor (square root of periods per year) for a given frequency.

    Args:
        freq: One of "daily", "hourly", "minute_15", "minute_5", "minute_1"

    Returns:
        Square root of annualized periods.
    """
    if freq == "daily":
        return math.sqrt(252)
    elif freq == "hourly":
        return math.sqrt(252 * 6.5)  # Equities trading hours per day
    elif freq == "minute_15":
        return math.sqrt(252 * 6.5 * 4)
    elif freq == "minute_5":
        return math.sqrt(252 * 6.5 * 12)
    elif freq == "minute_1":
        return math.sqrt(252 * 6.5 * 60)
    else:
        # Default to daily
        return math.sqrt(252)


def sharpe_ratio(equity: pd.Series, freq: str = "daily", risk_free: float = 0.0) -> float:
    """
    Calculate Sharpe ratio of equity series.

    Args:
        equity: Series of equity values indexed by datetime.
        freq: Frequency of data ("daily", "hourly", "minute_15", "minute_5", "minute_1").
        risk_free: Risk-free rate (annual), default 0.0.

    Returns:
        Sharpe ratio (float). Returns 0.0 if returns have no volatility.
    """
    returns = equity.pct_change().dropna()

    if len(returns) < 3 or returns.std() == 0:
        return 0.0

    ann_factor = _annualization_factor(freq)
    excess_return = returns.mean() - risk_free / 252  # Assuming daily data
    return float((excess_return / returns.std()) * ann_factor)


def sortino_ratio(equity: pd.Series, freq: str = "daily", risk_free: float = 0.0) -> float:
    """
    Calculate Sortino ratio of equity series (uses downside volatility only).

    Args:
        equity: Series of equity values indexed by datetime.
        freq: Frequency of data ("daily", "hourly", "minute_15", "minute_5", "minute_1").
        risk_free: Risk-free rate (annual), default 0.0.

    Returns:
        Sortino ratio (float). Returns 0.0 if no positive returns exist.
    """
    returns = equity.pct_change().dropna()

    if len(returns) < 3:
        return 0.0

    mean_return = returns.mean()

    # If mean return is not positive, Sortino doesn't make sense
    if mean_return <= 0:
        return 0.0

    # Downside deviation: std of negative returns only
    negative_returns = returns[returns < 0]

    # If no negative returns, downside volatility is 0 — treat as very high Sortino
    if len(negative_returns) == 0:
        return float(mean_return) * _annualization_factor(freq) / 1e-6  # Very high ratio

    downside_vol = negative_returns.std()
    if downside_vol == 0:
        return float(mean_return) * _annualization_factor(freq) / 1e-6  # Very high ratio

    ann_factor = _annualization_factor(freq)
    excess_return = mean_return - risk_free / 252
    return float((excess_return / downside_vol) * ann_factor)


def max_drawdown(equity: pd.Series) -> float:
    """
    Calculate maximum drawdown as a negative value.

    Args:
        equity: Series of equity values indexed by datetime.

    Returns:
        Max drawdown as a negative float (0.0 if no drawdown).
    """
    if len(equity) < 2:
        return 0.0

    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def cagr(equity: pd.Series) -> float:
    """
    Calculate Compound Annual Growth Rate.

    Args:
        equity: Series of equity values indexed by datetime.

    Returns:
        CAGR as a float (annualized return).
    """
    if len(equity) < 2:
        return 0.0

    start_date = equity.index[0]
    end_date = equity.index[-1]

    # Calculate years elapsed
    years = (end_date - start_date).total_seconds() / (365.25 * 24 * 3600)

    if years <= 0:
        return 0.0

    start_value = equity.iloc[0]
    end_value = equity.iloc[-1]

    return float((end_value / start_value) ** (1 / years) - 1)


def calmar_ratio(equity: pd.Series) -> float:
    """
    Calculate Calmar ratio (CAGR / abs(max drawdown)).

    Args:
        equity: Series of equity values indexed by datetime.

    Returns:
        Calmar ratio as a float. Returns 0.0 if CAGR is 0 or returns a very high value if no drawdown.
    """
    annual_return = cagr(equity)
    mdd = max_drawdown(equity)

    if annual_return <= 0:
        return 0.0

    # If no drawdown (perfectly ascending), return a very high ratio
    if mdd == 0:
        return float(annual_return) / 1e-6  # Very high ratio

    return float(annual_return / abs(mdd))


def win_rate(pnl_series: pd.Series) -> float:
    """
    Calculate win rate (fraction of winning trades).

    Args:
        pnl_series: Series of P&L values (positive = win, negative = loss).

    Returns:
        Win rate as a float between 0.0 and 1.0.
    """
    if len(pnl_series) == 0:
        return 0.0

    wins = (pnl_series > 0).sum()
    return float(wins / len(pnl_series))
