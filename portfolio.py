#!/usr/bin/env python
"""
Portfolio backtest — runs all tickers in parallel.

Layout:
  Single combined portfolio equity curve with BUY/SELL markers labelled by ticker
  Lower half: full trade list table (all tickers, sorted by date)

Usage:
    uv run python portfolio.py [START] [END] [--tickers AAPL,NVDA,...] [--no-plot]

Examples:
    uv run python portfolio.py
    uv run python portfolio.py 2020-01-01 2024-12-31
    uv run python portfolio.py 2022-01-01 2024-12-31 --tickers NVDA,AAPL,MSFT
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trading_system.backtest.runner import BacktestResult, run_backtest
from trading_system.config import DEFAULT_UNIVERSE
from trading_system.sizing import KellyStats, kelly_table

CHARTS_DIR = Path("charts")

_PALETTE = [
    "#42a5f5", "#ef5350", "#66bb6a", "#ffa726", "#ab47bc",
    "#26c6da", "#ff7043", "#8d6e63", "#78909c", "#ec407a",
]
C_ENTRY = "#00e676"
C_EXIT  = "#ff1744"


# ── Backtest runner ───────────────────────────────────────────────────────────

def _resolve_risk(risk_pct, ticker):
    """risk_pct may be a float (uniform) or dict (per-ticker)."""
    if isinstance(risk_pct, dict):
        return risk_pct.get(ticker, 0.01)
    return risk_pct


def _run_one(ticker, start, end, cash=10_000.0, risk_pct=0.01):
    try:
        r = _resolve_risk(risk_pct, ticker)
        return ticker, run_backtest(ticker, start, end, initial_cash=cash, risk_pct=r)
    except Exception as exc:
        return ticker, exc


def run_portfolio(tickers, start, end, cash=10_000.0, risk_pct=0.01):
    results = {}
    label = f"{risk_pct:.1%}" if isinstance(risk_pct, float) else "per-ticker Kelly"
    print(f"\nRunning {len(tickers)} backtests in parallel "
          f"({start} → {end}, cash=${cash:,.0f}, risk={label}) ...\n")
    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as pool:
        futures = {
            pool.submit(_run_one, t, start, end, cash, risk_pct): t
            for t in tickers
        }
        for future in as_completed(futures):
            ticker, result = future.result()
            if isinstance(result, Exception):
                print(f"  x {ticker:8}  ERROR: {result}")
            else:
                results[ticker] = result
                print(f"  + {ticker:8}  return={result.total_return_pct:+6.1f}%  "
                      f"Sharpe={result.sharpe_ratio:.2f}  trades={result.n_trades}")
    return results


# ── Summary table (terminal) ──────────────────────────────────────────────────

def print_summary(results):
    rows = []
    for ticker, r in sorted(results.items(), key=lambda x: x[1].total_return_pct, reverse=True):
        rows.append({
            "Ticker":   ticker,
            "Return":   f"{r.total_return_pct:+.1f}%",
            "CAGR":     f"{r.cagr_pct:+.1f}%",
            "Sharpe":   f"{r.sharpe_ratio:.2f}",
            "Max DD":   f"{r.max_drawdown_pct:.1f}%",
            "Win Rate": f"{r.win_rate:.0f}%",
            "Exposure": f"{r.exposure_pct:.0f}%",
            "Trades":   r.n_trades,
        })
    df = pd.DataFrame(rows).set_index("Ticker")
    avg_ret = sum(r.total_return_pct for r in results.values()) / len(results)
    avg_sr  = sum(r.sharpe_ratio     for r in results.values()) / len(results)
    avg_exp = sum(r.exposure_pct     for r in results.values()) / len(results)
    df.loc["AVG"] = {"Return": f"{avg_ret:+.1f}%", "CAGR": "-",
                     "Sharpe": f"{avg_sr:.2f}", "Max DD": "-",
                     "Win Rate": "-", "Exposure": f"{avg_exp:.0f}%", "Trades": "-"}
    print("\n" + "-" * 64)
    print(df.to_string())
    print("-" * 64 + "\n")


# ── Kelly summary ─────────────────────────────────────────────────────────────

def print_kelly_table(stats: list) -> None:
    rows = []
    for s in sorted(stats, key=lambda x: x.kelly_full, reverse=True):
        rows.append({
            "Ticker":       s.ticker,
            "Trades":       s.n_trades,
            "Win %":        f"{s.win_rate * 100:.0f}%",
            "Avg W/L (b)":  f"{s.b:.2f}",
            "Kelly f*":     f"{s.kelly_full * 100:+.1f}%",
            "¼-Kelly":      f"{s.kelly_quarter * 100:+.1f}%",
            "Risk used":    f"{s.risk_pct * 100:.2f}%" if s.keep else "DROPPED",
        })
    df = pd.DataFrame(rows).set_index("Ticker")
    print("\n" + "─" * 68)
    print("Per-ticker Kelly sizing (clamped to [0.5%, 2%], drop if f* ≤ 0)")
    print("─" * 68)
    print(df.to_string())
    print("─" * 68)


# ── Collect all trades ────────────────────────────────────────────────────────

def combine_trades(results: dict) -> pd.DataFrame:
    """Build one trade table from the trades already computed by each backtest.

    No re-running: run_backtest now returns the raw trades (tagged with Ticker),
    so this is the same data the metrics came from — same commission, same
    finalize policy — never a divergent second run.
    """
    frames = [r.trades for r in results.values() if not r.trades.empty]
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values("EntryTime")
        .reset_index(drop=True)
    )


# ── Chart ─────────────────────────────────────────────────────────────────────

def build_portfolio_chart(results, all_trades, start, end, risk_pct=0.01):
    tickers = list(results.keys())
    color_map = {t: _PALETTE[i % len(_PALETTE)] for i, t in enumerate(tickers)}

    # ── Build single combined portfolio equity curve ──────────────────────────
    normalised = []
    for ticker, r in results.items():
        eq = r.equity_curve
        normalised.append(eq / eq.iloc[0] * 100)

    portfolio = pd.concat(normalised, axis=1).ffill().mean(axis=1)

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.55, 0.45],
        vertical_spacing=0.06,
        specs=[[{"type": "xy"}], [{"type": "table"}]],
        subplot_titles=(
            "Portfolio equity curve (average, base 100)",
            "All trades",
        ),
    )

    # Portfolio curve
    fig.add_trace(go.Scatter(
        x=portfolio.index, y=portfolio,
        name="Portfolio",
        line=dict(color="#ffffff", width=2.5),
        hovertemplate="<b>Portfolio</b>  %{y:.1f}<extra></extra>",
    ), row=1, col=1)

    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.3, row=1, col=1)

    # ── Trade markers snapped to portfolio curve Y ────────────────────────────
    if not all_trades.empty:
        # Helper: snap a timestamp to nearest portfolio index value
        def snap_y(ts):
            idx = portfolio.index.get_indexer([ts], method="nearest")[0]
            return float(portfolio.iloc[idx])

        for ticker, grp in all_trades.groupby("Ticker"):
            col = color_map.get(ticker, "#888888")

            entry_y = [snap_y(t) for t in grp["EntryTime"]]
            exit_y  = [snap_y(t) for t in grp["ExitTime"]]

            entry_hover = [
                f"<b>BUY {ticker}</b><br>${row.EntryPrice:.2f}<br>{row.EntryTime.date()}"
                for _, row in grp.iterrows()
            ]
            exit_hover = [
                f"<b>{'WIN' if row.PnL >= 0 else 'LOSS'} SELL {ticker}</b>"
                f"<br>${row.ExitPrice:.2f}  {row.ReturnPct:+.1f}%"
                f"<br>{row.ExitTime.date()}"
                for _, row in grp.iterrows()
            ]

            fig.add_trace(go.Scatter(
                x=grp["EntryTime"], y=entry_y,
                mode="markers+text",
                name=f"BUY {ticker}",
                showlegend=False,
                text=[ticker] * len(grp),
                textposition="top center",
                textfont=dict(color=col, size=9, family="monospace"),
                marker=dict(symbol="triangle-up", size=11, color=C_ENTRY,
                            line=dict(color=col, width=1.5)),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=entry_hover,
            ), row=1, col=1)

            fig.add_trace(go.Scatter(
                x=grp["ExitTime"], y=exit_y,
                mode="markers+text",
                name=f"SELL {ticker}",
                showlegend=False,
                text=[ticker] * len(grp),
                textposition="bottom center",
                textfont=dict(color=col, size=9, family="monospace"),
                marker=dict(symbol="triangle-down", size=11, color=C_EXIT,
                            line=dict(color=col, width=1.5)),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=exit_hover,
            ), row=1, col=1)

    # ── Trade list table ──────────────────────────────────────────────────────
    if not all_trades.empty:
        df = all_trades.copy()
        pnl_colors = [C_ENTRY if v >= 0 else C_EXIT for v in df["PnL"]]

        direction = ["Long" if s > 0 else "Short" for s in df["Size"]]
        sl_vals   = df["SL"].map(lambda v: f"${v:.2f}" if pd.notna(v) else "-")
        tp_vals   = df["TP"].map(lambda v: f"${v:.2f}" if pd.notna(v) else "-")
        duration  = df["Duration"].map(lambda d: f"{d.days}d")
        dir_colors = ["#42a5f5" if d == "Long" else "#ffa726" for d in direction]

        fig.add_trace(go.Table(
            header=dict(
                values=[
                    "<b>Entry date</b>", "<b>Ticker</b>", "<b>Dir</b>",
                    "<b>Shares</b>", "<b>Risk %</b>",
                    "<b>Entry $</b>", "<b>Stop loss</b>", "<b>Take profit</b>",
                    "<b>Exit $</b>", "<b>Exit date</b>", "<b>Duration</b>",
                    "<b>P&L $</b>", "<b>Return %</b>",
                ],
                fill_color="#1e1e2e",
                font=dict(color="white", size=11),
                align="center",
                height=28,
            ),
            cells=dict(
                values=[
                    df["EntryTime"].dt.strftime("%Y-%m-%d"),
                    df["Ticker"],
                    direction,
                    df["Size"].abs().astype(int),
                    [f"{_resolve_risk(risk_pct, t):.2%}" for t in df["Ticker"]],
                    df["EntryPrice"].map("${:.2f}".format),
                    sl_vals,
                    tp_vals,
                    df["ExitPrice"].map("${:.2f}".format),
                    df["ExitTime"].dt.strftime("%Y-%m-%d"),
                    duration,
                    df["PnL"].map("${:+.2f}".format),
                    df["ReturnPct"].map("{:+.1f}%".format),
                ],
                fill_color=[
                    ["#13131f"] * len(df),
                    [[color_map.get(t, "#888") for t in df["Ticker"]]],
                    [dir_colors],
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    ["#13131f"] * len(df),
                    [pnl_colors],
                    [pnl_colors],
                ],
                font=dict(color="white", size=11),
                align="center",
                height=24,
            ),
        ), row=2, col=1)

    # ── Layout ────────────────────────────────────────────────────────────────
    avg_ret    = sum(r.total_return_pct for r in results.values()) / len(results)
    avg_sharpe = sum(r.sharpe_ratio     for r in results.values()) / len(results)
    n_trades   = len(all_trades) if not all_trades.empty else 0

    fig.update_layout(
        title=dict(
            text=(f"<b>Portfolio  {start} -> {end}</b>"
                  f"  |  Avg return {avg_ret:+.1f}%"
                  f"  Avg Sharpe {avg_sharpe:.2f}"
                  f"  Total trades {n_trades}"),
            font=dict(size=14),
        ),
        template="plotly_dark",
        height=950,
        legend=dict(orientation="h", y=1.03, x=0),
        margin=dict(l=60, r=20, t=80, b=20),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Equity (base 100)", row=1, col=1)

    return fig


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start",     nargs="?", default="2020-01-01")
    parser.add_argument("end",       nargs="?", default="2024-12-31")
    parser.add_argument("--tickers", type=str,    default=None)
    parser.add_argument("--cash",    type=float,  default=10_000.0)
    parser.add_argument("--risk",    type=float,  default=1.0,
                        help="Risk per trade as percent of equity (default 1.0 = 1%%)")
    parser.add_argument("--kelly",   action="store_true",
                        help="Use per-ticker quarter-Kelly sizing (Layer 1+2). Overrides --risk.")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")]
        if args.tickers else DEFAULT_UNIVERSE
    )

    if args.kelly:
        # Pass 1: uniform 1% to harvest per-ticker edge stats
        print("=" * 64)
        print("PASS 1 — uniform 1% risk to gather per-ticker edge stats")
        print("=" * 64)
        pass1_results = run_portfolio(tickers, args.start, args.end, cash=args.cash, risk_pct=0.01)
        pass1_trades = combine_trades(pass1_results)

        # Compute Kelly per ticker
        stats = kelly_table(pass1_trades, tickers)
        print_kelly_table(stats)

        # Build per-ticker risk dict; drop tickers that fail Layer 1
        risk_pct = {s.ticker: s.risk_pct for s in stats if s.keep}
        keep_tickers = list(risk_pct.keys())
        if not keep_tickers:
            print("No tickers passed the edge filter. Aborting.")
            sys.exit(1)
        print(f"\nKept {len(keep_tickers)}/{len(tickers)} tickers: {', '.join(keep_tickers)}")

        # Pass 2: per-ticker risk
        print("\n" + "=" * 64)
        print("PASS 2 — quarter-Kelly per-ticker risk (filtered universe)")
        print("=" * 64)
        results = run_portfolio(keep_tickers, args.start, args.end,
                                cash=args.cash, risk_pct=risk_pct)
    else:
        risk_pct = args.risk / 100.0
        results = run_portfolio(tickers, args.start, args.end,
                                cash=args.cash, risk_pct=risk_pct)

    if not results:
        print("No results — all tickers failed.")
        sys.exit(1)

    print_summary(results)

    if args.no_plot:
        return

    print("Collecting trade details ...")
    all_trades = combine_trades(results)

    print("Building portfolio chart ...")
    fig = build_portfolio_chart(results, all_trades, args.start, args.end, risk_pct=risk_pct)

    CHARTS_DIR.mkdir(exist_ok=True)
    suffix = "kelly" if args.kelly else f"risk{int(args.risk*10)}"
    out = CHARTS_DIR / f"portfolio_{args.start}_{args.end}_{suffix}.html"
    fig.write_html(str(out), include_plotlyjs=True,
                   config={"scrollZoom": True, "displaylogo": False})
    print(f"Saved -> {out}\n")

    import webbrowser
    webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
