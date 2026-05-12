#!/usr/bin/env python
"""
Ablation study — isolate which Tier 1 change caused the Sharpe regression.

Toggles each change one at a time, holding the other two ON, and reports
average Sharpe across the universe. Compare to:
  • ALL_OFF   — pre-Tier-1 baseline
  • ALL_ON    — current state (the regression)

Usage:
    uv run python ablation.py [START] [END] [--tickers AAPL,...]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from trading_system.backtest import strategy as strat_mod
from trading_system.backtest.runner import fetch_regime_series, run_backtest
from trading_system.rules import engine as engine_mod

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD",
    "GOOGL", "META", "NFLX",
    "AMZN", "TSLA", "HD", "LULU",
    "COST", "KO", "WMT",
    "JPM", "V", "MA",
    "JNJ", "UNH", "LLY",
    "CAT", "URI", "AXON",
    "XOM", "CVX",
    "FCX", "NEE", "AMT",
    "ELF",
]

# Snapshot of production constants we will toggle.
_ORIG = {
    "LOCK":  strat_mod._LOCK_R_THRESHOLD,
    "TRAIL": strat_mod._TRAIL_R_THRESHOLD,
    "TP":    strat_mod._INITIAL_TP_R,
    "EDIST": engine_mod._ENTRY_DISTANCE_ATR_CAP,
}


def configure(*, trail: bool, entry_dist: bool) -> None:
    """Patch module-level constants in place to toggle Tier 1 knobs."""
    if trail:
        strat_mod._LOCK_R_THRESHOLD  = _ORIG["LOCK"]
        strat_mod._TRAIL_R_THRESHOLD = _ORIG["TRAIL"]
        strat_mod._INITIAL_TP_R      = _ORIG["TP"]
    else:
        # Disable trailing entirely; revert to fixed 2R TP (pre-Tier-1 behaviour).
        strat_mod._LOCK_R_THRESHOLD  = float("inf")
        strat_mod._TRAIL_R_THRESHOLD = float("inf")
        strat_mod._INITIAL_TP_R      = 2.0
    engine_mod._ENTRY_DISTANCE_ATR_CAP = (
        _ORIG["EDIST"] if entry_dist else float("inf")
    )


def all_true_regime(real: pd.Series) -> pd.Series:
    """A regime series that never gates — same index as the real one."""
    return pd.Series(True, index=real.index)


def run_scenario(name, tickers, start, end, *, regime, trail, entry_dist, real_regime):
    configure(trail=trail, entry_dist=entry_dist)
    series = real_regime if regime else all_true_regime(real_regime)

    def _one(t):
        try:
            return t, run_backtest(t, start, end, risk_pct=0.01, regime_series=series)
        except Exception as exc:
            return t, exc

    sharpes, returns, trade_counts = [], [], []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for fut in as_completed([pool.submit(_one, t) for t in tickers]):
            t, r = fut.result()
            if isinstance(r, Exception):
                continue
            sharpes.append(r.sharpe_ratio)
            returns.append(r.total_return_pct)
            trade_counts.append(r.n_trades)

    n = len(sharpes) or 1
    return {
        "scenario": name,
        "avg_sharpe": sum(sharpes) / n,
        "avg_return": sum(returns) / n,
        "total_trades": sum(trade_counts),
        "tickers_run": len(sharpes),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start",     nargs="?", default="2020-01-01")
    parser.add_argument("end",       nargs="?", default="2024-12-31")
    parser.add_argument("--tickers", type=str, default=None)
    args = parser.parse_args()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")]
        if args.tickers else DEFAULT_UNIVERSE
    )

    print(f"Ablation — {args.start} → {args.end} | {len(tickers)} tickers\n")
    real_regime = fetch_regime_series(args.start, args.end)

    scenarios = [
        # name,            regime, trail, entry_dist
        ("ALL_OFF (pre-T1)", False, False, False),
        ("regime only",      True,  False, False),
        ("trail only",       False, True,  False),
        ("entry-dist only",  False, False, True),
        ("ALL_ON (current)", True,  True,  True),
        # Leave-one-out — which knob, when removed, RECOVERS Sharpe?
        ("ALL_ON − regime",      False, True,  True),
        ("ALL_ON − trail",       True,  False, True),
        ("ALL_ON − entry-dist",  True,  True,  False),
    ]

    rows = []
    for name, regime, trail, entry_dist in scenarios:
        print(f"  running: {name:<22}  regime={regime!s:<5}  trail={trail!s:<5}  edist={entry_dist}")
        rows.append(run_scenario(
            name, tickers, args.start, args.end,
            regime=regime, trail=trail, entry_dist=entry_dist,
            real_regime=real_regime,
        ))

    # Restore production state.
    configure(trail=True, entry_dist=True)

    df = pd.DataFrame(rows).set_index("scenario")
    df["avg_sharpe"] = df["avg_sharpe"].map(lambda v: f"{v:+.3f}")
    df["avg_return"] = df["avg_return"].map(lambda v: f"{v:+.1f}%")
    print("\n" + "─" * 68)
    print(df.to_string())
    print("─" * 68)


if __name__ == "__main__":
    main()
