"""
Two follow-up checks to scripts/run_real_backtest.py, kept as code so every
number in docs/BACKTEST_FINDINGS.md can be reproduced.

  pooled : pool trades across the pairs and re-run at several in/out-of-sample
           split points (does the daily result survive a different split?), plus
           the full-sample per-pair view for the mean-reversion strategy.
  h1     : the SAME strategies/engine/costs on H1 bars, the timeframe the live
           paper loop actually trades, with and without costs.

    python scripts/study_pooled_and_h1.py pooled --data-dir path/to/historical-data
    python scripts/study_pooled_and_h1.py h1     --data-dir path/to/historical-data   (~8 min)

Exploratory by nature: these were run AFTER seeing the first study's results,
so treat any standout as a hypothesis to test forward, not a finding.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_real_backtest as R  # noqa: E402
from app.backtest.engine import run_backtest  # noqa: E402
from app.data.historical_loader import validate_and_normalize  # noqa: E402
from app.features.engine import build_features  # noqa: E402

SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")
STRATS = (("trend_following", R.TrendFollowingStrategy), ("mean_reversion", R.MeanReversionStrategy),
          ("breakout", R.BreakoutStrategy), ("regime_selector", R.RegimeSelectorStrategy))


def pf(p):
    gl = -p[p < 0].sum()
    return float(p[p > 0].sum() / gl) if gl > 0 else float("nan")


def ci_bps(p, rng, b=20_000):
    m = rng.choice(p, size=(b, len(p)), replace=True).mean(1)
    return np.percentile(m, [2.5, 97.5]) * 1e4


def pooled(data_dir: Path):
    rng = np.random.default_rng(1)
    data = {s: R.load_symbol(s, data_dir, False) for s in SYMBOLS}
    print("=== pooled over", ", ".join(SYMBOLS), "(daily, 1.5 pips) ===")
    for pct in (50, 65, 80):
        for name, fac in STRATS[:2] + STRATS[3:]:
            isp, oop = [], []
            for s, df in data.items():
                si = int(len(df) * pct / 100)
                r = run_backtest(df, fac(), s, "D1", split_date=df.date.iloc[si], spread_pips=R.SPREAD_PIPS,
                                 pip_size=R.PIP_SIZE.get(s, 0.0001), max_holding_bars=R.MAX_HOLD)
                isp += [t.pnl_pct for t in r.trades if not t.is_out_of_sample]
                oop += [t.pnl_pct for t in r.trades if t.is_out_of_sample]
            isp, oop = np.array(isp), np.array(oop)
            c = ci_bps(oop, rng) if len(oop) > 4 else (np.nan, np.nan)
            print(f"split {pct}% {name:16s} IS n={len(isp):3d} PF={pf(isp):5.2f} avg={isp.mean()*1e4:6.1f}bps | "
                  f"OOS n={len(oop):3d} PF={pf(oop):5.2f} avg={oop.mean()*1e4:6.1f}bps CI=[{c[0]:.0f},{c[1]:.0f}]")
    print("\n=== mean_reversion, full sample per pair (no split) ===")
    allp = []
    for s, df in data.items():
        r = run_backtest(df, R.MeanReversionStrategy(), s, "D1", spread_pips=R.SPREAD_PIPS,
                         pip_size=R.PIP_SIZE.get(s, 0.0001))
        p = np.array([t.pnl_pct for t in r.trades]); allp += list(p)
        print(f"{s}: n={len(p)} PF={pf(p):.2f} avg={p.mean()*1e4:.1f}bps win={np.mean(p>0)*100:.0f}% "
              f"top3 trades = {np.sort(p)[-3:].sum()/p[p>0].sum()*100:.0f}% of gross profit")
    a = np.array(allp)
    lo, hi = ci_bps(a, rng)
    payoff = abs(a[a > 0].mean() / a[a < 0].mean())
    print(f"ALL: n={len(a)} PF={pf(a):.2f} avg={a.mean()*1e4:.1f}bps CI=[{lo:.0f},{hi:.0f}] win={np.mean(a>0)*100:.0f}% "
          f"payoff={payoff:.2f} breakeven win rate={1/(1+payoff)*100:.0f}%")


def h1(data_dir: Path):
    print("H1 bars, 65/35 chronological split, 1.5 pip round trip (and 0 for reference)")
    for s in SYMBOLS:
        path = next(p for p in (data_dir / s / f"{s}h1.csv", data_dir / f"{s}h1.csv") if p.exists())
        df = validate_and_normalize(pd.read_csv(path), price_scale=R.PRICE_SCALE.get(s, 100000.0))
        pip = R.PIP_SIZE.get(s, 0.0001)
        atr_pips = (build_features(df).atr_14 / pip).median()
        print(f"\n{s}: {len(df)} bars | median ATR14 = {atr_pips:.1f} pips; 1.5-pip cost = {1.5/atr_pips*100:.0f}% of one ATR")
        for name, fac in STRATS:
            out = {}
            for sp in (0.0, R.SPREAD_PIPS):
                r = run_backtest(df, fac(), s, "H1", spread_pips=sp, pip_size=pip, max_holding_bars=R.MAX_HOLD)
                i = np.array([t.pnl_pct for t in r.trades if not t.is_out_of_sample])
                o = np.array([t.pnl_pct for t in r.trades if t.is_out_of_sample])
                out[sp] = (len(i), pf(i), i.mean() * 1e4 if len(i) else 0.0, len(o), pf(o), o.mean() * 1e4 if len(o) else 0.0)
            z, c = out[0.0], out[R.SPREAD_PIPS]
            print(f"  {name:16s} cost=1.5: IS n={c[0]:4d} PF={c[1]:.2f} avg={c[2]:6.1f}bps | "
                  f"OOS n={c[3]:4d} PF={c[4]:.2f} avg={c[5]:6.1f}bps || zero-cost OOS PF={z[4]:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("check", choices=["pooled", "h1"])
    ap.add_argument("--data-dir", type=Path, required=True)
    a = ap.parse_args()
    (pooled if a.check == "pooled" else h1)(a.data_dir)
