"""
Heartbeat — the scheduler pings every minute; a separate check fires
every 2 minutes and alerts if the gap exceeds max_gap_seconds.
"""
from datetime import datetime, timezone

from loguru import logger
from telegram import Bot


class Heartbeat:
    def __init__(self, max_gap_seconds: int = 120) -> None:
        self.last_ping: datetime | None = None
        self.max_gap = max_gap_seconds
        self._alerted = False

    async def ping(self) -> None:
        self.last_ping = datetime.now(timezone.utc)
        self._alerted = False

    async def check(self, bot: Bot, chat_id: int) -> None:
        if self.last_ping is None:
            return

        elapsed = (datetime.now(timezone.utc) - self.last_ping).total_seconds()

        if elapsed > self.max_gap and not self._alerted:
            self._alerted = True
            logger.warning(f"Heartbeat missed — {elapsed:.0f}s since last ping")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ <b>Heartbeat missed</b> — scheduler last active {elapsed:.0f}s ago.",
                parse_mode="HTML",
            )
