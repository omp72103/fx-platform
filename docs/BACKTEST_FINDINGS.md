# Backtest findings on real data

**Date:** 2026-10-11 · **Data:** ejtraderLabs/historical-data (Apache-2.0), EURUSD / GBPUSD / USDJPY,
daily bars 2012-12-04 → 2022-03-04 (2,400 bars each) and H1 bars for the same span.
**Reproduce:** `scripts/run_real_backtest.py`, `scripts/study_pooled_and_h1.py`
(full tables in `reports/real_data_backtest_20261011.md`).

Nothing was tuned: strategy parameters are exactly what is in `app/strategies/`. Costs are 1.5 pips round
trip, entry at the next bar's open, stop assumed hit first when stop and target both fall inside a bar.

## Bottom line

1. **The configuration the live paper loop runs (M5/M15/H1) has no edge.** On H1, with hundreds to
   thousands of trades per cell, every strategy is a coin flip before costs (out-of-sample profit factor
   0.95-1.08) and loses steadily after a 1.5-pip cost (0.79-0.99). M5 was not testable with the available
   data and, with a larger spread-to-range ratio, would be worse, not better.
2. **Trend-following does not work as written**, on any pair or timeframe tested. In-sample it loses
   (pooled daily profit factor 0.87, 196 trades); out-of-sample it is roughly breakeven (0.95, 127 trades).
   This matches the 0.05 out-of-sample Sharpe already recorded in the registry.
3. **Breakout has no usable evidence**: 3-6 out-of-sample trades per pair, and poor in-sample (profit factor
   0.3-0.5, 12-16 trades per pair). Treat it as unvalidated and probably not working.
4. **The regime selector inherits trend-following's problem.** Pooled in-sample it loses (0.84, 250 trades).
   Its out-of-sample gain (1.10, 154 trades, CI spans zero) is not distinguishable from luck.
5. **One lead: daily mean reversion (RSI extremes in a RANGE regime).** Positive in every view examined,
   but only a *hypothesis*, see below.

## The one lead, stated carefully

| View (daily, 1.5 pips) | Trades | Profit factor | Avg trade |
|---|---|---|---|
| Pooled, 65/35 split: in-sample | 55 | 1.41 | +15 bps |
| Pooled, 65/35 split: out-of-sample | 39 | 2.37 | +32 bps (95% CI 8 to 56) |
| Pooled, 50/50 split: in / out | 35 / 59 | 1.82 / 1.63 | +27 / +19 bps |
| Pooled, 80/20 split: in / out | 67 / 27 | 1.62 / 1.96 | +20 / +27 bps |
| Full sample, pooled | 94 | 1.71 | +22 bps (95% CI 5 to 39), win rate 60%, break-even win rate 46% |
| Full sample per pair: EURUSD / GBPUSD / USDJPY | 26 / 41 / 27 | 2.60 / 1.37 / 1.75 | |

Why it is encouraging: it is positive in both halves of the data, at three different split points, on
three pairs, and survives zero-to-3-pip cost changes (it trades rarely, so costs barely matter).

Why it is **not** established:

- **Found by looking.** I picked it out of 12 strategy-pair combinations *after* seeing the results. A
  standout among 12 looks is expected by chance more often than intuition says. The pooled analysis was
  also run after the fact. The statistics above are therefore optimistic.
- **Small and slow.** About 10 trades a year across all three pairs. 94 trades is thin, and bootstrap
  intervals on samples this size are themselves unreliable.
- **The three pairs are not independent.** EURUSD and GBPUSD share a USD leg and move together, so the
  effective sample is smaller than 94.
- **Per-pair results are inconsistent by split.** For example GBPUSD was *negative* in-sample (profit
  factor 0.81, 26 trades) and strongly positive out-of-sample (3.31, 15 trades); a stable edge should
  not flip like that. Part of the GBPUSD out-of-sample gain comes from 2020 (+5.6% from 8 trades).
- **It fails on H1** (profit factor ~0.8, ~1,000 trades per pair out-of-sample). Whatever this is, it is
  not a general property of "RSI extremes in range markets".
- **Unit economics are modest.** At 1x notional that is roughly 0.5-1% a year per pair before leverage.

## What would settle it

A forward test, decided in advance, on data the strategy has never seen:

1. Export **post-March-2022 daily bars** for the same three pairs from MT5 (about 4.5 years, expect
   roughly 40-50 pooled trades).
2. Run `MeanReversionStrategy` with **no parameter changes** and the same costs.
3. Pre-commit to the pass criterion before looking: e.g. pooled profit factor > 1.2 **and** the
   bootstrap interval of the average trade above zero. If it fails, retire it; do not retune it on the
   new data and call that a pass.

Until then it should stay `candidate`, on a **daily** timeframe, on a demo account.

## Engine problems found and fixed while doing this

- **Sharpe was mis-annualised.** `app/backtest/engine.py` multiplied the per-trade Sharpe by sqrt(252),
  which assumes one trade a day. With ~13-day holds the reported magnitude was inflated several-fold
  (EURUSD trend-following out-of-sample: -1.74 reported vs -0.38 corrected, and -0.42 from an independent
  daily mark-to-market series). This feeds the `/models/{v}/promote` gate. It now annualises by trades per
  year.
- **Performance.** The engine rebuilt every feature at every bar: ~307 s for one daily backtest. Because
  all features are causal it now computes them once (`precompute_features=True`), cutting the full
  12-combination study to about 40 s. `tests/test_backtest_engine.py` asserts identical trades to the slow
  path with a strategy that trades constantly, and a deliberate one-bar look-ahead is caught by it.

## Limitations of this study

- Daily bars: no intraday path; stop-before-target assumed; fills at the stop even through gaps (slightly
  optimistic).
- No swap/rollover, no slippage beyond the flat spread; carry matters for multi-day holds.
- One position at a time, fixed 1x notional, no position sizing and no Risk Engine in the loop; returns
  are not account returns.
- Data ends March 2022 and covers a mostly low-rate, low-volatility FX era. It says nothing about the
  2022-2026 regime.
- Public price data and classic indicators only. An efficient market giving back roughly zero before
  costs is the normal outcome, not a surprise. This does not show that no strategy can work, only that
  these ones, as written, do not demonstrably do so.

## Consequences for the platform

- Do not read the paper loop's profit/loss on M5/M15/H1 as evidence of anything good or bad about the
  platform; it is evidence about strategies that this study shows have no edge there.
- Any claim of "profit for traders" would currently be unsupported. That matters legally as well as
  practically (see the SEBI note in `README.md`).
- Next-best uses of effort, in order: the forward test above; a cost-aware intraday study only if you
  have a hypothesis worth testing; then, and only then, ML on top of features that have shown some signal.
