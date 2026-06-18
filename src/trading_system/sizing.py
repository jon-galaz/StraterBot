"""
Per-ticker position sizing — quarter-Kelly with floors/caps.

Shared by backtest (portfolio.py) and live executor so sizing stays aligned.
The live executor reads risk_pct from a JSON cache that the monthly
recompute job writes; new universe entries fall back to DEFAULT_RISK_PCT.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from loguru import logger

MIN_TRADES = 20
MIN_RISK_PCT = 0.005
MAX_RISK_PCT = 0.02
KELLY_MULTIPLIER = 0.25
DEFAULT_RISK_PCT = 0.01


@dataclass
class KellyStats:
    ticker: str
    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    b: float
    kelly_full: float
    kelly_quarter: float
    risk_pct: float
    keep: bool


def kelly_fraction(trades: pd.DataFrame) -> tuple[float, float, float, float]:
    """
    Returns (kelly_full, win_rate, avg_win, avg_loss) for a trades DataFrame.

    Trades DataFrame must have a "PnL" column. Kelly is negative when there is
    no edge — caller decides what to do with that.
    """
    if trades.empty:
        return 0.0, 0.0, 0.0, 0.0
    wins = trades[trades["PnL"] > 0]
    losses = trades[trades["PnL"] < 0]
    # Scratch trades (PnL == 0) are neither wins nor losses — excluding them from
    # both keeps p and avg_loss internally consistent (p over decisive trades).
    n_decisive = len(wins) + len(losses)
    if len(wins) == 0 or len(losses) == 0 or n_decisive == 0:
        return 0.0, 0.0, 0.0, 0.0
    p = len(wins) / n_decisive
    avg_win = float(wins["PnL"].mean())
    avg_loss = abs(float(losses["PnL"].mean()))
    if avg_loss <= 0:
        return 0.0, p, avg_win, 0.0
    b = avg_win / avg_loss
    q = 1.0 - p
    f = (b * p - q) / b
    return f, p, avg_win, avg_loss


def compute_kelly_stats(ticker: str, trades: pd.DataFrame) -> KellyStats:
    """
    Build per-ticker sizing decision: drop if too few trades or no edge,
    otherwise quarter-Kelly clamped to [MIN_RISK_PCT, MAX_RISK_PCT].
    """
    n = len(trades)
    f_full, p, avg_win, avg_loss = kelly_fraction(trades)
    f_quarter = KELLY_MULTIPLIER * f_full
    b = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    if n < MIN_TRADES:
        # Not enough data to judge edge — fall back to conservative default.
        risk = DEFAULT_RISK_PCT
        keep = True
    elif f_full <= 0:
        # Enough trades AND non-positive edge — drop the ticker.
        risk = 0.0
        keep = False
    else:
        risk = max(MIN_RISK_PCT, min(MAX_RISK_PCT, f_quarter))
        keep = True

    return KellyStats(
        ticker=ticker,
        n_trades=n,
        win_rate=p,
        avg_win=avg_win,
        avg_loss=avg_loss,
        b=b,
        kelly_full=f_full,
        kelly_quarter=f_quarter,
        risk_pct=risk,
        keep=keep,
    )


def kelly_table(all_trades: pd.DataFrame, tickers: list[str]) -> list[KellyStats]:
    """Compute KellyStats per ticker from a combined trades DataFrame."""
    return [
        compute_kelly_stats(t, all_trades[all_trades["Ticker"] == t] if not all_trades.empty else pd.DataFrame())
        for t in tickers
    ]


# ── Persistence ──────────────────────────────────────────────────────────────

def sizing_path(database_url: str) -> Path:
    """Derive sizing.json path from DATABASE_URL — same dir as the DB file."""
    if database_url.startswith("sqlite:////"):
        db_path = Path("/" + database_url[len("sqlite:////"):])
    elif database_url.startswith("sqlite:///"):
        db_path = Path(database_url[len("sqlite:///"):])
    else:
        db_path = Path("trading.db")
    return db_path.parent / "sizing.json"


def load_risk_map(database_url: str) -> dict[str, float]:
    p = sizing_path(database_url)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        logger.error(f"Sizing: failed to read {p}: {exc}")
        return {}


def save_risk_map(risk_map: dict[str, float], database_url: str) -> Path:
    p = sizing_path(database_url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(risk_map, indent=2, sort_keys=True))
    return p


def get_risk_pct(ticker: str, database_url: str) -> float:
    """Risk % for a ticker — falls back to DEFAULT_RISK_PCT if not yet computed."""
    return load_risk_map(database_url).get(ticker, DEFAULT_RISK_PCT)


# ── Recompute job ────────────────────────────────────────────────────────────

def compute_universe_sizing(
    tickers: list[str],
    lookback_days: int = 1825,
) -> tuple[dict[str, float], list[KellyStats]]:
    """
    Run a `lookback_days`-day backtest per ticker at uniform 1% risk,
    compute per-ticker Kelly, return (risk_map, stats).

    Tickers with insufficient trades or non-positive Kelly get risk_pct = 0.
    """
    from trading_system.backtest.runner import run_strategy
    from trading_system.data.yfinance_adapter import fetch_bars

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    end_s, start_s = end.isoformat(), start.isoformat()

    all_trades_list: list[pd.DataFrame] = []
    for ticker in tickers:
        try:
            bars = fetch_bars(ticker, start_s, end_s)
            _, stats_obj = run_strategy(bars, ticker, risk_pct=0.01, cash=10_000.0)
            df = stats_obj["_trades"].copy()
            if not df.empty:
                df["Ticker"] = ticker
                all_trades_list.append(df)
        except Exception as exc:
            logger.warning(f"Sizing: backtest failed for {ticker}: {exc}")

    combined = (
        pd.concat(all_trades_list, ignore_index=True)
        if all_trades_list else pd.DataFrame()
    )
    stats = kelly_table(combined, tickers)
    risk_map = {s.ticker: s.risk_pct for s in stats}
    return risk_map, stats
