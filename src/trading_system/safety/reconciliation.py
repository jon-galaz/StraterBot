"""
Reconciliation — daily job that compares local open trades vs Alpaca
positions and alerts the trader on any mismatch.
"""
import asyncio

from alpaca.trading.client import TradingClient
from loguru import logger
from sqlalchemy import select
from telegram import Bot

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

            with self.session_factory() as session:
                open_trades = session.execute(
                    select(TradeRecord).where(TradeRecord.status == "open")
                ).scalars().all()
                # Sum qty per ticker inside the session (avoids detached access).
                local_qty: dict[str, float] = {}
                for t in open_trades:
                    local_qty[t.ticker] = local_qty.get(t.ticker, 0.0) + float(t.qty or 0.0)

            mismatches: list[str] = []
            for ticker, qty in local_qty.items():
                pos = alpaca_positions.get(ticker)
                if pos is None:
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

            if mismatches:
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ <b>Reconciliation mismatch</b>\n\n" + "\n".join(mismatches),
                    parse_mode="HTML",
                )
                logger.warning(f"Reconciliation: {len(mismatches)} mismatch(es)")
            else:
                logger.info("Reconciliation: OK")
                await bot.send_message(
                    chat_id=chat_id,
                    text="✅ <b>Daily reconciliation</b>: local state matches Alpaca.",
                    parse_mode="HTML",
                )

        except Exception as exc:
            logger.error(f"Reconciliation failed: {exc}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Reconciliation error: <code>{exc}</code>",
                parse_mode="HTML",
            )
