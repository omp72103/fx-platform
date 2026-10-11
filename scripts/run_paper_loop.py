#!/usr/bin/env python3
"""
Entrypoint for the continuous trading loop. Runs the orchestrator on a fixed
interval, forever, logging every cycle.

    ACCOUNT_MODE=demo (default)
      GATEWAY_MODE=mock  -> pure simulation, no external dependency at all
      GATEWAY_MODE=mt5   -> MT5 DEMO account (requires MT5 terminal + RUNBOOK.md)
    ACCOUNT_MODE=live
      Real money. Starts only if the live guard passes (app/live_guard.py,
      RUNBOOK.md section 6): GATEWAY_MODE=mt5, LIVE_TRADING_ACK, a pinned
      LIVE_EXPECTED_LOGIN, a non-default API token. The broker must also
      report the account as REAL, re-checked on every order.

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
from app.live_guard import describe_mode, is_live, live_config_problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=int, default=60, help="seconds between cycles")
    args = parser.parse_args()

    print(f"[fx-platform] GATEWAY_MODE={settings.gateway_mode} ACCOUNT_MODE={settings.account_mode} "
          f"symbols={settings.symbols} timeframes={settings.timeframes}")
    if settings.account_mode not in ("demo", "live"):
        print(f"[fx-platform] FATAL: ACCOUNT_MODE={settings.account_mode!r} is not 'demo' or 'live'.",
              file=sys.stderr)
        sys.exit(1)
    if is_live(settings):
        problems = live_config_problems(settings)
        if problems:
            print("[fx-platform] FATAL: ACCOUNT_MODE=live but the live guard is not satisfied:",
                  file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("See RUNBOOK.md section 6.", file=sys.stderr)
            sys.exit(1)
        lim = describe_mode(settings)["limits"]
        print("=" * 64)
        print("  LIVE TRADING: REAL MONEY. Orders will be sent to a real account.")
        print(f"  account {settings.live_expected_login} | max {lim['max_volume_lots']} lots/order | "
              f"max {lim['max_orders_per_day']} orders/day")
        print(f"  risk/trade {lim['risk_per_trade_pct']}% | daily loss stop {lim['max_daily_loss_pct']}%")
        print("  Emergency stop: `touch KILL_SWITCH` (blocks new orders; keeps positions).")
        print("=" * 64)

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
