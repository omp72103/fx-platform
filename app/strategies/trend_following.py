"""
Trend-following strategy — EMA-spread + trend-slope filter, ATR-based stops.

THIS IS THE EXACT STRATEGY LOGIC DESCRIBED IN
Forex_Strategy_Backtest_Explained.pdf. Its own honest out-of-sample backtest
(2016-2026, Federal Reserve H.10 daily EURUSD) came back at Sharpe 0.05 and
profit factor 1.03 — essentially breakeven after costs, no durable edge.

Per blueprint section 33 ("A high-return backtest with data leakage,
unrealistic execution or unstable drawdown is considered a failed
engineering result") and section 11 ("Do not rank only by total historical
profit"), this strategy_version is marked status="candidate" with its real
validation_metrics attached (see scripts/register_strategies.py) — NOT
"approved". The Strategy Selector (app/strategies/registry.py) and Risk
Engine both check status, and the paper loop will run it (that's the point
of paper trading — watch a candidate strategy safely) but it must not be
promoted to real capital without re-validation on fresh out-of-sample data.
"""
from app.strategies.base import Strategy, MarketState, StrategySignal


class TrendFollowingStrategy(Strategy):
    strategy_id = "trend_following_ema"
    strategy_version = "trend_following_ema_v1"
    family = "trend_following"
    supported_regimes = ("TREND_UP", "TREND_DOWN")

    def __init__(self, atr_sl_mult: float = 1.5, atr_tp_mult: float = 2.5):
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult

    def generate_signal(self, ms: MarketState) -> StrategySignal:
        df = ms.features_df
        if len(df) == 0:
            return self._hold(ms, "no_data")
        row = df.iloc[-1]
        if ms.regime not in self.supported_regimes:
            return self._hold(ms, f"regime_{ms.regime}_not_supported")

        price = row["close"]
        atr = row.get("atr_14")
        if atr is None or atr != atr:  # NaN check
            return self._hold(ms, "atr_unavailable")

        direction = "BUY" if ms.regime == "TREND_UP" else "SELL"
        if direction == "BUY":
            sl = price - self.atr_sl_mult * atr
            tp = price + self.atr_tp_mult * atr
        else:
            sl = price + self.atr_sl_mult * atr
            tp = price - self.atr_tp_mult * atr

        return StrategySignal(
            strategy_id=self.strategy_id, strategy_version=self.strategy_version,
            symbol=ms.symbol, timeframe=ms.timeframe, direction=direction,
            confidence=round(ms.regime_confidence, 3),
            entry_reference=float(price), stop_loss=float(sl), take_profit=float(tp),
            regime=ms.regime,
            reason_codes=["ema_trend_regime_match", f"atr_{self.atr_sl_mult}x_stop"],
        )
