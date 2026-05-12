#!/usr/bin/env python
"""
Trading system entry point.

    uv run python main.py

The process runs until interrupted (Ctrl+C).
On startup it sends a status message to the configured Telegram chat.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from trading_system.config import Settings
from trading_system.data.alpaca_adapter import make_data_client
from trading_system.executor.alpaca_executor import AlpacaExecutor
from trading_system.monitor.position_monitor import PositionMonitor
from trading_system.notifier.bot import TelegramNotifier
from trading_system.safety.heartbeat import Heartbeat
from trading_system.safety.kill_switch import KillSwitch
from trading_system.safety.reconciliation import Reconciliation
from trading_system.scanner.job import scan_universe
from trading_system.sizing import compute_universe_sizing, save_risk_map
from trading_system.store.db import init_db, make_engine, make_session_factory


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
    heartbeat      = Heartbeat()
    reconciliation = Reconciliation(executor.client, session_factory)
    data_client    = make_data_client(settings.alpaca_api_key, settings.alpaca_secret_key)
    monitor        = PositionMonitor(executor.client, session_factory, data_client=data_client)

    # ── Telegram application ──────────────────────────────────────────────────
    app = Application.builder().token(settings.telegram_bot_token).build()

    notifier = TelegramNotifier(
        bot=app.bot,
        chat_id=settings.telegram_chat_id,
        executor=executor,
        session_factory=session_factory,
        timeout_minutes=settings.approval_timeout_minutes,
        kill_switch=kill_switch,
    )

    chat_id = int(settings.telegram_chat_id)

    # ── Handlers ──────────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(notifier.handle_callback))

    async def cmd_status(update, context):
        account = executor.client.get_account()
        positions = executor.client.get_all_positions()
        mode = "📄 PAPER" if settings.alpaca_paper else "💰 LIVE"
        await update.message.reply_text(
            f"<b>System status</b>  {mode}\n\n"
            f"Equity:     ${float(account.equity):,.2f}\n"
            f"Cash:       ${float(account.cash):,.2f}\n"
            f"Positions:  {len(positions)} / {settings.max_concurrent_positions}\n"
            f"Kill switch: {'🔴 TRIGGERED' if kill_switch.triggered else '🟢 OK'}",
            parse_mode="HTML",
        )

    async def cmd_reset_killswitch(update, context):
        kill_switch.reset()
        await update.message.reply_text("✅ Kill switch reset. New entries allowed.")

    async def cmd_scan(update, context):
        await update.message.reply_text("🔍 Running manual scan…")
        await scan_universe(settings, notifier, session_factory)

    async def recompute_sizing(bot=None, target_chat_id=None):
        """Refresh per-ticker risk_pct from a 2-yr rolling backtest."""
        logger.info("Sizing: recomputing per-ticker Kelly")
        risk_map, stats = compute_universe_sizing(list(settings.universe))
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
        await update.message.reply_text("📐 Recomputing per-ticker sizing… (~30s)")
        await recompute_sizing(bot=context.bot, target_chat_id=chat_id)

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset_killswitch", cmd_reset_killswitch))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("recompute_sizing", cmd_recompute_sizing))

    # ── Scheduler (starts after Telegram bot is up) ───────────────────────────
    async def post_init(application: Application) -> None:
        scheduler = AsyncIOScheduler(timezone="UTC")

        # Scanner: weekdays at 21:05 UTC (17:05 ET — 5 min after close)
        scheduler.add_job(
            scan_universe, "cron",
            day_of_week="mon-fri", hour=21, minute=5,
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
        # Daily reconciliation: weekdays at 22:00 UTC
        scheduler.add_job(
            reconciliation.run, "cron",
            day_of_week="mon-fri", hour=22, minute=0,
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
                f"Scanner:  weekdays 21:05 UTC\n"
                f"Max positions: {settings.max_concurrent_positions}\n"
                f"Daily max loss: {settings.daily_max_loss_pct}%\n"
                f"Approval timeout: {settings.approval_timeout_minutes} min\n\n"
                f"Commands: /status  /scan  /recompute_sizing  /reset_killswitch"
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
    app = build_app(settings)
    logger.info(f"Starting — paper={settings.alpaca_paper}")
    app.run_polling(drop_pending_updates=True)
