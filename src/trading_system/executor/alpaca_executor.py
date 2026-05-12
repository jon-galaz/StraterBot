"""
AlpacaExecutor — places bracket orders on Alpaca (paper or live).
Uses the same position sizing logic as StraterStrategy in the backtester.
Idempotency is guaranteed via a UUID client_order_id on every submission.
"""
import uuid

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest
from loguru import logger

from trading_system.sizing import DEFAULT_RISK_PCT, get_risk_pct
from trading_system.store.models import SignalRecord, TradeRecord


class AlpacaExecutor:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        session_factory,
        database_url: str,
        max_concurrent_positions: int = 5,
    ) -> None:
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.session_factory = session_factory
        self.database_url = database_url
        self.max_concurrent_positions = max_concurrent_positions

    # ── Public ────────────────────────────────────────────────────────────────

    def execute(self, signal_id: int) -> str:
        """
        Place a bracket order for the given signal. Returns the Alpaca order ID.
        Raises if the signal is not found, not pending, or the position cap is hit.
        """
        with self.session_factory() as session:
            record = session.get(SignalRecord, signal_id)
            if record is None:
                raise ValueError(f"Signal {signal_id} not found")
            if record.status != "pending":
                raise ValueError(f"Signal {signal_id} is already {record.status}")

            self._check_position_cap()
            self._check_no_duplicate(record.ticker)

            risk_pct = get_risk_pct(record.ticker, self.database_url)
            if risk_pct <= 0:
                raise RuntimeError(
                    f"{record.ticker}: sizing model says no edge — refusing to trade."
                )

            entry = record.price
            atr = record.atr or (entry * 0.01)
            stop_dist = max(1.5 * atr, 0.01 * entry)
            sl_price = round(entry - stop_dist, 2)
            # TP is intentionally far (5R) — the position monitor trails
            # the SL upward so we capture asymmetric trend payoffs.
            tp_price = round(entry + 5.0 * stop_dist, 2)

            account = self.client.get_account()
            equity = float(account.equity)
            risk_dollars = equity * risk_pct
            qty = max(1, int(risk_dollars / stop_dist))
            logger.info(
                f"{record.ticker}: risk_pct={risk_pct:.2%} → ${risk_dollars:.0f} risk → {qty} shares"
            )

            client_order_id = str(uuid.uuid4())

            logger.info(
                f"Placing order: {record.ticker} x{qty} "
                f"entry≈${entry:.2f}  SL=${sl_price}  TP=${tp_price}"
            )

            order = self.client.submit_order(
                MarketOrderRequest(
                    symbol=record.ticker,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tp_price),
                    stop_loss=StopLossRequest(stop_price=sl_price),
                    client_order_id=client_order_id,
                )
            )

            record.status = "executed"
            session.add(TradeRecord(
                signal_id=signal_id,
                ticker=record.ticker,
                side="buy",
                qty=float(qty),
                stop_loss=sl_price,
                take_profit=tp_price,
                status="open",
                alpaca_order_id=str(order.id),
                client_order_id=client_order_id,
            ))
            session.commit()

            return str(order.id)

    # ── Private ───────────────────────────────────────────────────────────────

    def _check_position_cap(self) -> None:
        positions = self.client.get_all_positions()
        if len(positions) >= self.max_concurrent_positions:
            raise RuntimeError(
                f"Position cap reached ({self.max_concurrent_positions}). "
                "Close an existing position before opening a new one."
            )

    def _check_no_duplicate(self, ticker: str) -> None:
        positions = self.client.get_all_positions()
        if any(p.symbol == ticker for p in positions):
            raise RuntimeError(f"Already holding an open position in {ticker}.")
