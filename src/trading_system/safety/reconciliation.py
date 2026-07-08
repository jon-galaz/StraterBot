"""
Reconciliation — daily job that compares local open trades vs Alpaca
positions and alerts the trader on any mismatch.

Order-aware: because the scanner runs after the 16:00 close, an approved
bracket order is accepted by Alpaca but queued for the next open — it has no
position yet. Such an order is a legitimate PENDING FILL, not a mismatch. We
only flag a local open trade as missing when Alpaca has neither a position nor
an accepted/pending order for it (same "never assume gone" rule the position
monitor uses).
"""
import asyncio

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest
from loguru import logger
from telegram import Bot

from sqlalchemy import select

from trading_system.store.models import TradeRecord


class Reconciliation:
    def __init__(self, trading_client: TradingClient, session_factory) -> None:
        self.client = trading_client
        self.session_factory = session_factory

    async def run(self, bot: Bot, chat_id: int) -> None:
        logger.info("Running daily reconciliation")
        try:
            positions = await asyncio.to_thread(self.client.get_all_positions)
            alpaca_positions = {p.symbol: p for p in positions}

            # Accepted-but-unfilled orders (e.g. approved after close, queued for
            # the next open). Needed to tell "pending fill" from "truly gone".
            try:
                open_orders = await asyncio.to_thread(
                    self.client.get_orders,
                    filter=GetOrdersRequest(status=QueryOrderStatus.OPEN),
                )
                pending_orders = {o.symbol for o in open_orders}
                orders_known = True
            except Exception as exc:
                logger.warning(f"Reconciliation: could not fetch open orders: {exc}")
                pending_orders = set()
                orders_known = False

            with self.session_factory() as session:
                open_trades = session.execute(
                    select(TradeRecord).where(TradeRecord.status == "open")
                ).scalars().all()
                # Sum qty per ticker inside the session (avoids detached access),
                # and note whether the ticker has any filled leg. A ticker with no
                # fill is only "pending" (order accepted, awaiting a fill).
                local_qty: dict[str, float] = {}
                has_fill: dict[str, bool] = {}
                for t in open_trades:
                    local_qty[t.ticker] = local_qty.get(t.ticker, 0.0) + float(t.qty or 0.0)
                    has_fill[t.ticker] = has_fill.get(t.ticker, False) or (t.fill_price is not None)

            mismatches: list[str] = []
            pending: list[str] = []
            for ticker, qty in local_qty.items():
                pos = alpaca_positions.get(ticker)
                if pos is None:
                    # No position on Alpaca. If we have no fill yet AND Alpaca has
                    # an accepted order for it, this is a pending fill — not drift.
                    if not has_fill.get(ticker) and orders_known and ticker in pending_orders:
                        pending.append(f"• <b>{ticker}</b> — order accepted, awaiting fill")
                    else:
                        mismatches.append(f"• LOCAL open: <b>{ticker}</b> — not found in Alpaca")
                else:
                    broker_qty = abs(float(pos.qty))
                    # Tolerate sub-share float noise only.
                    if abs(broker_qty - qty) > 1e-6:
                        mismatches.append(
                            f"• QTY mismatch <b>{ticker}</b>: local {qty:g} vs Alpaca {broker_qty:g}"
                        )
            for symbol in alpaca_positions:
                if symbol not in local_qty:
                    mismatches.append(f"• ALPACA open: <b>{symbol}</b> — not in local DB")

            pending_note = ("\n\n⏳ <b>Pending fills</b>\n" + "\n".join(pending)) if pending else ""

            if mismatches:
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ <b>Reconciliation mismatch</b>\n\n"
                    + "\n".join(mismatches) + pending_note,
                    parse_mode="HTML",
                )
                logger.warning(
                    f"Reconciliation: {len(mismatches)} mismatch(es), {len(pending)} pending"
                )
            else:
                logger.info(f"Reconciliation: OK ({len(pending)} pending fill(s))")
                await bot.send_message(
                    chat_id=chat_id,
                    text="✅ <b>Daily reconciliation</b>: local state matches Alpaca."
                    + pending_note,
                    parse_mode="HTML",
                )

        except Exception as exc:
            logger.error(f"Reconciliation failed: {exc}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Reconciliation error: <code>{exc}</code>",
                parse_mode="HTML",
            )
