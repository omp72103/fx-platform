"""
Common gateway interface — mirrors blueprint section 16 ("Gateway
responsibilities: get_tick, get_rates, get_symbol_info, get_account,
get_positions, get_orders, order_check, order_send, execution monitoring,
error/reconnect handling").

Both MockGateway (app/data/mock_gateway.py) and MT5Gateway
(app/data/mt5_gateway.py) implement this exact interface, so the rest of the
system (orchestrator, execution engine) never has to know or care which one
it's talking to. That's what makes "start on demo, flip to a real broker
later" a one-line config change (GATEWAY_MODE env var) instead of a rewrite.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Tick:
    symbol: str
    timestamp_utc: datetime
    bid: float
    ask: float
    last: Optional[float] = None
    volume: Optional[float] = None


@dataclass
class Bar:
    symbol: str
    timeframe: str
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


@dataclass
class AccountInfo:
    login: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    currency: str = "USD"
    is_demo: bool = True


@dataclass
class OrderRequest:
    symbol: str
    direction: str       # BUY / SELL
    volume: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = ""
    order_id: Optional[str] = None  # our internal id, for round-tripping


@dataclass
class OrderResult:
    status: str                    # filled / rejected / error
    executed_price: Optional[float] = None
    executed_volume: Optional[float] = None
    broker_response_code: Optional[str] = None
    latency_ms: Optional[float] = None
    slippage: Optional[float] = None
    raw: Optional[dict] = None


class Gateway(ABC):
    """Every method here must be implemented by both the mock and the real
    MT5 gateway, with matching semantics."""

    @abstractmethod
    def get_tick(self, symbol: str) -> Tick: ...

    @abstractmethod
    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[Bar]: ...

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> dict: ...

    @abstractmethod
    def get_account(self) -> AccountInfo: ...

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    def get_orders(self) -> list[dict]: ...

    @abstractmethod
    def order_check(self, req: OrderRequest) -> dict:
        """Dry-run validation (margin, symbol tradeable, volume steps...)."""
        ...

    @abstractmethod
    def order_send(self, req: OrderRequest) -> OrderResult: ...
