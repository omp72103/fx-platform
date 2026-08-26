import dataclasses
import pytest
from app.data.mock_gateway import MockGateway
from app.risk_engine import RiskDecision
from app.strategies.base import StrategySignal
from app.execution_engine import execute_signal


def _signal():
    return StrategySignal(strategy_id="trend_following_ema", strategy_version="trend_following_ema_v1",
                           symbol="EURUSD", timeframe="H1", direction="BUY", confidence=0.7,
                           entry_reference=1.0850, stop_loss=1.0800, take_profit=1.0950)


def test_execute_signal_rejects_reject_decision():
    gw = MockGateway()
    reject = RiskDecision(decision="REJECT", reason_codes=["kill_switch_active"])
    with pytest.raises(ValueError):
        execute_signal(gw, _signal(), reject, order_id="test-1")


def test_execute_signal_fills_on_allow():
    gw = MockGateway()
    allow = RiskDecision(decision="ALLOW", reason_codes=["within_all_limits"], approved_volume=0.05)
    result = execute_signal(gw, _signal(), allow, order_id="test-2")
    assert result.status == "filled"
    assert result.executed_price is not None
    assert result.executed_volume == 0.05
    assert result.broker_response_code == "MOCK_OK"


def test_execute_signal_blocked_when_account_mode_not_demo(monkeypatch):
    # Settings is a frozen dataclass on purpose; swap the module-level
    # `settings` reference rather than mutating a frozen field.
    from app import execution_engine
    live_settings = dataclasses.replace(execution_engine.settings, account_mode="live")
    monkeypatch.setattr(execution_engine, "settings", live_settings)
    gw = MockGateway()
    allow = RiskDecision(decision="ALLOW", reason_codes=["within_all_limits"], approved_volume=0.05)
    result = execute_signal(gw, _signal(), allow, order_id="test-3")
    assert result.status == "blocked"
    assert result.broker_response_code == "ACCOUNT_MODE_NOT_DEMO"


def test_mock_gateway_position_reflects_fill():
    gw = MockGateway()
    allow = RiskDecision(decision="ALLOW", reason_codes=["within_all_limits"], approved_volume=0.1)
    execute_signal(gw, _signal(), allow, order_id="test-4")
    positions = gw.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "EURUSD"
    assert positions[0]["volume"] == 0.1
