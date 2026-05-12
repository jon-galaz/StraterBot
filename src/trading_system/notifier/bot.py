"""
TelegramNotifier — sends signal cards with Approve/Reject buttons and
handles trader callbacks. All approval routing goes through here.
"""
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from trading_system.rules.engine import Signal
from trading_system.store.models import SignalRecord


class TelegramNotifier:
    def __init__(
        self,
        bot: Bot,
        chat_id: str,
        executor,
        session_factory,
        timeout_minutes: int = 15,
        kill_switch=None,
    ) -> None:
        self.bot = bot
        self.chat_id = int(chat_id)
        self.executor = executor
        self.session_factory = session_factory
        self.timeout_minutes = timeout_minutes
        self.kill_switch = kill_switch

    # ── Signal card ───────────────────────────────────────────────────────────

    async def send_signal(self, signal: Signal, signal_id: int) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{signal_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{signal_id}"),
        ]])

        text = (
            f"🔔 <b>Signal #{signal_id} — {signal.ticker}</b>\n\n"
            f"Direction: <b>{signal.direction.upper()}</b>\n"
            f"Price:     <b>${signal.price:.2f}</b>\n"
            f"Donchian high: ${signal.donchian_high:.2f}\n"
            f"EMA-50:        ${signal.ema_50:.2f}\n"
            f"ATR-14:        ${signal.atr:.2f}\n\n"
            f"<i>Auto-expires in {self.timeout_minutes} min if not actioned.</i>"
        )

        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        with self.session_factory() as session:
            record = session.get(SignalRecord, signal_id)
            if record:
                record.telegram_message_id = msg.message_id
                session.commit()

    # ── Callback handler (registered with Application) ────────────────────────

    async def handle_callback(self, update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        try:
            action, signal_id_str = query.data.split(":")
            signal_id = int(signal_id_str)
        except (ValueError, AttributeError):
            return

        with self.session_factory() as session:
            record = session.get(SignalRecord, signal_id)

        if record is None:
            await query.edit_message_text(f"⚠️ Signal #{signal_id} not found.")
            return

        if record.status != "pending":
            await query.edit_message_text(
                f"⚠️ Signal #{signal_id} ({record.ticker}) already <b>{record.status}</b>.",
                parse_mode="HTML",
            )
            return

        if action == "approve":
            if self.kill_switch and self.kill_switch.triggered:
                await query.edit_message_text(
                    "🚨 <b>Kill switch is active</b> — no new entries until reset.\n"
                    "Use /reset_killswitch to re-enable trading.",
                    parse_mode="HTML",
                )
                return
            try:
                order_id = self.executor.execute(signal_id)
                await query.edit_message_text(
                    f"✅ <b>#{signal_id} {record.ticker} — APPROVED</b>\n"
                    f"Order submitted to Alpaca.\n"
                    f"ID: <code>{order_id[:8]}…</code>",
                    parse_mode="HTML",
                )
                logger.info(f"Signal {signal_id} approved — order {order_id}")
            except Exception as exc:
                logger.error(f"Execution failed for signal {signal_id}: {exc}")
                await query.edit_message_text(
                    f"❌ <b>Execution failed</b> for #{signal_id}:\n<code>{exc}</code>",
                    parse_mode="HTML",
                )

        elif action == "reject":
            with self.session_factory() as session:
                rec = session.get(SignalRecord, signal_id)
                if rec:
                    rec.status = "rejected"
                    session.commit()
            await query.edit_message_text(f"❌ Signal #{signal_id} ({record.ticker}) rejected.")
            logger.info(f"Signal {signal_id} rejected by trader")

    # ── Timeout sweep (called by scheduler every 5 min) ───────────────────────

    async def expire_pending_signals(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.timeout_minutes)

        with self.session_factory() as session:
            records = session.execute(
                select(SignalRecord).where(
                    SignalRecord.status == "pending",
                    SignalRecord.timestamp < cutoff,
                )
            ).scalars().all()

            for record in records:
                record.status = "expired"
                if record.telegram_message_id:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=self.chat_id,
                            message_id=record.telegram_message_id,
                            text=f"⏰ Signal #{record.id} ({record.ticker}) expired — no action taken.",
                        )
                    except Exception:
                        pass
                logger.info(f"Signal {record.id} ({record.ticker}) expired")

            session.commit()
