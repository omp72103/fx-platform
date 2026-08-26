# fx-platform

A working implementation of the pipeline described in
`AI_Adaptive_Forex_Trading_Platform_Blueprint_Technical_Architecture_v1.pdf`:

```
MARKET DATA -> FEATURES -> REGIME -> STRATEGY SELECTOR -> SIGNAL ->
RISK ENGINE -> EXECUTION -> TRADE + AUDIT -> LEARNING/MLOps
```

It runs end-to-end today against a deterministic mock broker (zero setup,
zero risk) and is wired to switch to a real MT5 **demo** account with one
config change — see `RUNBOOK.md` for that path. It does **not** place real
orders with real money, and two independent code paths refuse to let it,
until a human deliberately changes `ACCOUNT_MODE` after the validation
gates in blueprint section 33 are actually met (see `RUNBOOK.md` section 6).

## Quickstart (2 minutes, no broker needed)

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/run_paper_loop.py --once
```

That's the whole pipeline running once against the built-in paper simulator.
Run it continuously with `python scripts/run_paper_loop.py`, or bring up the
full stack (Postgres + API + loop) with `docker compose up -d --build`.

## What's actually here

| Blueprint section | File(s) |
|---|---|
| 2. Core System Pipeline | `app/orchestrator.py` |
| 4. Architecture Principles | throughout — see docstrings |
| 7. Market Data Pipeline | `app/data/historical_loader.py`, `app/data/mt5_gateway.py`, `app/data/mock_gateway.py` |
| 8. Feature Engineering | `app/features/engine.py` |
| 9. Market Regime Engine | `app/features/regime.py` |
| 10-11. Strategy Framework, Registry & Selection | `app/strategies/` |
| 14. Risk Engine | `app/risk_engine.py` |
| 16. MT5 Integration | `app/data/mt5_gateway.py`, `RUNBOOK.md` |
| 17. Execution Engine | `app/execution_engine.py` |
| 18-19. Backtesting & Validation | `app/backtest/engine.py` |
| 20. Model Governance | `app/db/models.py` (`StrategyVersion.status`), `/models/{v}/promote` |
| 23. Database Blueprint | `db/init.sql` (full spec) + `app/db/models.py` (what the MVP loop actually uses) |
| 24. API Blueprint | `app/api/routes.py` |
| 27. Deployment Pipeline | `docker-compose.yml`, `RUNBOOK.md` |

`sample_data/EURUSD_sample.csv` is **synthetic** (clearly marked), included
only so `scripts/run_paper_loop.py` and the backtester have something to
run against out of the box. Point `HISTORICAL_CSV_PATH` in `.env` at your
real historical export (the ejtraderLabs data referenced in the original
`data_pipeline_ohlc.py`, an MT5 export, or similar) before drawing any real
conclusions from a backtest.

## What was verified while building this (not just written — run)

- `pytest tests/` — 25 tests covering the feature/regime engine, all three
  Risk Engine decision paths (ALLOW/MODIFY/REJECT), the strategy layer, and
  the execution engine, including a full pipeline test against SQLite.
- The FastAPI app was started and every endpoint hit manually (auth,
  market data, risk evaluation, order placement through the Risk Engine,
  backtests, model listing/promotion with the validation gate).
- The backtester was run against all three strategies; this caught two real
  bugs in the process (see below), now fixed and covered by regression
  tests.

### Bugs this process actually caught

1. **Donchian channel look-ahead**: the breakout strategy's channel
   originally included the current bar's own high/low, so a "breakout"
   could almost never fire. Fixed in `app/features/engine.py`
   (`donchian_high_20`/`_low_20` now `shift(1)` before the rolling window),
   with a regression test in `tests/test_features_regime.py`.
2. **`profit_factor: inf` breaking the API**: a backtest sample with zero
   losing trades produced `float('inf')`, which Starlette's JSON response
   (correctly) refuses to serialize — every `/backtests` call 500'd. Fixed
   in `app/backtest/engine.py` to report `None` ("undefined, no losers in
   this sample yet") instead.

This is the kind of thing "genuine" is supposed to mean here: run it, watch
it break, fix the real cause, write a test so it stays fixed.

## What's still ahead of this MVP

- News/economic intelligence (blueprint section 12) — not built.
- Supervised-model regime classification (section 9's "rule-based baseline
  -> supervised ML" progression) — currently only the rule-based baseline.
- Prometheus/Grafana wiring (section 26) — `docker-compose.yml` has the
  services commented in, ready to enable.
- Correlation-aware exposure is currently a simple USD-bucket grouping
  (section 15) — extend `_CORRELATION_GROUPS` in `app/risk_engine.py` as
  more symbols/asset classes are added.
- Alembic migrations — `app/db/session.py`'s `create_all()` is fine for
  dev/SQLite; add real migrations before this schema evolves further on a
  production Postgres instance.

## Project layout

```
app/
  data/          gateway interface + mock/MT5 implementations + historical loader
  features/      feature engine + regime engine
  strategies/     common interface, 3 baseline strategies, registry/selector
  backtest/       event-driven backtester
  db/             SQLAlchemy models + session
  api/            FastAPI routes/schemas/deps
  risk_engine.py
  execution_engine.py
  orchestrator.py
  audit.py
  config.py
  main.py
db/init.sql        full blueprint-spec Postgres schema
scripts/
  run_paper_loop.py
tests/
RUNBOOK.md          MT5 demo account + VPS deployment, step by step
docker-compose.yml
```
