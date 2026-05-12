#!/usr/bin/env python
"""
Interactive chart with drawing tools for trader analysis.

Usage:
    uv run python chart.py TICKER [START] [END] [--annotations FILE]

Examples:
    uv run python chart.py NVDA
    uv run python chart.py NVDA 2020-01-01 2024-12-31
    uv run python chart.py NVDA --annotations charts/NVDA_annotations.json

Drawing tools in the chart toolbar:
    ✏  Draw line          □  Draw rectangle
    ◯  Draw circle        ~  Draw freehand
    ⌫  Erase shape

To save your drawings:
    Click the 💾 button in the chart → saves annotations/TICKER.json
    Next run automatically loads them back.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trading_system.backtest.runner import run_backtest
from trading_system.data.yfinance_adapter import fetch_bars
from trading_system.rules.indicators import compute_donchian, compute_ema

ANNOTATIONS_DIR = Path("charts")

# ── Colour palette ────────────────────────────────────────────────────────────
C_UP        = "#26a69a"
C_DOWN      = "#ef5350"
C_EMA       = "#ffa726"
C_DCH_UPPER = "#42a5f5"
C_DCH_LOWER = "#42a5f5"
C_ENTRY     = "#00e676"
C_EXIT      = "#ff1744"
C_VOLUME    = "#546e7a"


def _annotations_path(ticker: str) -> Path:
    ANNOTATIONS_DIR.mkdir(exist_ok=True)
    return ANNOTATIONS_DIR / f"{ticker}_annotations.json"


def _load_annotations(path: Path) -> dict:
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return {"shapes": [], "annotations": []}


def build_chart(
    ticker: str,
    start: str,
    end: str,
    saved: dict,
) -> go.Figure:
    print(f"Fetching {ticker} {start} → {end} ...")
    bars = fetch_bars(ticker, start, end)

    # ── Indicators ────────────────────────────────────────────────────────────
    ema_50 = compute_ema(bars["Close"], period=50)
    dc_high, dc_low = compute_donchian(bars, period=20)

    # ── Backtest — get trade entries/exits ────────────────────────────────────
    print("Running backtest for signal markers ...")
    result = run_backtest(ticker, start, end)
    trades = result.equity_curve  # we need the _trades from stats

    # Re-run to grab the raw stats (runner doesn't expose _trades)
    from backtesting import Backtest
    from trading_system.backtest.strategy import StraterStrategy
    bt = Backtest(bars, StraterStrategy, cash=10_000, commission=0.0, exclusive_orders=True)
    bt._strategy.ticker = ticker
    stats = bt.run()
    trades_df = stats["_trades"]

    # ── Layout: price (row 1) + volume (row 2) ────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.02,
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=bars.index,
        open=bars["Open"], high=bars["High"],
        low=bars["Low"],   close=bars["Close"],
        name=ticker,
        increasing_line_color=C_UP,
        decreasing_line_color=C_DOWN,
        increasing_fillcolor=C_UP,
        decreasing_fillcolor=C_DOWN,
    ), row=1, col=1)

    # EMA-50
    fig.add_trace(go.Scatter(
        x=bars.index, y=ema_50,
        name="EMA 50",
        line=dict(color=C_EMA, width=1.5, dash="solid"),
        opacity=0.85,
    ), row=1, col=1)

    # Donchian channels (filled band)
    fig.add_trace(go.Scatter(
        x=bars.index, y=dc_high,
        name="Donchian High",
        line=dict(color=C_DCH_UPPER, width=1, dash="dot"),
        opacity=0.6,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=bars.index, y=dc_low,
        name="Donchian Low",
        fill="tonexty",
        fillcolor="rgba(66,165,245,0.07)",
        line=dict(color=C_DCH_LOWER, width=1, dash="dot"),
        opacity=0.6,
    ), row=1, col=1)

    # Entry / exit markers from backtest
    if not trades_df.empty:
        fig.add_trace(go.Scatter(
            x=trades_df["EntryTime"],
            y=trades_df["EntryPrice"],
            mode="markers",
            name="Entry",
            marker=dict(symbol="triangle-up", size=12, color=C_ENTRY,
                        line=dict(color="white", width=1)),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=trades_df["ExitTime"],
            y=trades_df["ExitPrice"],
            mode="markers",
            name="Exit",
            marker=dict(symbol="triangle-down", size=12, color=C_EXIT,
                        line=dict(color="white", width=1)),
        ), row=1, col=1)

    # Volume bars
    colors = [C_UP if c >= o else C_DOWN
              for c, o in zip(bars["Close"], bars["Open"])]
    fig.add_trace(go.Bar(
        x=bars.index, y=bars["Volume"],
        name="Volume",
        marker_color=colors,
        opacity=0.6,
        showlegend=False,
    ), row=2, col=1)

    # ── Apply saved shapes / annotations ─────────────────────────────────────
    layout_extra = {}
    if saved.get("shapes"):
        layout_extra["shapes"] = saved["shapes"]
    if saved.get("annotations"):
        layout_extra["annotations"] = saved["annotations"]

    # ── Styling ───────────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(
            text=f"<b>{ticker}</b>  {start} → {end}"
                 f"  |  Return {result.total_return_pct:+.1f}%"
                 f"  Sharpe {result.sharpe_ratio:.2f}"
                 f"  Trades {result.n_trades}",
            font=dict(size=14),
        ),
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02, x=0),
        margin=dict(l=60, r=20, t=80, b=40),
        dragmode="zoom",
        height=750,
        **layout_extra,
    )
    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_xaxes(showspikes=True, spikemode="across", row=2, col=1)

    return fig


# ── Custom HTML wrapper with Save button ──────────────────────────────────────
_SAVE_JS = """
<script>
(function() {
  // Wait for Plotly to render, then inject the Save button
  function injectSaveButton() {
    var toolbar = document.querySelector('.modebar-group');
    if (!toolbar) { setTimeout(injectSaveButton, 300); return; }

    var btn = document.createElement('a');
    btn.className = 'modebar-btn';
    btn.title = 'Save annotations';
    btn.style.cssText = 'cursor:pointer; color:#a0a0a0; font-size:14px; padding:3px 6px;';
    btn.innerHTML = '&#128190;';  // 💾

    btn.addEventListener('click', function() {
      var gd = document.querySelector('.js-plotly-plot');
      var data = {
        shapes:      (gd.layout.shapes      || []),
        annotations: (gd.layout.annotations || [])
      };
      var blob = new Blob([JSON.stringify(data, null, 2)],
                          {type: 'application/json'});
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = '__TICKER___annotations.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      btn.style.color = '#00e676';
      setTimeout(function(){ btn.style.color = '#a0a0a0'; }, 800);
    });

    toolbar.parentNode.insertBefore(btn, toolbar);
  }
  injectSaveButton();
})();
</script>
"""


def main():
    parser = argparse.ArgumentParser(description="Interactive trading chart with drawing tools")
    parser.add_argument("ticker", type=str.upper)
    parser.add_argument("start", nargs="?", default="2020-01-01")
    parser.add_argument("end",   nargs="?", default="2024-12-31")
    parser.add_argument("--annotations", type=Path, default=None,
                        help="Path to annotations JSON (default: charts/TICKER_annotations.json)")
    args = parser.parse_args()

    ann_path = args.annotations or _annotations_path(args.ticker)
    saved = _load_annotations(ann_path)
    if saved["shapes"] or saved["annotations"]:
        n = len(saved["shapes"]) + len(saved["annotations"])
        print(f"Loaded {n} saved drawings from {ann_path}")

    try:
        fig = build_chart(args.ticker, args.start, args.end, saved)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # ── Write HTML with drawing tools + Save button ───────────────────────────
    ANNOTATIONS_DIR.mkdir(exist_ok=True)
    out = ANNOTATIONS_DIR / f"{args.ticker}_{args.start}_{args.end}.html"

    html = fig.to_html(
        full_html=True,
        include_plotlyjs=True,
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "modeBarButtonsToAdd": [
                "drawline",
                "drawopenpath",
                "drawclosedpath",
                "drawcircle",
                "drawrect",
                "eraseshape",
            ],
            "editable": True,
        },
    )
    # Inject save button JS, replacing ticker placeholder
    save_js = _SAVE_JS.replace("__TICKER__", args.ticker)
    html = html.replace("</body>", save_js + "\n</body>")
    out.write_text(html)

    print(f"\nChart saved → {out}")
    print(f"Annotations will save to: {ann_path.resolve()}")
    print("\nDrawing tools: line · rectangle · circle · freehand · erase")
    print("Click 💾 in the toolbar to download your drawings as JSON.\n")

    import webbrowser
    webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
