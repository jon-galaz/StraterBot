from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    donchian_high: Mapped[float] = mapped_column(Float, nullable=False)
    donchian_low: Mapped[float] = mapped_column(Float, nullable=False)
    ema_50: Mapped[float] = mapped_column(Float, nullable=False)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # pending/approved/rejected/executed/expired
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    trades: Mapped[list["TradeRecord"]] = relationship("TradeRecord", back_populates="signal")


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, ForeignKey("signals.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # open/closed/cancelled
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Deterministic per-signal key (sig-<signal_id>). Unique so a duplicate
    # submission can never create a second trade row for the same signal.
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    signal: Mapped["SignalRecord"] = relationship("SignalRecord", back_populates="trades")
