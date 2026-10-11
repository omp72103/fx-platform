"""
Tests for the news / economic-calendar risk module (blueprint section 12).

Covers: feed parsing (incl. timezone conversion and malformed rows), the
scoring windows, symbol->currency matching, caching/backoff, the fail-closed /
fail-open behaviour when the feed is down or stale, and the end-to-end path
into the Risk Engine.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.account_state import build_account_state
from app.db.models import Base
from app.news.events import (
    EconomicEvent, ParseResult, currencies_for_symbol, parse_events, parse_feed_json,
)
from app.news.providers import FileProvider, NullProvider, provider_from_settings
from app.news.service import NewsService, score_event, set_news_service
from app.risk_engine import evaluate
from app.strategies.base import StrategySignal
from tests.test_account_state import FakeGateway, SYMBOL_INFO

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

# Rows in the real feed's shape (first three are samples of the live feed).
FEED = [
    {"title": "Bank Holiday", "country": "JPY", "date": "2026-10-11T19:00:00-04:00",
     "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00",
     "impact": "High", "forecast": "0.2%", "previous": "0.3%"},
    {"title": "ECB President Lagarde Speaks", "country": "EUR", "date": "2026-10-13T22:10:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
]


@pytest.fixture(autouse=True)
def _reset_service():
    set_news_service(None)
    yield
    set_news_service(None)


def _event(impact="HIGH", currency="USD", minutes_from_now=10, title="Test Event"):
    return EconomicEvent(title=title, currency=currency,
                         time_utc=NOW + timedelta(minutes=minutes_from_now), impact=impact)


class FakeProvider:
    def __init__(self, events=(), fail=False):
        self.events, self.fail, self.calls = list(events), fail, 0

    def fetch(self):
        self.calls += 1
        if self.fail:
            raise OSError("feed down")
        return ParseResult(events=list(self.events))


def _svc(provider, **kw):
    return NewsService(provider, **kw)


# --- parsing ----------------------------------------------------------------

def test_parse_converts_to_utc_and_normalises():
    result = parse_events(FEED)
    assert result.skipped == 0 and len(result.events) == 3
    cpi = next(e for e in result.events if e.title == "Core CPI m/m")
    assert cpi.time_utc == datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)   # 08:30 -04:00
    assert cpi.currency == "USD" and cpi.impact == "HIGH"


def test_parse_skips_malformed_rows_without_failing_the_feed():
    rows = FEED + [{"title": "x", "country": "USD", "date": "not-a-date", "impact": "High"},
                   {"title": "y", "date": "2026-10-14T08:30:00-04:00", "impact": "High"},
                   {"title": "z", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "Weird"},
                   "garbage"]
    result = parse_events(rows)
    assert len(result.events) == 3 and result.skipped == 4


def test_parse_naive_timestamp_is_treated_as_utc():
    ev = parse_events([{"title": "t", "country": "usd", "date": "2026-10-14T12:30:00",
                        "impact": "high"}]).events[0]
    assert ev.time_utc == datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc) and ev.currency == "USD"


def test_parse_feed_json_rejects_non_list():
    with pytest.raises(ValueError):
        parse_feed_json('{"not": "a list"}')


def test_currencies_for_symbol():
    assert currencies_for_symbol("EURUSD") == {"EUR", "USD"}
    assert currencies_for_symbol("xauusd") == {"XAU", "USD"}
    assert currencies_for_symbol("EUR") == {"EUR"}


# --- scoring windows --------------------------------------------------------

@pytest.mark.parametrize("minutes,expected", [
    (10, 1.0),        # inside the pre-event window
    (30, 1.0),        # exactly at the edge of the pre-event window
    (-15, 1.0),       # exactly at the edge of the post-event window
    (45, 0.5),        # halfway down the 30-minute ramp before the window
    (-30, 0.5),       # halfway down the ramp after the window
    (60, 0.0),        # ramp fully elapsed
    (-45, 0.0),
    (600, 0.0),
])
def test_high_impact_scoring(minutes, expected):
    assert score_event(_event("HIGH", minutes_from_now=minutes), NOW, 30, 15, 30) == pytest.approx(expected)


def test_medium_never_reaches_blocking_threshold_and_low_holiday_are_ignored():
    assert score_event(_event("MEDIUM", minutes_from_now=1), NOW, 30, 15, 30) == 0.5
    assert score_event(_event("LOW", minutes_from_now=1), NOW, 30, 15, 30) == 0.0
    assert score_event(_event("HOLIDAY", minutes_from_now=1), NOW, 30, 15, 30) == 0.0


# --- service: matching, caching, failure modes ------------------------------

def test_matches_symbol_currencies_only():
    svc = _svc(FakeProvider([_event("HIGH", "USD", 10, "Core CPI m/m")]))
    r = svc.risk_for("EURUSD", NOW)
    assert r.score == 1.0 and "Core CPI m/m" in r.reason and r.reason.startswith("news:USD:HIGH")
    assert svc.risk_for("GBPJPY", NOW).score == 0.0          # no USD leg -> unaffected
    assert svc.risk_for("XAUUSD", NOW).score == 1.0          # USD leg -> affected


def test_all_currency_events_affect_every_symbol():
    svc = _svc(FakeProvider([_event("HIGH", "ALL", 5, "Global event")]))
    assert svc.risk_for("GBPJPY", NOW).score == 1.0


def test_worst_event_wins():
    svc = _svc(FakeProvider([_event("MEDIUM", "USD", 5), _event("HIGH", "USD", 20, "Big one")]))
    r = svc.risk_for("EURUSD", NOW)
    assert r.score == 1.0 and "Big one" in r.reason


def test_disabled_provider_scores_zero_and_never_fails_closed():
    svc = _svc(NullProvider())
    assert not svc.enabled
    assert svc.risk_for("EURUSD", NOW).score == 0.0


def test_data_is_cached_between_refreshes():
    p = FakeProvider([_event()])
    svc = _svc(p, refresh_min=30)
    svc.risk_for("EURUSD", NOW)
    svc.risk_for("EURUSD", NOW + timedelta(minutes=5))
    assert p.calls == 1
    svc.risk_for("EURUSD", NOW + timedelta(minutes=31))
    assert p.calls == 2


def test_fail_closed_when_feed_never_loads():
    svc = _svc(FakeProvider(fail=True), fail_mode="closed")
    r = svc.risk_for("EURUSD", NOW)
    assert r.score == 1.0 and r.reason == "news_feed_unavailable"


def test_fail_open_when_feed_never_loads():
    svc = _svc(FakeProvider(fail=True), fail_mode="open")
    assert svc.risk_for("EURUSD", NOW).score == 0.0


def test_failed_fetch_is_not_retried_every_call():
    p = FakeProvider(fail=True)
    svc = _svc(p)
    for i in range(5):
        svc.risk_for("EURUSD", NOW + timedelta(seconds=i * 5))
    assert p.calls == 1                                       # backoff window holds
    svc.risk_for("EURUSD", NOW + timedelta(seconds=61))
    assert p.calls == 2


def test_stale_data_fails_closed_but_recent_data_survives_a_blip():
    p = FakeProvider([_event("HIGH", "USD", 600)])
    svc = _svc(p, refresh_min=30, stale_after_min=360, fail_mode="closed")
    assert svc.risk_for("EURUSD", NOW).score == 0.0           # loaded, event far away
    p.fail = True
    assert svc.risk_for("EURUSD", NOW + timedelta(hours=2)).score == 0.0      # blip: cached data still usable
    r = svc.risk_for("EURUSD", NOW + timedelta(hours=7))                       # older than stale_after
    assert r.score == 1.0 and r.reason == "news_feed_unavailable"


def test_invalid_fail_mode_rejected():
    with pytest.raises(ValueError):
        NewsService(NullProvider(), fail_mode="maybe")


# --- providers --------------------------------------------------------------

def test_file_provider_reads_feed_format(tmp_path):
    f = tmp_path / "cal.json"
    f.write_text(json.dumps(FEED))
    result = FileProvider(str(f)).fetch()
    assert len(result.events) == 3


def test_file_provider_requires_path():
    with pytest.raises(ValueError):
        FileProvider("")


def test_provider_from_settings():
    class S:  # minimal stand-in for Settings
        news_provider, news_calendar_path, news_feed_url = "none", "", "https://example.invalid/x.json"
    assert isinstance(provider_from_settings(S()), NullProvider)
    S.news_provider = "bogus"
    with pytest.raises(ValueError):
        provider_from_settings(S())


# --- end to end into the Risk Engine ----------------------------------------

@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'n.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _signal():
    return StrategySignal(strategy_id="t", strategy_version="t_v1", symbol="EURUSD", timeframe="H1",
                          direction="BUY", confidence=0.7, entry_reference=1.0850,
                          stop_loss=1.0800, take_profit=1.0950)


def test_high_impact_event_rejects_trade_with_reason_in_audit_trail(session):
    set_news_service(_svc(FakeProvider([_event("HIGH", "USD", 10, "Core CPI m/m")])))
    state = build_account_state(session, FakeGateway(), spread_pips=1.0, symbol="EURUSD", now=NOW)
    d = evaluate(_signal(), state, SYMBOL_INFO)
    assert d.decision == "REJECT"
    assert "high_impact_news_window" in d.reason_codes
    assert any("Core CPI m/m" in c for c in d.reason_codes)


def test_unrelated_news_does_not_block(session):
    set_news_service(_svc(FakeProvider([_event("HIGH", "JPY", 10)])))
    state = build_account_state(session, FakeGateway(), spread_pips=1.0, symbol="EURUSD", now=NOW)
    assert state.news_risk_score == 0.0
    assert "high_impact_news_window" not in evaluate(_signal(), state, SYMBOL_INFO).reason_codes


def test_no_symbol_means_no_news_check(session):
    set_news_service(_svc(FakeProvider(fail=True), fail_mode="closed"))
    state = build_account_state(session, FakeGateway(), spread_pips=0.0)   # e.g. portfolio snapshot
    assert state.news_risk_score == 0.0


def test_feed_outage_blocks_new_trades_by_default(session):
    set_news_service(_svc(FakeProvider(fail=True)))
    state = build_account_state(session, FakeGateway(), spread_pips=1.0, symbol="EURUSD", now=NOW)
    d = evaluate(_signal(), state, SYMBOL_INFO)
    assert d.decision == "REJECT" and "news_feed_unavailable" in d.reason_codes


def test_orchestrator_places_no_orders_while_high_impact_usd_news_is_imminent(tmp_path):
    """Whole pipeline, not just the builder: every signal on a USD pair must be
    REJECTed with the event named in the audit trail, and nothing may execute."""
    from app.data.mock_gateway import MockGateway
    from app.db.models import Order, RiskEvent
    from app.orchestrator import run_once, seed_reference_data
    from app.strategies.registry import default_registry

    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_reference_data(session)

    soon = EconomicEvent(title="Core CPI m/m", currency="USD", impact="HIGH",
                         time_utc=datetime.now(timezone.utc) + timedelta(minutes=10))
    set_news_service(_svc(FakeProvider([soon])))

    gw, reg = MockGateway(seed=123), default_registry()
    for _ in range(12):
        run_once(session, gw, reg)

    events = session.query(RiskEvent).all()
    assert events, "no signals reached the Risk Engine; the test would be vacuous"
    assert all(e.decision == "REJECT" and "high_impact_news_window" in e.reason_codes for e in events)
    assert any("Core CPI m/m" in c for e in events for c in e.reason_codes)
    assert session.query(Order).count() == 0
    session.close()
