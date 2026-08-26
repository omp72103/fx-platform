-- ---------------------------------------------------------------------------
-- fx-platform database schema
-- Implements the "Database Blueprint" (section 23) of
-- AI_Adaptive_Forex_Trading_Platform_Blueprint_Technical_Architecture_v1
--
-- Core tables: symbols, ticks, bars, features, news, economic_events,
-- strategies, strategy_versions, strategy_signals, market_regimes,
-- model_versions, orders, executions, positions, trades, risk_events,
-- portfolio_snapshots, backtest_runs, backtest_trades, model_metrics,
-- system_events, audit_logs.
--
-- Traceability IDs (event_id, signal_id, order_id, execution_id, trade_id,
-- strategy_version, model_version) are carried end to end per the blueprint.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================= Reference data ===============================

CREATE TABLE IF NOT EXISTS symbols (
    symbol          TEXT PRIMARY KEY,          -- e.g. EURUSD
    base_ccy        TEXT NOT NULL,
    quote_ccy       TEXT NOT NULL,
    pip_size        NUMERIC NOT NULL DEFAULT 0.0001,
    contract_size   NUMERIC NOT NULL DEFAULT 100000,
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

-- ============================= Market data ==================================

CREATE TABLE IF NOT EXISTS ticks (
    event_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    timestamp_utc   TIMESTAMPTZ NOT NULL,
    bid             NUMERIC NOT NULL,
    ask             NUMERIC NOT NULL,
    last            NUMERIC,
    volume          NUMERIC,
    flags           TEXT,
    source          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, timestamp_utc);

CREATE TABLE IF NOT EXISTS bars (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    timeframe       TEXT NOT NULL,              -- M5 / M15 / H1 / D1 ...
    timestamp_utc   TIMESTAMPTZ NOT NULL,
    open            NUMERIC NOT NULL,
    high            NUMERIC NOT NULL,
    low             NUMERIC NOT NULL,
    close           NUMERIC NOT NULL,
    volume          NUMERIC,
    source          TEXT NOT NULL,
    UNIQUE(symbol, timeframe, timestamp_utc)
);
CREATE INDEX IF NOT EXISTS idx_bars_symbol_tf_ts ON bars(symbol, timeframe, timestamp_utc);

CREATE TABLE IF NOT EXISTS features (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    timeframe       TEXT NOT NULL,
    timestamp_utc   TIMESTAMPTZ NOT NULL,
    feature_schema_version TEXT NOT NULL,       -- deterministic, versioned (section 8)
    payload         JSONB NOT NULL,              -- ret_1d, ema_spread, rsi_14, atr_14, ...
    UNIQUE(symbol, timeframe, timestamp_utc, feature_schema_version)
);
CREATE INDEX IF NOT EXISTS idx_features_symbol_tf_ts ON features(symbol, timeframe, timestamp_utc);

-- ============================= News / calendar ===============================

CREATE TABLE IF NOT EXISTS economic_events (
    event_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    currency        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    importance      TEXT NOT NULL,               -- LOW / MEDIUM / HIGH
    forecast        NUMERIC,
    previous        NUMERIC,
    actual          NUMERIC,
    event_risk_score NUMERIC,
    scheduled_utc   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS news (
    event_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_timestamp TIMESTAMPTZ NOT NULL,
    currency        TEXT,
    event_type      TEXT,
    sentiment       NUMERIC,
    impact_score    NUMERIC,
    confidence      NUMERIC,
    raw_text        TEXT,
    structured      JSONB
);

-- ============================= Regime ========================================

CREATE TABLE IF NOT EXISTS market_regimes (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    timeframe       TEXT NOT NULL,
    timestamp_utc   TIMESTAMPTZ NOT NULL,
    regime          TEXT NOT NULL,               -- TREND_UP / TREND_DOWN / RANGE / ...
    confidence      NUMERIC NOT NULL,
    model_version   TEXT NOT NULL,
    UNIQUE(symbol, timeframe, timestamp_utc, model_version)
);

-- ============================= Strategies ====================================

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id     TEXT PRIMARY KEY,
    family          TEXT NOT NULL,               -- trend_following / mean_reversion / breakout ...
    description     TEXT
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_version TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL REFERENCES strategies(strategy_id),
    supported_symbols TEXT[],
    supported_timeframes TEXT[],
    required_features TEXT[],
    preferred_regimes TEXT[],
    parameters      JSONB,
    validation_metrics JSONB,
    status          TEXT NOT NULL DEFAULT 'candidate', -- candidate/approved/deprecated
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS strategy_signals (
    signal_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    strategy_id     TEXT NOT NULL REFERENCES strategies(strategy_id),
    strategy_version TEXT NOT NULL REFERENCES strategy_versions(strategy_version),
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    timeframe       TEXT NOT NULL,
    timestamp_utc   TIMESTAMPTZ NOT NULL,
    regime          TEXT,
    direction       TEXT NOT NULL,               -- BUY / SELL / HOLD
    confidence      NUMERIC,
    entry_reference NUMERIC,
    stop_loss       NUMERIC,
    take_profit     NUMERIC,
    reason_codes    TEXT[],
    selected        BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON strategy_signals(symbol, timestamp_utc);

-- ============================= Models =========================================

CREATE TABLE IF NOT EXISTS model_versions (
    model_version   TEXT PRIMARY KEY,
    model_name      TEXT NOT NULL,
    training_dataset_version TEXT,
    training_period_start TIMESTAMPTZ,
    training_period_end   TIMESTAMPTZ,
    feature_schema_version TEXT,
    metrics         JSONB,
    approval_status TEXT NOT NULL DEFAULT 'candidate', -- candidate/shadow/approved/rejected
    deployment_status TEXT NOT NULL DEFAULT 'none',    -- none/shadow/production/retired
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_metrics (
    id              BIGSERIAL PRIMARY KEY,
    model_version   TEXT NOT NULL REFERENCES model_versions(model_version),
    metric_name     TEXT NOT NULL,
    metric_value    NUMERIC NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================= Risk / execution ==============================

CREATE TABLE IF NOT EXISTS risk_events (
    risk_event_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    signal_id       UUID REFERENCES strategy_signals(signal_id),
    timestamp_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision        TEXT NOT NULL,               -- ALLOW / MODIFY / REJECT
    reason_codes    TEXT[],
    account_state   JSONB,
    applied_limits  JSONB
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    signal_id       UUID REFERENCES strategy_signals(signal_id),
    risk_event_id   UUID REFERENCES risk_events(risk_event_id),
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    direction       TEXT NOT NULL,
    requested_volume NUMERIC NOT NULL,
    requested_price NUMERIC,
    sl              NUMERIC,
    tp              NUMERIC,
    request_timestamp_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/sent/filled/rejected/error
    account_mode    TEXT NOT NULL                    -- demo / live -- see execution_engine guard
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id        UUID NOT NULL REFERENCES orders(order_id),
    strategy_version TEXT,
    executed_price  NUMERIC,
    executed_volume NUMERIC,
    execute_timestamp_utc TIMESTAMPTZ,
    latency_ms      NUMERIC,
    slippage        NUMERIC,
    spread          NUMERIC,
    status          TEXT NOT NULL,               -- filled/partial/rejected/error
    broker_response_code TEXT,
    raw_broker_response JSONB
);

CREATE TABLE IF NOT EXISTS positions (
    position_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    direction       TEXT NOT NULL,
    volume          NUMERIC NOT NULL,
    avg_open_price  NUMERIC NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    position_id     UUID REFERENCES positions(position_id),
    strategy_id     TEXT REFERENCES strategies(strategy_id),
    strategy_version TEXT,
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    regime          TEXT,
    entry_time_utc  TIMESTAMPTZ,
    exit_time_utc   TIMESTAMPTZ,
    entry_price     NUMERIC,
    exit_price      NUMERIC,
    volume          NUMERIC,
    pnl             NUMERIC,
    mfe             NUMERIC,
    mae             NUMERIC,
    holding_seconds NUMERIC,
    slippage        NUMERIC,
    result          TEXT                          -- win/loss/breakeven
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    timestamp_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
    equity          NUMERIC NOT NULL,
    balance         NUMERIC NOT NULL,
    margin_used     NUMERIC,
    open_positions  INTEGER,
    net_exposure_by_symbol JSONB,
    drawdown_pct    NUMERIC
);

-- ============================= Backtesting ====================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    backtest_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    strategy_version TEXT REFERENCES strategy_versions(strategy_version),
    symbol          TEXT,
    timeframe       TEXT,
    period_start    TIMESTAMPTZ,
    period_end      TIMESTAMPTZ,
    in_sample_end   TIMESTAMPTZ,
    data_source     TEXT,
    metrics         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id              BIGSERIAL PRIMARY KEY,
    backtest_run_id UUID NOT NULL REFERENCES backtest_runs(backtest_run_id),
    entry_time_utc  TIMESTAMPTZ,
    exit_time_utc   TIMESTAMPTZ,
    direction       TEXT,
    entry_price     NUMERIC,
    exit_price      NUMERIC,
    pnl             NUMERIC,
    is_out_of_sample BOOLEAN NOT NULL DEFAULT FALSE
);

-- ============================= Audit / observability ==========================

CREATE TABLE IF NOT EXISTS system_events (
    id              BIGSERIAL PRIMARY KEY,
    timestamp_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
    component       TEXT NOT NULL,
    level           TEXT NOT NULL,               -- INFO/WARN/ERROR/CRITICAL
    message         TEXT NOT NULL,
    request_id      UUID,
    payload         JSONB
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    timestamp_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT NOT NULL,               -- service name / user
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    request_id      UUID,
    details         JSONB
);

-- ============================= Seed reference data =============================

INSERT INTO symbols (symbol, base_ccy, quote_ccy, pip_size, contract_size) VALUES
    ('EURUSD', 'EUR', 'USD', 0.0001, 100000),
    ('GBPUSD', 'GBP', 'USD', 0.0001, 100000),
    ('USDJPY', 'USD', 'JPY', 0.01,   100000)
ON CONFLICT (symbol) DO NOTHING;
