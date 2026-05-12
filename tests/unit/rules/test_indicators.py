import pandas as pd
import numpy as np
import pytest
from trading_system.rules.indicators import (
    compute_sma,
    compute_ema,
    compute_rsi,
    compute_atr,
    compute_donchian,
    compute_volume_sma,
)


@pytest.fixture
def ohlcv():
    n = 300
    rng = np.random.default_rng(42)
    close = 100 + rng.normal(0, 1, n).cumsum()
    high = close + rng.uniform(0.5, 2, n)
    low = close - rng.uniform(0.5, 2, n)
    open_ = close - rng.normal(0, 0.5, n)
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def test_sma_200_warmup(ohlcv):
    result = compute_sma(ohlcv["Close"], period=200)
    assert result.iloc[:199].isna().all()
    assert not pd.isna(result.iloc[199])


def test_ema_50_warmup(ohlcv):
    result = compute_ema(ohlcv["Close"], period=50)
    assert not result.dropna().empty


def test_rsi_range(ohlcv):
    result = compute_rsi(ohlcv["Close"], period=14)
    valid = result.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_atr_positive(ohlcv):
    result = compute_atr(ohlcv, period=14)
    assert result.dropna().gt(0).all()


def test_donchian_high_gte_low(ohlcv):
    high_ch, low_ch = compute_donchian(ohlcv, period=20)
    valid = high_ch.dropna().index.intersection(low_ch.dropna().index)
    assert (high_ch[valid] >= low_ch[valid]).all()


def test_donchian_uses_previous_bar(ohlcv):
    """Donchian must use shift(1) — no same-bar lookahead."""
    high_ch, _ = compute_donchian(ohlcv, period=20)
    # At bar 20 (index 20), channel high should be max of bars 0-19, not 0-20
    expected = ohlcv["High"].iloc[:20].max()
    assert high_ch.iloc[20] == pytest.approx(expected)


def test_volume_sma_positive(ohlcv):
    result = compute_volume_sma(ohlcv["Volume"], period=20)
    assert result.dropna().gt(0).all()
