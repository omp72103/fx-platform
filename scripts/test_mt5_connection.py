"""
Standalone MT5 demo-account connectivity check.

WHERE TO RUN THIS: on the machine that actually has the MT5 terminal
installed and logged in -- a Windows PC/VPS, or a Linux VPS with Wine (see
RUNBOOK.md sections 2-3). It will NOT work on a plain Linux box (no MT5
terminal, no MetaTrader5 wheel available), including this project's own
cloud/CI environment -- that's expected, not a bug.

WHAT IT DOES: uses the project's own MT5Gateway (app/data/mt5_gateway.py),
not a raw mt5.* call, so it exercises the exact same safety guards the real
app uses -- if this script says "connected", the app will connect the same
way. If it refuses, the app will refuse for the same reason.

CREDENTIALS: read only from your local .env (MT5_LOGIN, MT5_PASSWORD,
MT5_SERVER, GATEWAY_MODE=mt5). Never paste real broker credentials into
chat, source control, or anywhere other than your own .env file, which
.gitignore already excludes from git.

Usage:
    cd fx-platform
    cp .env.example .env        # if you haven't already
    # edit .env: GATEWAY_MODE=mt5, MT5_LOGIN=..., MT5_PASSWORD=..., MT5_SERVER=...
    python scripts/test_mt5_connection.py
    python scripts/test_mt5_connection.py --symbol GBPUSD   # optional
"""
import argparse
import sys

sys.path.insert(0, ".")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="EURUSD", help="Symbol to fetch a test tick for")
    args = parser.parse_args()

    from app.config import settings

    print("=" * 64)
    print("MT5 demo-account connectivity check")
    print("=" * 64)
    print(f"GATEWAY_MODE   = {settings.gateway_mode!r}")
    print(f"ACCOUNT_MODE   = {settings.account_mode!r}")
    print(f"MT5_SERVER     = {settings.mt5_server or '(not set)'}")
    print(f"MT5_LOGIN      = {settings.mt5_login or '(not set)'}")
    print(f"MT5_PASSWORD   = {'*' * len(settings.mt5_password) if settings.mt5_password else '(not set)'}")
    print()

    if settings.gateway_mode != "mt5":
        print("REFUSING TO PROCEED: GATEWAY_MODE is not 'mt5' (it's "
              f"{settings.gateway_mode!r}). Set GATEWAY_MODE=mt5 in your .env "
              "and re-run. (This is deliberate -- this script won't silently "
              "fall back to the mock gateway and report a false 'connected'.)")
        sys.exit(1)

    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        print("REFUSING TO PROCEED: MT5_LOGIN, MT5_PASSWORD, and/or MT5_SERVER "
              "are missing from your .env. Fill in all three from what your MT5 "
              "terminal showed you when you opened the demo account (RUNBOOK.md "
              "section 2), then re-run.")
        sys.exit(1)

    from app.data.mt5_gateway import MT5Gateway, MT5ConnectionError, MT5SafetyError

    gw = MT5Gateway()
    try:
        print("Connecting...")
        account = gw.get_account()
    except MT5ConnectionError as e:
        print("\nFAILED -- could not connect to the MT5 terminal / broker server.")
        print(f"Reason: {e}")
        print("\nMost common causes:")
        print("  - MT5 terminal isn't installed/running on THIS machine (see RUNBOOK.md")
        print("    section 3 -- this script must run on the VPS/Windows box with the")
        print("    terminal, not on a laptop or in this project's cloud sandbox).")
        print("  - MT5_SERVER name doesn't exactly match what the terminal showed you.")
        print("  - MT5_LOGIN / MT5_PASSWORD typo.")
        sys.exit(1)
    except MT5SafetyError as e:
        print("\nCONNECTED, but refused by this project's own safety guard:")
        print(f"Reason: {e}")
        print("\nThis means the account MT5 connected you to is NOT reporting itself")
        print("as a demo account, or ACCOUNT_MODE isn't 'demo'. This is intentional --")
        print("this platform will not silently proceed against a live account.")
        sys.exit(1)

    print("\nCONNECTED successfully.")
    print(f"  login        : {account.login}")
    print(f"  is_demo      : {account.is_demo}")
    print(f"  balance      : {account.balance} {account.currency}")
    print(f"  equity       : {account.equity} {account.currency}")
    print(f"  margin_free  : {account.margin_free} {account.currency}")

    if not account.is_demo:
        print("\nWARNING: account.is_demo is False. The app's own guard should have")
        print("already refused above -- if you're seeing this, something is wrong;")
        print("do not proceed to order_send in this state.")
        sys.exit(1)

    try:
        print(f"\nFetching a live tick for {args.symbol} to confirm market data flows too...")
        tick = gw.get_tick(args.symbol)
        print(f"  {args.symbol}: bid={tick.bid}  ask={tick.ask}  time={tick.timestamp_utc}")
    except MT5ConnectionError as e:
        print(f"  Could not fetch a tick for {args.symbol}: {e}")
        print(f"  (Account connection itself is fine -- check the symbol is enabled/visible")
        print(f"  in Market Watch inside the MT5 terminal.)")
        sys.exit(1)

    print("\nAll checks passed. GATEWAY_MODE=mt5 is safe to use for this account.")
    print("Next: run the API/paper-loop the normal way (RUNBOOK.md section 4) and it")
    print("will use this exact same connection.")


if __name__ == "__main__":
    main()
