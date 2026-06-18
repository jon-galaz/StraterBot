"""
Heartbeat — the scheduler pings every minute; a separate check alerts if the
gap since the last ping exceeds max_gap_seconds.

LIMITATION: this is an *in-process* monitor — if the whole event loop/scheduler
dies, the check job dies with it and cannot alert. It catches a stalled or
erroring single job, not a dead process. A dead process needs an EXTERNAL
watchdog (systemd Restart=, a healthcheck cron, or docker healthcheck) — see
the deployment notes. The startup-grace logic below at least flags a scheduler
that started but never managed its first ping.
"""
from datetime import datetime, timezone

from loguru import logger
from telegram import Bot


class Heartbeat:
    def __init__(self, max_gap_seconds: int = 120, startup_grace_seconds: int = 180) -> None:
        self.last_ping: datetime | None = None
        self.max_gap = max_gap_seconds
        self.startup_grace = startup_grace_seconds
        self._created = datetime.now(timezone.utc)
        self._alerted = False

    async def ping(self) -> None:
        self.last_ping = datetime.now(timezone.utc)
        self._alerted = False

    async def check(self, bot: Bot, chat_id: int) -> None:
        now = datetime.now(timezone.utc)

        if self.last_ping is None:
            # Never pinged. Only alarm once the startup grace has elapsed,
            # otherwise we'd false-alarm during normal boot.
            elapsed = (now - self._created).total_seconds()
            if elapsed <= self.startup_grace:
                return
        else:
            elapsed = (now - self.last_ping).total_seconds()
            if elapsed <= self.max_gap:
                return

        if not self._alerted:
            self._alerted = True
            logger.warning(f"Heartbeat missed — {elapsed:.0f}s since last ping")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ <b>Heartbeat missed</b> — scheduler last active {elapsed:.0f}s ago.",
                parse_mode="HTML",
            )
