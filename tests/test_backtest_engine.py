"""
Backtest engine guarantees: the fast (precomputed-features) path must be
identical to the slow rebuild-every-bar path, and Sharpe must be annualised by
trades-per-year (not sqrt(252) per trade).
"""
import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import _period_metrics, run_backtest
from app.features.engine import build_features
from app.strategies.base import Strategy, StrategySignal
from app.strategies.breakout import BreakoutStrategy
from app.strategies.trend_following import TrendFollowingStrategy


def test_features_are_causal(synthetic_ohlc):
    """Row i of features computed on the whole series must equal the last row of
    features computed on data up to i. This is the property that makes the fast
    path legitimate; a feature that peeks forward breaks it."""
    full = build_features(synthetic_ohlc)
    cols = ["ema_fast", "ema_slow", "ema_spread", "rsi_14", "atr_14", "vol_pctile",
            "trend_slope_50", "donchian_high_20", "donchian_low_20"]
    for i in (60, 150, 300, 555, 899):
        part = build_features(synthetic_ohlc.iloc[:i + 1]).iloc[-1][cols].astype(float)
        np.testing.assert_allclose(full.iloc[i][cols].astype(float).values, part.values,
                                   rtol=1e-9, atol=1e-12, equal_nan=True)


class _FeatureProbeStrategy(Strategy):
    """Trades constantly and reads several different features (RSI, vol percentile,
    trend slope, ATR, regime), so if any feature differed between the fast and
    slow paths the resulting trades would differ. Needed because the real
    strategies are selective and can legitimately take zero trades on a short
    synthetic slice, which would make an equivalence test pass vacuously."""
    strategy_id, strategy_version, family = "probe", "probe_v1", "test"

    def generate_signal(self, ms):
        row = ms.features_df.iloc[-1]
        atr = row["atr_14"]
        if atr != atr or row["vol_pctile"] != row["vol_pctile"]:
            return self._hold(ms, "warmup")
        long = (row["rsi_14"] < 50) ^ (row["vol_pctile"] > 0.6) ^ (row["trend_slope_50"] > 0)
        price = float(row["close"])
        d = "BUY" if long else "SELL"
        sl = price - 0.8 * atr if long else price + 0.8 * atr
        tp = price + 1.1 * atr if long else price - 1.1 * atr
        return StrategySignal(self.strategy_id, self.strategy_version, ms.symbol, ms.timeframe, d,
                              0.5, price, float(sl), float(tp), ms.regime)


def _trade_key(result):
    return [(t.entry_time, t.exit_time, t.direction, round(t.entry_price, 10),
             round(t.exit_price, 10), t.exit_reason) for t in result.trades]


def test_fast_path_matches_slow_path_on_a_trading_strategy(synthetic_ohlc):
    df = synthetic_ohlc.iloc[:520].reset_index(drop=True)
    fast = run_backtest(df, _FeatureProbeStrategy(), "EURUSD", "D1", precompute_features=True)
    slow = run_backtest(df, _FeatureProbeStrategy(), "EURUSD", "D1", precompute_features=False)
    assert len(fast.trades) >= 20, "probe must actually trade or this test proves nothing"
    assert len({t.exit_reason for t in fast.trades}) >= 2
    assert _trade_key(fast) == _trade_key(slow)
    assert fast.full_sample == slow.full_sample


@pytest.mark.parametrize("strategy", [TrendFollowingStrategy(), BreakoutStrategy()])
def test_fast_path_matches_slow_path_on_real_strategies(synthetic_ohlc, strategy):
    # May legitimately be empty on this slice; equality still must hold.
    df = synthetic_ohlc.iloc[:520].reset_index(drop=True)
    assert _trade_key(run_backtest(df, strategy, "EURUSD", "D1", precompute_features=True)) == \
           _trade_key(run_backtest(df, strategy, "EURUSD", "D1", precompute_features=False))


def test_sharpe_is_annualised_by_trades_per_year_not_sqrt_252():
    pnls = [0.01, -0.005, 0.012, -0.004, 0.008, -0.006, 0.009, -0.003]   # 8 trades
    arr = np.array(pnls)
    per_trade = arr.mean() / arr.std()

    m = _period_metrics(pnls, years=2.0)                      # 4 trades / year
    assert m.sharpe == pytest.approx(per_trade * np.sqrt(4.0), abs=1e-3)
    old_style = per_trade * np.sqrt(252)
    assert abs(m.sharpe) < abs(old_style) / 5                  # old formula was wildly larger

    assert _period_metrics(pnls).sharpe == pytest.approx(per_trade, abs=1e-3)   # no years -> per-trade


def test_empty_and_flat_periods_are_safe():
    assert _period_metrics([], years=1.0).n_trades == 0
    assert _period_metrics([0.01, 0.01], years=1.0).sharpe == 0.0   # zero variance
