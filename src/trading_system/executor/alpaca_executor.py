"""
AlpacaExecutor — places bracket orders on Alpaca (paper or live).
Uses the same position sizing logic as StraterStrategy in the backtester.

Idempotency & safety:
  • client_order_id is DETERMINISTIC (sig-<signal_id>) so a retry or duplicate
    approval reuses the same key — Alpaca rejects the duplicate, and the unique
    constraint on TradeRecord.client_order_id is a second backstop.
  • The signal row is claimed pending→executing atomically before the broker
    call; a losing concurrent caller (double-click, two traders) gets rejected.
  • A process-wide lock serialises executions so the position-cap / duplicate
    checks see a consistent view (no in-flight order slips past the cap).
  • The kill switch is re-checked here, not just in the notifier, so no caller
    can bypass it.
"""
import threading

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest
from loguru import logger
from sqlalchemy import func, select, update

from trading_system.sizing import get_risk_pct
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
        kill_switch=None,
    ) -> None:
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.session_factory = session_factory
        self.database_url = database_url
        self.max_concurrent_positions = max_concurrent_positions
        self.kill_switch = kill_switch
        self._lock = threading.Lock()

    # ── Public ────────────────────────────────────────────────────────────────

    def execute(self, signal_id: int) -> str:
        """
        Place a bracket order for the given signal. Returns the Alpaca order ID.
        Raises if the signal is not found/pending, the kill switch is active, the
        position cap is hit, the ticker is already held, or the broker rejects.

        Serialised process-wide so cap/duplicate checks are race-free.
        """
        with self._lock:
            return self._execute_locked(signal_id)

    def _execute_locked(self, signal_id: int) -> str:
        with self.session_factory() as session:
            record = session.get(SignalRecord, signal_id)
            if record is None:
                raise ValueError(f"Signal {signal_id} not found")
            if record.status != "pending":
                raise ValueError(f"Signal {signal_id} is already {record.status}")

            # Never enter after the kill switch has fired.
            if self.kill_switch is not None and self.kill_switch.triggered:
                raise RuntimeError("Kill switch is active — no new entries.")

            # Atomic claim: only one caller can flip pending → executing.
            claimed = session.execute(
                update(SignalRecord)
                .where(SignalRecord.id == signal_id, SignalRecord.status == "pending")
                .values(status="executing")
            ).rowcount
            session.commit()
            if claimed != 1:
                raise ValueError(f"Signal {signal_id} was already actioned")

            ticker = record.ticker
            try:
                # Pre-submit checks. Failure here releases the claim so the
                # trader can retry later (no order was placed).
                self._check_position_cap(session)
                self._check_no_duplicate(session, ticker)

                risk_pct = get_risk_pct(ticker, self.database_url)
                if risk_pct <= 0:
                    raise RuntimeError(
                        f"{ticker}: sizing model says no edge — refusing to trade."
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
                    f"{ticker}: risk_pct={risk_pct:.2%} → ${risk_dollars:.0f} risk → {qty} shares"
                )
            except Exception:
                self._release_claim(session, signal_id)
                raise

            # Deterministic key — a retry reuses it and Alpaca rejects the dup.
            client_order_id = f"sig-{signal_id}"
            logger.info(
                f"Placing order: {ticker} x{qty} "
                f"entry≈${entry:.2f}  SL=${sl_price}  TP=${tp_price}"
            )

            # If the broker call fails we deliberately leave the signal in
            # 'executing' (fail closed): we never auto-retry, because the order
            # may in fact have reached Alpaca. Manual review via /status.
            order = self.client.submit_order(
                MarketOrderRequest(
                    symbol=ticker,
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
                ticker=ticker,
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

    def _release_claim(self, session, signal_id: int) -> None:
        """Return a claimed-but-not-submitted signal to pending for retry."""
        session.execute(
            update(SignalRecord)
            .where(SignalRecord.id == signal_id, SignalRecord.status == "executing")
            .values(status="pending")
        )
        session.commit()

    def _open_trade_count(self, session) -> int:
        return int(session.scalar(
            select(func.count()).select_from(TradeRecord)
            .where(TradeRecord.status == "open")
        ) or 0)

    def _check_position_cap(self, session) -> None:
        # Count local open trades (includes submitted-but-unfilled orders), so a
        # burst of approvals before any fill cannot blow past the cap.
        if self._open_trade_count(session) >= self.max_concurrent_positions:
            raise RuntimeError(
                f"Position cap reached ({self.max_concurrent_positions}). "
                "Close an existing position before opening a new one."
            )

    def _check_no_duplicate(self, session, ticker: str) -> None:
        existing = session.scalar(
            select(TradeRecord.id).where(
                TradeRecord.ticker == ticker,
                TradeRecord.status == "open",
            )
        )
        if existing is not None:
            raise RuntimeError(f"Already holding an open position in {ticker}.")
