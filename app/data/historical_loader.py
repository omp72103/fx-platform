"""
Historical OHLC loader — this is the user's original data_pipeline_ohlc.py,
kept intact (it already matched blueprint sections 4 and 7 closely: immutable
raw data, validation, OHLC sanity checks) and wrapped into the app package so
research/backtesting code and the live pipeline can both import it.

Usage note: this reads a static CSV. It is the RESEARCH/BACKTEST data path.
The LIVE data path (real-time bars for the paper/demo loop) is
app/data/mt5_gateway.py or app/data/mock_gateway.py — see the gateway
interface below. Point RAW_PATH at whatever historical export you're using
(e.g. the ejtraderLabs EURUSD_d1.csv, or an MT5 export).
"""
import os
import pandas as pd
import numpy as np

RAW_PATH = os.getenv("HISTORICAL_CSV_PATH", "data/EURUSD_d1.csv")
PRICE_SCALE = float(os.getenv("HISTORICAL_PRICE_SCALE", "100000.0"))


def load_raw(path: str = None) -> pd.DataFrame:
    df = pd.read_csv(path or RAW_PATH)
    return df


def validate_and_normalize(df: pd.DataFrame, price_scale: float = None) -> pd.DataFrame:
    price_scale = price_scale if price_scale is not None else PRICE_SCALE
    issues = []
    # Accept either "Date"/"date" and either cased OHLC column names — the
    # exact casing depends on which historical export you point this at.
    rename_map = {c: c.lower() for c in df.columns}
    df = df.rename(columns=rename_map)
    if "date" not in df.columns and "time" in df.columns:
        df = df.rename(columns={"time": "date"})

    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    bad_dates = df["date"].isna().sum()
    if bad_dates:
        issues.append(f"{bad_dates} rows with unparseable dates dropped")
    df = df.dropna(subset=["date"])

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"historical source is missing required columns: {missing}. "
                          f"Found columns: {list(df.columns)}")

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce") / price_scale

    df = df.dropna(subset=required)

    bad_range = df[(df["high"] < df["low"]) | (df["high"] < df["open"]) |
                    (df["high"] < df["close"]) | (df["low"] > df["open"]) |
                    (df["low"] > df["close"])]
    if len(bad_range):
        issues.append(f"{len(bad_range)} rows with impossible OHLC relationships dropped")
    df = df.drop(bad_range.index)

    zero_or_neg = df[(df[required] <= 0).any(axis=1)]
    if len(zero_or_neg):
        issues.append(f"{len(zero_or_neg)} rows with zero/negative prices dropped")
    df = df.drop(zero_or_neg.index)

    dupes = df.duplicated(subset=["date"]).sum()
    if dupes:
        issues.append(f"{dupes} duplicate dates found, keeping first")
    df = df.drop_duplicates(subset=["date"], keep="first")

    df = df.sort_values("date").reset_index(drop=True)

    ret = df["close"].pct_change()
    spikes = df[ret.abs() > 0.05]
    if len(spikes):
        issues.append(f"{len(spikes)} rows flagged as >5% single-day moves (kept, logged)")

    df.attrs["quality_report"] = issues
    return df[["date", "open", "high", "low", "close"]].reset_index(drop=True)


def load_clean_ohlc(path: str = None, price_scale: float = None) -> pd.DataFrame:
    raw = load_raw(path)
    return validate_and_normalize(raw, price_scale)


# Backwards-compatible name matching the originally-uploaded script.
def load_clean_eurusd_ohlc() -> pd.DataFrame:
    return load_clean_ohlc()


if __name__ == "__main__":
    df = load_clean_ohlc()
    print(f"Loaded {len(df)} clean OHLC bars")
    print(f"Range: {df['date'].min()} to {df['date'].max()}")
    for note in df.attrs.get("quality_report", []):
        print(f"  - {note}")
    print(df.tail())
