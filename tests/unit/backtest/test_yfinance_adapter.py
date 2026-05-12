import pandas as pd
import pytest
from unittest.mock import patch
from trading_system.data.yfinance_adapter import fetch_bars


@pytest.fixture
def mock_df():
    idx = pd.date_range("2023-01-01", periods=5, freq="B")
    return pd.DataFrame({
        "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "High": [105.0, 106.0, 107.0, 108.0, 109.0],
        "Low":  [99.0, 100.0, 101.0, 102.0, 103.0],
        "Close": [104.0, 105.0, 106.0, 107.0, 108.0],
        "Volume": [1e6, 1e6, 1e6, 1e6, 1e6],
    }, index=idx)


def test_fetch_bars_returns_dataframe(mock_df):
    with patch("trading_system.data.yfinance_adapter.yf.download", return_value=mock_df):
        result = fetch_bars("AAPL", "2023-01-01", "2023-01-10")
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_fetch_bars_raises_on_empty():
    with patch("trading_system.data.yfinance_adapter.yf.download", return_value=pd.DataFrame()):
        with pytest.raises(ValueError, match="No data returned"):
            fetch_bars("INVALID", "2023-01-01", "2023-01-10")


def test_fetch_bars_interval_forwarded(mock_df):
    with patch("trading_system.data.yfinance_adapter.yf.download", return_value=mock_df) as mock_dl:
        fetch_bars("AAPL", "2023-01-01", "2023-01-10", interval="1h")
        _, kwargs = mock_dl.call_args
        assert kwargs["interval"] == "1h"
