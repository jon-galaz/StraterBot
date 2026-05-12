"""
PositionMonitor — uses Alpaca's positions endpoint as the source of truth.

Lifecycle per trade:
  open (no fill) → position appears on Alpaca → fill recorded
  position disappears from Alpaca → bracket exited (SL or TP hit)

Also responsible for trailing the SL upward as price runs:
  +1R → lock SL at entry + 0.25R (breakeven + buffer)
  +2R → Chandelier trail (highest_high − 3 × ATR)
The TP leg of the bracket is set wide (5R) so trailing manages real exits.
"""
from datetime import datetime, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType, QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest, ReplaceOrderRequest
from loguru import logger
from sqlalchemy import select
from telegram import Bot

from trading_system.data.alpaca_adapter import fetch_bars as fetch_alpaca_bars
from trading_system.rules.indicators import compute_atr
from trading_system.store.models import TradeRecord

_LOCK_R_THRESHOLD = 1.0
_LOCK_R_BUFFER = 0.25
_TRAIL_R_THRESHOLD = 2.0
_CHANDELIER_ATR_MULT = 3.0


class PositionMonitor:
    def __init__(
        self,
        trading_client: TradingClient,
        session_factory,
        data_client: StockHistoricalDataClient | None = None,
    ) -> None:
        self.client = trading_client
        self.session_factory = session_factory
        self.data_client = data_client

    async def check_positions(self, bot: Bot, chat_id: int) -> None:
        with self.session_factory() as session:
            open_trades = session.execute(
                select(TradeRecord).where(TradeRecord.status == "open")
            ).scalars().all()

        if not open_trades:
            return

        # Snapshot current Alpaca state
        try:
            alpaca_positions = {p.symbol: p for p in self.client.get_all_positions()}
        except Exception as exc:
            logger.error(f"Monitor: could not fetch Alpaca positions: {exc}")
            return

        try:
            pending_orders = {
                o.symbol
                for o in self.client.get_orders(
                    filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
                )
            }
        except Exception as exc:
            logger.error(f"Monitor: could not fetch open orders: {exc}")
            pending_orders = set()

        for trade in open_trades:
            try:
                await self._check_trade(trade, alpaca_positions, pending_orders, bot, chat_id)
            except Exception as exc:
                logger.error(f"Monitor: error on trade {trade.id} ({trade.ticker}): {exc}")

    async def _check_trade(
        self,
        trade: TradeRecord,
        alpaca_positions: dict,
        pending_orders: set,
        bot: Bot,
        chat_id: int,
    ) -> None:
        position = alpaca_positions.get(trade.ticker)

        with self.session_factory() as session:
            t = session.get(TradeRecord, trade.id)
            if t is None or t.status != "open":
                return

            if position is not None:
                # Position is live on Alpaca — record fill price on first sight
                if t.fill_price is None:
                    fill_price = float(position.avg_entry_price)
                    t.fill_price = fill_price
                    t.filled_at = datetime.now(timezone.utc)
                    session.commit()
                    logger.info(f"Trade {t.id} {t.ticker} filled @ ${fill_price:.2f}")
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"✅ <b>Order filled</b>\n"
                            f"{t.ticker} × {int(t.qty)} @ <b>${fill_price:.2f}</b>\n"
                            f"SL: ${t.stop_loss:.2f}   TP: ${t.take_profit:.2f}"
                        ),
                        parse_mode="HTML",
                    )
                else:
                    # Already filled — try to trail the SL upward.
                    await self._maybe_trail_sl(session, t, position)

            else:
                # No position on Alpaca
                if t.fill_price is None:
                    if t.ticker in pending_orders:
                        # Order still queued (e.g. market closed) — wait
                        logger.debug(f"Trade {t.id} {t.ticker}: order pending, skipping")
                        return
                    # No position and no pending order → cancelled/rejected
                    t.status = "cancelled"
                    session.commit()
                    logger.warning(f"Trade {t.id} {t.ticker}: order not filled and not pending, marking cancelled")
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ Order for <b>{t.ticker}</b> was never filled — marked cancelled.",
                        parse_mode="HTML",
                    )
                else:
                    # Was filled, position now gone → bracket exited (SL or TP hit)
                    t.status = "closed"
                    t.closed_at = datetime.now(timezone.utc)
                    exit_price = self._get_exit_price(t)
                    if exit_price is not None and t.fill_price is not None:
                        t.exit_price = exit_price
                        t.pnl = round((exit_price - float(t.fill_price)) * float(t.qty), 4)
                    session.commit()
                    pnl_str = f"  P&L: ${t.pnl:+.2f}" if t.pnl is not None else ""
                    logger.info(f"Trade {t.id} {t.ticker} closed{pnl_str}")
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"📊 <b>Position closed</b>\n"
                            f"{t.ticker}: exit @ <b>${exit_price:.2f}</b>\n"
                            f"P&L: <b>${t.pnl:+.2f}</b>"
                            if exit_price is not None and t.pnl is not None
                            else f"📊 <b>Position closed</b>\n{t.ticker}: stop or take profit hit."
                        ),
                        parse_mode="HTML",
                    )

    def _get_exit_price(self, t: TradeRecord) -> float | None:
        """
        Retrieve exit fill price from the bracket order's closed sell legs.
        Uses the stored alpaca_order_id to find the parent bracket, then
        reads the filled leg (SL stop or TP limit) directly.
        """
        if t.alpaca_order_id is None:
            return None
        try:
            order = self.client.get_order_by_id(t.alpaca_order_id)
            for leg in (order.legs or []):
                if (
                    leg.side == OrderSide.SELL
                    and leg.status == OrderStatus.FILLED
                    and leg.filled_avg_price is not None
                ):
                    return float(leg.filled_avg_price)
        except Exception as exc:
            logger.warning(f"Exit price fetch for {t.ticker}: {exc}")
        return None

    async def _maybe_trail_sl(self, session, t: TradeRecord, position) -> None:
        """
        Walk the SL upward according to the +1R lock / +2R Chandelier rules.
        Only ever raises the stop, never lowers.
        """
        if self.data_client is None or t.fill_price is None:
            return

        fill = float(t.fill_price)
        current_sl = float(t.stop_loss)
        stop_dist = fill - current_sl
        if stop_dist <= 0:
            return

        try:
            bars = fetch_alpaca_bars(self.data_client, t.ticker, days_back=60)
        except Exception as exc:
            logger.warning(f"Trail {t.ticker}: bar fetch failed: {exc}")
            return

        if t.filled_at is not None:
            filled_ts = pd.Timestamp(t.filled_at).tz_localize(None)
            since = bars[bars.index >= filled_ts.normalize()]
            high_since = float(since["High"].max()) if not since.empty else float(bars["High"].iloc[-1])
        else:
            high_since = float(bars["High"].iloc[-1])

        pnl_in_R = (high_since - fill) / stop_dist
        new_sl = current_sl
        if pnl_in_R >= _LOCK_R_THRESHOLD:
            target = fill + _LOCK_R_BUFFER * stop_dist
            if target > new_sl:
                new_sl = target
        if pnl_in_R >= _TRAIL_R_THRESHOLD:
            atr_series = compute_atr(bars, period=14)
            atr_now = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0
            if atr_now > 0:
                target = high_since - _CHANDELIER_ATR_MULT * atr_now
                if target > new_sl:
                    new_sl = target

        new_sl = round(new_sl, 2)
        if new_sl <= current_sl:
            return

        try:
            open_orders = self.client.get_orders(
                filter=GetOrdersRequest(
                    status=QueryOrderStatus.OPEN, symbols=[t.ticker]
                )
            )
        except Exception as exc:
            logger.warning(f"Trail {t.ticker}: order fetch failed: {exc}")
            return

        sl_order = next(
            (o for o in open_orders
             if o.order_type == OrderType.STOP and o.side == OrderSide.SELL),
            None,
        )
        if sl_order is None:
            logger.warning(f"Trail {t.ticker}: no SL order found, skipping")
            return

        try:
            self.client.replace_order_by_id(
                sl_order.id, ReplaceOrderRequest(stop_price=new_sl)
            )
        except Exception as exc:
            logger.warning(f"Trail {t.ticker}: replace failed: {exc}")
            return

        t.stop_loss = new_sl
        session.commit()
        logger.info(
            f"Trail {t.ticker}: SL ${current_sl:.2f} → ${new_sl:.2f} (R={pnl_in_R:.2f})"
        )
