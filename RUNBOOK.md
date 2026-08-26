# Runbook: MT5 Demo Account + VPS Deployment

This is the concrete, step-by-step path from "code on a laptop" to "automated
buy/sell running continuously against a real MT5 **demo** account on your own
VPS." It does not cover going live with real money — see **Section 6** for
why that's a separate, deliberate decision, not a config flag.

---

## 0. What you'll have running at the end of this

- A Postgres/TimescaleDB database, an API, and a paper-trading loop, all in
  Docker containers on a VPS you control (Ionos, AWS, or anywhere else that
  gives you a Linux box with Docker).
- The paper-trading loop polling market data, detecting regimes, generating
  strategy signals, running them through the deterministic Risk Engine, and —
  for anything the Risk Engine approves — placing real orders on your MT5
  **demo** account (fake money, real market conditions, real broker
  order-handling).
- Everything reconstructable afterwards from the database (every signal,
  every risk decision, every order, every fill).

## 1. Try it with zero setup first (GATEWAY_MODE=mock)

Before touching a broker at all, confirm the software itself works:

```bash
cp .env.example .env
pip install -r requirements.txt
python scripts/run_paper_loop.py --once
```

This runs one full cycle — data → features → regime → strategy → risk →
execution — against the built-in deterministic simulator
(`app/data/mock_gateway.py`). No broker, no internet dependency, no risk.
Confirm it prints `cycle 1 complete` with no errors before moving on.

## 2. Open an MT5 demo account

1. Pick a broker that offers MetaTrader 5 (most forex brokers do). Your team
   already has broker/VPS discussions in progress — use whichever broker you
   settle on; the platform doesn't care which one, as long as it's MT5.
2. Download the MT5 terminal from that broker (or from metatrader5.com) and
   open it.
3. In the terminal: **File → Open an Account → (your broker) → Demo
   Account**. Fill in a starting balance and leverage (defaults are fine).
4. MT5 will show you three things — **write them down**: the demo account
   **login number**, the **password**, and the **server name**
   (e.g. `YourBroker-Demo`). These go into `.env` as `MT5_LOGIN`,
   `MT5_PASSWORD`, `MT5_SERVER`.

## 3. Install the MT5 terminal on the VPS

The `MetaTrader5` Python package only talks to a **running MT5 terminal**.
There is no native Linux MT5 terminal, so a Linux VPS needs Wine.

**Option A — Windows VPS (simplest, recommended if cost allows):**
Remote desktop in, install MT5 normally, log into the demo account once by
hand (so it caches the connection), then run this project's `api` and
`paper-loop` services directly with Python (Docker Desktop on Windows Server
also works if you'd rather keep everything containerized).

**Option B — Linux VPS + Wine (cheaper, more moving parts):**
```bash
# On a fresh Ubuntu 22.04 VPS:
sudo dpkg --add-architecture i386
sudo apt update && sudo apt install -y wine64 wine32 xvfb
# Run MT5's installer under Wine, headless, via Xvfb:
Xvfb :1 -screen 0 1024x768x16 &
export DISPLAY=:1
wine mt5setup.exe   # downloaded from your broker
# Log into your demo account inside the Wine-hosted terminal once.
# Then install Python for Windows *inside* Wine and the MetaTrader5 package
# there, since MetaTrader5 is a Windows-only wheel:
wine python-3.11.exe /quiet
wine pip install MetaTrader5
```
This is fiddly — budget a few hours the first time, and expect to reboot
Xvfb/Wine if the terminal disconnects. Community Docker images exist that
package this pattern (search "MetaTrader5 docker wine") if you'd rather not
hand-roll it; vet whichever one you pick before trusting it with even a demo
account.

Either way, once the MT5 terminal is running and logged into the demo
account on the VPS, confirm Python can reach it:
```bash
python3 -c "import MetaTrader5 as mt5; print(mt5.initialize()); print(mt5.account_info())"
```
`account_info().trade_mode` must print as the demo constant — if it doesn't,
stop and fix the login before going further. `app/data/mt5_gateway.py`
checks this same thing itself and will refuse to run otherwise.

## 4. Configure and deploy

On the VPS:
```bash
git clone <your-repo-url> fx-platform   # or scp the project over
cd fx-platform
cp .env.example .env
```
Edit `.env`:
```
GATEWAY_MODE=mt5
ACCOUNT_MODE=demo          # do not change — see section 6
MT5_LOGIN=12345678
MT5_PASSWORD=your-demo-password
MT5_SERVER=YourBroker-Demo
```
Then:
```bash
sudo apt install -y docker.io docker-compose-plugin
sudo docker compose up -d --build
sudo docker compose logs -f paper-loop
```
You should see `cycle N complete` lines appearing on the interval you set.
Check the API is reachable: `curl http://<vps-ip>:8000/health`.

## 5. Verify it end to end

```bash
# From your machine, replace <vps-ip> and the token in .env:
curl -H "Authorization: Bearer <API_AUTH_TOKEN>" http://<vps-ip>:8000/account
curl -H "Authorization: Bearer <API_AUTH_TOKEN>" http://<vps-ip>:8000/positions
```
Or query Postgres directly:
```sql
select decision, reason_codes, timestamp_utc from risk_events order by timestamp_utc desc limit 20;
select symbol, direction, status, requested_volume from orders order by request_timestamp_utc desc limit 20;
```
Every REJECT, MODIFY, ALLOW, and every order/fill is in there with reasons —
that's the audit trail blueprint section 4 and 26 call for.

## 6. What has to be true before ACCOUNT_MODE ever becomes anything other than "demo"

This is deliberate friction, not an oversight. Two independent code paths
(`app/config.py`'s guard and `app/data/mt5_gateway.py`'s own re-check on
every single order) both refuse to place an order unless the connected
account reports itself as a demo account AND `ACCOUNT_MODE=demo`. Before a
human ever changes that:

1. Every strategy you intend to run must have cleared the same honest
   in-sample/out-of-sample backtest process as
   `Forex_Strategy_Backtest_Explained.pdf` — with an out-of-sample Sharpe and
   profit factor that hold up, not just an in-sample number. Use
   `POST /backtests` or `app/backtest/engine.py` directly, and check
   `GET /models` — nothing should be `approved` on a weak out-of-sample
   result (the `/models/{version}/promote` endpoint already refuses this
   automatically for Sharpe < 0.3).
2. It must have run in **paper/demo mode continuously** for a meaningful
   stretch (blueprint section 28: "compare expected vs observed
   market/execution conditions") — not just once.
3. Someone on your team should deliberately review and raise the risk
   limits in `.env` from their conservative defaults, understanding exactly
   what each one does (`app/risk_engine.py` has them all documented inline).
4. Only then, deliberately, does a human change `ACCOUNT_MODE` — and even
   then, blueprint section 27's own pipeline says to start "SMALL LIVE"
   before "CONTROLLED PRODUCTION", not to jump straight to full size.

## 7. Troubleshooting

- **"MetaTrader5 package is not installed"** — you're running the API/loop
  outside the Wine/Windows environment that has it. Either run those
  processes inside that environment, or use `GATEWAY_MODE=mock` to confirm
  everything else works first.
- **`MT5SafetyError` on startup** — the connected account isn't reporting as
  demo, or `ACCOUNT_MODE` isn't `demo`. Don't work around this — figure out
  why (wrong login, wrong server, or a genuinely live account) before
  proceeding.
- **No signals ever appear** — normal for a market spending most of its time
  ranging (see `GET /models` for each strategy's `preferred_regimes`); check
  `market_regimes` in the DB to see what regime is actually being detected.
- **Container can't reach the DB** — `docker compose logs db` and confirm
  the healthcheck passed before `api`/`paper-loop` started; `depends_on:
  condition: service_healthy` in `docker-compose.yml` should handle ordering
  automatically.
