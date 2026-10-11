"""
Regression tests for the Risk Engine's *inputs*.

The Risk Engine's own logic was already tested (test_risk_engine.py), but the
orchestrator and API routes fed it placeholders, so several limits could never
fire in practice: correlated-group exposure was always {}, the kill switch was
hard-coded False, weekly PnL equalled daily PnL, and the API routes also passed
daily_pnl=0 / peak_equity=equity / exposure={}. These tests pin the real
behaviour so it can't silently regress.
"""
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import risk_engine
from app.account_state import (
    build_account_state, correlated_group_exposure, exposure_by_symbol,
    period_pnl, position_open_price,
)
from app.data.gateway_base import AccountInfo
from app.db.models import Base, PortfolioSnapshot
from app.kill_switch import is_kill_switch_active
from app.risk_engine import evaluate
from app.strategies.base import StrategySignal

SYMBOL_INFO = {"pip_size": 0.0001, "contract_size": 100_000, "min_volume": 0.01}


class FakeGateway:
    """Minimal gateway returning MT5-shaped positions (`price_open`)."""
    def __init__(self, positions=(), equity=10_000.0):
        self._positions = list(positions)
        self._equity = equity

    def get_account(self):
        return AccountInfo(login="1", balance=self._equity, equity=self._equity,
                           margin=0.0, margin_free=self._equity)

    def get_positions(self):
        return self._positions


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _signal(symbol="EURUSD", entry=1.0850, sl=1.0800):
    return StrategySignal(strategy_id="t", strategy_version="t_v1", symbol=symbol, timeframe="H1",
                          direction="BUY", confidence=0.7, entry_reference=entry,
                          stop_loss=sl, take_profit=1.0950)


# --- position field compatibility (MT5 vs mock) -----------------------------

def test_open_price_accepts_mt5_and_mock_field_names():
    assert position_open_price({"price_open": 1.25}) == 1.25
    assert position_open_price({"avg_open_price": 1.10}) == 1.10
    with pytest.raises(KeyError):
        position_open_price({"volume": 1})


def test_mt5_shaped_positions_do_not_crash_state_building(session):
    gw = FakeGateway([{"symbol": "EURUSD", "volume": 0.1, "price_open": 1.08}])
    state = build_account_state(session, gw, spread_pips=1.0)
    assert state.exposure_by_symbol["EURUSD"] == pytest.approx(0.1 * 100_000 * 1.08)


# --- correlated exposure ----------------------------------------------------

def test_correlated_group_exposure_sums_usd_pairs():
    positions = [{"symbol": "EURUSD", "volume": 0.1, "price_open": 1.0},
                 {"symbol": "GBPUSD", "volume": 0.2, "price_open": 1.0}]
    assert correlated_group_exposure(positions)["USD"] == pytest.approx(30_000)
    assert exposure_by_symbol(positions) == {"EURUSD": 10_000.0, "GBPUSD": 20_000.0}


def test_correlated_limit_now_rejects_when_existing_usd_exposure_is_large(session, monkeypatch):
    # Symbol cap relaxed and portfolio cap set so that the NEW trade alone fits,
    # but new + existing USD-pair exposure does not. With the old placeholder
    # ({}), this would have been ALLOWed.
    relaxed = dataclasses.replace(risk_engine.settings,
                                  max_symbol_exposure_pct=300.0, max_portfolio_exposure_pct=400.0)
    monkeypatch.setattr(risk_engine, "settings", relaxed)
    gw = FakeGateway([{"symbol": "GBPUSD", "volume": 0.5, "price_open": 1.27}])
    state = build_account_state(session, gw, spread_pips=1.0)
    d = evaluate(_signal(), state, SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert "correlated_portfolio_exposure_limit" in d.reason_codes

    # Sanity: the same trade with no existing exposure is allowed.
    clean = build_account_state(session, FakeGateway(), spread_pips=1.0)
    assert evaluate(_signal(), clean, SYMBOL_INFO).decision in ("ALLOW", "MODIFY")


# --- kill switch ------------------------------------------------------------

def test_kill_switch_via_env(monkeypatch, session):
    monkeypatch.setenv("KILL_SWITCH", "1")
    assert is_kill_switch_active()
    state = build_account_state(session, FakeGateway(), spread_pips=1.0)
    assert state.kill_switch_active is True
    d = evaluate(_signal(), state, SYMBOL_INFO)
    assert d.decision == "REJECT" and "kill_switch_active" in d.reason_codes


def test_kill_switch_via_file(monkeypatch, tmp_path):
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    flag = tmp_path / "KILL_SWITCH"
    monkeypatch.setenv("KILL_SWITCH_FILE", str(flag))
    assert not is_kill_switch_active()
    flag.write_text("")
    assert is_kill_switch_active()
    flag.unlink()
    assert not is_kill_switch_active()


# --- daily vs weekly PnL ----------------------------------------------------

def test_daily_and_weekly_pnl_are_measured_separately():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)          # a Wednesday
    snaps = [
        PortfolioSnapshot(equity=10_000, balance=10_000, timestamp_utc=now - timedelta(days=5)),   # prior Fri
        PortfolioSnapshot(equity=9_900, balance=9_900, timestamp_utc=now - timedelta(hours=13)),   # Tue night
    ]
    daily, weekly = period_pnl(snaps, equity=9_600, now=now)
    assert daily == pytest.approx(-300)     # vs Tuesday-night close
    assert weekly == pytest.approx(-400)    # vs last snapshot before Monday 00:00


def test_pnl_is_zero_with_no_history():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    assert period_pnl([], equity=10_000, now=now) == (0.0, 0.0)


def test_weekly_loss_limit_can_trip_while_daily_does_not(session, monkeypatch):
    now = datetime.now(timezone.utc)
    week_start = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                  - timedelta(days=now.weekday()))
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Equity fell 7% before today, flat today -> weekly breach (6%), daily fine.
    session.add(PortfolioSnapshot(equity=10_000, balance=10_000, timestamp_utc=week_start - timedelta(hours=1)))
    session.add(PortfolioSnapshot(equity=9_300, balance=9_300, timestamp_utc=day_start - timedelta(minutes=1)))
    session.commit()
    if now.weekday() == 0:
        pytest.skip("On Mondays the week and day boundaries coincide.")
    relaxed = dataclasses.replace(risk_engine.settings, max_drawdown_pct=50.0)
    monkeypatch.setattr(risk_engine, "settings", relaxed)
    state = build_account_state(session, FakeGateway(equity=9_300), spread_pips=1.0)
    d = evaluate(_signal(), state, SYMBOL_INFO)
    assert d.decision == "REJECT" and "max_weekly_loss_breached" in d.reason_codes


# --- API no longer bypasses the limits --------------------------------------

def test_orders_send_respects_kill_switch(tmp_path, monkeypatch):
    from app.main import app
    from app.api.deps import get_db
    from app.config import settings

    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setenv("KILL_SWITCH", "1")
    try:
        client = TestClient(app)
        r = client.post("/orders/send", json={"symbol": "EURUSD", "direction": "BUY",
                                              "volume": 0.01, "sl": 0.5},
                        headers={"Authorization": f"Bearer {settings.api_auth_token}"})
        assert r.status_code == 422, r.text
        assert "kill_switch_active" in r.json()["detail"]["reason_codes"]
    finally:
        app.dependency_overrides.clear()
