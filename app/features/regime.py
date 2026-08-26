"""
Market Regime Engine — rule-based baseline, per blueprint section 9's
implementation progression: "rule-based baseline -> supervised ML ->
optional clustering/advanced models -> controlled adaptive learning. Do not
begin with reinforcement learning."

This keeps the user's original classify_regime() thresholds/logic exactly,
but now returns the full spec'd output (section 9: "Outputs: regime +
confidence + model_version") instead of a bare label string, so it can
actually be written to the `market_regimes` table and consumed by the
Strategy Selector.

Confidence here is a deterministic, explainable function of how far the row
sits from each rule's decision boundary — NOT a calibrated probability.
Blueprint section 13 is explicit: "Do not represent model confidence as
guaranteed probability of profit unless statistically calibrated and
validated." When this progresses to a supervised model, replace
`classify_regime_with_confidence` but keep the same return contract so
nothing downstream has to change.
"""
import pandas as pd

REGIME_MODEL_VERSION = "regime_rule_baseline_v1"

VOL_HIGH = 0.85
VOL_LOW = 0.15
SLOPE_THRESH = 0.00015


def classify_regime(row) -> str:
    """Kept for backwards compatibility with the originally-uploaded script."""
    return classify_regime_with_confidence(row)[0]


def classify_regime_with_confidence(row) -> tuple[str, float]:
    if pd.isna(row["vol_pctile"]) or pd.isna(row["trend_slope_50"]):
        return "UNCERTAIN", 0.0

    vol = row["vol_pctile"]
    slope = row["trend_slope_50"]
    spread = row["ema_spread"]

    if vol > VOL_HIGH:
        confidence = min(1.0, (vol - VOL_HIGH) / (1 - VOL_HIGH) * 0.6 + 0.4)
        return "HIGH_VOLATILITY", round(confidence, 3)
    if vol < VOL_LOW:
        confidence = min(1.0, (VOL_LOW - vol) / VOL_LOW * 0.6 + 0.4)
        return "LOW_VOLATILITY", round(confidence, 3)

    if slope > SLOPE_THRESH and spread > 0:
        margin = min(abs(slope) / (SLOPE_THRESH * 4), 1.0)
        return "TREND_UP", round(0.4 + 0.6 * margin, 3)
    if slope < -SLOPE_THRESH and spread < 0:
        margin = min(abs(slope) / (SLOPE_THRESH * 4), 1.0)
        return "TREND_DOWN", round(0.4 + 0.6 * margin, 3)

    return "RANGE", 0.5


def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    results = df.apply(classify_regime_with_confidence, axis=1)
    df["regime"] = results.apply(lambda t: t[0])
    df["regime_confidence"] = results.apply(lambda t: t[1])
    df["model_version"] = REGIME_MODEL_VERSION
    return df


if __name__ == "__main__":
    from app.data.historical_loader import load_clean_ohlc
    from app.features.engine import build_features

    df = load_clean_ohlc()
    df = build_features(df)
    df = add_regime(df)
    print(df["regime"].value_counts())
    print(df.tail(5)[["date", "close", "atr_14", "regime", "regime_confidence", "model_version"]])
