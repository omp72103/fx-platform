"""
SQLAlchemy models for the tables actually exercised by the running MVP loop
(data -> features -> regime -> strategy -> signal -> risk -> execution ->
persistence). Deliberately portable (JSON columns, string UUIDs) so the same
code runs against SQLite in this sandbox and Postgres/TimescaleDB on the VPS.

db/init.sql is the canonical, full blueprint-spec (section 23) schema for
direct SQL / reporting use on Postgres, including tables (ticks, news,
economic_events, model_versions, model_metrics) not yet wired into the ORM
because the MVP loop doesn't exercise them yet. backtest_runs/backtest_trades
ARE wired below since the /backtests API actually runs the engine. Wire up
Alembic migrations before this diverges further.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, JSON, Boolean, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Symbol(Base):
    __tablename__ = "symbols"
    symbol = Column(String, primary_key=True)
    base_ccy = Column(String, nullable=False)
    quote_ccy = Column(String, nullable=False)
    pip_size = Column(Float, nullable=False, default=0.0001)
    contract_size = Column(Float, nullable=False, default=100000)
    active = Column(Boolean, nullable=False, default=True)


class Bar(Base):
    __tablename__ = "bars"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    timeframe = Column(String, nullable=False)
    timestamp_utc = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float)
    source = Column(String, nullable=False)


class FeatureRow(Base):
    __tablename__ = "features"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    timeframe = Column(String, nullable=False)
    timestamp_utc = Column(DateTime(timezone=True), nullable=False)
    feature_schema_version = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)


class MarketRegime(Base):
    __tablename__ = "market_regimes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    timeframe = Column(String, nullable=False)
    timestamp_utc = Column(DateTime(timezone=True), nullable=False)
    regime = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    model_version = Column(String, nullable=False)


class Strategy(Base):
    __tablename__ = "strategies"
    strategy_id = Column(String, primary_key=True)
    family = Column(String, nullable=False)
    description = Column(Text)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    strategy_version = Column(String, primary_key=True)
    strategy_id = Column(String, ForeignKey("strategies.strategy_id"), nullable=False)
    supported_symbols = Column(JSON)
    supported_timeframes = Column(JSON)
    required_features = Column(JSON)
    preferred_regimes = Column(JSON)
    parameters = Column(JSON)
    validation_metrics = Column(JSON)
    status = Column(String, nullable=False, default="candidate")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class StrategySignal(Base):
    __tablename__ = "strategy_signals"
    signal_id = Column(String, primary_key=True, default=_uuid)
    strategy_id = Column(String, ForeignKey("strategies.strategy_id"), nullable=False)
    strategy_version = Column(String, ForeignKey("strategy_versions.strategy_version"), nullable=False)
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    timeframe = Column(String, nullable=False)
    timestamp_utc = Column(DateTime(timezone=True), nullable=False)
    regime = Column(String)
    direction = Column(String, nullable=False)  # BUY / SELL / HOLD
    confidence = Column(Float)
    entry_reference = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    reason_codes = Column(JSON)
    selected = Column(Boolean, default=False)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    risk_event_id = Column(String, primary_key=True, default=_uuid)
    signal_id = Column(String, ForeignKey("strategy_signals.signal_id"))
    timestamp_utc = Column(DateTime(timezone=True), default=utcnow)
    decision = Column(String, nullable=False)  # ALLOW / MODIFY / REJECT
    reason_codes = Column(JSON)
    account_state = Column(JSON)
    applied_limits = Column(JSON)


class Order(Base):
    __tablename__ = "orders"
    order_id = Column(String, primary_key=True, default=_uuid)
    signal_id = Column(String, ForeignKey("strategy_signals.signal_id"))
    risk_event_id = Column(String, ForeignKey("risk_events.risk_event_id"))
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    direction = Column(String, nullable=False)
    requested_volume = Column(Float, nullable=False)
    requested_price = Column(Float)
    sl = Column(Float)
    tp = Column(Float)
    request_timestamp_utc = Column(DateTime(timezone=True), default=utcnow)
    status = Column(String, nullable=False, default="pending")
    account_mode = Column(String, nullable=False)


class Execution(Base):
    __tablename__ = "executions"
    execution_id = Column(String, primary_key=True, default=_uuid)
    order_id = Column(String, ForeignKey("orders.order_id"), nullable=False)
    strategy_version = Column(String)
    executed_price = Column(Float)
    executed_volume = Column(Float)
    execute_timestamp_utc = Column(DateTime(timezone=True))
    latency_ms = Column(Float)
    slippage = Column(Float)
    spread = Column(Float)
    status = Column(String, nullable=False)
    broker_response_code = Column(String)
    raw_broker_response = Column(JSON)


class Position(Base):
    __tablename__ = "positions"
    position_id = Column(String, primary_key=True, default=_uuid)
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    direction = Column(String, nullable=False)
    volume = Column(Float, nullable=False)
    avg_open_price = Column(Float, nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True))
    status = Column(String, nullable=False, default="open")


class Trade(Base):
    __tablename__ = "trades"
    trade_id = Column(String, primary_key=True, default=_uuid)
    position_id = Column(String, ForeignKey("positions.position_id"))
    strategy_id = Column(String, ForeignKey("strategies.strategy_id"))
    strategy_version = Column(String)
    symbol = Column(String, ForeignKey("symbols.symbol"), nullable=False)
    regime = Column(String)
    entry_time_utc = Column(DateTime(timezone=True))
    exit_time_utc = Column(DateTime(timezone=True))
    entry_price = Column(Float)
    exit_price = Column(Float)
    volume = Column(Float)
    pnl = Column(Float)
    mfe = Column(Float)
    mae = Column(Float)
    holding_seconds = Column(Float)
    slippage = Column(Float)
    result = Column(String)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc = Column(DateTime(timezone=True), default=utcnow)
    equity = Column(Float, nullable=False)
    balance = Column(Float, nullable=False)
    margin_used = Column(Float)
    open_positions = Column(Integer)
    net_exposure_by_symbol = Column(JSON)
    drawdown_pct = Column(Float)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc = Column(DateTime(timezone=True), default=utcnow)
    component = Column(String, nullable=False)
    level = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    request_id = Column(String)
    payload = Column(JSON)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    backtest_run_id = Column(String, primary_key=True, default=_uuid)
    strategy_version = Column(String)
    symbol = Column(String)
    timeframe = Column(String)
    period_start = Column(DateTime(timezone=True))
    period_end = Column(DateTime(timezone=True))
    in_sample_end = Column(DateTime(timezone=True))
    data_source = Column(String)
    metrics = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class BacktestTradeRow(Base):
    __tablename__ = "backtest_trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id = Column(String, ForeignKey("backtest_runs.backtest_run_id"), nullable=False)
    entry_time_utc = Column(DateTime(timezone=True))
    exit_time_utc = Column(DateTime(timezone=True))
    direction = Column(String)
    entry_price = Column(Float)
    exit_price = Column(Float)
    pnl = Column(Float)
    is_out_of_sample = Column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc = Column(DateTime(timezone=True), default=utcnow)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String)
    entity_id = Column(String)
    request_id = Column(String)
    details = Column(JSON)
