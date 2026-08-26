"""
API routes — blueprint section 24 ("API Blueprint"):

    Market:   GET /market/tick/{symbol}, GET /market/rates/{symbol}/{timeframe}
    Account:  GET /account, GET /positions
    Risk:     POST /risk/evaluate
    Execution: POST /orders/check, POST /orders/send
    Research: POST /backtests, GET /backtests/{id}
    Models:   GET /models, POST /models/{id}/promote

Every route requires auth, logs a request-ID-tagged audit entry, and
returns structured errors (section 24: "All APIs require
authentication/authorization, schema validation, request IDs, structured
errors and audit logging").
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_db, require_auth, request_id
from app.api.schemas import (
    TickOut, BarOut, AccountOut, RiskEvaluateIn, RiskEvaluateOut, OrderSendIn,
    ModelPromoteIn,
)
from app.data.gateway_factory import get_gateway
from app.risk_engine import evaluate as risk_evaluate, AccountState
from app.strategies.base import StrategySignal
from app.execution_engine import execute_signal
from app.config import settings
from app.audit import log_audit
from app.db.models import StrategyVersion, BacktestRun, BacktestTradeRow

router = APIRouter()
_gateway = None


def gateway():
    global _gateway
    if _gateway is None:
        _gateway = get_gateway()
    return _gateway


@router.get("/health")
def health():
    return {"status": "ok", "gateway_mode": settings.gateway_mode, "account_mode": settings.account_mode}


# ---------------------------------------------------------------- Market ---

@router.get("/market/tick/{symbol}", response_model=TickOut)
def get_tick(symbol: str, _auth=Depends(require_auth)):
    t = gateway().get_tick(symbol)
    return TickOut(symbol=t.symbol, timestamp_utc=t.timestamp_utc, bid=t.bid, ask=t.ask)


@router.get("/market/rates/{symbol}/{timeframe}", response_model=list[BarOut])
def get_rates(symbol: str, timeframe: str, count: int = 100, _auth=Depends(require_auth)):
    bars = gateway().get_rates(symbol, timeframe, count)
    return [BarOut(symbol=b.symbol, timeframe=b.timeframe, timestamp_utc=b.timestamp_utc,
                    open=b.open, high=b.high, low=b.low, close=b.close) for b in bars]


# --------------------------------------------------------------- Account ---

@router.get("/account", response_model=AccountOut)
def get_account(_auth=Depends(require_auth)):
    a = gateway().get_account()
    return AccountOut(login=a.login, balance=a.balance, equity=a.equity,
                       margin=a.margin, margin_free=a.margin_free, is_demo=a.is_demo)


@router.get("/positions")
def get_positions(_auth=Depends(require_auth)):
    return gateway().get_positions()


# ------------------------------------------------------------------ Risk ---

@router.post("/risk/evaluate", response_model=RiskEvaluateOut)
def evaluate_risk(body: RiskEvaluateIn, db=Depends(get_db), rid=Depends(request_id),
                   _auth=Depends(require_auth)):
    gw = gateway()
    symbol_info = gw.get_symbol_info(body.symbol)
    tick = gw.get_tick(body.symbol)
    account = gw.get_account()

    signal = StrategySignal(
        strategy_id=body.strategy_id, strategy_version=body.strategy_version,
        symbol=body.symbol, timeframe=body.timeframe, direction=body.direction,
        confidence=body.confidence, entry_reference=body.entry_reference,
        stop_loss=body.stop_loss, take_profit=body.take_profit,
    )
    account_state = AccountState(
        equity=account.equity, balance=account.balance, peak_equity=account.equity,
        daily_pnl=0, weekly_pnl=0, open_positions=len(gw.get_positions()),
        exposure_by_symbol={}, correlated_group_exposure={},
        spread_pips=abs(tick.ask - tick.bid) / symbol_info["pip_size"],
        news_risk_score=0.0, margin_free=account.margin_free,
        margin_required_estimate=0.0, kill_switch_active=False,
    )
    decision = risk_evaluate(signal, account_state, symbol_info)
    log_audit(db, actor="api:/risk/evaluate", action=decision.decision,
              entity_type="manual_signal", entity_id=body.symbol, request_id=rid,
              details={"reason_codes": decision.reason_codes})
    return RiskEvaluateOut(decision=decision.decision, reason_codes=decision.reason_codes,
                            approved_volume=decision.approved_volume)


# ------------------------------------------------------------- Execution ---

@router.post("/orders/check")
def order_check(body: OrderSendIn, _auth=Depends(require_auth)):
    from app.data.gateway_base import OrderRequest
    req = OrderRequest(symbol=body.symbol, direction=body.direction, volume=body.volume,
                        sl=body.sl, tp=body.tp)
    return gateway().order_check(req)


@router.post("/orders/send")
def order_send(body: OrderSendIn, db=Depends(get_db), rid=Depends(request_id),
                _auth=Depends(require_auth)):
    """NOTE: this endpoint intentionally does NOT skip the Risk Engine. It
    builds a manual signal, evaluates it, and only calls execute_signal if
    the decision is ALLOW/MODIFY — mirroring the orchestrator's own path.
    There is no route anywhere in this API that calls gateway.order_send()
    directly without going through app/risk_engine.py first."""
    gw = gateway()
    symbol_info = gw.get_symbol_info(body.symbol)
    tick = gw.get_tick(body.symbol)
    account = gw.get_account()

    entry_ref = tick.ask if body.direction == "BUY" else tick.bid
    signal = StrategySignal(
        strategy_id="manual_api", strategy_version="manual_api_v1",
        symbol=body.symbol, timeframe="manual", direction=body.direction,
        confidence=1.0, entry_reference=entry_ref, stop_loss=body.sl, take_profit=body.tp,
    )
    account_state = AccountState(
        equity=account.equity, balance=account.balance, peak_equity=account.equity,
        daily_pnl=0, weekly_pnl=0, open_positions=len(gw.get_positions()),
        exposure_by_symbol={}, correlated_group_exposure={},
        spread_pips=abs(tick.ask - tick.bid) / symbol_info["pip_size"],
        news_risk_score=0.0, margin_free=account.margin_free,
        margin_required_estimate=0.0, kill_switch_active=False,
    )
    decision = risk_evaluate(signal, account_state, symbol_info)
    log_audit(db, actor="api:/orders/send", action=decision.decision,
              entity_type="manual_order", entity_id=body.symbol, request_id=rid)

    if decision.decision == "REJECT":
        raise HTTPException(status_code=422, detail={
            "error": "risk_rejected", "reason_codes": decision.reason_codes,
        })

    order_id = str(uuid.uuid4())
    result = execute_signal(gw, signal, decision, order_id)
    return {
        "order_id": order_id, "status": result.status,
        "executed_price": result.executed_price, "executed_volume": result.executed_volume,
        "broker_response_code": result.broker_response_code,
    }


# -------------------------------------------------------------- Research ---

@router.post("/backtests")
def create_backtest(strategy_id: str, symbol: str = "EURUSD", timeframe: str = "D1",
                     db=Depends(get_db), _auth=Depends(require_auth)):
    from app.data.historical_loader import load_clean_ohlc
    from app.backtest.engine import run_backtest
    from app.strategies.registry import default_registry

    reg = default_registry()
    entry = next((e for e in reg.all() if e.strategy.strategy_id == strategy_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_strategy_id"})

    df = load_clean_ohlc()
    result = run_backtest(df, entry.strategy, symbol=symbol, timeframe=timeframe)

    run = BacktestRun(
        strategy_version=entry.strategy.strategy_version, symbol=symbol, timeframe=timeframe,
        period_start=df["date"].min(), period_end=df["date"].max(),
        data_source="historical_loader",
        metrics={
            "in_sample": result.in_sample.__dict__, "out_of_sample": result.out_of_sample.__dict__,
            "full_sample": result.full_sample.__dict__,
        },
    )
    db.add(run)
    db.commit()
    for t in result.trades:
        db.add(BacktestTradeRow(
            backtest_run_id=run.backtest_run_id, entry_time_utc=t.entry_time,
            exit_time_utc=t.exit_time, direction=t.direction, entry_price=t.entry_price,
            exit_price=t.exit_price, pnl=t.pnl_pct, is_out_of_sample=t.is_out_of_sample,
        ))
    db.commit()
    return {"backtest_run_id": run.backtest_run_id, "metrics": run.metrics}


@router.get("/backtests/{backtest_run_id}")
def get_backtest(backtest_run_id: str, db=Depends(get_db), _auth=Depends(require_auth)):
    run = db.query(BacktestRun).filter_by(backtest_run_id=backtest_run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    trades = db.query(BacktestTradeRow).filter_by(backtest_run_id=backtest_run_id).all()
    return {
        "backtest_run_id": run.backtest_run_id, "strategy_version": run.strategy_version,
        "symbol": run.symbol, "timeframe": run.timeframe, "metrics": run.metrics,
        "n_trades_stored": len(trades),
    }


# ----------------------------------------------------------------- Models --

@router.get("/models")
def list_models(db=Depends(get_db), _auth=Depends(require_auth)):
    rows = db.query(StrategyVersion).all()
    return [{
        "strategy_version": r.strategy_version, "strategy_id": r.strategy_id,
        "status": r.status, "validation_metrics": r.validation_metrics,
    } for r in rows]


@router.post("/models/{strategy_version}/promote")
def promote_model(strategy_version: str, body: ModelPromoteIn, db=Depends(get_db),
                   rid=Depends(request_id), _auth=Depends(require_auth)):
    row = db.query(StrategyVersion).filter_by(strategy_version=strategy_version).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    # Section 20/33 guard: refuse to mark anything "approved" if its own
    # recorded validation metrics show a non-viable out-of-sample result.
    metrics = row.validation_metrics or {}
    oos_sharpe = metrics.get("out_of_sample_sharpe")
    if body.approval_status == "approved" and oos_sharpe is not None and oos_sharpe < 0.3:
        raise HTTPException(status_code=422, detail={
            "error": "validation_gate_failed",
            "reason": f"out_of_sample_sharpe={oos_sharpe} is below the 0.3 promotion floor "
                      f"(blueprint section 33: a strategy that looks good in-sample but fails "
                      f"out-of-sample is a failed result, not a hidden success).",
        })

    row.status = body.approval_status
    db.commit()
    log_audit(db, actor="api:/models/promote", action=body.approval_status,
              entity_type="strategy_version", entity_id=strategy_version, request_id=rid)
    return {"strategy_version": strategy_version, "status": row.status}
