"""
RuleEngine — single shared module for both live scanner and backtester.

CRITICAL: Never duplicate this logic. Import from here; never copy.

v1 signal: Donchian Breakout (ported from Strater v1)
  Entry (all must be true):
    1. close > previous N-bar Donchian high  (breakout, shift(1) — no lookahead)
    2. close > 50-bar EMA                    (trend filter)
    3. volume > 1.5 × 20-bar volume SMA      (volume confirmation)
  Exit signal:
    - close < previous N-bar Donchian low    (breakdown)
"""
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from trading_system.rules.indicators import (
    compute_atr,
    compute_donchian,
    compute_ema,
    compute_volume_sma,
)

_DONCHIAN_PERIOD = 20
_EMA_PERIOD = 50
_VOLUME_SMA_PERIOD = 20
_VOLUME_CONFIRM_MULT = 1.5
_WARMUP_BARS = 60
_ENTRY_DISTANCE_ATR_CAP = 1.0  # reject breakouts > 1*ATR above the level


@dataclass
class Signal:
    ticker: str
    direction: str
    price: float
    donchian_high: float
    donchian_low: float
    ema_50: float
    atr: float
    timestamp: datetime


class RuleEngine:
    def __init__(self, ticker: str = "UNKNOWN", donchian_period: int = _DONCHIAN_PERIOD) -> None:
        self.ticker = ticker
        self.donchian_period = donchian_period

    def evaluate(self, bars: pd.DataFrame) -> Signal | None:
        """
        Evaluate entry conditions on full OHLCV history.
        Returns Signal if all conditions met on last bar, else None.
        """
        if len(bars) < _WARMUP_BARS:
            return None

        close = bars["Close"]
        volume = bars["Volume"]

        donchian_high, donchian_low = compute_donchian(bars, period=self.donchian_period)
        ema_50 = compute_ema(close, period=_EMA_PERIOD)
        vol_sma = compute_volume_sma(volume, period=_VOLUME_SMA_PERIOD)
        atr = compute_atr(bars, period=14)

        last_close = float(close.iloc[-1])
        last_dch = donchian_high.iloc[-1]
        last_dcl = donchian_low.iloc[-1]
        last_ema = ema_50.iloc[-1]
        last_vol = float(volume.iloc[-1])
        last_vol_sma = vol_sma.iloc[-1]
        last_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0

        if any(pd.isna(v) for v in [last_dch, last_dcl, last_ema, last_vol_sma]):
            return None

        if last_close <= float(last_dch):
            return None
        if last_close <= float(last_ema):
            return None
        if last_vol < _VOLUME_CONFIRM_MULT * float(last_vol_sma):
            return None
        # Entry-distance filter: reject overextended breakouts (chasing).
        if last_atr > 0 and (last_close - float(last_dch)) > _ENTRY_DISTANCE_ATR_CAP * last_atr:
            return None

        return Signal(
            ticker=self.ticker,
            direction="long",
            price=last_close,
            donchian_high=float(last_dch),
            donchian_low=float(last_dcl),
            ema_50=float(last_ema),
            atr=last_atr,
            timestamp=bars.index[-1].to_pydatetime(),
        )

    def is_exit(self, bars: pd.DataFrame) -> bool:
        """Returns True if last bar is a breakdown below Donchian low."""
        if len(bars) < _WARMUP_BARS:
            return False
        close = bars["Close"]
        _, donchian_low = compute_donchian(bars, period=self.donchian_period)
        last_dcl = donchian_low.iloc[-1]
        if pd.isna(last_dcl):
            return False
        return float(bars["Close"].iloc[-1]) < float(last_dcl)
