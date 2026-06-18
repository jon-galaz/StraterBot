import pandas as pd
from backtesting import Strategy

from trading_system.rules.engine import RuleEngine
from trading_system.rules.indicators import compute_atr

_STOP_ATR_MULT = 1.5
_STOP_FLOOR_PCT = 0.01
_INITIAL_TP_R = 5.0           # very wide; trailing manages real exits
_LOCK_R_THRESHOLD = 1.0       # at +1R, lock SL to entry + 0.25R
_LOCK_R_BUFFER = 0.25
_TRAIL_R_THRESHOLD = 2.0      # at +2R, switch to ATR-based trailing
_CHANDELIER_ATR_MULT = 3.0


class StraterStrategy(Strategy):
    """
    backtesting.py Strategy — delegates entry signals to RuleEngine.
    Sizing mirrors the live executor (risk_pct of equity, ATR stop, cash cap).
    Exits use a two-stage trailing scheme:
      • +1R   → lock SL at entry + 0.25R
      • +2R   → ATR trailing (Chandelier: highest_high − 3×ATR)
    Plus the original Donchian-low breakdown still closes the position.
    """
    ticker: str = "UNKNOWN"
    risk_pct: float = 0.01
    regime_series = None  # optional boolean Series; entries only when True

    def init(self):
        self._engine = RuleEngine(ticker=self.ticker)
        self._atr_series = compute_atr(self.data.df, period=14)
        if self.regime_series is not None:
            aligned = self.regime_series.reindex(self.data.df.index).ffill().fillna(False)
            self._regime = aligned.to_numpy(dtype=bool)
        else:
            self._regime = None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _current_atr(self) -> float:
        i = len(self.data.Close) - 1
        if i < 0:
            return 0.0
        v = self._atr_series.iloc[i]
        return float(v) if not pd.isna(v) else 0.0

    def _trail_open_position(self) -> None:
        if not self.trades:
            return
        trade = self.trades[-1]
        entry = float(trade.entry_price)
        # `tag` carries the original 1R distance (set at entry time).
        try:
            stop_dist = float(trade.tag) if trade.tag else (entry - float(trade.sl))
        except (TypeError, ValueError):
            stop_dist = entry - float(trade.sl)
        if stop_dist <= 0:
            return

        high_since_entry = float(self.data.High[trade.entry_bar:].max())
        pnl_in_R = (high_since_entry - entry) / stop_dist

        new_sl = float(trade.sl)
        if pnl_in_R >= _LOCK_R_THRESHOLD:
            target = entry + _LOCK_R_BUFFER * stop_dist
            if target > new_sl:
                new_sl = target
        if pnl_in_R >= _TRAIL_R_THRESHOLD:
            atr_now = self._current_atr()
            if atr_now > 0:
                target = high_since_entry - _CHANDELIER_ATR_MULT * atr_now
                if target > new_sl:
                    new_sl = target

        if new_sl > float(trade.sl):
            trade.sl = new_sl

    # ── main loop ─────────────────────────────────────────────────────────────

    def next(self):
        # Inside next(), self.data.df is already masked to the current bar.
        bars = self.data.df
        if self.position:
            self._trail_open_position()
            if self._engine.is_exit(bars):
                self.position.close()
            return

        # Optional market-regime gate (research tool): no new entries when off.
        if self._regime is not None and not self._regime[len(self.data.Close) - 1]:
            return

        signal = self._engine.evaluate(bars)
        if signal is None:
            return

        entry = self.data.Close[-1]
        atr = signal.atr if signal.atr > 0 else entry * _STOP_FLOOR_PCT
        stop_dist = max(_STOP_ATR_MULT * atr, _STOP_FLOOR_PCT * entry)
        sl = entry - stop_dist
        tp = entry + _INITIAL_TP_R * stop_dist

        risk_dollars = self.equity * self.risk_pct
        qty = max(1, int(risk_dollars / stop_dist))
        max_affordable = int(self.equity * 0.999 / entry)
        qty = min(qty, max_affordable)
        if qty < 1:
            return
        self.buy(size=qty, sl=sl, tp=tp, tag=str(stop_dist))
