# Real-data backtest study

Generated 2026-10-11 10:53 UTC by `scripts/run_real_backtest.py`. Data: ejtraderLabs/historical-data (Apache-2.0), daily bars. Split: chronological 65% in-sample / rest out-of-sample (split date per symbol below). Costs: 1.5 pips round trip, entry at next bar's open, SL assumed hit first when SL and TP are both inside a bar. **Nothing was tuned.**

## Data

| Symbol | Bars | From | To | Out-of-sample starts | OOS bars |
|---|---|---|---|---|---|
| EURUSD | 2400 | 2012-12-04 | 2022-03-04 | 2018-12-07 | 840 |
| GBPUSD | 2400 | 2012-12-04 | 2022-03-04 | 2018-12-07 | 840 |
| USDJPY | 2400 | 2012-12-04 | 2022-03-04 | 2018-12-07 | 840 |

## Out-of-sample results (the ones that matter)

`Beats random` = share of 500 coin-flip-entry runs (same stops, costs, ~same trade count) whose average trade was worse than the strategy's. Around 50% means no better than luck; a real edge would sit near or above 95%. `CI` = bootstrap 95% interval of the average trade; if it spans 0 the edge is not statistically distinguishable from nothing.

| Symbol | Strategy | Trades | Win % | Profit factor | Avg trade (bps) | CI of avg trade (bps) | Return % | Max DD % | Sharpe (daily) | Beats random |
|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | trend_following | 40 | 32.5 | 0.80 | -12.3 | [-46.5, 23.2] | -5.65 | 12.19 | -0.42 | 31% |
| EURUSD | mean_reversion | 9 | 55.6 | 1.29 | 8.4 | [-35.6, 51.4] | 0.73 | 1.48 | 0.22 | 60% |
| EURUSD | breakout | 3 | 33.3 | 0.41 | -85.1 | n/a (<5 trades) | -2.53 | 4.21 | -0.61 | n/a |
| EURUSD | regime_selector | 50 | 36.0 | 0.80 | -11.6 | [-42.7, 21.1] | -6.48 | 11.90 | -0.47 | n/a |
| GBPUSD | trend_following | 47 | 38.3 | 0.97 | -2.2 | [-45.8, 44.0] | 0.57 | 8.47 | 0.06 | 49% |
| GBPUSD | mean_reversion | 15 | 73.3 | 3.31 | 55.6 | [7.6, 98.4] | 8.61 | 1.15 | 1.15 | 100% |
| GBPUSD | breakout | 6 | 50.0 | 1.07 | 10.7 | [-222.0, 230.4] | 0.44 | 7.45 | 0.05 | n/a |
| GBPUSD | regime_selector | 52 | 46.2 | 1.20 | 14.2 | [-34.3, 61.3] | 9.27 | 8.33 | 0.43 | n/a |
| USDJPY | trend_following | 40 | 42.5 | 1.10 | 5.0 | [-28.4, 40.1] | 0.75 | 7.27 | 0.08 | 71% |
| USDJPY | mean_reversion | 15 | 66.7 | 2.15 | 23.3 | [-8.7, 51.7] | 3.52 | 1.29 | 0.91 | 91% |
| USDJPY | breakout | 5 | 40.0 | 0.55 | -53.9 | [-206.5, 98.6] | -2.74 | 4.72 | -0.35 | n/a |
| USDJPY | regime_selector | 52 | 50.0 | 1.32 | 13.6 | [-15.1, 42.5] | 6.00 | 4.37 | 0.42 | n/a |

## In-sample vs out-of-sample

| Symbol | Strategy | IS trades | IS profit factor | IS Sharpe (daily) | OOS trades | OOS profit factor | OOS Sharpe (daily) | Engine OOS Sharpe (fixed formula) |
|---|---|---|---|---|---|---|---|---|
| EURUSD | trend_following | 62 | 1.02 | 0.05 | 40 | 0.80 | -0.42 | -0.38 |
| EURUSD | mean_reversion | 17 | 3.61 | 1.09 | 9 | 1.29 | 0.22 | 0.21 |
| EURUSD | breakout | 12 | 0.48 | -0.54 | 3 | 0.41 | -0.61 | -0.43 |
| EURUSD | regime_selector | 79 | 1.05 | 0.11 | 50 | 0.80 | -0.47 | -0.40 |
| GBPUSD | trend_following | 66 | 1.08 | 0.05 | 47 | 0.97 | 0.06 | -0.05 |
| GBPUSD | mean_reversion | 26 | 0.81 | -0.25 | 15 | 3.31 | 1.15 | 1.33 |
| GBPUSD | breakout | 16 | 0.31 | -0.73 | 6 | 1.07 | 0.05 | 0.05 |
| GBPUSD | regime_selector | 91 | 0.80 | -0.48 | 52 | 1.20 | 0.43 | 0.33 |
| USDJPY | trend_following | 68 | 0.61 | -0.76 | 40 | 1.10 | 0.08 | 0.16 |
| USDJPY | mean_reversion | 12 | 1.47 | 0.30 | 15 | 2.15 | 0.91 | 0.83 |
| USDJPY | breakout | 13 | 0.52 | -0.41 | 5 | 0.55 | -0.35 | -0.37 |
| USDJPY | regime_selector | 80 | 0.72 | -0.59 | 52 | 1.32 | 0.42 | 0.52 |

## Cost sensitivity (out-of-sample)

| Symbol | Strategy | 0 pips: return % / PF | 1.5 pips | 3 pips |
|---|---|---|---|---|
| EURUSD | trend_following | -4.55 / 0.81 | -5.05 / 0.80 | -5.55 / 0.78 |
| EURUSD | mean_reversion | 0.85 / 1.35 | 0.73 / 1.29 | 0.62 / 1.24 |
| EURUSD | breakout | -2.55 / 0.41 | -2.59 / 0.41 | -2.63 / 0.40 |
| EURUSD | regime_selector | -5.33 / 0.82 | -5.95 / 0.80 | -6.57 / 0.78 |
| GBPUSD | trend_following | -1.06 / 0.99 | -1.59 / 0.97 | -2.12 / 0.96 |
| GBPUSD | mean_reversion | 8.80 / 3.39 | 8.61 / 3.31 | 8.42 / 3.24 |
| GBPUSD | breakout | 0.43 / 1.08 | 0.36 / 1.07 | 0.29 / 1.06 |
| GBPUSD | regime_selector | 7.47 / 1.21 | 6.82 / 1.20 | 6.19 / 1.18 |
| USDJPY | trend_following | 2.34 / 1.13 | 1.77 / 1.10 | 1.21 / 1.07 |
| USDJPY | mean_reversion | 3.73 / 2.25 | 3.52 / 2.15 | 3.31 / 2.06 |
| USDJPY | breakout | -2.68 / 0.56 | -2.75 / 0.55 | -2.82 / 0.55 |
| USDJPY | regime_selector | 7.80 / 1.36 | 7.03 / 1.32 | 6.26 / 1.29 |

## Buy and hold (same period, for scale)

| Symbol | OOS return % | OOS Sharpe | OOS max DD % |
|---|---|---|---|
| EURUSD | -3.90 | -0.16 | 11.32 |
| GBPUSD | 3.52 | 0.16 | 14.01 |
| USDJPY | 1.94 | 0.12 | 9.99 |

## Stability by entry year (full sample, compounded % per year; n trades)

| Symbol | Strategy | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | trend_following | -0.9 (1) | +6.6 (6) | -9.9 (13) | +1.2 (13) | +1.1 (15) | +1.5 (15) | -5.9 (10) | +0.1 (14) | +2.9 (14) | -0.9 (1) |
| EURUSD | mean_reversion | +0.7 (1) | +1.4 (2) | +1.7 (3) | +1.1 (1) | +2.7 (7) | +0.9 (3) | +1.7 (3) | -0.6 (3) | -0.4 (3) | - |
| EURUSD | breakout | - | -2.1 (4) | -2.1 (5) | -2.3 (1) | - | -3.0 (2) | - | -2.6 (3) | - | - |
| EURUSD | regime_selector | -0.2 (2) | +3.6 (8) | -7.9 (18) | +1.2 (13) | +3.9 (20) | +0.8 (19) | -4.1 (13) | -1.4 (19) | +1.4 (16) | -0.9 (1) |
| GBPUSD | trend_following | +0.7 (2) | +3.5 (11) | +3.7 (13) | -6.9 (16) | -1.8 (9) | +2.5 (16) | -2.2 (15) | +0.5 (18) | +1.0 (11) | +0.6 (2) |
| GBPUSD | mean_reversion | - | +0.5 (1) | +0.4 (6) | -2.7 (5) | +1.5 (7) | -2.1 (7) | +1.9 (4) | +5.6 (8) | +1.6 (2) | -0.6 (1) |
| GBPUSD | breakout | - | -0.8 (3) | -2.5 (6) | -9.0 (5) | - | -5.6 (3) | +6.2 (2) | -3.5 (3) | - | - |
| GBPUSD | regime_selector | +0.7 (2) | +2.9 (13) | -2.0 (20) | -12.2 (20) | -1.7 (14) | -3.7 (23) | +8.2 (10) | -1.5 (26) | +1.8 (12) | -0.0 (3) |
| USDJPY | trend_following | +0.6 (2) | -1.4 (10) | -8.0 (11) | -0.3 (20) | -11.9 (11) | -1.3 (15) | -0.4 (9) | -1.1 (13) | +1.8 (14) | -0.1 (3) |
| USDJPY | mean_reversion | - | - | +0.0 (7) | +1.5 (1) | -0.3 (3) | +0.1 (2) | +2.0 (3) | +1.4 (6) | +0.1 (4) | +0.6 (1) |
| USDJPY | breakout | - | -1.0 (5) | +0.6 (1) | -7.7 (6) | - | -0.3 (1) | -1.9 (1) | -2.5 (3) | +1.7 (1) | - |
| USDJPY | regime_selector | +0.6 (2) | -0.2 (14) | -7.7 (14) | +0.5 (24) | -12.7 (12) | -0.2 (16) | +0.3 (12) | +3.2 (17) | +2.5 (18) | -0.1 (3) |

## Limitations (read before concluding anything)

- Daily bars only: no intraday path, so SL/TP ordering inside a bar is assumed worst-case, and gaps through a stop are filled at the stop (slightly optimistic).
- No swap/rollover costs and no slippage beyond the flat spread assumption; multi-day holds pay or earn carry in reality.
- One position at a time, fixed 1x notional, no position sizing and no Risk Engine in the loop; returns are not account returns.
- Few trades per cell (see table): wide intervals are the honest answer, not a defect.
- 12 symbol-strategy combinations were tested; with that many looks, one or two can look good by chance. Treat any single standout skeptically unless it also beats the random baseline and holds across symbols and years.
- The live paper loop trades M5/M15/H1, but only daily bars are studied here. Results do not transfer: shorter timeframes have far larger spread-to-ATR ratios, so costs bite much harder. They need their own study on intraday data.
- Data ends March 2022; later regimes (2022-2026 rate-hike cycle) are not covered.
