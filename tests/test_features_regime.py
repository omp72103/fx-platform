import math
from app.features.engine import build_features, FEATURE_SCHEMA_VERSION
from app.features.regime import add_regime, classify_regime_with_confidence, REGIME_MODEL_VERSION


def test_build_features_produces_expected_columns(synthetic_ohlc):
    df = build_features(synthetic_ohlc)
    for col in ["ret_1d", "ema_fast", "ema_slow", "ema_spread", "rsi_14", "atr_14",
                "vol_pctile", "trend_slope_50", "donchian_high_20", "donchian_low_20"]:
        assert col in df.columns
    assert df.attrs["feature_schema_version"] == FEATURE_SCHEMA_VERSION


def test_atr_is_never_negative(synthetic_ohlc):
    df = build_features(synthetic_ohlc)
    assert (df["atr_14"].dropna() >= 0).all()


def test_donchian_channel_excludes_current_bar(synthetic_ohlc):
    """Regression test for a real bug caught during development: the
    Donchian channel must be computed over the PRIOR 20 bars only. If it
    includes the current bar's own high/low, a breakout condition
    (close >= channel high) can almost never fire because the channel would
    trivially already contain today's own extreme."""
    df = build_features(synthetic_ohlc)
    valid = df.dropna(subset=["donchian_high_20", "donchian_low_20"])
    # The channel for row i must equal the max/min of rows [i-20, i-1], i.e.
    # it must NOT always be >= today's own high (that would indicate leakage).
    leaks = (valid["donchian_high_20"] >= valid["high"]).sum()
    assert leaks < len(valid), "Donchian channel appears to include the current bar (look-ahead bug)"


def test_regime_labels_are_from_the_known_set(synthetic_ohlc):
    df = build_features(synthetic_ohlc)
    df = add_regime(df)
    allowed = {"TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNCERTAIN"}
    assert set(df["regime"].unique()) <= allowed
    assert (df["model_version"] == REGIME_MODEL_VERSION).all()


def test_regime_confidence_in_valid_range(synthetic_ohlc):
    df = build_features(synthetic_ohlc)
    df = add_regime(df)
    assert (df["regime_confidence"] >= 0).all()
    assert (df["regime_confidence"] <= 1).all()


def test_uncertain_regime_before_warmup(synthetic_ohlc):
    df = build_features(synthetic_ohlc)
    row = df.iloc[5]  # not enough history yet for vol_pctile / trend_slope_50
    regime, confidence = classify_regime_with_confidence(row)
    assert regime == "UNCERTAIN"
    assert confidence == 0.0
