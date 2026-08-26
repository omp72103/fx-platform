#!/usr/bin/env python3
"""
Entrypoint for continuous paper/demo trading — blueprint section 27
deployment pipeline stage "PAPER" / "DEMO". Runs the orchestrator on a fixed
interval, forever, logging every cycle.

    GATEWAY_MODE=mock  -> pure simulation, no external dependency at all
    GATEWAY_MODE=mt5   -> real MT5 demo account (requires MT5 terminal +
                          RUNBOOK.md setup on the VPS; still demo-only,
                          enforced independently by app/config.py and
                          app/execution_engine.py)

Usage:
    python scripts/run_paper_loop.py             # loop forever, 60s interval
    python scripts/run_paper_loop.py --once       # single pass, then exit
    python scripts/run_paper_loop.py --interval 30
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db.session import init_db, SessionLocal
from app.data.gateway_factory import get_gateway
from app.strategies.registry import default_registry
from app.orchestrator import run_once, seed_reference_data
from app.audit import log_event


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=int, default=60, help="seconds between cycles")
    args = parser.parse_args()

    print(f"[fx-platform] GATEWAY_MODE={settings.gateway_mode} ACCOUNT_MODE={settings.account_mode} "
          f"symbols={settings.symbols} timeframes={settings.timeframes}")
    if settings.account_mode != "demo":
        print("[fx-platform] FATAL: ACCOUNT_MODE is not 'demo'. Refusing to start. "
              "See RUNBOOK.md before ever changing this.", file=sys.stderr)
        sys.exit(1)

    init_db()
    session = SessionLocal()
    seed_reference_data(session)

    gateway = get_gateway()
    registry = default_registry()

    log_event(session, component="paper_loop", level="INFO", message="paper loop starting")

    cycle = 0
    try:
        while True:
            cycle += 1
            t0 = time.time()
            run_once(session, gateway, registry)
            elapsed = time.time() - t0
            print(f"[fx-platform] cycle {cycle} complete in {elapsed:.2f}s")
            if args.once:
                break
            time.sleep(max(0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\n[fx-platform] stopped by user")
    finally:
        session.close()


if __name__ == "__main__":
    main()
