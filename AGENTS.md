# Base44 Development Environment

## Architecture

FastAPI backend (`app/main.py`) + static HTML dashboard (`dashboard/index.html`).
The dashboard is a standalone HTML page that calls the API via fetch with a
bearer token. In the Base44 preview, nginx serves the dashboard at `/` on
port 3000 and reverse-proxies all other paths to the FastAPI app on port 8000.

## Running

```bash
docker compose -f docker-compose.base44.yml up -d
```

- **API**: `python:3.11-slim`, bind-mounted at `/app`, installs
  `requirements.txt` on startup, runs `uvicorn --reload` on port 8000.
- **Web**: `nginx:alpine`, serves `dashboard/` at `/` and proxies `/api` paths
  to the API container. Exposed on host port 3000.
- **DB**: SQLite at `/tmp/fx_platform.db` (no Postgres needed for dev).

## Key details

- The dashboard's default API base URL is empty (relative), so it talks
  through the nginx proxy. For a separate static deployment, set the API
  URL in the dashboard's input field.
- `API_AUTH_TOKEN` in `.env.base44-defaults` matches the dashboard's default
  token so the preview connects without manual config.
- `httpx` is not in `requirements.txt` (it's a test-only dep). Install it
  separately to run the FastAPI TestClient tests:
  `pip install httpx && pytest tests/`
- The app uses `GATEWAY_MODE=mock` and `ACCOUNT_MODE=demo` by default — no
  broker or external services needed.

## Verifying

- `curl http://localhost:3000/health` → `{"status":"ok",...}`
- Open the preview at port 3000 → dashboard auto-connects and loads data.
- Click "Run trading cycle" → generates a signal and order through the full
  pipeline (data → features → regime → strategy → risk → execution).
- `docker compose -f docker-compose.base44.yml exec -T api python -m pytest
  tests/` (after `pip install httpx`) → all tests pass.
