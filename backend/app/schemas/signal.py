from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Candle Schemas
class CandleBase(BaseModel):
    pair: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class CandleCreate(CandleBase):
    pass


class CandleResponse(CandleBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


# Signal Schemas
SignalDirection = Literal["UP", "DOWN", "NO_SIGNAL"]
SignalStatus = Literal["PENDING", "WIN", "LOSS", "DRAW"]


class SignalBase(BaseModel):
    pair: str
    timeframe: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    expiry_timestamp: datetime
    direction: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    entry_price: float
    exit_price: Optional[float] = None
    status: SignalStatus = "PENDING"


class SignalCreate(BaseModel):
    pair: str
    timeframe: str
    expiry_timestamp: datetime
    direction: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    entry_price: float
    timestamp: Optional[datetime] = None
    status: SignalStatus = "PENDING"


class SignalUpdate(BaseModel):
    exit_price: Optional[float] = None
    status: Optional[SignalStatus] = None


class SignalResponse(SignalBase):
    id: str

    model_config = ConfigDict(from_attributes=True)
