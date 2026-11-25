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
SignalStatus = Literal["PENDING", "WIN", "LOSS", "DRAW"]


class SignalBase(BaseModel):
    pair: str
    timeframe: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    expiry_timestamp: datetime
    direction: str  # "UP", "DOWN", "NO_SIGNAL", "UP (CALL)", "DOWN (PUT)"
    confidence: float = Field(ge=0.0, le=1.0)
    entry_price: float
    exit_price: Optional[float] = None
    status: SignalStatus = "PENDING"
    next_candle_entry_in_sec: Optional[int] = None
    entry_instruction: Optional[str] = None
    confluence_score: Optional[float] = None
    status_reason: Optional[str] = None
    gate_reason: Optional[str] = None
    call_put: Optional[str] = None


class SignalCreate(BaseModel):
    pair: str
    timeframe: str
    expiry_timestamp: datetime
    direction: str
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
