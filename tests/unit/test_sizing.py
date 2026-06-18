"""Kelly-fraction edge cases, especially scratch-trade (PnL==0) handling."""
import pandas as pd

from trading_system.sizing import kelly_fraction


def _trades(pnls):
    return pd.DataFrame({"PnL": pnls})


def test_scratch_trades_excluded_from_win_and_loss():
    # 2 wins, 1 loss, plus 7 scratch (==0) trades. p must be over decisive only.
    pnls = [100, 100, -50] + [0] * 7
    f, p, avg_win, avg_loss = kelly_fraction(_trades(pnls))
    assert p == 2 / 3            # 2 wins / 3 decisive, NOT 2/10
    assert avg_win == 100.0
    assert avg_loss == 50.0
    # b = 2, q = 1/3 → f = (2*2/3 - 1/3)/2 = 0.5
    assert round(f, 4) == 0.5


def test_no_losses_returns_zero_edge():
    f, p, w, l = kelly_fraction(_trades([10, 20, 30]))
    assert f == 0.0


def test_empty_is_safe():
    f, p, w, l = kelly_fraction(_trades([]))
    assert (f, p, w, l) == (0.0, 0.0, 0.0, 0.0)
