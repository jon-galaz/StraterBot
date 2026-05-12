"""
Reconciliation — daily job that compares local open trades vs Alpaca
positions and alerts the trader on any mismatch.
"""
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
            alpaca_positions = {p.symbol: p for p in self.client.get_all_positions()}

            with self.session_factory() as session:
                open_trades = session.execute(
                    select(TradeRecord).where(TradeRecord.status == "open")
                ).scalars().all()
            local_tickers = {t.ticker for t in open_trades}

            mismatches: list[str] = []
            for ticker in local_tickers:
                if ticker not in alpaca_positions:
                    mismatches.append(f"• LOCAL open: <b>{ticker}</b> — not found in Alpaca")
            for symbol in alpaca_positions:
                if symbol not in local_tickers:
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
