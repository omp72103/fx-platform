import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def synthetic_ohlc() -> pd.DataFrame:
    """A synthetic OHLC series with distinct regime segments (range, uptrend,
    high-volatility chop, downtrend) so tests can exercise every regime
    branch deterministically. NOT real market data -- see RUNBOOK.md /
    README.md for pointing the platform at a real historical export."""
    rng = np.random.default_rng(7)
    n = 900
    seg1 = rng.normal(0, 3, 300)
    seg2 = np.full(300, 8) + rng.normal(0, 4, 300)
    seg3 = rng.normal(0, 40, 150)
    seg4 = np.full(150, -8) + rng.normal(0, 4, 150)
    drift = np.concatenate([seg1, seg2, seg3, seg4])
    price = 108_500 + np.cumsum(drift)
    dates = pd.bdate_range("2019-01-01", periods=n)
    open_ = price + rng.normal(0, 3, n)
    high = np.maximum(open_, price) + np.abs(rng.normal(0, 15, n))
    low = np.minimum(open_, price) - np.abs(rng.normal(0, 15, n))
    close = price
    df = pd.DataFrame({
        "date": pd.to_datetime(dates, utc=True),
        "open": open_ / 100_000, "high": high / 100_000,
        "low": low / 100_000, "close": close / 100_000,
    })
    return df
