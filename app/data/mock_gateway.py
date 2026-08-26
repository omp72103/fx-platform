"""
Deterministic paper-trading simulator that implements the exact same
Gateway interface as the real MT5 gateway (app/data/mt5_gateway.py).

This is what makes it possible to build, test, and run the ENTIRE platform
end-to-end right now, with no broker account, no MT5 terminal, and zero
real-world dependency — and to swap in the real gateway later by changing
one env var (GATEWAY_MODE=mt5) without touching a single line of the
strategy, risk, or execution code.

It is intentionally simple: a seeded random walk per symbol (so results are
reproducible run-to-run for a given seed), a fixed spread, and an in-memory
account/position book. It is NOT a substitute for a real historical
backtest (use app/data/historical_loader.py + a real backtester for that) —
it exists to exercise the live/paper CODE PATH, not to prove a strategy is
profitable.
"""
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.data.gateway_base import (
    Gateway, Tick, Bar, AccountInfo, OrderRequest, OrderResult,
)

_TIMEFRAME_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}

_STARTING_PRICE = {
    "EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 151.50, "XAUUSD": 2350.0,
}


@dataclass
class _SymbolState:
    price: float
    rng: random.Random


@dataclass
class MockGateway(Gateway):
    seed: int = 42
    spread_pips: float = 1.2
    starting_balance: float = 10_000.0

    _symbols: dict = field(default_factory=dict)
    _balance: float = field(default=0.0)
    _positions: dict = field(default_factory=dict)  # position_id -> dict
    _orders: list = field(default_factory=list)
    _next_pos_id: int = field(default=1)

    def __post_init__(self):
        self._balance = self.starting_balance

    def _state(self, symbol: str) -> _SymbolState:
        if symbol not in self._symbols:
            price = _STARTING_PRICE.get(symbol, 1.0000)
            self._symbols[symbol] = _SymbolState(
                price=price, rng=random.Random(self.seed ^ hash(symbol))
            )
        return self._symbols[symbol]

    def _pip_size(self, symbol: str) -> float:
        return 0.01 if symbol.endswith("JPY") else 0.0001

    def _step_price(self, symbol: str) -> float:
        st = self._state(symbol)
        pip = self._pip_size(symbol)
        # small random-walk step, deterministic given the seeded RNG
        drift = st.rng.gauss(0, 3) * pip
        st.price = max(st.price + drift, pip)
        return st.price

    # ---- market data --------------------------------------------------

    def get_tick(self, symbol: str) -> Tick:
        mid = self._step_price(symbol)
        pip = self._pip_size(symbol)
        half_spread = (self.spread_pips / 2) * pip
        return Tick(
            symbol=symbol,
            timestamp_utc=datetime.now(timezone.utc),
            bid=round(mid - half_spread, 6),
            ask=round(mid + half_spread, 6),
            last=round(mid, 6),
            volume=1.0,
        )

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        step_s = _TIMEFRAME_SECONDS.get(timeframe, 300)
        st = self._state(symbol)
        pip = self._pip_size(symbol)
        now = datetime.now(timezone.utc)
        bars = []
        price = st.price
        for i in range(count, 0, -1):
            ts = now - timedelta(seconds=step_s * i)
            o = price
            path = [o + st.rng.gauss(0, 2) * pip for _ in range(4)]
            h = max([o] + path)
            l = min([o] + path)
            c = path[-1]
            bars.append(Bar(symbol=symbol, timeframe=timeframe, timestamp_utc=ts,
                             open=round(o, 6), high=round(h, 6), low=round(l, 6),
                             close=round(c, 6), volume=1.0))
            price = c
        st.price = price
        return bars

    def get_symbol_info(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "pip_size": self._pip_size(symbol),
            "contract_size": 100_000,
            "tradeable": True,
            "min_volume": 0.01,
            "volume_step": 0.01,
        }

    # ---- account --------------------------------------------------------

    def get_account(self) -> AccountInfo:
        equity = self._balance + sum(p["unrealized_pnl"] for p in self._positions.values())
        return AccountInfo(
            login="MOCK-DEMO-0001", balance=self._balance, equity=equity,
            margin=sum(p["margin"] for p in self._positions.values()),
            margin_free=max(equity * 0.9, 0), currency="USD", is_demo=True,
        )

    def get_positions(self) -> list[dict]:
        return list(self._positions.values())

    def get_orders(self) -> list[dict]:
        return list(self._orders)

    # ---- trading ----------------------------------------------------------

    def order_check(self, req: OrderRequest) -> dict:
        info = self.get_symbol_info(req.symbol)
        if req.volume < info["min_volume"]:
            return {"ok": False, "reason": "volume below minimum"}
        return {"ok": True}

    def order_send(self, req: OrderRequest) -> OrderResult:
        check = self.order_check(req)
        if not check["ok"]:
            return OrderResult(status="rejected", broker_response_code=check["reason"])

        tick = self.get_tick(req.symbol)
        fill_price = tick.ask if req.direction == "BUY" else tick.bid
        pos_id = f"MOCK-{self._next_pos_id}"
        self._next_pos_id += 1
        contract_size = 100_000
        margin = fill_price * req.volume * contract_size / 30  # ~30x leverage assumption

        self._positions[pos_id] = {
            "position_id": pos_id, "symbol": req.symbol, "direction": req.direction,
            "volume": req.volume, "avg_open_price": fill_price,
            "opened_at": tick.timestamp_utc, "sl": req.sl, "tp": req.tp,
            "margin": margin, "unrealized_pnl": 0.0,
        }
        self._orders.append({"order_id": req.order_id, "position_id": pos_id, "status": "filled"})

        return OrderResult(
            status="filled", executed_price=fill_price, executed_volume=req.volume,
            broker_response_code="MOCK_OK", latency_ms=self._state(req.symbol).rng.uniform(5, 40),
            slippage=0.0, raw={"position_id": pos_id, "mode": "mock"},
        )
