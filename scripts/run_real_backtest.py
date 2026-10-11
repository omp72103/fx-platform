"""
Honest real-data backtest study (blueprint sections 18-19, 33).

Runs every strategy (plus the regime selector the live loop actually uses) on
real daily FX history with the engine's chronological in-sample / out-of-sample
split, and reports what the engine's own summary can't:

  * daily mark-to-market Sharpe (time flat counts as zero return)
  * bootstrap 95% CI on the mean trade return -> "is the edge distinguishable from 0?"
  * a random-entry baseline: same stops/holding/costs, coin-flip timing and
    direction, calibrated to the same number of trades -> "does the signal beat luck?"
  * transaction-cost sensitivity, buy-and-hold, and per-year stability

Nothing is tuned here. Strategy parameters are the ones in app/strategies/,
exactly as shipped; the only choices are the data, the split (engine default,
65/35 chronological) and the cost assumption (1.5 pips round trip). Every
symbol x strategy combination is reported, not just the good ones.

Data: ejtraderLabs/historical-data (Apache-2.0), daily bars, ~10 years.

    python scripts/run_real_backtest.py --data-dir path/to/historical-data
    python scripts/run_real_backtest.py --download          # fetches D1 CSVs

Limitations are written into the report; read them before drawing conclusions.
"""
from __future__ import annotations
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest.engine import run_backtest, _check_exit  # noqa: E402
from app.data.historical_loader import validate_and_normalize  # noqa: E402
from app.features.engine import build_features  # noqa: E402
from app.strategies.base import MarketState, Strategy  # noqa: E402
from app.strategies.breakout import BreakoutStrategy  # noqa: E402
from app.strategies.mean_reversion import MeanReversionStrategy  # noqa: E402
from app.strategies.registry import default_registry, select_signal  # noqa: E402
from app.strategies.trend_following import TrendFollowingStrategy  # noqa: E402

RAW_URL = "https://raw.githubusercontent.com/ejtraderLabs/historical-data/main/{sym}/{sym}d1.csv"
PRICE_SCALE = {"USDJPY": 1000.0}          # JPY pairs are stored x1000, the rest x100000
PIP_SIZE = {"USDJPY": 0.01}
SPREAD_PIPS = 1.5
MAX_HOLD = 40
TRADING_DAYS = 252


class RegimeSelectorStrategy(Strategy):
    """What the live loop does: classify the regime, let the registry pick the
    eligible strategy, rank by the validation-weighted score. Run on daily bars
    by presenting the timeframe as 'H1' so the registry's eligibility check
    (which only lists M5/M15/H1) lets the strategies through."""
    strategy_id, strategy_version, family = "regime_selector", "regime_selector_v1", "selector"

    def __init__(self):
        self.registry = default_registry()

    def generate_signal(self, ms: MarketState):
        proxy = MarketState(symbol=ms.symbol, timeframe="H1", features_df=ms.features_df,
                            regime=ms.regime, regime_confidence=ms.regime_confidence,
                            account_equity=ms.account_equity)
        sig = select_signal(self.registry, proxy)
        return sig if sig is not None else self._hold(ms, "no_eligible_strategy")


STRATEGIES = {
    "trend_following": (TrendFollowingStrategy, True),
    "mean_reversion": (MeanReversionStrategy, True),
    "breakout": (BreakoutStrategy, True),
    "regime_selector": (RegimeSelectorStrategy, False),   # (class, has single ATR multiples)
}


# ----------------------------------------------------------------------------- data

def load_symbol(sym: str, data_dir: Path | None, download: bool) -> pd.DataFrame:
    candidates = [] if data_dir is None else [data_dir / sym / f"{sym}d1.csv", data_dir / f"{sym}d1.csv"]
    path = next((c for c in candidates if c.exists()), None)
    if path is None:
        if not download:
            raise SystemExit(f"No data for {sym}. Pass --data-dir or --download.")
        cache = Path("data_cache"); cache.mkdir(exist_ok=True)
        path = cache / f"{sym}d1.csv"
        if not path.exists():
            with urllib.request.urlopen(RAW_URL.format(sym=sym), timeout=60) as r:  # noqa: S310
                path.write_bytes(r.read())
    df = validate_and_normalize(pd.read_csv(path), price_scale=PRICE_SCALE.get(sym, 100000.0))
    close = df["close"]
    if not (0.4 < close.median() < 200):   # catches a wrong price scale immediately
        raise SystemExit(f"{sym}: median close {close.median():.4f} looks mis-scaled; check PRICE_SCALE")
    return df


# --------------------------------------------------------------------------- metrics

def profit_factor(p: np.ndarray):
    gl = -p[p < 0].sum()
    return float(p[p > 0].sum() / gl) if gl > 0 else None


def equity_stats(daily: np.ndarray):
    if len(daily) == 0:
        return dict(total_return_pct=0.0, max_dd_pct=0.0, sharpe=0.0)
    eq = np.cumprod(1 + daily)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    sd = daily.std()
    return dict(total_return_pct=float((eq[-1] - 1) * 100), max_dd_pct=float(((peak - eq) / peak).max() * 100),
                sharpe=float(daily.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0)


def mtm_daily_returns(df: pd.DataFrame, trades, spread_price: float):
    n = len(df); r = np.zeros(n); inmkt = np.zeros(n, bool)
    idx = {d: i for i, d in enumerate(df["date"])}; close = df["close"].values
    for t in trades:
        ie, ix = idx[t.entry_time], idx[t.exit_time]
        s = 1.0 if t.direction == "BUY" else -1.0
        e, x = t.entry_price, t.exit_price
        cost = spread_price / e
        inmkt[ie:ix + 1] = True
        if ix == ie:
            r[ie] += s * (x - e) / e - cost
            continue
        r[ie] += s * (close[ie] - e) / e
        for d in range(ie + 1, ix):
            r[d] += s * (close[d] - close[d - 1]) / close[d - 1]
        r[ix] += s * (x - close[ix - 1]) / close[ix - 1] - cost
    return r, inmkt


def bootstrap_ci(p: np.ndarray, rng, b=10_000):
    if len(p) < 5:
        return None
    means = rng.choice(p, size=(b, len(p)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def random_entry_baseline(df, feats, sl_m, tp_m, split_idx, n_target, avg_hold, spread_price, rng, sims):
    """Coin-flip entries with the strategy's own ATR stops/targets, the engine's
    exit rules, holding cap and costs, over the same out-of-sample window."""
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    atr = feats["atr_14"].values
    n = len(df); lo = max(split_idx - 1, 0)
    free = max((n - 1 - lo) - n_target * max(avg_hold, 1.0), 1.0)
    p = min(n_target / free, 1.0)
    out = []
    for _ in range(sims):
        pnls, pos, i = [], None, lo
        while i < n - 1:
            if pos is not None:
                bar = {"low": l[i], "high": h[i]}
                ex, _r = _check_exit(pos, bar)
                if ex is None and i - pos["ei"] >= MAX_HOLD:
                    ex = c[i]
                if ex is not None:
                    raw = (ex - pos["e"]) if pos["direction"] == "BUY" else (pos["e"] - ex)
                    pnls.append((raw - spread_price) / pos["e"])
                    pos = None
                i += 1
                continue
            if not np.isnan(atr[i]) and rng.random() < p:
                d = "BUY" if rng.random() < 0.5 else "SELL"
                a = atr[i]
                sl = c[i] - sl_m * a if d == "BUY" else c[i] + sl_m * a
                tp = c[i] + tp_m * a if d == "BUY" else c[i] - tp_m * a
                pos = {"direction": d, "e": o[i + 1], "ei": i + 1, "sl": sl, "tp": tp}
            i += 1
        arr = np.array(pnls)
        out.append((len(arr), float(arr.mean()) if len(arr) else 0.0,
                    float((np.prod(1 + arr) - 1) * 100) if len(arr) else 0.0))
    return np.array(out), p


# --------------------------------------------------------------------------- study

def study_combo(df, feats, sym, name, factory, has_mult, split_idx, split_date, sims, seed):
    pip = PIP_SIZE.get(sym, 0.0001)
    strat = factory()
    res = run_backtest(df, strat, sym, "D1", split_date=split_date, spread_pips=SPREAD_PIPS, pip_size=pip,
                       max_holding_bars=MAX_HOLD)
    spread_price = SPREAD_PIPS * pip
    rng = np.random.default_rng(seed)
    tr = res.trades
    oos = [t for t in tr if t.is_out_of_sample]; ins = [t for t in tr if not t.is_out_of_sample]
    po, pi = np.array([t.pnl_pct for t in oos]), np.array([t.pnl_pct for t in ins])

    daily, inmkt = mtm_daily_returns(df, tr, spread_price)
    warm = 260
    out = {"symbol": sym, "strategy": name, "trades_total": len(tr)}
    for label, sl_, trades_, pn in (("oos", slice(split_idx, None), oos, po), ("is", slice(warm, split_idx), ins, pi)):
        es = equity_stats(daily[sl_])
        out[label] = dict(n=len(pn), win_rate=float((pn > 0).mean() * 100) if len(pn) else 0.0,
                          avg_trade_bps=float(pn.mean() * 1e4) if len(pn) else 0.0,
                          profit_factor=profit_factor(pn) if len(pn) else None,
                          time_in_market_pct=float(inmkt[sl_].mean() * 100),
                          engine_sharpe=getattr(res, "out_of_sample" if label == "oos" else "in_sample").sharpe, **es)
    ci = bootstrap_ci(po, rng)
    out["oos"]["avg_trade_ci_bps"] = [ci[0] * 1e4, ci[1] * 1e4] if ci else None
    out["oos"]["t_stat"] = float(po.mean() / (po.std(ddof=1) / np.sqrt(len(po)))) if len(po) > 2 and po.std() > 0 else None

    out["baseline"] = None
    if has_mult and len(po) >= 8:
        sims_arr, p_enter = random_entry_baseline(
            df, feats, strat.atr_sl_mult, strat.atr_tp_mult, split_idx, len(po),
            float(np.mean([(t.exit_time - t.entry_time).days for t in oos])), spread_price, rng, sims)
        strat_total = out["oos"]["total_return_pct"]
        # engine-comparable total (per-trade compounding) for apples-to-apples with the sims
        strat_total_trades = float((np.prod(1 + po) - 1) * 100)
        out["baseline"] = dict(
            sims=sims, enter_prob=p_enter, mean_trades=float(sims_arr[:, 0].mean()),
            random_mean_trade_bps_median=float(np.median(sims_arr[:, 1]) * 1e4),
            random_mean_trade_bps_p05_p95=[float(np.percentile(sims_arr[:, 1], 5) * 1e4),
                                           float(np.percentile(sims_arr[:, 1], 95) * 1e4)],
            pct_random_runs_beaten=float((sims_arr[:, 1] < po.mean()).mean() * 100),
            random_total_median=float(np.median(sims_arr[:, 2])),
            strategy_total_trade_compounded=strat_total_trades)

    out["by_year"] = {}
    for y, g in pd.Series([t.pnl_pct for t in tr], index=[t.entry_time.year for t in tr]).groupby(level=0):
        out["by_year"][int(y)] = dict(n=int(len(g)), total_pct=float((np.prod(1 + g.values) - 1) * 100))

    out["costs"] = {}
    for sp in (0.0, SPREAD_PIPS, 3.0):
        rr = run_backtest(df, factory(), sym, "D1", split_date=split_date, spread_pips=sp, pip_size=pip,
                          max_holding_bars=MAX_HOLD)
        q = np.array([t.pnl_pct for t in rr.trades if t.is_out_of_sample])
        out["costs"][str(sp)] = dict(n=len(q), total_return_pct=float((np.prod(1 + q) - 1) * 100) if len(q) else 0.0,
                                     profit_factor=profit_factor(q) if len(q) else None)
    return out


def buy_hold(df, split_idx):
    c = df["close"].values; r = np.diff(c) / c[:-1]
    return dict(oos=equity_stats(r[split_idx - 1:]), full_after_warmup=equity_stats(r[259:]))


def fmt(x, nd=2, dash="n/a"):
    return dash if x is None else f"{x:.{nd}f}"


def write_markdown(results, bh, meta, path):
    L = []
    a = L.append
    a(f"# Real-data backtest study\n")
    a(f"Generated {meta['generated']} by `scripts/run_real_backtest.py`. Data: ejtraderLabs/historical-data (Apache-2.0), "
      f"daily bars. Split: chronological {meta['split_pct']}% in-sample / rest out-of-sample "
      f"(split date per symbol below). Costs: {SPREAD_PIPS} pips round trip, entry at next bar's open, "
      f"SL assumed hit first when SL and TP are both inside a bar. **Nothing was tuned.**\n")
    a("## Data\n")
    a("| Symbol | Bars | From | To | Out-of-sample starts | OOS bars |")
    a("|---|---|---|---|---|---|")
    for s, m in meta["symbols"].items():
        a(f"| {s} | {m['bars']} | {m['start']} | {m['end']} | {m['split_date']} | {m['oos_bars']} |")
    a("\n## Out-of-sample results (the ones that matter)\n")
    a(f"`Beats random` = share of {meta['sims']} coin-flip-entry runs (same stops, costs, ~same trade count) whose average trade was "
      "worse than the strategy's. Around 50% means no better than luck; a real edge would sit near or above 95%. "
      "`CI` = bootstrap 95% interval of the average trade; if it spans 0 the edge is not statistically distinguishable from nothing.\n")
    a("| Symbol | Strategy | Trades | Win % | Profit factor | Avg trade (bps) | CI of avg trade (bps) | Return % | Max DD % | Sharpe (daily) | Beats random |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        o = r["oos"]; ci = o["avg_trade_ci_bps"]; b = r["baseline"]
        a(f"| {r['symbol']} | {r['strategy']} | {o['n']} | {fmt(o['win_rate'],1)} | {fmt(o['profit_factor'])} | "
          f"{fmt(o['avg_trade_bps'],1)} | {('['+fmt(ci[0],1)+', '+fmt(ci[1],1)+']') if ci else 'n/a (<5 trades)'} | "
          f"{fmt(o['total_return_pct'])} | {fmt(o['max_dd_pct'])} | {fmt(o['sharpe'])} | "
          f"{(fmt(b['pct_random_runs_beaten'],0)+'%') if b else 'n/a'} |")
    a("\n## In-sample vs out-of-sample\n")
    a("| Symbol | Strategy | IS trades | IS profit factor | IS Sharpe (daily) | OOS trades | OOS profit factor | OOS Sharpe (daily) | Engine OOS Sharpe (fixed formula) |")
    a("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        i, o = r["is"], r["oos"]
        a(f"| {r['symbol']} | {r['strategy']} | {i['n']} | {fmt(i['profit_factor'])} | {fmt(i['sharpe'])} | "
          f"{o['n']} | {fmt(o['profit_factor'])} | {fmt(o['sharpe'])} | {fmt(o['engine_sharpe'])} |")
    a("\n## Cost sensitivity (out-of-sample)\n")
    a("| Symbol | Strategy | 0 pips: return % / PF | 1.5 pips | 3 pips |")
    a("|---|---|---|---|---|")
    for r in results:
        c = r["costs"]
        cell = lambda k: f"{fmt(c[k]['total_return_pct'])} / {fmt(c[k]['profit_factor'])}"
        a(f"| {r['symbol']} | {r['strategy']} | {cell('0.0')} | {cell('1.5')} | {cell('3.0')} |")
    a("\n## Buy and hold (same period, for scale)\n")
    a("| Symbol | OOS return % | OOS Sharpe | OOS max DD % |")
    a("|---|---|---|---|")
    for s, v in bh.items():
        a(f"| {s} | {fmt(v['oos']['total_return_pct'])} | {fmt(v['oos']['sharpe'])} | {fmt(v['oos']['max_dd_pct'])} |")
    a("\n## Stability by entry year (full sample, compounded % per year; n trades)\n")
    years = sorted({y for r in results for y in r["by_year"]})
    a("| Symbol | Strategy | " + " | ".join(str(y) for y in years) + " |")
    a("|---|---|" + "---|" * len(years))
    for r in results:
        cells = [(f"{r['by_year'][y]['total_pct']:+.1f} ({r['by_year'][y]['n']})" if y in r["by_year"] else "-") for y in years]
        a(f"| {r['symbol']} | {r['strategy']} | " + " | ".join(cells) + " |")
    a("\n## Limitations (read before concluding anything)\n")
    for t in [
        "Daily bars only: no intraday path, so SL/TP ordering inside a bar is assumed worst-case, and gaps through a stop are filled at the stop (slightly optimistic).",
        "No swap/rollover costs and no slippage beyond the flat spread assumption; multi-day holds pay or earn carry in reality.",
        "One position at a time, fixed 1x notional, no position sizing and no Risk Engine in the loop; returns are not account returns.",
        "Few trades per cell (see table): wide intervals are the honest answer, not a defect.",
        f"{len(results)} symbol-strategy combinations were tested; with that many looks, one or two can look good by chance. Treat any single standout skeptically unless it also beats the random baseline and holds across symbols and years.",
        "The live paper loop trades M5/M15/H1, but only daily bars are studied here. Results do not transfer: shorter timeframes have far larger spread-to-ATR ratios, so costs bite much harder. They need their own study on intraday data.",
        "Data ends March 2022; later regimes (2022-2026 rate-hike cycle) are not covered.",
    ]:
        a(f"- {t}")
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--symbols", default="EURUSD,GBPUSD,USDJPY")
    ap.add_argument("--sims", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("reports"))
    ap.add_argument("--split-pct", type=int, default=65)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    results, bh, meta = [], {}, {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                                 "split_pct": a.split_pct, "sims": a.sims, "symbols": {}}
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        df = load_symbol(sym, a.data_dir, a.download)
        split_idx = int(len(df) * a.split_pct / 100)
        split_date = df["date"].iloc[split_idx]
        feats = build_features(df)
        meta["symbols"][sym] = dict(bars=len(df), start=str(df.date.min().date()), end=str(df.date.max().date()),
                                    split_date=str(split_date.date()), oos_bars=len(df) - split_idx)
        bh[sym] = buy_hold(df, split_idx)
        for name, (factory, has_mult) in STRATEGIES.items():
            print(f"[{sym}] {name} ...", flush=True)
            results.append(study_combo(df, feats, sym, name, factory, has_mult, split_idx, split_date,
                                       a.sims, a.seed))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (a.out / f"real_data_backtest_{stamp}.json").write_text(json.dumps(
        {"meta": meta, "results": results, "buy_hold": bh}, indent=2, default=float))
    write_markdown(results, bh, meta, a.out / f"real_data_backtest_{stamp}.md")
    print(f"wrote {a.out}/real_data_backtest_{stamp}.md / .json")


if __name__ == "__main__":
    main()
