"""
Real MT5 gateway — wraps the official MetaTrader5 Python package per
blueprint section 16 ("Python Trading Engine -> MT5 Python package -> MT5
Terminal -> Broker Trade Server") and section 36 (official MQL5/MT5
references, which must be treated as the source of truth for exact API
behavior — verify against the installed package version).

IMPORTANT — this only works where a real MT5 terminal is installed, logged
into a broker account, and running:
  - Natively on Windows, or
  - On Linux via Wine (there is no native Linux MT5 terminal) — see
    RUNBOOK.md for a tested Docker+Wine setup for a headless VPS.

The `MetaTrader5` package is intentionally NOT imported at module load time
so the rest of the app (API, mock-gateway paper loop, tests) can run on any
machine without it installed. It's imported lazily inside __post_init__/
connect(), and this module will raise a clear error if it's missing or if
mt5.initialize() fails — it will NOT silently fall back to mock, because
silently trading against the wrong thing is worse than crashing loudly.

SAFETY: this class refuses to place a single order unless the connected
account reports itself as a demo account (mt5 account_info().trade_mode ==
ACCOUNT_TRADE_MODE_DEMO) AND app.config.settings.account_mode == "demo".
Both checks must independently agree. See order_send() below and
app/execution_engine.py for the second, independent guard.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.data.gateway_base import (
    Gateway, Tick, Bar, AccountInfo, OrderRequest, OrderResult,
)
from app.config import settings

_TIMEFRAME_MAP_NAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5ConnectionError(RuntimeError):
    pass


class MT5SafetyError(RuntimeError):
    """Raised whenever the demo-only safety guard would be violated."""
    pass


@dataclass
class MT5Gateway(Gateway):
    login: str = field(default_factory=lambda: settings.mt5_login)
    password: str = field(default_factory=lambda: settings.mt5_password)
    server: str = field(default_factory=lambda: settings.mt5_server)
    terminal_path: str = field(default_factory=lambda: settings.mt5_terminal_path)

    _mt5 = None
    _connected: bool = False

    def _ensure_connected(self):
        if self._connected:
            return
        try:
            import MetaTrader5 as mt5  # noqa: N814  (package's own naming)
        except ImportError as e:
            raise MT5ConnectionError(
                "MetaTrader5 package is not installed on this machine. "
                "It only installs on Windows, or Linux+Wine with a running "
                "MT5 terminal. See RUNBOOK.md. Original error: " + str(e)
            ) from e

        self._mt5 = mt5
        kwargs = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        ok = mt5.initialize(**kwargs)
        if not ok:
            raise MT5ConnectionError(f"mt5.initialize() failed: {mt5.last_error()}")

        if self.login and self.password and self.server:
            authorized = mt5.login(int(self.login), password=self.password, server=self.server)
            if not authorized:
                raise MT5ConnectionError(f"mt5.login() failed: {mt5.last_error()}")

        acct = mt5.account_info()
        if acct is None:
            raise MT5ConnectionError("mt5.account_info() returned None after connect")

        # --- Hard safety guard #1: refuse anything that isn't a demo account.
        is_demo = getattr(acct, "trade_mode", None) == mt5.ACCOUNT_TRADE_MODE_DEMO
        if settings.account_mode != "demo":
            raise MT5SafetyError(
                "ACCOUNT_MODE is not 'demo'. This build refuses to connect "
                "under any other setting until the strategy has cleared the "
                "full validation gate in blueprint section 33 AND a human "
                "has deliberately reviewed and changed this setting."
            )
        if not is_demo:
            raise MT5SafetyError(
                f"Connected MT5 account (login={acct.login}) is NOT reported "
                f"as a demo account by the broker (trade_mode={acct.trade_mode}). "
                "Refusing to proceed — this platform will not trade a real "
                "account automatically."
            )

        self._connected = True

    def _pip_size(self, symbol: str) -> float:
        info = self._mt5.symbol_info(symbol)
        return info.point * 10 if info else (0.01 if symbol.endswith("JPY") else 0.0001)

    # ---- market data --------------------------------------------------

    def get_tick(self, symbol: str) -> Tick:
        self._ensure_connected()
        mt5 = self._mt5
        mt5.symbol_select(symbol, True)
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise MT5ConnectionError(f"symbol_info_tick returned None for {symbol}: {mt5.last_error()}")
        return Tick(
            symbol=symbol,
            timestamp_utc=datetime.fromtimestamp(t.time, tz=timezone.utc),
            bid=t.bid, ask=t.ask, last=t.last, volume=t.volume,
        )

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        self._ensure_connected()
        mt5 = self._mt5
        tf_const = getattr(mt5, _TIMEFRAME_MAP_NAMES.get(timeframe, "TIMEFRAME_M5"))
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None:
            raise MT5ConnectionError(f"copy_rates_from_pos failed for {symbol}/{timeframe}: {mt5.last_error()}")
        bars = []
        for r in rates:
            bars.append(Bar(
                symbol=symbol, timeframe=timeframe,
                timestamp_utc=datetime.fromtimestamp(r["time"], tz=timezone.utc),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                volume=float(r["tick_volume"]),
            ))
        return bars

    def get_symbol_info(self, symbol: str) -> dict:
        self._ensure_connected()
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise MT5ConnectionError(f"symbol_info returned None for {symbol}")
        return {
            "symbol": symbol, "pip_size": self._pip_size(symbol),
            "contract_size": info.trade_contract_size,
            "tradeable": info.trade_mode != 0,
            "min_volume": info.volume_min, "volume_step": info.volume_step,
        }

    # ---- account --------------------------------------------------------

    def get_account(self) -> AccountInfo:
        self._ensure_connected()
        a = self._mt5.account_info()
        return AccountInfo(
            login=str(a.login), balance=a.balance, equity=a.equity,
            margin=a.margin, margin_free=a.margin_free, currency=a.currency,
            is_demo=(a.trade_mode == self._mt5.ACCOUNT_TRADE_MODE_DEMO),
        )

    def get_positions(self) -> list[dict]:
        self._ensure_connected()
        return [p._asdict() for p in (self._mt5.positions_get() or [])]

    def get_orders(self) -> list[dict]:
        self._ensure_connected()
        return [o._asdict() for o in (self._mt5.orders_get() or [])]

    # ---- trading ----------------------------------------------------------

    def order_check(self, req: OrderRequest) -> dict:
        self._ensure_connected()
        mt5 = self._mt5
        request = self._build_request(req, mt5)
        result = mt5.order_check(request)
        return {"ok": result.retcode == mt5.TRADE_RETCODE_DONE if result else False,
                "raw": result._asdict() if result else None}

    def _build_request(self, req: OrderRequest, mt5) -> dict:
        tick = self.get_tick(req.symbol)
        price = tick.ask if req.direction == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if req.direction == "BUY" else mt5.ORDER_TYPE_SELL
        return {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": req.symbol,
            "volume": req.volume,
            "type": order_type,
            "price": price,
            "sl": req.sl or 0.0,
            "tp": req.tp or 0.0,
            "deviation": 20,
            "comment": req.comment or "fx-platform",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

    def order_send(self, req: OrderRequest) -> OrderResult:
        self._ensure_connected()
        mt5 = self._mt5

        # --- Hard safety guard #2 (independent of the connect-time check):
        # re-verify on every single order, not just once at startup.
        acct = mt5.account_info()
        if settings.account_mode != "demo" or acct.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise MT5SafetyError(
                "Refusing order_send: ACCOUNT_MODE or the connected account's "
                "trade_mode is not demo. This is a deliberate hard stop."
            )

        request = self._build_request(req, mt5)
        t0 = datetime.now(timezone.utc)
        result = mt5.order_send(request)
        t1 = datetime.now(timezone.utc)

        if result is None:
            return OrderResult(status="error", broker_response_code=str(mt5.last_error()))

        status = "filled" if result.retcode == mt5.TRADE_RETCODE_DONE else "rejected"
        return OrderResult(
            status=status,
            executed_price=getattr(result, "price", None),
            executed_volume=getattr(result, "volume", None),
            broker_response_code=str(result.retcode),
            latency_ms=(t1 - t0).total_seconds() * 1000,
            slippage=abs((getattr(result, "price", request["price"]) or request["price"]) - request["price"]),
            raw=result._asdict(),
        )
