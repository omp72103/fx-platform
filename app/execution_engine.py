"""
Execution Engine — blueprint section 17:

    Flow: receive risk-approved order -> validate symbol/market state ->
    validate price/spread -> order/margin check -> send -> capture broker
    response -> reconcile position -> persist execution.

    Mandatory execution data: request_id, signal_id, strategy_version,
    requested/executed price, requested/executed volume, SL/TP,
    request/execution timestamps, latency_ms, slippage, spread, status,
    broker response/error code.

This module is the ONLY place in the codebase allowed to call
gateway.order_send(). It refuses to run unless it is handed an already-ALLOW
or already-MODIFY RiskDecision — a REJECT (or anything else) never reaches
here. That mirrors blueprint section 34: "Never send an order without an
auditable risk decision."

Independent of the mock/MT5 gateway split, this module carries its OWN
demo-only guard (settings.account_mode) so that even a future gateway
implementation that forgets its own safety check cannot cause a live order.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.data.gateway_base import Gateway, OrderRequest
from app.risk_engine import RiskDecision
from app.strategies.base import StrategySignal


class LiveTradingBlockedError(RuntimeError):
    """Raised if anything ever tries to execute outside of ACCOUNT_MODE=demo
    without going through the deliberate promotion process in blueprint
    section 20 (Model Governance) / section 27 (Deployment Pipeline)."""
    pass


@dataclass
class ExecutionResult:
    order_id: str
    status: str                  # filled / rejected / error / blocked
    requested_price: float
    executed_price: float | None
    requested_volume: float
    executed_volume: float | None
    latency_ms: float | None
    slippage: float | None
    spread: float | None
    broker_response_code: str | None
    request_timestamp_utc: datetime
    execute_timestamp_utc: datetime | None


def execute_signal(gateway: Gateway, signal: StrategySignal, risk_decision: RiskDecision,
                    order_id: str) -> ExecutionResult:
    request_ts = datetime.now(timezone.utc)

    if risk_decision.decision not in ("ALLOW", "MODIFY"):
        raise ValueError(
            f"execute_signal called with a {risk_decision.decision} risk decision "
            f"for order {order_id}. This must never happen — the caller is "
            f"responsible for stopping at the Risk Engine's REJECT."
        )

    # Hard guard, independent of whichever gateway is configured.
    if settings.account_mode != "demo":
        return ExecutionResult(
            order_id=order_id, status="blocked",
            requested_price=signal.entry_reference, executed_price=None,
            requested_volume=risk_decision.approved_volume, executed_volume=None,
            latency_ms=None, slippage=None, spread=None,
            broker_response_code="ACCOUNT_MODE_NOT_DEMO",
            request_timestamp_utc=request_ts, execute_timestamp_utc=None,
        )

    tick = gateway.get_tick(signal.symbol)
    spread = abs(tick.ask - tick.bid)

    req = OrderRequest(
        symbol=signal.symbol, direction=signal.direction,
        volume=risk_decision.approved_volume, sl=signal.stop_loss, tp=signal.take_profit,
        comment=f"{signal.strategy_id}:{signal.strategy_version}", order_id=order_id,
    )

    check = gateway.order_check(req)
    if not check.get("ok", False):
        return ExecutionResult(
            order_id=order_id, status="rejected",
            requested_price=signal.entry_reference, executed_price=None,
            requested_volume=req.volume, executed_volume=None,
            latency_ms=None, slippage=None, spread=spread,
            broker_response_code=str(check.get("reason", "order_check_failed")),
            request_timestamp_utc=request_ts, execute_timestamp_utc=None,
        )

    result = gateway.order_send(req)
    execute_ts = datetime.now(timezone.utc)

    slippage = None
    if result.executed_price is not None and signal.entry_reference:
        slippage = abs(result.executed_price - signal.entry_reference)

    return ExecutionResult(
        order_id=order_id, status=result.status,
        requested_price=signal.entry_reference, executed_price=result.executed_price,
        requested_volume=req.volume, executed_volume=result.executed_volume,
        latency_ms=result.latency_ms, slippage=slippage, spread=spread,
        broker_response_code=result.broker_response_code,
        request_timestamp_utc=request_ts, execute_timestamp_utc=execute_ts,
    )
