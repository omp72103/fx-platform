"""
Event-driven backtester — blueprint section 18:

    Historical data -> market replay -> feature calculation -> regime
    detection -> strategy selection -> signal -> risk engine ->
    execution simulator -> portfolio accounting -> performance report.

    "Backtests must model spread, commissions where applicable, slippage
    and latency assumptions. Event-driven simulation is preferred."

And section 19 (Validation Methodology):

    "Do not randomly shuffle financial time series. Use chronological
    train/validation/out-of-sample/forward periods... At time T the model
    may use only information that was available at or before T."

No-look-ahead is enforced structurally here, not just by convention: at
each bar i, the strategy only ever sees df.iloc[:i+1] (an expanding window
ending at i), and entry is simulated at bar i+1's OPEN — never at bar i's
own close — mirroring "execute at the next bar's actual open" from
data_pipeline_ohlc.py's own docstring and blueprint section 4.

This is deliberately a bar-level (not tick-level) simulator, consistent
with the honest limitation Forex_Strategy_Backtest_Explained.pdf itself
flags for the daily-bar backtest: "not the same as second-by-second broker
data." Re-run against real MT5 tick/minute data once connected for a finer
result — see RUNBOOK.md.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np
import pandas as pd

from app.features.engine import build_features
from app.features.regime import classify_regime_with_confidence
from app.strategies.base import MarketState, Strategy


@dataclass
class BacktestTrade:
    entry_time: datetime
    exit_time: datetime
    direction: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    is_out_of_sample: bool
    exit_reason: str


@dataclass
class PeriodMetrics:
    n_trades: int
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float | None  # None = undefined (zero losing trades in this sample) -- see note below


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    in_sample: PeriodMetrics = None
    out_of_sample: PeriodMetrics = None
    full_sample: PeriodMetrics = None
    equity_curve: list = field(default_factory=list)  # [(timestamp, equity), ...]


def _period_metrics(trade_pnls: list[float], bars_per_trade_estimate: int = 1) -> PeriodMetrics:
    if not trade_pnls:
        return PeriodMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    arr = np.array(trade_pnls)
    equity = np.cumprod(1 + arr) * 10_000
    peak = np.maximum.accumulate(equity)
    drawdown = (peak - equity) / peak
    total_return_pct = (equity[-1] / 10_000 - 1) * 100
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    win_rate = len(wins) / len(arr) * 100 if len(arr) else 0.0
    # profit_factor = gross wins / gross losses. With zero losing trades this
    # is mathematically infinite -- and float('inf') is NOT valid JSON
    # (Starlette's JSONResponse uses allow_nan=False and will 500 on it), so
    # it's represented as None ("undefined, no losers in this sample") rather
    # than silently breaking every API response with a lucky small sample.
    # A handful of winning trades and zero losers is a small-sample artifact,
    # not evidence of a working strategy -- treat None as a "not enough
    # losing trades yet to compute this" flag, not as "infinitely good".
    if len(losses) and losses.sum() != 0:
        profit_factor = round(float(wins.sum() / abs(losses.sum())), 3)
    else:
        profit_factor = None
    sharpe = (arr.mean() / arr.std() * np.sqrt(252)) if arr.std() > 0 else 0.0
    return PeriodMetrics(
        n_trades=len(arr), total_return_pct=round(float(total_return_pct), 2),
        sharpe=round(float(sharpe), 3), max_drawdown_pct=round(float(drawdown.max() * 100), 2),
        win_rate=round(win_rate, 1), profit_factor=profit_factor,
    )


def run_backtest(ohlc_df: pd.DataFrame, strategy: Strategy, symbol: str, timeframe: str,
                  split_date: pd.Timestamp = None, spread_pips: float = 1.5,
                  pip_size: float = 0.0001, min_warmup: int = 260,
                  max_holding_bars: int = 40) -> BacktestResult:
    """ohlc_df must have columns date/open/high/low/close, sorted ascending."""
    df = ohlc_df.reset_index(drop=True)
    if split_date is None:
        split_date = df["date"].iloc[int(len(df) * 0.65)]

    trades: list[BacktestTrade] = []
    equity_curve = []
    open_trade = None  # dict with entry info while a position is open

    for i in range(min_warmup, len(df) - 1):
        window = df.iloc[:i + 1]  # <= only data at/before bar i: no look-ahead
        feats = build_features(window)
        row = feats.iloc[-1]
        regime, confidence = classify_regime_with_confidence(row)

        if open_trade is not None:
            bar = df.iloc[i]
            exit_price, exit_reason = _check_exit(open_trade, bar)
            if exit_price is not None or (i - open_trade["entry_idx"]) >= max_holding_bars:
                if exit_price is None:
                    exit_price = bar["close"]
                    exit_reason = "max_holding"
                spread_cost = spread_pips * pip_size
                raw_move = (exit_price - open_trade["entry_price"]) if open_trade["direction"] == "BUY" \
                    else (open_trade["entry_price"] - exit_price)
                pnl_pct = (raw_move - spread_cost) / open_trade["entry_price"]
                trades.append(BacktestTrade(
                    entry_time=open_trade["entry_time"], exit_time=df.iloc[i]["date"],
                    direction=open_trade["direction"], entry_price=open_trade["entry_price"],
                    exit_price=exit_price, pnl_pct=pnl_pct,
                    is_out_of_sample=open_trade["entry_time"] >= split_date, exit_reason=exit_reason,
                ))
                open_trade = None
            continue  # only manage one position at a time, keep it simple/honest

        ms = MarketState(symbol=symbol, timeframe=timeframe, features_df=feats,
                          regime=regime, regime_confidence=confidence, account_equity=10_000)
        signal = strategy.generate_signal(ms)
        if signal.direction not in ("BUY", "SELL"):
            continue

        # Enter at the NEXT bar's open — not this bar's close.
        next_bar = df.iloc[i + 1]
        open_trade = {
            "direction": signal.direction, "entry_price": next_bar["open"],
            "entry_time": next_bar["date"], "entry_idx": i + 1,
            "sl": signal.stop_loss, "tp": signal.take_profit,
        }

    in_sample_pnls = [t.pnl_pct for t in trades if not t.is_out_of_sample]
    oos_pnls = [t.pnl_pct for t in trades if t.is_out_of_sample]
    all_pnls = [t.pnl_pct for t in trades]

    return BacktestResult(
        trades=trades,
        in_sample=_period_metrics(in_sample_pnls),
        out_of_sample=_period_metrics(oos_pnls),
        full_sample=_period_metrics(all_pnls),
    )


def _check_exit(open_trade: dict, bar) -> tuple:
    """Conservative: if a bar's range touches SL, assume SL hit first
    (worst-case ordering, since we don't have intrabar sequencing)."""
    d = open_trade["direction"]
    sl, tp = open_trade["sl"], open_trade["tp"]
    if d == "BUY":
        if sl is not None and bar["low"] <= sl:
            return sl, "stop_loss"
        if tp is not None and bar["high"] >= tp:
            return tp, "take_profit"
    else:
        if sl is not None and bar["high"] >= sl:
            return sl, "stop_loss"
        if tp is not None and bar["low"] <= tp:
            return tp, "take_profit"
    return None, None
