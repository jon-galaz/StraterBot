#!/usr/bin/env python
"""
Backtest CLI

Usage:
    uv run python bt.py TICKER [START] [END] [--cash N] [--no-plot]

Examples:
    uv run python bt.py NVDA
    uv run python bt.py NVDA 2020-01-01 2024-12-31
    uv run python bt.py NVDA 2020-01-01 2024-12-31 --cash 50000
    uv run python bt.py NVDA --no-plot
"""
import argparse
import sys

from backtesting import Backtest

from trading_system.backtest.metrics import (
    calmar_ratio, cagr, max_drawdown, sharpe_ratio, sortino_ratio, win_rate,
)
from trading_system.backtest.strategy import StraterStrategy
from trading_system.data.yfinance_adapter import fetch_bars


def main():
    parser = argparse.ArgumentParser(description="Run a Donchian Breakout backtest")
    parser.add_argument("ticker", type=str.upper)
    parser.add_argument("start", nargs="?", default="2020-01-01")
    parser.add_argument("end",   nargs="?", default="2024-12-31")
    parser.add_argument("--cash",    type=float, default=10_000.0)
    parser.add_argument("--risk",    type=float, default=1.0,
                        help="Risk per trade as percent of equity (default 1.0 = 1%%)")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    risk_pct = args.risk / 100.0
    print(f"\nFetching {args.ticker} {args.start} → {args.end} (risk={args.risk}%) ...")
    try:
        bars = fetch_bars(args.ticker, args.start, args.end)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    bt = Backtest(bars, StraterStrategy, cash=args.cash, commission=0.0, exclusive_orders=True)
    bt._strategy.ticker = args.ticker
    bt._strategy.risk_pct = risk_pct
    stats = bt.run()

    equity = stats["_equity_curve"]["Equity"]
    trades_df = stats["_trades"]
    pnl = trades_df["PnL"] if not trades_df.empty else __import__("pandas").Series([], dtype=float)

    total_return = (equity.iloc[-1] - args.cash) / args.cash * 100

    print(f"\n{'─' * 40}")
    print(f"  {args.ticker}  {args.start} → {args.end}")
    print(f"{'─' * 40}")
    print(f"  Total return   {total_return:+.1f}%")
    print(f"  CAGR           {cagr(equity) * 100:+.1f}%")
    print(f"  Sharpe         {sharpe_ratio(equity):.2f}")
    print(f"  Sortino        {sortino_ratio(equity):.2f}")
    print(f"  Max drawdown   {max_drawdown(equity) * 100:.1f}%")
    print(f"  Calmar         {calmar_ratio(equity):.2f}")
    print(f"  Win rate       {win_rate(pnl) * 100:.0f}%")
    print(f"  Trades         {len(trades_df)}")
    print(f"{'─' * 40}\n")

    if not args.no_plot:
        print("Opening chart in browser ...")
        bt.plot()


if __name__ == "__main__":
    main()
