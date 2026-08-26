import dataclasses
import pytest
from app.risk_engine import evaluate, AccountState
from app.strategies.base import StrategySignal

SYMBOL_INFO = {"pip_size": 0.0001, "contract_size": 100_000, "min_volume": 0.01}


def _signal(**overrides):
    base = dict(strategy_id="trend_following_ema", strategy_version="trend_following_ema_v1",
                symbol="EURUSD", timeframe="H1", direction="BUY", confidence=0.7,
                entry_reference=1.0850, stop_loss=1.0800, take_profit=1.0950)
    base.update(overrides)
    return StrategySignal(**base)


def _account(**overrides):
    base = dict(equity=10_000, balance=10_000, peak_equity=10_000, daily_pnl=0, weekly_pnl=0,
                open_positions=0, exposure_by_symbol={}, correlated_group_exposure={},
                spread_pips=1.0, news_risk_score=0.0, margin_free=9_000,
                margin_required_estimate=0, kill_switch_active=False)
    base.update(overrides)
    return AccountState(**base)


def test_kill_switch_hard_rejects_everything():
    d = evaluate(_signal(), _account(kill_switch_active=True), SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert "kill_switch_active" in d.reason_codes


def test_hold_signal_never_reaches_execution():
    d = evaluate(_signal(direction="HOLD"), _account(), SYMBOL_INFO)
    assert d.decision == "REJECT"


def test_max_drawdown_breach_rejects():
    d = evaluate(_signal(), _account(equity=7_000, peak_equity=10_000), SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert "max_drawdown_breached" in d.reason_codes


def test_daily_loss_limit_rejects():
    d = evaluate(_signal(), _account(daily_pnl=-350), SYMBOL_INFO)  # -3.5% > default 3.0% cap
    assert d.decision == "REJECT"
    assert "max_daily_loss_breached" in d.reason_codes


def test_wide_spread_rejects():
    d = evaluate(_signal(), _account(spread_pips=10.0), SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert "spread_too_wide" in d.reason_codes


def test_high_impact_news_rejects():
    d = evaluate(_signal(), _account(news_risk_score=0.9), SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert "high_impact_news_window" in d.reason_codes


def test_max_open_positions_rejects():
    d = evaluate(_signal(), _account(open_positions=5), SYMBOL_INFO)  # default cap is 5
    assert d.decision == "REJECT"
    assert "max_open_positions_reached" in d.reason_codes


def test_oversized_notional_is_modified_not_silently_allowed():
    """With default 1% risk-per-trade sizing off a wide stop, the resulting
    notional can exceed the symbol exposure cap on a small demo account --
    the engine must shrink the trade (MODIFY), never silently ALLOW an
    oversized position."""
    d = evaluate(_signal(), _account(), SYMBOL_INFO)
    assert d.decision in ("MODIFY", "REJECT")
    if d.decision == "MODIFY":
        assert d.approved_volume > 0


def test_healthy_account_within_relaxed_limits_allows(monkeypatch):
    # Settings is a frozen dataclass (deliberately -- config shouldn't be
    # mutable at runtime), so swap the module-level `settings` reference
    # itself rather than trying to mutate a frozen field.
    from app import risk_engine
    relaxed = dataclasses.replace(risk_engine.settings,
                                   max_symbol_exposure_pct=300.0, max_portfolio_exposure_pct=300.0)
    monkeypatch.setattr(risk_engine, "settings", relaxed)
    d = evaluate(_signal(), _account(), SYMBOL_INFO)
    assert d.decision == "ALLOW"
    assert d.approved_volume > 0


def test_no_reject_ever_returns_positive_volume():
    """An auditable invariant: REJECT must never carry an approved_volume,
    since nothing downstream should ever be tempted to execute it anyway."""
    d = evaluate(_signal(), _account(kill_switch_active=True), SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert d.approved_volume == 0.0
