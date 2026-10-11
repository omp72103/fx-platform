# Base44 Setup Notes — fx-platform

## What this is
A Python FastAPI backend (AI Adaptive Forex Trading Platform) with a static HTML
dashboard. Runs with a mock gateway (no broker, no real money) against SQLite by
default in the Base44 sandbox.

## Architecture (Base44 sandbox)
- **web** (nginx, port 3000): serves `dashboard/index.html` at `/`, proxies all
  other paths to the API. The dashboard's default API base URL is set to empty
  (same-origin) so requests go through nginx.
- **api** (python:3.11-slim, port 8000): bind-mounted source, runs
  `uvicorn --reload`. Installs deps from `requirements.txt` on startup.
- **db** (TimescaleDB): included but not used by default — the app uses SQLite
  (`sqlite:////tmp/fx_platform.db`) because the platform masks passwords in
  DATABASE_URL env vars. To use Postgres, set `POSTGRES_HOST_AUTH_METHOD=trust`
  on the db service and `DATABASE_URL=postgresql+psycopg2://***@db:5432/fx_platform`
  (without a password).

## Key details
- **Auth**: `API_AUTH_TOKEN=pZ8nZ0Q0Fu4vgY4rZtFi-4yF0wEai4Zw` (set in
  `.env.base44-defaults`, matches the dashboard's token). All API endpoints
  require `Authorization: Bearer pZ8nZ0Q0Fu4vgY4rZtFi-4yF0wEai4Zw`.
- **No external secrets needed**: GATEWAY_MODE=mock, ACCOUNT_MODE=demo,
  NEWS_PROVIDER=none. Everything runs locally.
- **Database init**: `app/db/session.py:init_db()` calls
  `Base.metadata.create_all()` on startup — works with SQLite and Postgres.
  `app/orchestrator.py:seed_reference_data()` seeds symbols and strategies.
- **Run a trading cycle**: POST `/run-cycle` (from the dashboard button or curl).

## Verify it works
```bash
docker compose -f docker-compose.base44.yml up -d
curl http://localhost:3000/health
# → {"status":"ok","gateway_mode":"mock","account_mode":"demo"}
curl -H "Authorization: Bearer pZ8nZ0Q0Fu4vgY4rZtFi-4yF0wEai4Zw" http://localhost:3000/account
curl -X POST -H "Authorization: Bearer pZ8nZ0Q0Fu4vgY4rZtFi-4yF0wEai4Zw" http://localhost:3000/run-cycle
```

## Files added for Base44
- `docker-compose.base44.yml` — dev compose (bind-mounted source, live reload)
- `nginx/default.conf` — reverse proxy: dashboard at `/`, API proxy for all else
- `.env.base44-defaults` — env var defaults (no real secrets)
- `.base44/environment.json` — platform metadata
- `dashboard/index.html` — default API base URL changed to empty (same-origin)
