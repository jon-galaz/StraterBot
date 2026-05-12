"""
Scanner job — runs after US market close (scheduled via APScheduler).
Fetches latest bars from Alpaca, evaluates the rule engine for every
ticker in the universe, and sends any signals to Telegram for approval.
"""
from loguru import logger
from sqlalchemy import select

from trading_system.config import Settings
from trading_system.data.alpaca_adapter import fetch_bars, make_data_client
from trading_system.rules.engine import RuleEngine
from trading_system.sizing import load_risk_map
from trading_system.store.models import SignalRecord, TradeRecord


def _ticker_is_active(ticker: str, session_factory) -> bool:
    """Return True if ticker already has a pending signal or open trade."""
    with session_factory() as session:
        pending = session.execute(
            select(SignalRecord).where(
                SignalRecord.ticker == ticker,
                SignalRecord.status == "pending",
            )
        ).first()
        if pending:
            return True

        open_trade = session.execute(
            select(TradeRecord).where(
                TradeRecord.ticker == ticker,
                TradeRecord.status == "open",
            )
        ).first()
        return open_trade is not None


async def scan_universe(settings: Settings, notifier, session_factory) -> None:
    logger.info("Scanner: starting universe scan")

    data_client = make_data_client(settings.alpaca_api_key, settings.alpaca_secret_key)
    risk_map = load_risk_map(settings.database_url)
    signals_found = 0

    for ticker in settings.universe:
        try:
            if _ticker_is_active(ticker, session_factory):
                logger.debug(f"{ticker}: skipped — already pending or open")
                continue

            # Layer 1: drop tickers the sizing model says have no edge.
            # Tickers absent from the map (new universe entries) fall through
            # to the executor's default 1% until the next monthly recompute.
            if ticker in risk_map and risk_map[ticker] <= 0:
                logger.debug(f"{ticker}: skipped — dropped by sizing model")
                continue

            bars = fetch_bars(data_client, ticker)
            signal = RuleEngine(ticker).evaluate(bars)

            if signal is None:
                logger.debug(f"{ticker}: no signal")
                continue

            logger.info(f"Signal: {ticker} @ ${signal.price:.2f}")
            signals_found += 1

            with session_factory() as session:
                record = SignalRecord(
                    ticker=signal.ticker,
                    direction=signal.direction,
                    price=signal.price,
                    donchian_high=signal.donchian_high,
                    donchian_low=signal.donchian_low,
                    ema_50=signal.ema_50,
                    atr=signal.atr,
                    status="pending",
                    timestamp=signal.timestamp,
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                signal_id = record.id

            await notifier.send_signal(signal, signal_id)

        except Exception as exc:
            logger.error(f"Scanner error [{ticker}]: {exc}")

    logger.info(f"Scanner: done — {signals_found} signal(s) found")
