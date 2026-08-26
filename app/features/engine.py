"""
Feature engine — this is the user's original features_regime_ohlc.py logic,
unchanged in its math (same EMA/RSI/ATR/Donchian/trend-slope definitions),
but with a version stamp added per blueprint section 8: "feature definitions
must be deterministic and versioned. The same feature code/schema must be
used across training, backtesting, paper and live environments."

FEATURE_SCHEMA_VERSION must be bumped any time the feature math changes.
The persisted `features` table (db/init.sql) stores this version alongside
every row precisely so a live signal can never silently drift from what a
strategy was validated against.
"""
import numpy as np
import pandas as pd

FEATURE_SCHEMA_VERSION = "features_v1"


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ret_1d"] = df["close"].pct_change()

    df["ema_fast"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=26, adjust=False).mean()
    df["ema_spread"] = (df["ema_fast"] - df["ema_slow"]) / df["ema_slow"]

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi_14"] = (100 - (100 / (1 + rs))).fillna(50)

    # TRUE ATR (Wilder), using real high/low/prev-close — not a proxy
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr_14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    df["vol_pctile"] = df["atr_14"].rolling(252, min_periods=60).rank(pct=True)

    def _slope(x):
        if x.isna().any():
            return np.nan
        y = x.values
        t = np.arange(len(y))
        return np.polyfit(t, y, 1)[0] / y.mean()

    df["trend_slope_50"] = df["close"].rolling(50).apply(_slope, raw=False)

    # shift(1) BEFORE the rolling window is deliberate: without it, the
    # current bar's own high/low would be included in "the channel", so a
    # breakout (close >= channel high) could almost never fire — the
    # channel would trivially already contain today's own extreme. The
    # channel must be the range of the PRIOR 20 bars only.
    df["donchian_high_20"] = df["high"].shift(1).rolling(20).max()
    df["donchian_low_20"] = df["low"].shift(1).rolling(20).min()

    df.attrs["feature_schema_version"] = FEATURE_SCHEMA_VERSION
    return df


def latest_feature_row(df: pd.DataFrame) -> dict:
    """Return the most recent row as a plain dict payload, suitable for
    persisting into the `features` table's JSON `payload` column."""
    row = df.iloc[-1]
    cols = ["ret_1d", "ema_fast", "ema_slow", "ema_spread", "rsi_14", "atr_14",
            "vol_pctile", "trend_slope_50", "donchian_high_20", "donchian_low_20"]
    return {c: (None if pd.isna(row[c]) else float(row[c])) for c in cols}
