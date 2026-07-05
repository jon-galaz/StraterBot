#!/usr/bin/env python
"""
Trading system entry point.

    uv run python main.py

The process runs until interrupted (Ctrl+C).
On startup it sends a status message to the configured Telegram chat.
"""
import asyncio
import io
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlalchemy import select
from telegram import BotCommand
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

# US market close is 16:00 ET year-round; scheduling market-relative jobs in ET
# (not a fixed UTC hour) keeps them correct across DST changes.
ET = ZoneInfo("America/New_York")

_SIGNAL_STATUS_EMOJI = {
    "pending": "⏳", "executing": "⚙️", "executed": "✅",
    "rejected": "❌", "expired": "⌛",
}


def format_signals(rows: list[tuple]) -> str:
    """Render (timestamp, ticker, price, status) rows (newest first) as an HTML
    Telegram message. Kept module-level so it's unit-testable."""
    if not rows:
        return "No signals recorded yet."
    lines = [f"🔔 <b>Last {len(rows)} signal(s)</b>  (newest first)", ""]
    for ts, ticker, price, status in rows:
        emoji = _SIGNAL_STATUS_EMOJI.get(status, "•")
        lines.append(
            f"{emoji} <code>{ts:%m-%d %H:%M}</code>  "
            f"<b>{ticker}</b> ${price:.2f}  {status}"
        )
    lines += ["", "⌛ expired = fired but never actioned"]
    return "\n".join(lines)

from trading_system.config import Settings
from trading_system.data.alpaca_adapter import make_data_client
from trading_system.executor.alpaca_executor import AlpacaExecutor
from trading_system.monitor.position_monitor import PositionMonitor
from trading_system.notifier.bot import TelegramNotifier
from trading_system.safety.heartbeat import Heartbeat
from trading_system.safety.kill_switch import KillSwitch
from trading_system.safety.reconciliation import Reconciliation
from trading_system.logging_setup import configure_logging, log_file_path, read_tail
from trading_system.scanner.job import scan_universe
from trading_system.sizing import compute_universe_sizing, save_risk_map
from trading_system.store.db import init_db, make_engine, make_session_factory
from trading_system.store.models import SignalRecord, TradeRecord


def build_app(settings: Settings) -> Application:
    # ── Persistence ───────────────────────────────────────────────────────────
    engine = make_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)

    # ── Alpaca executor ───────────────────────────────────────────────────────
    executor = AlpacaExecutor(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.alpaca_paper,
        session_factory=session_factory,
        database_url=settings.database_url,
        max_concurrent_positions=settings.max_concurrent_positions,
    )

    # ── Safety ────────────────────────────────────────────────────────────────
    kill_switch    = KillSwitch(executor.client, settings)
    executor.kill_switch = kill_switch  # executor re-checks it before every entry
    heartbeat      = Heartbeat()
    reconciliation = Reconciliation(executor.client, session_factory)
    data_client    = make_data_client(settings.alpaca_api_key, settings.alpaca_secret_key)
    monitor        = PositionMonitor(executor.client, session_factory,
                                     data_client=data_client, feed=settings.alpaca_data_feed)

    if not settings.trader_user_ids:
        logger.warning(
            "TRADER_USER_IDS is empty — NO ONE can approve trades or run admin "
            "commands (fail-closed). Set TRADER_USER_IDS before going live."
        )

    # ── Telegram application ──────────────────────────────────────────────────
    app = Application.builder().token(settings.telegram_bot_token).build()

    notifier = TelegramNotifier(
        bot=app.bot,
        chat_id=settings.telegram_chat_id,
        executor=executor,
        session_factory=session_factory,
        timeout_minutes=settings.approval_timeout_minutes,
        kill_switch=kill_switch,
        trader_user_ids=settings.trader_user_ids,
    )

    def _is_trader(update) -> bool:
        # Fail closed: empty whitelist authorises no one.
        user = update.effective_user
        return user is not None and user.id in settings.trader_user_ids

    chat_id = int(settings.telegram_chat_id)

    # ── Handlers ──────────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(notifier.handle_callback))

    async def cmd_status(update, context):
        account = await asyncio.to_thread(executor.client.get_account)
        positions = await asyncio.to_thread(executor.client.get_all_positions)
        # Local open trades that haven't filled yet — approved orders that are
        # in flight but aren't Alpaca *positions* yet (e.g. submitted after close,
        # awaiting next open). Without this, an approved trade looks invisible.
        with session_factory() as session:
            pending = session.execute(
                select(TradeRecord.ticker).where(
                    TradeRecord.status == "open",
                    TradeRecord.fill_price.is_(None),
                )
            ).scalars().all()
        mode = "📄 PAPER" if settings.alpaca_paper else "💰 LIVE"
        pending_line = (
            f"Pending:    {len(pending)}  ({', '.join(pending)})\n"
            if pending else "Pending:    0\n"
        )
        await update.message.reply_text(
            f"<b>System status</b>  {mode}\n\n"
            f"Equity:     ${float(account.equity):,.2f}\n"
            f"Cash:       ${float(account.cash):,.2f}\n"
            f"Positions:  {len(positions)} / {settings.max_concurrent_positions}  (filled)\n"
            f"{pending_line}"
            f"Kill switch: {'🔴 TRIGGERED' if kill_switch.triggered else '🟢 OK'}",
            parse_mode="HTML",
        )

    async def cmd_reset_killswitch(update, context):
        if not _is_trader(update):
            await update.message.reply_text("⛔ Only authorised traders can use this command.")
            return
        kill_switch.reset()
        await update.message.reply_text("✅ Kill switch reset. New entries allowed.")

    async def cmd_scan(update, context):
        if not _is_trader(update):
            await update.message.reply_text("⛔ Only authorised traders can use this command.")
            return
        await update.message.reply_text("🔍 Running manual scan…")
        n = await scan_universe(settings, notifier, session_factory)
        await update.message.reply_text(f"✅ Scan complete — {n} signal(s) found.")

    async def cmd_logs(update, context):
        # Sends logs to the chat for local analysis. Trader-only: logs include
        # equity, tickers and order IDs (no API keys). NOTE: in a group chat the
        # file is visible to everyone in the group — DM the bot for privacy.
        if not _is_trader(update):
            await update.message.reply_text("⛔ Only authorised traders can use this command.")
            return
        path = log_file_path(settings.database_url)
        if not path.exists():
            await update.message.reply_text("No log file yet.")
            return

        arg = context.args[0].lower() if context.args else "500"
        if arg == "full":
            await update.message.reply_document(
                document=path.open("rb"),
                filename=path.name,
                caption="📜 Full current log file",
            )
            return

        try:
            n = max(1, min(int(arg), 5000))
        except ValueError:
            n = 500
        text = await asyncio.to_thread(read_tail, path, n)
        if not text.strip():
            await update.message.reply_text("Log file is empty.")
            return
        buf = io.BytesIO(text.encode("utf-8"))
        buf.name = f"strater_tail_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.log"
        await update.message.reply_document(document=buf, caption=f"📜 Last {n} log lines")

    async def cmd_signals(update, context):
        # Recent signal history so you can audit what fired and what happened to
        # each — an 'expired' signal is one that fired but was never actioned.
        if not _is_trader(update):
            await update.message.reply_text("⛔ Only authorised traders can use this command.")
            return
        try:
            n = max(1, min(int(context.args[0]), 50)) if context.args else 15
        except ValueError:
            n = 15

        def _fetch():
            with session_factory() as session:
                rows = session.execute(
                    select(SignalRecord).order_by(SignalRecord.timestamp.desc()).limit(n)
                ).scalars().all()
                # Detach into plain tuples inside the session.
                return [(r.timestamp, r.ticker, r.price, r.status) for r in rows]

        rows = await asyncio.to_thread(_fetch)
        await update.message.reply_text(format_signals(rows), parse_mode="HTML")

    async def recompute_sizing(bot=None, target_chat_id=None):
        """Refresh per-ticker risk_pct from a 2-yr rolling backtest."""
        logger.info("Sizing: recomputing per-ticker Kelly")
        # ~30s, CPU/network heavy — keep it off the event loop so the bot,
        # heartbeat and kill switch stay responsive while it runs.
        risk_map, stats = await asyncio.to_thread(compute_universe_sizing, list(settings.universe))
        path = save_risk_map(risk_map, settings.database_url)
        logger.info(f"Sizing: wrote {len(risk_map)} entries to {path}")
        if bot is None:
            return
        lines = ["📐 <b>Per-ticker sizing updated</b>", ""]
        for s in sorted(stats, key=lambda x: x.kelly_full, reverse=True):
            tag = f"{s.risk_pct * 100:.2f}%" if s.keep else "DROPPED"
            lines.append(
                f"<code>{s.ticker:<6}</code> "
                f"win={s.win_rate * 100:.0f}%  "
                f"f*={s.kelly_full * 100:+.1f}%  →  <b>{tag}</b>"
            )
        await bot.send_message(
            chat_id=target_chat_id, text="\n".join(lines), parse_mode="HTML"
        )

    async def cmd_recompute_sizing(update, context):
        if not _is_trader(update):
            await update.message.reply_text("⛔ Only authorised traders can use this command.")
            return
        await update.message.reply_text("📐 Recomputing per-ticker sizing… (~30s)")
        await recompute_sizing(bot=context.bot, target_chat_id=chat_id)

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset_killswitch", cmd_reset_killswitch))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("recompute_sizing", cmd_recompute_sizing))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("signals", cmd_signals))

    _last_error_notify = {"at": 0.0, "text": ""}

    async def on_error(update, context):
        # Surface genuine handler failures to the trader — but NOT transient
        # network blips. python-telegram-bot's long-poll regularly raises
        # NetworkError/TimedOut (e.g. httpx.ReadError) when a connection drops
        # mid-read; PTB retries automatically, so these are noise, not incidents.
        err = context.error
        if isinstance(err, (NetworkError, TimedOut, RetryAfter)) or \
                type(err).__module__.split(".")[0] in ("httpx", "httpcore"):
            logger.warning(f"Transient network error (auto-retried): {err!r}")
            return

        logger.exception(f"Unhandled handler error: {err}")
        # Throttle chat alerts so a repeating error can't flood the chat either.
        now = time.monotonic()
        msg = str(err)
        if msg == _last_error_notify["text"] and now - _last_error_notify["at"] < 60:
            return
        _last_error_notify.update(at=now, text=msg)
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>Internal error</b>: <code>{err}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    app.add_error_handler(on_error)

    # ── Scheduler (starts after Telegram bot is up) ───────────────────────────
    async def post_init(application: Application) -> None:
        # Persistent command menu (the "/" autocomplete in Telegram clients) so
        # the commands are always visible, not just in the startup message.
        await application.bot.set_my_commands([
            BotCommand("status", "Equity, positions, pending orders, kill switch"),
            BotCommand("signals", "Recent signal history and their status"),
            BotCommand("scan", "Run a manual universe scan now"),
            BotCommand("recompute_sizing", "Refresh per-ticker Kelly sizing (~30s)"),
            BotCommand("reset_killswitch", "Clear the kill switch, allow new entries"),
            BotCommand("logs", "Fetch recent logs as a file"),
        ])

        scheduler = AsyncIOScheduler(timezone="UTC")

        # Scanner: weekdays at 16:05 ET (5 min after the 16:00 close, DST-safe)
        scheduler.add_job(
            scan_universe, "cron",
            day_of_week="mon-fri", hour=16, minute=5, timezone=ET,
            args=[settings, notifier, session_factory],
            id="scanner",
        )
        # Position monitor: every 5 min
        scheduler.add_job(
            monitor.check_positions, "interval", minutes=5,
            args=[application.bot, chat_id],
            id="monitor",
        )
        # Kill switch: every 5 min
        scheduler.add_job(
            kill_switch.check, "interval", minutes=5,
            args=[application.bot, chat_id],
            id="kill_switch",
        )
        # Heartbeat ping: every 1 min
        scheduler.add_job(heartbeat.ping, "interval", minutes=1, id="hb_ping")
        # Heartbeat check: every 2 min
        scheduler.add_job(
            heartbeat.check, "interval", minutes=2,
            args=[application.bot, chat_id],
            id="hb_check",
        )
        # Signal timeout sweep: every 5 min
        scheduler.add_job(
            notifier.expire_pending_signals, "interval", minutes=5,
            id="signal_timeout",
        )
        # Daily reconciliation: weekdays at 16:30 ET (after close, DST-safe)
        scheduler.add_job(
            reconciliation.run, "cron",
            day_of_week="mon-fri", hour=16, minute=30, timezone=ET,
            args=[application.bot, chat_id],
            id="reconciliation",
        )
        # Monthly sizing recompute: 1st of every month at 02:00 UTC
        scheduler.add_job(
            recompute_sizing, "cron",
            day=1, hour=2, minute=0,
            kwargs={"bot": application.bot, "target_chat_id": chat_id},
            id="sizing_recompute",
        )

        scheduler.start()
        application.bot_data["scheduler"] = scheduler
        logger.info("Scheduler started — system ready")

        mode = "📄 PAPER" if settings.alpaca_paper else "💰 LIVE"
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🟢 <b>Trading system started</b>  {mode}\n\n"
                f"Universe: {', '.join(settings.universe)}\n"
                f"Scanner:  weekdays 16:05 ET\n"
                f"Max positions: {settings.max_concurrent_positions}\n"
                f"Daily max loss: {settings.daily_max_loss_pct}%\n"
                f"Approval timeout: {settings.approval_timeout_minutes} min\n\n"
                f"Commands: /status  /signals  /scan  /recompute_sizing  /reset_killswitch  /logs"
            ),
            parse_mode="HTML",
        )

    async def post_shutdown(application: Application) -> None:
        scheduler = application.bot_data.get("scheduler")
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
        logger.info("System shut down")

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    return app


if __name__ == "__main__":
    settings = Settings()
    configure_logging(settings.database_url)
    app = build_app(settings)
    logger.info(f"Starting — paper={settings.alpaca_paper}")
    app.run_polling(drop_pending_updates=True)
