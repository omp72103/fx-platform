"""Unit tests for the helper maths in scripts/run_real_backtest.py."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_real_backtest as R  # noqa: E402
from app.backtest.engine import BacktestTrade  # noqa: E402


def test_profit_factor():
    assert R.profit_factor(np.array([0.02, -0.01, 0.01, -0.01])) == pytest.approx(1.5)
    assert R.profit_factor(np.array([0.01, 0.02])) is None        # no losers: undefined, not inf


def test_bootstrap_ci_needs_enough_trades_and_brackets_the_mean():
    rng = np.random.default_rng(0)
    assert R.bootstrap_ci(np.array([0.01, 0.02]), rng) is None
    p = np.array([0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.012])
    lo, hi = R.bootstrap_ci(p, rng)
    assert lo < p.mean() < hi


def _df(closes):
    d = pd.date_range("2020-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({"date": d, "open": closes, "high": closes, "low": closes, "close": closes})


def test_mtm_daily_returns_sum_to_trade_pnl_for_long_and_short():
    closes = [1.00, 1.00, 1.01, 1.03, 1.02, 1.02]
    df = _df(closes)
    spread = 0.0002
    long_t = BacktestTrade(entry_time=df.date[1], exit_time=df.date[4], direction="BUY",
                           entry_price=1.00, exit_price=1.02, pnl_pct=0.0, is_out_of_sample=True, exit_reason="x")
    r, inmkt = R.mtm_daily_returns(df, [long_t], spread)
    assert r.sum() == pytest.approx((1.02 - 1.00) / 1.00 - spread / 1.00, abs=2e-3)   # MTM path ~ end-to-end result
    assert inmkt.tolist() == [False, True, True, True, True, False]

    short_t = BacktestTrade(entry_time=df.date[1], exit_time=df.date[4], direction="SELL",
                            entry_price=1.00, exit_price=1.02, pnl_pct=0.0, is_out_of_sample=True, exit_reason="x")
    rs, _ = R.mtm_daily_returns(df, [short_t], spread)
    assert rs.sum() < 0 and rs.sum() == pytest.approx(-(1.02 - 1.00) - spread, abs=2e-3)


def test_same_day_trade_uses_entry_and_exit_prices():
    df = _df([1.0, 1.0, 1.0])
    t = BacktestTrade(entry_time=df.date[1], exit_time=df.date[1], direction="BUY", entry_price=1.0,
                      exit_price=1.01, pnl_pct=0.0, is_out_of_sample=True, exit_reason="x")
    r, _ = R.mtm_daily_returns(df, [t], 0.0)
    assert r[1] == pytest.approx(0.01) and r.sum() == pytest.approx(0.01)
