import pandas as pd
import yfinance as yf
from loguru import logger


def fetch_bars(
    ticker: str,
    start: str,
    end: str,
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Download OHLCV bars from Yahoo Finance (for backtesting only).
    For live/paper trading use src/trading_system/data/alpaca_adapter.py.

    Raises:
        ValueError: If no data returned.
    """
    logger.debug(f"Fetching {ticker} {start}→{end} interval={interval}")
    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False, auto_adjust=True)

    if df.empty:
        raise ValueError(f"No data returned for {ticker} between {start} and {end}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df[["Open", "High", "Low", "Close", "Volume"]].copy()
