"""
Risk Engine — blueprint section 14, "Highest Priority":

    Signal -> account state -> drawdown checks -> loss limits -> position
    sizing -> symbol/portfolio exposure -> correlation exposure ->
    spread/slippage checks -> news restrictions -> margin checks ->
    kill switch -> ALLOW / MODIFY / REJECT

Non-negotiable (blueprint section 2 and 34): "LLM/AI services must never
have direct order-execution authority. Every proposed trade must pass the
deterministic Risk Engine before execution." "AI/LLM output must never
bypass the Risk Engine." "Never send an order without an auditable risk
decision."

This module is 100% deterministic: no model calls, no randomness, only
arithmetic against the account/portfolio state and the limits in
app/config.py. Every call returns a RiskDecision that gets persisted to the
risk_events table (see app/db/models.py) BEFORE any order is ever built —
that persisted row is the audit trail this function's decision rests on.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import settings
from app.strategies.base import StrategySignal


@dataclass
class AccountState:
    equity: float
    balance: float
    peak_equity: float
    daily_pnl: float
    weekly_pnl: float
    open_positions: int
    exposure_by_symbol: dict            # {"EURUSD": notional_usd, ...}
    correlated_group_exposure: dict     # {"USD": notional_usd, ...}
    spread_pips: float
    news_risk_score: float              # 0..1, 0 = no scheduled high-impact news nearby
    margin_free: float
    margin_required_estimate: float
    kill_switch_active: bool = False


@dataclass
class RiskDecision:
    decision: str                       # ALLOW / MODIFY / REJECT
    reason_codes: list = field(default_factory=list)
    approved_volume: float = 0.0
    applied_limits: dict = field(default_factory=dict)
    account_state_snapshot: dict = field(default_factory=dict)
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Currencies treated as correlated for the portfolio-exposure check
# (blueprint section 15: "EURUSD, GBPUSD, USDCHF and XAUUSD can create
# overlapping USD exposure"). Extend as more symbols are added.
_CORRELATION_GROUPS = {
    "EURUSD": "USD", "GBPUSD": "USD", "USDJPY": "USD", "USDCHF": "USD", "XAUUSD": "USD",
}


def _position_size(account: AccountState, signal: StrategySignal, pip_size: float,
                    contract_size: float) -> float:
    """Fixed-fractional sizing off the stop distance — 'never risk more than
    X% of the account on any one trade' (Forex_Strategy_Backtest_Explained.pdf
    section 3, blueprint section 14 'Risk per trade')."""
    if signal.stop_loss is None or signal.entry_reference is None:
        return 0.0
    stop_distance = abs(signal.entry_reference - signal.stop_loss)
    if stop_distance <= 0:
        return 0.0
    risk_amount = account.equity * (settings.risk_per_trade_pct / 100.0)
    volume = risk_amount / (stop_distance * contract_size)
    return max(round(volume, 2), 0.0)


def evaluate(signal: StrategySignal, account: AccountState,
             symbol_info: dict) -> RiskDecision:
    reasons = []
    applied = {
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_weekly_loss_pct": settings.max_weekly_loss_pct,
        "max_drawdown_pct": settings.max_drawdown_pct,
        "max_open_positions": settings.max_open_positions,
        "max_symbol_exposure_pct": settings.max_symbol_exposure_pct,
        "max_portfolio_exposure_pct": settings.max_portfolio_exposure_pct,
        "max_spread_pips": settings.max_spread_pips,
    }
    snapshot = {
        "equity": account.equity, "balance": account.balance,
        "daily_pnl": account.daily_pnl, "weekly_pnl": account.weekly_pnl,
        "open_positions": account.open_positions,
        "drawdown_pct": _drawdown_pct(account),
    }

    def reject(*codes):
        return RiskDecision(decision="REJECT", reason_codes=list(codes),
                             applied_limits=applied, account_state_snapshot=snapshot)

    # 1. Global kill switch — highest priority, checked first.
    if account.kill_switch_active:
        return reject("kill_switch_active")

    # 2. HOLD signals never reach risk (should already be filtered upstream,
    #    but the risk engine treats it as a hard boundary too, not a courtesy).
    if signal.direction not in ("BUY", "SELL"):
        return reject("signal_not_actionable")

    # 3. Drawdown check.
    dd = _drawdown_pct(account)
    if dd >= settings.max_drawdown_pct:
        return reject("max_drawdown_breached", f"drawdown_{dd:.2f}pct")

    # 4. Daily / weekly loss limits.
    daily_loss_pct = max(0.0, -account.daily_pnl / account.equity * 100) if account.equity else 100
    weekly_loss_pct = max(0.0, -account.weekly_pnl / account.equity * 100) if account.equity else 100
    if daily_loss_pct >= settings.max_daily_loss_pct:
        return reject("max_daily_loss_breached", f"daily_loss_{daily_loss_pct:.2f}pct")
    if weekly_loss_pct >= settings.max_weekly_loss_pct:
        return reject("max_weekly_loss_breached", f"weekly_loss_{weekly_loss_pct:.2f}pct")

    # 5. Max open positions.
    if account.open_positions >= settings.max_open_positions:
        return reject("max_open_positions_reached")

    # 6. Spread check — refuse to trade into an abnormally wide spread.
    if account.spread_pips > settings.max_spread_pips:
        return reject("spread_too_wide", f"spread_{account.spread_pips:.1f}pips")

    # 7. News restriction.
    if account.news_risk_score >= 0.8:
        return reject("high_impact_news_window")

    # 8. Position sizing.
    volume = _position_size(account, signal, symbol_info.get("pip_size", 0.0001),
                             symbol_info.get("contract_size", 100_000))
    if volume < symbol_info.get("min_volume", 0.01):
        return reject("computed_size_below_minimum")

    # 9. Symbol / portfolio / correlation exposure (section 15).
    notional = volume * symbol_info.get("contract_size", 100_000) * signal.entry_reference
    existing_symbol_exposure = account.exposure_by_symbol.get(signal.symbol, 0.0)
    symbol_exposure_pct = (existing_symbol_exposure + notional) / account.equity * 100 if account.equity else 100
    if symbol_exposure_pct > settings.max_symbol_exposure_pct:
        # MODIFY: shrink the trade to fit inside the remaining symbol budget
        # instead of an outright reject, per section 14's ALLOW/MODIFY/REJECT
        # output (not everything above a limit needs to be a hard no).
        room_pct = max(settings.max_symbol_exposure_pct - existing_symbol_exposure / account.equity * 100, 0)
        if room_pct <= 0:
            return reject("symbol_exposure_limit_reached")
        scaled_notional = room_pct / 100 * account.equity
        volume = round(scaled_notional / (symbol_info.get("contract_size", 100_000) * signal.entry_reference), 2)
        if volume < symbol_info.get("min_volume", 0.01):
            return reject("symbol_exposure_limit_leaves_no_room")
        reasons.append("volume_reduced_for_symbol_exposure_limit")
        notional = volume * symbol_info.get("contract_size", 100_000) * signal.entry_reference

    group = _CORRELATION_GROUPS.get(signal.symbol)
    if group:
        existing_group_exposure = account.correlated_group_exposure.get(group, 0.0)
        group_exposure_pct = (existing_group_exposure + notional) / account.equity * 100 if account.equity else 100
        if group_exposure_pct > settings.max_portfolio_exposure_pct:
            return reject("correlated_portfolio_exposure_limit", f"group_{group}")

    # 10. Margin check.
    est_margin = notional / 30  # matches the mock gateway's leverage assumption;
    # on the real MT5 gateway, use order_check() for the broker's true margin figure.
    if est_margin > account.margin_free:
        return reject("insufficient_margin")

    decision = "MODIFY" if reasons else "ALLOW"
    return RiskDecision(
        decision=decision, reason_codes=reasons or ["within_all_limits"],
        approved_volume=volume, applied_limits=applied, account_state_snapshot=snapshot,
    )


def _drawdown_pct(account: AccountState) -> float:
    if account.peak_equity <= 0:
        return 0.0
    return max(0.0, (account.peak_equity - account.equity) / account.peak_equity * 100)
