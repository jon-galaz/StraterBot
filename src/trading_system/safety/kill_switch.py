"""
KillSwitch — monitors daily P&L. If the loss threshold is breached,
liquidates all positions and halts new entries for the rest of the day.
"""
from alpaca.trading.client import TradingClient
from loguru import logger
from telegram import Bot

from trading_system.config import Settings


class KillSwitch:
    def __init__(self, trading_client: TradingClient, settings: Settings) -> None:
        self.client = trading_client
        self.settings = settings
        self.triggered = False

    async def check(self, bot: Bot, chat_id: int) -> None:
        if self.triggered:
            return

        try:
            account = self.client.get_account()
            equity = float(account.equity)
            last_equity = float(account.last_equity)

            if last_equity <= 0:
                return

            daily_pnl_pct = (equity - last_equity) / last_equity * 100

            if daily_pnl_pct < -self.settings.daily_max_loss_pct:
                self.triggered = True
                logger.warning(f"Kill switch triggered — daily P&L: {daily_pnl_pct:.2f}%")

                self.client.close_all_positions(cancel_orders=True)

                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🚨 <b>KILL SWITCH TRIGGERED</b>\n\n"
                        f"Daily P&L: <b>{daily_pnl_pct:.2f}%</b>  "
                        f"(limit: −{self.settings.daily_max_loss_pct}%)\n\n"
                        f"All positions liquidated. No new entries today.\n"
                        f"Reset with /reset_killswitch."
                    ),
                    parse_mode="HTML",
                )

        except Exception as exc:
            logger.error(f"Kill switch check failed: {exc}")

    def reset(self) -> None:
        self.triggered = False
        logger.info("Kill switch reset")
