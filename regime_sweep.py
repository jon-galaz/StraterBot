#!/usr/bin/env python
"""
Sweep SPY regime MA period to find the optimal setting.

Compares: no regime, 50d, 100d, 150d, 200d SMA on SPY.

Usage:
    uv run python regime_sweep.py [START] [END] [--tickers AAPL,...]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from loguru import logger

from trading_system.backtest import strategy as strat_mod
from trading_system.backtest.runner import run_backtest
from trading_system.config import DEFAULT_UNIVERSE
from trading_system.data.yfinance_adapter import fetch_bars
from trading_system.rules.indicators import compute_market_regime

# Keep trail + entry-dist ON for the sweep (both confirmed net-positive).
strat_mod._LOCK_R_THRESHOLD  = 1.0
strat_mod._TRAIL_R_THRESHOLD = 2.0
strat_mod._INITIAL_TP_R      = 5.0

from trading_system.rules import engine as engine_mod
engine_mod._ENTRY_DISTANCE_ATR_CAP = 1.0


def build_regime_series(start: str, end: str, ma_period: int | None) -> pd.Series | None:
    if ma_period is None:
        return None
    spy = fetch_bars("SPY", start, end, interval="1d")
    return compute_market_regime(spy["Close"], ma_period=ma_period)


def _all_true(real_spy, start, end):
    spy = fetch_bars("SPY", start, end, interval="1d")
    return pd.Series(True, index=compute_market_regime(spy["Close"]).index)


def run_with_regime(tickers, start, end, regime_series):
    def _one(t):
        try:
            return t, run_backtest(t, start, end, risk_pct=0.01,
                                   regime_series=regime_series)
        except Exception as exc:
            logger.warning(f"regime_sweep {t}: {exc}")
            return t, exc

    sharpes, returns, trades = [], [], []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_one, t) for t in tickers]
        for fut in as_completed(futs):
            t, r = fut.result()
            if isinstance(r, Exception):
                continue
            sharpes.append(r.sharpe_ratio)
            returns.append(r.total_return_pct)
            trades.append(r.n_trades)

    n = len(sharpes) or 1
    return dict(
        avg_sharpe=sum(sharpes) / n,
        avg_return=sum(returns) / n,
        total_trades=sum(trades),
        tickers_ok=len(sharpes),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start",     nargs="?", default="2020-01-01")
    parser.add_argument("end",       nargs="?", default="2024-12-31")
    parser.add_argument("--tickers", type=str,  default=None)
    args = parser.parse_args()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")]
        if args.tickers else DEFAULT_UNIVERSE
    )

    print(f"Regime sweep — {args.start} → {args.end} | {len(tickers)} tickers")
    print("(trail=ON, entry-dist=ON for all runs)\n")

    periods = [None, 50, 100, 150, 200]
    rows = []
    spy_cache = {}  # avoid re-downloading SPY repeatedly

    for ma in periods:
        label = f"SPY>{ma}d MA" if ma else "No regime gate"
        print(f"  running: {label:<20}", end="  ", flush=True)

        if ma is None:
            # All-true series: never gates
            spy = fetch_bars("SPY", args.start, args.end, interval="1d")
            regime_series = pd.Series(True, index=compute_market_regime(spy["Close"]).index)
        else:
            spy = fetch_bars("SPY", args.start, args.end, interval="1d")
            regime_series = compute_market_regime(spy["Close"], ma_period=ma)

        pct_on = regime_series.mean() * 100
        result = run_with_regime(tickers, args.start, args.end, regime_series)
        print(f"regime_on={pct_on:.0f}%  sharpe={result['avg_sharpe']:+.3f}  "
              f"return={result['avg_return']:+.1f}%  trades={result['total_trades']}")
        rows.append({"MA period": label, "Regime on %": f"{pct_on:.0f}%", **result})

    df = pd.DataFrame(rows).set_index("MA period")
    # Keep the raw numeric Sharpes for the decision before formatting for display.
    raw_sharpes = df["avg_sharpe"].tolist()
    df["avg_sharpe"] = df["avg_sharpe"].map("{:+.3f}".format)
    df["avg_return"] = df["avg_return"].map("{:+.1f}%".format)
    print("\n" + "─" * 70)
    print(df.to_string())
    print("─" * 70)

    # Decision: is any filtered version better than no-filter?
    no_gate_sharpe = raw_sharpes[0]
    best_filtered = max(raw_sharpes[1:])
    best_label = df.index[raw_sharpes.index(best_filtered)]
    if best_filtered > no_gate_sharpe + 0.01:
        print(f"\n✓ KEEP regime filter — best: {best_label}  ({best_filtered:+.3f} > {no_gate_sharpe:+.3f})")
    else:
        print(f"\n✗ DROP regime filter — no filtered variant beats no-gate ({no_gate_sharpe:+.3f})")


if __name__ == "__main__":
    main()
