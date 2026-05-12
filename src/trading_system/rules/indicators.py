"""
Technical indicator wrappers using pandas-ta.
Pure functions, no side effects. Used by RuleEngine.
"""
import pandas as pd
import pandas_ta as ta


def compute_sma(close: pd.Series, period: int) -> pd.Series:
    return ta.sma(close, length=period)


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    return ta.ema(close, length=period)


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    return ta.rsi(close, length=period)


def compute_atr(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.atr(ohlcv["High"], ohlcv["Low"], ohlcv["Close"], length=period)


def compute_donchian(ohlcv: pd.DataFrame, period: int = 20) -> tuple[pd.Series, pd.Series]:
    """
    Donchian channel using previous `period` bars only.
    shift(1) avoids same-bar lookahead bias.
    Returns (upper_channel, lower_channel).
    """
    upper = ohlcv["High"].shift(1).rolling(period).max()
    lower = ohlcv["Low"].shift(1).rolling(period).min()
    return upper, lower


def compute_volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return ta.sma(volume, length=period)


def compute_market_regime(close: pd.Series, ma_period: int = 200) -> pd.Series:
    """
    Boolean Series — True when close > ma_period SMA. Use against SPY for
    market-regime gating: only allow long entries during bull regimes.
    """
    ma = ta.sma(close, length=ma_period)
    return close > ma
