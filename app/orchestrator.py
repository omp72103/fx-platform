"""
Orchestrator — wires every layer together into the one pipeline the whole
blueprint describes (section 2 / section 35):

    MARKET DATA -> FEATURES -> REGIME -> STRATEGY SELECTOR -> SIGNAL ->
    RISK ENGINE -> EXECUTION -> TRADE + AUDIT -> LEARNING/MLOps

Each run_once() call does exactly one pass over every configured
symbol/timeframe. scripts/run_paper_loop.py calls this on a schedule. Every
step's output is persisted (bars/features/market_regimes/strategy_signals/
risk_events/orders/executions/portfolio_snapshots) so the whole thing is
reconstructable from the database afterwards — blueprint section 4: "every
order must be reconstructable from an audit trail."
"""
import uuid
import pandas as pd
from datetime import datetime, timezone

from app.config import settings
from app.data.gateway_base import Gateway
from app.features.engine import build_features, latest_feature_row, FEATURE_SCHEMA_VERSION
from app.features.regime import add_regime, classify_regime_with_confidence, REGIME_MODEL_VERSION
from app.strategies.base import MarketState
from app.strategies.registry import StrategyRegistry, select_signal
from app.risk_engine import evaluate as risk_evaluate, AccountState
from app.execution_engine import execute_signal
from app.audit import log_event, log_audit, new_request_id
from app.db.models import (
    Symbol, Bar, FeatureRow, MarketRegime, Strategy as StrategyRow,
    StrategyVersion, StrategySignal as StrategySignalRow, RiskEvent, Order,
    Execution, PortfolioSnapshot,
)

MIN_BARS_REQUIRED = 60  # enough history for the 50-period slope + 252-window vol percentile to warm up somewhat


def seed_reference_data(session):
    """Idempotent seed — needed on SQLite (dev/sandbox) since db/init.sql's
    seed INSERT only runs against Postgres via docker-entrypoint-initdb.d."""
    existing = {s.symbol for s in session.query(Symbol).all()}
    defaults = {
        "EURUSD": ("EUR", "USD", 0.0001), "GBPUSD": ("GBP", "USD", 0.0001),
        "USDJPY": ("USD", "JPY", 0.01),
    }
    for sym in settings.symbols:
        if sym in existing:
            continue
        base, quote, pip = defaults.get(sym, (sym[:3], sym[3:], 0.0001))
        session.add(Symbol(symbol=sym, base_ccy=base, quote_ccy=quote, pip_size=pip, contract_size=100_000))
    session.commit()

    existing_strats = {s.strategy_id for s in session.query(StrategyRow).all()}
    for entry in _registry_entries_for_seed():
        if entry["strategy_id"] not in existing_strats:
            session.add(StrategyRow(strategy_id=entry["strategy_id"], family=entry["family"],
                                     description=entry["description"]))
    session.commit()

    existing_versions = {v.strategy_version for v in session.query(StrategyVersion).all()}
    for entry in _registry_entries_for_seed():
        if entry["strategy_version"] not in existing_versions:
            session.add(StrategyVersion(
                strategy_version=entry["strategy_version"], strategy_id=entry["strategy_id"],
                supported_symbols=settings.symbols, supported_timeframes=settings.timeframes,
                preferred_regimes=list(entry["preferred_regimes"]),
                status="candidate", validation_metrics=entry["validation_metrics"],
            ))
    session.commit()


def _registry_entries_for_seed():
    from app.strategies.registry import default_registry
    reg = default_registry()
    out = []
    for e in reg.all():
        out.append({
            "strategy_id": e.strategy.strategy_id, "strategy_version": e.strategy.strategy_version,
            "family": e.strategy.family, "description": f"{e.strategy.family} strategy",
            "preferred_regimes": e.preferred_regimes, "validation_metrics": e.validation_metrics,
        })
    return out


def _bars_to_df(bars) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": b.timestamp_utc, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
    } for b in bars])


def run_once(session, gateway: Gateway, registry: StrategyRegistry):
    request_id = new_request_id()
    account = gateway.get_account()

    # Track a naive running peak/PnL in-memory-per-call via portfolio_snapshots
    # history; good enough for paper trading, replace with a proper equity
    # curve service before any live promotion.
    snapshots = session.query(PortfolioSnapshot).order_by(PortfolioSnapshot.timestamp_utc.desc()).limit(200).all()
    peak_equity = max([s.equity for s in snapshots] + [account.equity])
    today = datetime.now(timezone.utc).date()
    daily_pnl = sum(s.equity - account.balance for s in snapshots
                     if s.timestamp_utc and s.timestamp_utc.date() == today) or 0.0

    for symbol in settings.symbols:
        for timeframe in settings.timeframes:
            try:
                _process_symbol_timeframe(session, gateway, registry, symbol, timeframe,
                                           account, peak_equity, daily_pnl, request_id)
            except Exception as e:  # noqa: BLE001 - one symbol failing must not kill the loop
                log_event(session, component="orchestrator", level="ERROR",
                           message=f"{symbol}/{timeframe} failed: {e}", request_id=request_id)

    session.add(PortfolioSnapshot(
        equity=account.equity, balance=account.balance, margin_used=account.margin,
        open_positions=len(gateway.get_positions()),
        net_exposure_by_symbol=_exposure_by_symbol(gateway),
        drawdown_pct=max(0.0, (peak_equity - account.equity) / peak_equity * 100) if peak_equity else 0.0,
    ))
    session.commit()


def _exposure_by_symbol(gateway: Gateway) -> dict:
    out = {}
    for p in gateway.get_positions():
        notional = p["volume"] * 100_000 * p["avg_open_price"]
        out[p["symbol"]] = out.get(p["symbol"], 0.0) + notional
    return out


def _process_symbol_timeframe(session, gateway, registry, symbol, timeframe,
                               account, peak_equity, daily_pnl, request_id):
    bars = gateway.get_rates(symbol, timeframe, max(300, MIN_BARS_REQUIRED + 60))
    if len(bars) < MIN_BARS_REQUIRED:
        return

    latest_bar = bars[-1]
    session.add(Bar(symbol=symbol, timeframe=timeframe, timestamp_utc=latest_bar.timestamp_utc,
                     open=latest_bar.open, high=latest_bar.high, low=latest_bar.low,
                     close=latest_bar.close, volume=latest_bar.volume, source=gateway.__class__.__name__))

    df = _bars_to_df(bars)
    df = build_features(df)
    row = df.iloc[-1]
    regime, confidence = classify_regime_with_confidence(row)

    session.add(FeatureRow(symbol=symbol, timeframe=timeframe, timestamp_utc=latest_bar.timestamp_utc,
                            feature_schema_version=FEATURE_SCHEMA_VERSION, payload=latest_feature_row(df)))
    session.add(MarketRegime(symbol=symbol, timeframe=timeframe, timestamp_utc=latest_bar.timestamp_utc,
                              regime=regime, confidence=confidence, model_version=REGIME_MODEL_VERSION))
    session.commit()

    ms = MarketState(symbol=symbol, timeframe=timeframe, features_df=df,
                      regime=regime, regime_confidence=confidence, account_equity=account.equity)
    signal = select_signal(registry, ms)
    if signal is None:
        return

    signal_row = StrategySignalRow(
        strategy_id=signal.strategy_id, strategy_version=signal.strategy_version,
        symbol=symbol, timeframe=timeframe, timestamp_utc=latest_bar.timestamp_utc,
        regime=regime, direction=signal.direction, confidence=signal.confidence,
        entry_reference=signal.entry_reference, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, reason_codes=signal.reason_codes, selected=True,
    )
    session.add(signal_row)
    session.commit()

    symbol_info = gateway.get_symbol_info(symbol)
    tick = gateway.get_tick(symbol)
    account_state = AccountState(
        equity=account.equity, balance=account.balance, peak_equity=peak_equity,
        daily_pnl=daily_pnl, weekly_pnl=daily_pnl, open_positions=len(gateway.get_positions()),
        exposure_by_symbol=_exposure_by_symbol(gateway), correlated_group_exposure={},
        spread_pips=abs(tick.ask - tick.bid) / symbol_info["pip_size"],
        news_risk_score=0.0, margin_free=account.margin_free,
        margin_required_estimate=0.0, kill_switch_active=False,
    )
    risk_decision = risk_evaluate(signal, account_state, symbol_info)

    risk_row = RiskEvent(
        signal_id=signal_row.signal_id, decision=risk_decision.decision,
        reason_codes=risk_decision.reason_codes, account_state=risk_decision.account_state_snapshot,
        applied_limits=risk_decision.applied_limits,
    )
    session.add(risk_row)
    session.commit()
    log_audit(session, actor="risk_engine", action=risk_decision.decision,
              entity_type="strategy_signal", entity_id=signal_row.signal_id,
              request_id=request_id, details={"reason_codes": risk_decision.reason_codes})

    if risk_decision.decision == "REJECT":
        return

    order_id = str(uuid.uuid4())
    order_row = Order(
        order_id=order_id, signal_id=signal_row.signal_id, risk_event_id=risk_row.risk_event_id,
        symbol=symbol, direction=signal.direction, requested_volume=risk_decision.approved_volume,
        requested_price=signal.entry_reference, sl=signal.stop_loss, tp=signal.take_profit,
        status="pending", account_mode=settings.account_mode,
    )
    session.add(order_row)
    session.commit()

    exec_result = execute_signal(gateway, signal, risk_decision, order_id)
    order_row.status = exec_result.status
    session.add(Execution(
        order_id=order_id, strategy_version=signal.strategy_version,
        executed_price=exec_result.executed_price, executed_volume=exec_result.executed_volume,
        execute_timestamp_utc=exec_result.execute_timestamp_utc, latency_ms=exec_result.latency_ms,
        slippage=exec_result.slippage, spread=exec_result.spread, status=exec_result.status,
        broker_response_code=exec_result.broker_response_code,
    ))
    session.commit()
    log_audit(session, actor="execution_engine", action=exec_result.status,
              entity_type="order", entity_id=order_id, request_id=request_id,
              details={"symbol": symbol, "direction": signal.direction, "volume": risk_decision.approved_volume})
