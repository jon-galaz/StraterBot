"""
Alpaca historical bar adapter — same interface as yfinance_adapter.
Used by the live scanner (Phase 2+). Backtest still uses yfinance.

Uses the IEX feed (free tier). SIP feed requires a paid subscription.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from loguru import logger

def make_data_client(api_key: str, secret_key: str) -> StockHistoricalDataClient:
    return StockHistoricalDataClient(api_key, secret_key)


def fetch_bars(
    client: StockHistoricalDataClient,
    ticker: str,
    days_back: int = 120,
    feed: str = "iex",
) -> pd.DataFrame:
    """
    Fetch the last `days_back` calendar days of daily bars for `ticker`.
    Returns a DataFrame with columns Open/High/Low/Close/Volume and a
    timezone-naive DatetimeIndex — identical shape to yfinance_adapter output.

    `feed` is "iex" (free, partial volume) or "sip" (paid, consolidated tape).
    Use "sip" in production so the volume-confirmation filter matches backtest.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    data_feed = DataFeed.SIP if feed.lower() == "sip" else DataFeed.IEX
    logger.debug(f"Fetching {ticker} from Alpaca {data_feed.value.upper()} ({days_back}d back)")

    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=data_feed,
    )
    df = client.get_stock_bars(request).df

    if df.empty:
        raise ValueError(f"No data returned from Alpaca for {ticker}")

    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel(0)

    df = df.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    df.index.name = "Date"

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df
