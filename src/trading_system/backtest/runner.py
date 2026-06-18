from dataclasses import dataclass
import pandas as pd
from backtesting import Backtest
from loguru import logger
from trading_system.backtest.metrics import (
    calmar_ratio, cagr, max_drawdown, sharpe_ratio, sortino_ratio, win_rate,
)
from trading_system.backtest.strategy import StraterStrategy
from trading_system.data.yfinance_adapter import fetch_bars

COMMISSION = 0.0005  # 0.05% per leg (≈ bid-ask spread; Alpaca charges $0 brokerage)


@dataclass
class BacktestResult:
    ticker: str
    start: str
    end: str
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    win_rate: float
    n_trades: int
    exposure_pct: float
    equity_curve: pd.Series
    trades: pd.DataFrame  # raw _trades with a "Ticker" column (empty if none)


def build_backtest(bars: pd.DataFrame, cash: float = 10_000.0) -> Backtest:
    """Single source of truth for backtest construction — same commission and
    finalize policy everywhere, so every entry point reports comparable numbers."""
    return Backtest(
        bars, StraterStrategy,
        cash=cash, commission=COMMISSION,
        exclusive_orders=True, finalize_trades=True,
    )


def run_strategy(
    bars: pd.DataFrame,
    ticker: str,
    risk_pct: float,
    cash: float = 10_000.0,
    regime_series: pd.Series | None = None,
):
    """
    Run the shared strategy once and return (bt, stats).

    Thread-safe: parameters are passed through Backtest.run(**kwargs), which sets
    them on the per-run strategy *instance*, never on the shared class — so
    parallel backtests can't clobber each other's ticker/risk.
    """
    bt = build_backtest(bars, cash=cash)
    stats = bt.run(ticker=ticker, risk_pct=risk_pct, regime_series=regime_series)
    return bt, stats


def fetch_regime_series(start: str, end: str, ma_period: int = 200, symbol: str = "SPY") -> pd.Series:
    """Boolean market-regime series (index close > `ma_period` SMA) for gating
    long entries. Used by the ablation / regime-sweep research tools."""
    from trading_system.rules.indicators import compute_market_regime
    index = fetch_bars(symbol, start, end, interval="1d")
    return compute_market_regime(index["Close"], ma_period=ma_period)


def run_backtest(
    ticker: str,
    start: str,
    end: str,
    initial_cash: float = 10_000.0,
    risk_pct: float = 0.01,
    regime_series: pd.Series | None = None,
) -> BacktestResult:
    """Run a full backtest using the shared RuleEngine."""
    logger.info(f"Backtest: {ticker} {start} → {end} risk={risk_pct:.2%}")
    bars = fetch_bars(ticker, start, end, interval="1d")
    _, stats = run_strategy(bars, ticker, risk_pct, cash=initial_cash, regime_series=regime_series)
    equity = stats["_equity_curve"]["Equity"]
    trades_df = stats["_trades"].copy()
    if not trades_df.empty:
        trades_df["Ticker"] = ticker
    pnl = trades_df["PnL"] if not trades_df.empty else pd.Series([], dtype=float)
    total_return = float((equity.iloc[-1] - initial_cash) / initial_cash * 100)
    exposure = float(stats.get("Exposure Time [%]", 0.0))
    return BacktestResult(
        ticker=ticker,
        start=start,
        end=end,
        total_return_pct=total_return,
        cagr_pct=cagr(equity) * 100,
        sharpe_ratio=sharpe_ratio(equity),
        sortino_ratio=sortino_ratio(equity),
        max_drawdown_pct=max_drawdown(equity) * 100,
        calmar_ratio=calmar_ratio(equity),
        win_rate=win_rate(pnl) * 100,
        n_trades=len(trades_df),
        exposure_pct=exposure,
        equity_curve=equity,
        trades=trades_df,
    )
