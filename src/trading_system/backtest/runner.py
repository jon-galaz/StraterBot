from dataclasses import dataclass
import pandas as pd
from backtesting import Backtest
from loguru import logger
from trading_system.backtest.metrics import (
    calmar_ratio, cagr, max_drawdown, sharpe_ratio, sortino_ratio, win_rate,
)
from trading_system.backtest.strategy import StraterStrategy
from trading_system.data.yfinance_adapter import fetch_bars


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
    equity_curve: pd.Series


def run_backtest(
    ticker: str,
    start: str,
    end: str,
    initial_cash: float = 10_000.0,
    risk_pct: float = 0.01,
) -> BacktestResult:
    """Run a full backtest using the shared RuleEngine."""
    logger.info(f"Backtest: {ticker} {start} → {end} risk={risk_pct:.2%}")
    bars = fetch_bars(ticker, start, end, interval="1d")
    bt = Backtest(bars, StraterStrategy, cash=initial_cash, commission=0.0, exclusive_orders=True)
    bt._strategy.ticker = ticker
    bt._strategy.risk_pct = risk_pct
    stats = bt.run()
    equity = stats["_equity_curve"]["Equity"]
    trades_df = stats["_trades"]
    pnl = trades_df["PnL"] if not trades_df.empty else pd.Series([], dtype=float)
    total_return = float((equity.iloc[-1] - initial_cash) / initial_cash * 100)
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
        equity_curve=equity,
    )
