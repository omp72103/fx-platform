from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TickOut(BaseModel):
    symbol: str
    timestamp_utc: datetime
    bid: float
    ask: float


class BarOut(BaseModel):
    symbol: str
    timeframe: str
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float


class AccountOut(BaseModel):
    login: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    is_demo: bool


class RiskEvaluateIn(BaseModel):
    symbol: str
    timeframe: str
    direction: str
    entry_reference: float
    stop_loss: float
    take_profit: Optional[float] = None
    strategy_id: str = "manual"
    strategy_version: str = "manual_v1"
    confidence: float = 0.5


class RiskEvaluateOut(BaseModel):
    decision: str
    reason_codes: list[str]
    approved_volume: float


class OrderSendIn(BaseModel):
    symbol: str
    direction: str
    volume: float
    sl: Optional[float] = None
    tp: Optional[float] = None


class ModelPromoteIn(BaseModel):
    approval_status: str  # candidate/shadow/approved/rejected
