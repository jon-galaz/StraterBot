"""
KillSwitch — monitors daily P&L. If the loss threshold is breached,
liquidates all positions and halts new entries for the rest of the day.

The triggered state is persisted to disk (date-keyed) so a crash/restart
cannot silently re-open trading on a day the switch already fired.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from alpaca.trading.client import TradingClient
from loguru import logger
from telegram import Bot

from trading_system.config import Settings
from trading_system.sizing import sizing_path


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _state_path(database_url: str) -> Path:
    # Same directory as the DB / sizing.json.
    return sizing_path(database_url).parent / "kill_switch.json"


class KillSwitch:
    def __init__(self, trading_client: TradingClient, settings: Settings) -> None:
        self.client = trading_client
        self.settings = settings
        self.state_path = _state_path(settings.database_url)
        self._triggered_date = self._load()

    # ── Durable state ───────────────────────────────────────────────────────────

    def _load(self) -> str | None:
        try:
            if self.state_path.exists():
                return json.loads(self.state_path.read_text()).get("triggered_date")
        except Exception as exc:
            logger.error(f"Kill switch: failed to read state {self.state_path}: {exc}")
        return None

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"triggered_date": self._triggered_date}))
        except Exception as exc:
            logger.error(f"Kill switch: failed to persist state {self.state_path}: {exc}")

    @property
    def triggered(self) -> bool:
        """True only if the switch fired today (survives process restarts)."""
        return self._triggered_date == _today()

    # ── Monitoring ──────────────────────────────────────────────────────────────

    async def check(self, bot: Bot, chat_id: int) -> None:
        if self.triggered:
            return

        try:
            account = await asyncio.to_thread(self.client.get_account)
            equity = float(account.equity)
            last_equity = float(account.last_equity)
        except Exception as exc:
            logger.error(f"Kill switch check failed: {exc}")
            return

        if last_equity <= 0:
            return

        daily_pnl_pct = (equity - last_equity) / last_equity * 100
        if daily_pnl_pct >= -self.settings.daily_max_loss_pct:
            return

        # ── Breach: latch the halt FIRST so entries stop even if anything below
        # fails, then alert, then liquidate. Alerting and liquidation are
        # independent so a liquidation error can never suppress the alarm.
        self._triggered_date = _today()
        self._save()
        logger.warning(f"Kill switch triggered — daily P&L: {daily_pnl_pct:.2f}%")

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🚨 <b>KILL SWITCH TRIGGERED</b>\n\n"
                f"Daily P&L: <b>{daily_pnl_pct:.2f}%</b>  "
                f"(limit: −{self.settings.daily_max_loss_pct}%)\n\n"
                f"Halting new entries. Liquidating all positions…\n"
                f"Reset with /reset_killswitch."
            ),
            parse_mode="HTML",
        )

        try:
            results = await asyncio.to_thread(self.client.close_all_positions, cancel_orders=True)
            failed = [
                r for r in (results or [])
                if getattr(r, "status", 200) and int(getattr(r, "status", 200)) >= 300
            ]
            if failed:
                symbols = ", ".join(str(getattr(r, "symbol", "?")) for r in failed)
                logger.error(f"Kill switch: {len(failed)} position(s) failed to close: {symbols}")
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⚠️ <b>Liquidation partially FAILED</b>\n"
                        f"Could not close: <b>{symbols}</b>\n"
                        f"Close these manually in Alpaca NOW."
                    ),
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="✅ All positions liquidated. No new entries today.",
                    parse_mode="HTML",
                )
        except Exception as exc:
            logger.error(f"Kill switch liquidation failed: {exc}")
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ <b>LIQUIDATION FAILED</b> — <code>{exc}</code>\n"
                    f"Entries are halted, but positions may still be OPEN.\n"
                    f"Close them manually in Alpaca NOW."
                ),
                parse_mode="HTML",
            )

    def reset(self) -> None:
        self._triggered_date = None
        self._save()
        logger.info("Kill switch reset")
