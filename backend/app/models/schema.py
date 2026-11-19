import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from app.core.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pair = Column(String(20), index=True, nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_candles_pair_timeframe_timestamp", "pair", "timeframe", "timestamp"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    pair = Column(String(20), index=True, nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime, default=get_utc_now, nullable=False)
    expiry_timestamp = Column(DateTime, nullable=False)
    direction = Column(String(10), nullable=False)  # "UP", "DOWN", "NO_SIGNAL"
    confidence = Column(Float, nullable=False)      # 0.0 to 1.0
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")  # "PENDING", "WIN", "LOSS", "DRAW"

