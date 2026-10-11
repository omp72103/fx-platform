"""
Live-trading guard tests.

Live trading is allowed only when every layer agrees. These tests pin each
layer separately (config validation, MT5 gateway, execution engine, risk
engine) so removing or weakening any one of them fails a test.
"""
import dataclasses
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, execution_engine, live_guard, risk_engine
from app.account_state import build_account_state
from app.data import mt5_gateway
from app.data.gateway_base import AccountInfo
from app.data.mock_gateway import MockGateway
from app.db.models import Base, Order, Strategy, StrategyVersion
from app.live_guard import LIVE_ACK_PHRASE
from app.risk_engine import AccountState, RiskDecision, evaluate
from app.strategies.base import StrategySignal

LOGIN = "777001"
SYMBOL_INFO = {"pip_size": 0.0001, "contract_size": 100_000, "min_volume": 0.01}


def live_cfg(**over):
    base = dict(account_mode="live", gateway_mode="mt5", live_trading_ack=LIVE_ACK_PHRASE,
                live_expected_login=LOGIN, mt5_login=LOGIN, api_auth_token="a-long-random-token-123",
                live_max_volume_lots=0.01, live_max_orders_per_day=3)
    base.update(over)
    return dataclasses.replace(config.settings, **base)


def use_cfg(monkeypatch, cfg):
    for mod in (live_guard, execution_engine, risk_engine, mt5_gateway):
        monkeypatch.setattr(mod, "settings", cfg)
    return cfg


def signal(strategy_id="mean_reversion_rsi", version="mean_reversion_rsi_v1"):
    return StrategySignal(strategy_id=strategy_id, strategy_version=version, symbol="EURUSD",
                          timeframe="D1", direction="BUY", confidence=0.7,
                          entry_reference=1.0850, stop_loss=1.0800, take_profit=1.0950)


def account(**over):
    base = dict(equity=100_000.0, balance=100_000.0, peak_equity=100_000.0, daily_pnl=0.0,
                weekly_pnl=0.0, open_positions=0, exposure_by_symbol={}, correlated_group_exposure={},
                spread_pips=0.8, news_risk_score=0.0, margin_free=90_000.0,
                margin_required_estimate=0.0, strategy_approved=True, orders_today=0)
    base.update(over)
    return AccountState(**base)


# --------------------------------------------------------------- config ----

def test_valid_live_config_has_no_problems():
    assert live_guard.live_config_problems(live_cfg()) == []


@pytest.mark.parametrize("over,needle", [
    (dict(gateway_mode="mock"), "GATEWAY_MODE"),
    (dict(live_trading_ack=""), "LIVE_TRADING_ACK"),
    (dict(live_trading_ack="yes"), "LIVE_TRADING_ACK"),
    (dict(live_expected_login=""), "LIVE_EXPECTED_LOGIN"),
    (dict(mt5_login="999"), "MT5_LOGIN"),
    (dict(api_auth_token="change-me"), "API_AUTH_TOKEN"),
    (dict(api_auth_token="short"), "API_AUTH_TOKEN"),
    (dict(live_max_volume_lots=0.0), "LIVE_MAX_VOLUME_LOTS"),
    (dict(live_max_orders_per_day=0), "LIVE_MAX_ORDERS_PER_DAY"),
])
def test_each_live_requirement_is_enforced(over, needle):
    problems = live_guard.live_config_problems(live_cfg(**over))
    assert any(needle in p for p in problems), problems


def test_default_settings_are_demo_and_not_live():
    assert config.Settings().account_mode == "demo"
    assert not live_guard.is_live(config.Settings())


def test_unknown_account_mode_is_never_allowed():
    cfg = dataclasses.replace(config.settings, account_mode="paper")
    assert live_guard.account_problems(cfg, login="1", is_demo=True, is_real=False)


def test_demo_mode_refuses_real_account():
    cfg = dataclasses.replace(config.settings, account_mode="demo")
    assert live_guard.account_problems(cfg, login="1", is_demo=False, is_real=True)
    assert not live_guard.account_problems(cfg, login="1", is_demo=True, is_real=False)


def test_live_mode_refuses_demo_contest_and_wrong_login():
    cfg = live_cfg()
    assert live_guard.account_problems(cfg, login=LOGIN, is_demo=False, is_real=True) == []
    assert live_guard.account_problems(cfg, login=LOGIN, is_demo=True, is_real=False)
    assert live_guard.account_problems(cfg, login=LOGIN, is_demo=False, is_real=False)   # contest
    assert live_guard.account_problems(cfg, login="555", is_demo=False, is_real=True)


def test_effective_limits_only_tighten():
    cfg = live_cfg(risk_per_trade_pct=0.1, live_risk_per_trade_pct=0.5,
                   max_daily_loss_pct=0.5, live_max_daily_loss_pct=1.0)
    lim = live_guard.effective_limits(cfg)
    assert lim["risk_per_trade_pct"] == 0.1 and lim["max_daily_loss_pct"] == 0.5


# ------------------------------------------------------ execution engine ---

class RealAccountGateway(MockGateway):
    """A mock whose account reports itself as a real account."""
    def __init__(self, login=LOGIN):
        super().__init__()
        self._login = login

    def get_account(self):
        a = super().get_account()
        return AccountInfo(login=self._login, balance=a.balance, equity=a.equity, margin=a.margin,
                           margin_free=a.margin_free, currency=a.currency, is_demo=False)


def allow(volume):
    return RiskDecision(decision="ALLOW", reason_codes=["within_all_limits"], approved_volume=volume)


def test_live_order_goes_through_only_when_everything_agrees(monkeypatch):
    use_cfg(monkeypatch, live_cfg())
    r = execution_engine.execute_signal(RealAccountGateway(), signal(), allow(0.01), "o1")
    assert r.status == "filled"


def test_live_blocked_on_wrong_login(monkeypatch):
    use_cfg(monkeypatch, live_cfg())
    r = execution_engine.execute_signal(RealAccountGateway(login="1"), signal(), allow(0.01), "o2")
    assert (r.status, r.broker_response_code) == ("blocked", "LIVE_GUARD_FAILED")


def test_live_blocked_on_missing_ack(monkeypatch):
    use_cfg(monkeypatch, live_cfg(live_trading_ack=""))
    r = execution_engine.execute_signal(RealAccountGateway(), signal(), allow(0.01), "o3")
    assert (r.status, r.broker_response_code) == ("blocked", "LIVE_GUARD_FAILED")


def test_live_blocked_when_gateway_reports_demo(monkeypatch):
    use_cfg(monkeypatch, live_cfg())
    r = execution_engine.execute_signal(MockGateway(), signal(), allow(0.01), "o4")
    assert r.broker_response_code == "LIVE_GUARD_FAILED"


def test_live_volume_cap_is_a_hard_stop(monkeypatch):
    use_cfg(monkeypatch, live_cfg(live_max_volume_lots=0.01))
    r = execution_engine.execute_signal(RealAccountGateway(), signal(), allow(0.02), "o5")
    assert (r.status, r.broker_response_code) == ("blocked", "LIVE_VOLUME_CAP")


def test_demo_mode_blocked_on_real_account(monkeypatch):
    use_cfg(monkeypatch, dataclasses.replace(config.settings, account_mode="demo"))
    r = execution_engine.execute_signal(RealAccountGateway(), signal(), allow(0.01), "o6")
    assert (r.status, r.broker_response_code) == ("blocked", "DEMO_GUARD_FAILED")


def test_invalid_account_mode_blocks(monkeypatch):
    use_cfg(monkeypatch, dataclasses.replace(config.settings, account_mode="paper"))
    r = execution_engine.execute_signal(MockGateway(), signal(), allow(0.01), "o7")
    assert r.broker_response_code == "ACCOUNT_MODE_INVALID"


def test_kill_switch_blocks_at_execution_time(monkeypatch):
    use_cfg(monkeypatch, dataclasses.replace(config.settings, account_mode="demo"))
    monkeypatch.setenv("KILL_SWITCH", "1")
    r = execution_engine.execute_signal(MockGateway(), signal(), allow(0.01), "o8")
    assert (r.status, r.broker_response_code) == ("blocked", "KILL_SWITCH_ACTIVE")


# ------------------------------------------------------------ risk engine --

def test_live_rejects_unapproved_strategy(monkeypatch):
    use_cfg(monkeypatch, live_cfg())
    d = evaluate(signal(), account(strategy_approved=False), SYMBOL_INFO)
    assert d.decision == "REJECT" and "strategy_not_approved_for_live" in d.reason_codes


def test_live_manual_orders_exempt_from_strategy_approval_but_not_caps(monkeypatch):
    use_cfg(monkeypatch, live_cfg())
    d = evaluate(signal("manual_api", "manual_api_v1"), account(strategy_approved=False), SYMBOL_INFO)
    assert d.decision == "MODIFY" and d.approved_volume == 0.01


def test_live_orders_per_day_budget(monkeypatch):
    use_cfg(monkeypatch, live_cfg(live_max_orders_per_day=3))
    assert evaluate(signal(), account(orders_today=2), SYMBOL_INFO).decision != "REJECT"
    d = evaluate(signal(), account(orders_today=3), SYMBOL_INFO)
    assert d.decision == "REJECT" and "live_max_orders_per_day_reached" in d.reason_codes


def test_live_volume_is_capped_not_rejected(monkeypatch):
    use_cfg(monkeypatch, live_cfg(live_max_volume_lots=0.01))
    d = evaluate(signal(), account(), SYMBOL_INFO)
    assert d.decision == "MODIFY"
    assert d.approved_volume == 0.01
    assert "volume_capped_for_live" in d.reason_codes


def test_live_uses_tighter_daily_loss_limit(monkeypatch):
    use_cfg(monkeypatch, live_cfg(max_daily_loss_pct=3.0, live_max_daily_loss_pct=1.0))
    d = evaluate(signal(), account(daily_pnl=-1_500.0), SYMBOL_INFO)   # -1.5% of 100k
    assert d.decision == "REJECT" and "max_daily_loss_breached" in d.reason_codes


def test_demo_mode_behaviour_unchanged(monkeypatch):
    use_cfg(monkeypatch, dataclasses.replace(config.settings, account_mode="demo"))
    d = evaluate(signal(), account(strategy_approved=False, orders_today=99), SYMBOL_INFO)
    assert d.decision in ("ALLOW", "MODIFY") and "volume_capped_for_live" not in d.reason_codes


# -------------------------------------------------------------- MT5 layer --

def fake_mt5(login=int(LOGIN), trade_mode=2):
    """trade_mode: 0 demo, 1 contest, 2 real (MT5's own constants)."""
    state = SimpleNamespace(login=login, trade_mode=trade_mode, sent=[])
    acct = lambda: SimpleNamespace(login=state.login, trade_mode=state.trade_mode, balance=1000.0,
                                   equity=1000.0, margin=0.0, margin_free=1000.0, currency="USD")
    tick = SimpleNamespace(time=1_700_000_000, bid=1.0849, ask=1.0850, last=0.0, volume=1)
    info = SimpleNamespace(point=0.00001, trade_contract_size=100_000, trade_mode=4,
                           volume_min=0.01, volume_step=0.01, filling_mode=2)
    mod = SimpleNamespace(
        ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_CONTEST=1, ACCOUNT_TRADE_MODE_REAL=2,
        TRADE_RETCODE_DONE=10009, TRADE_ACTION_DEAL=1, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1,
        ORDER_TIME_GTC=0, ORDER_FILLING_IOC=1, ORDER_FILLING_FOK=0, ORDER_FILLING_RETURN=2,
        initialize=lambda **k: True, login=lambda *a, **k: True, account_info=acct,
        symbol_info=lambda s: info, symbol_select=lambda s, v: True, symbol_info_tick=lambda s: tick,
        last_error=lambda: (1, "ok"),
        order_check=lambda r: SimpleNamespace(retcode=0, comment="Done", _asdict=lambda: {}),
        order_send=lambda r: (state.sent.append(r) or
                              SimpleNamespace(retcode=10009, price=1.0850, volume=r["volume"],
                                              _asdict=lambda: {})),
    )
    return mod, state


def make_gateway(monkeypatch, cfg, **kw):
    mod, state = fake_mt5(**kw)
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    use_cfg(monkeypatch, cfg)
    return mt5_gateway.MT5Gateway(login=LOGIN, password="pw", server="srv"), state


def req():
    from app.data.gateway_base import OrderRequest
    return OrderRequest(symbol="EURUSD", direction="BUY", volume=0.01, sl=1.08, tp=1.09)


def test_mt5_live_connects_and_sends_on_matching_real_account(monkeypatch):
    gw, state = make_gateway(monkeypatch, live_cfg())
    assert gw.order_check(req())["ok"] is True          # order_check success is retcode 0
    assert gw.order_send(req()).status == "filled"
    assert len(state.sent) == 1
    assert state.sent[0]["type_filling"] == 1            # IOC advertised by the symbol


def test_mt5_live_refuses_demo_account(monkeypatch):
    gw, state = make_gateway(monkeypatch, live_cfg(), trade_mode=0)
    with pytest.raises(mt5_gateway.MT5SafetyError):
        gw.get_account()
    assert state.sent == []


def test_mt5_live_refuses_contest_account(monkeypatch):
    gw, _ = make_gateway(monkeypatch, live_cfg(), trade_mode=1)
    with pytest.raises(mt5_gateway.MT5SafetyError):
        gw.get_account()


def test_mt5_live_refuses_wrong_login(monkeypatch):
    gw, state = make_gateway(monkeypatch, live_cfg(), login=424242)
    with pytest.raises(mt5_gateway.MT5SafetyError):
        gw.order_send(req())
    assert state.sent == []


def test_mt5_live_refuses_without_ack(monkeypatch):
    gw, state = make_gateway(monkeypatch, live_cfg(live_trading_ack=""))
    with pytest.raises(mt5_gateway.MT5SafetyError):
        gw.order_send(req())
    assert state.sent == []


def test_mt5_demo_mode_refuses_real_account(monkeypatch):
    gw, state = make_gateway(monkeypatch, dataclasses.replace(config.settings, account_mode="demo"),
                             trade_mode=2)
    with pytest.raises(mt5_gateway.MT5SafetyError):
        gw.get_account()
    assert state.sent == []


def test_mt5_rechecks_account_on_every_order(monkeypatch):
    gw, state = make_gateway(monkeypatch, live_cfg())
    assert gw.order_send(req()).status == "filled"
    state.login = 1                                       # terminal silently switched accounts
    with pytest.raises(mt5_gateway.MT5SafetyError):
        gw.order_send(req())
    assert len(state.sent) == 1


def test_gateway_factory_refuses_invalid_live_config(monkeypatch):
    from app.data import gateway_factory
    monkeypatch.setattr(gateway_factory, "settings", live_cfg(live_trading_ack=""))
    monkeypatch.setattr(gateway_factory, "is_live", live_guard.is_live)
    with pytest.raises(live_guard.LiveTradingConfigError):
        gateway_factory.get_gateway()


# ----------------------------------------------------- account-state inputs --

@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_account_state_counts_live_orders_and_strategy_approval(session):
    session.add(Strategy(strategy_id="mean_reversion_rsi", family="mr"))
    session.add(StrategyVersion(strategy_version="mean_reversion_rsi_v1",
                                strategy_id="mean_reversion_rsi", status="approved"))
    now = datetime.now(timezone.utc)
    session.add_all([
        Order(symbol="EURUSD", direction="BUY", requested_volume=0.01, status="filled",
              account_mode="live", request_timestamp_utc=now),
        Order(symbol="EURUSD", direction="BUY", requested_volume=0.01, status="blocked",
              account_mode="live", request_timestamp_utc=now),       # blocked: never reached broker
        Order(symbol="EURUSD", direction="BUY", requested_volume=0.01, status="filled",
              account_mode="demo", request_timestamp_utc=now),       # demo: not counted
    ])
    session.commit()
    state = build_account_state(session, MockGateway(), spread_pips=1.0,
                                strategy_version="mean_reversion_rsi_v1")
    assert state.orders_today == 1
    assert state.strategy_approved is True
    assert build_account_state(session, MockGateway(), spread_pips=1.0,
                               strategy_version="unknown_v1").strategy_approved is False
