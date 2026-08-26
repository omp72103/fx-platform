"""
Vercel serverless entrypoint for the demo deployment.

GATEWAY_MODE=mock here means every trade this live URL places is simulated
-- no MT5, no broker, no real money, ever. DATABASE_URL points at /tmp,
which Vercel's Python runtime allows writes to but does NOT persist across
cold starts or share across concurrent instances -- this is a live demo of
the running pipeline (real HTTP, real risk-engine logic, real simulated
fills), not the durable production deployment. A real production deploy
needs a managed Postgres (DATABASE_URL pointed at it) and a persistent
worker for scripts/run_paper_loop.py -- see RUNBOOK.md, which also covers
wiring GATEWAY_MODE=mt5 to a real MT5 demo account on your own VPS.

API_AUTH_TOKEN is set here for the demo; rotate it in the Vercel dashboard
(Project Settings -> Environment Variables) whenever you like -- once you
set it there, remove the hardcoded default below so the dashboard value is
the only source of truth.
"""
import os

os.environ.setdefault("GATEWAY_MODE", "mock")
os.environ.setdefault("ACCOUNT_MODE", "demo")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/fx_platform_demo.db")
os.environ.setdefault("API_AUTH_TOKEN", "pZ8nZ0Q0Fu4vgY4rZtFi-4yF0wEai4Zw")
os.environ.setdefault("HISTORICAL_CSV_PATH", "sample_data/EURUSD_sample.csv")

from app.main import app  # noqa: E402
