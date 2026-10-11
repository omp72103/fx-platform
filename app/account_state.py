"""
Builds the AccountState the Risk Engine evaluates, from real inputs.

Why this module exists: the orchestrator and the API routes each used to
assemble AccountState by hand, and both fed the Risk Engine placeholders
(correlated_group_exposure={}, kill_switch_active=False, weekly_pnl equal to
daily_pnl, and — in the API routes — daily_pnl=0, peak_equity=equity,
exposure_by_symbol={}). That meant several limits could never fire. Keeping
one builder means every caller gets the same, real numbers.

Known limitation: PnL is measured as equity change between portfolio
snapshots. Deposits/withdrawals during the period look like gains/losses.
Fine for demo/paper trading; replace with deal-history-based PnL before any
live use.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db.models import PortfolioSnapshot
from app.kill_switch import is_kill_switch_active
from app.risk_engine import AccountState, correlation_group_for

DEFAULT_CONTRACT_SIZE = 100_000


def position_open_price(position: dict) -> float:
    """MT5 reports `price_open`; the mock gateway reports `avg_open_price`.
    Accept both so the same code runs against either gateway."""
    for key in ("price_open", "avg_open_price"):
        value = position.get(key)
        if value is not None:
            return float(value)
    raise KeyError(f"position has no open-price field: {sorted(position)}")


def position_notional(position: dict) -> float:
    return float(position["volume"]) * DEFAULT_CONTRACT_SIZE * position_open_price(position)


def exposure_by_symbol(positions: list[dict]) -> dict:
    out: dict = {}
    for p in positions:
        out[p["symbol"]] = out.get(p["symbol"], 0.0) + position_notional(p)
    return out


def correlated_group_exposure(positions: list[dict]) -> dict:
    out: dict = {}
    for p in positions:
        group = correlation_group_for(p["symbol"])
        if group:
            out[group] = out.get(group, 0.0) + position_notional(p)
    return out


def _aware(ts: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone=True columns.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _period_start_equity(snapshots, period_start: datetime, current_equity: float) -> float:
    """Equity at the start of the period: the last snapshot taken before it,
    else the earliest snapshot inside it, else current equity (no history yet
    means no measurable loss yet)."""
    before = [s for s in snapshots if s.timestamp_utc and _aware(s.timestamp_utc) < period_start]
    if before:
        return max(before, key=lambda s: _aware(s.timestamp_utc)).equity
    within = [s for s in snapshots if s.timestamp_utc and _aware(s.timestamp_utc) >= period_start]
    if within:
        return min(within, key=lambda s: _aware(s.timestamp_utc)).equity
    return current_equity


def period_pnl(snapshots, equity: float, now: datetime) -> tuple[float, float]:
    """(daily_pnl, weekly_pnl). Day starts 00:00 UTC; week starts Monday 00:00 UTC."""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    return (equity - _period_start_equity(snapshots, day_start, equity),
            equity - _period_start_equity(snapshots, week_start, equity))


def build_account_state(session, gateway, spread_pips: float, news_risk_score: float = 0.0,
                         now: datetime | None = None) -> AccountState:
    now = now or datetime.now(timezone.utc)
    account = gateway.get_account()
    positions = gateway.get_positions()

    snapshots = (session.query(PortfolioSnapshot)
                 .order_by(PortfolioSnapshot.timestamp_utc.desc()).limit(5000).all())
    peak_equity = max([s.equity for s in snapshots] + [account.equity])
    daily_pnl, weekly_pnl = period_pnl(snapshots, account.equity, now)

    return AccountState(
        equity=account.equity, balance=account.balance, peak_equity=peak_equity,
        daily_pnl=daily_pnl, weekly_pnl=weekly_pnl, open_positions=len(positions),
        exposure_by_symbol=exposure_by_symbol(positions),
        correlated_group_exposure=correlated_group_exposure(positions),
        spread_pips=spread_pips, news_risk_score=news_risk_score,
        margin_free=account.margin_free, margin_required_estimate=0.0,
        kill_switch_active=is_kill_switch_active(),
    )
