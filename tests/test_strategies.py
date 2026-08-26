from app.features.engine import build_features
from app.features.regime import add_regime
from app.strategies.base import MarketState
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.breakout import BreakoutStrategy
from app.strategies.registry import default_registry, select_signal


def _featured(synthetic_ohlc):
    df = build_features(synthetic_ohlc)
    return add_regime(df)


def test_strategies_never_signal_outside_their_supported_regime(synthetic_ohlc):
    df = _featured(synthetic_ohlc)
    for strat in [TrendFollowingStrategy(), MeanReversionStrategy(), BreakoutStrategy()]:
        for i in range(60, len(df), 25):  # sample every 25th bar for speed
            window = df.iloc[:i + 1]
            row = window.iloc[-1]
            ms = MarketState(symbol="EURUSD", timeframe="H1", features_df=window,
                              regime=row["regime"], regime_confidence=row["regime_confidence"],
                              account_equity=10_000)
            sig = strat.generate_signal(ms)
            if sig.direction != "HOLD":
                assert row["regime"] in strat.supported_regimes


def test_strategies_never_send_orders_directly():
    """Structural guard: no strategy module may import the gateway or
    execution engine -- strategies propose, they never execute."""
    import app.strategies.trend_following as tf
    import app.strategies.mean_reversion as mr
    import app.strategies.breakout as bo
    for mod in (tf, mr, bo):
        src = open(mod.__file__).read()
        assert "gateway" not in src.lower()
        assert "order_send" not in src


def test_registry_produces_at_least_one_signal_over_a_long_run(synthetic_ohlc):
    df = _featured(synthetic_ohlc)
    reg = default_registry()
    signals = []
    for i in range(60, len(df)):
        window = df.iloc[:i + 1]
        row = window.iloc[-1]
        ms = MarketState(symbol="EURUSD", timeframe="H1", features_df=window,
                          regime=row["regime"], regime_confidence=row["regime_confidence"],
                          account_equity=10_000)
        sig = select_signal(reg, ms)
        if sig is not None:
            signals.append(sig)
    assert len(signals) > 0
    assert all(s.direction in ("BUY", "SELL") for s in signals)


def test_unvalidated_strategies_are_registered_as_candidate_not_approved():
    reg = default_registry()
    for entry in reg.all():
        assert entry.status == "candidate", (
            f"{entry.strategy.strategy_id} must not default to 'approved' -- "
            f"promotion requires an explicit, validated decision (section 33)."
        )
