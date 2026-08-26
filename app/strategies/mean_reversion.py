"""
Mean-reversion strategy — RSI extremes inside a RANGE regime, betting price
snaps back toward the recent mean rather than continuing.

UNVALIDATED, same as every strategy_version in this repo: it has NOT been
backtested yet (Forex_Strategy_Backtest_Explained.pdf section 6 explicitly
lists mean reversion as "not yet tested"). It exists so the Strategy
Selector has more than one candidate to rank, and so paper trading exercises
more than a single code path. Run it through the same honest
in-sample/out-of-sample backtest process before trusting its signals.
"""
from app.strategies.base import Strategy, MarketState, StrategySignal

RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30


class MeanReversionStrategy(Strategy):
    strategy_id = "mean_reversion_rsi"
    strategy_version = "mean_reversion_rsi_v1"
    family = "mean_reversion"
    supported_regimes = ("RANGE",)

    def __init__(self, atr_sl_mult: float = 1.0, atr_tp_mult: float = 1.2):
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult

    def generate_signal(self, ms: MarketState) -> StrategySignal:
        df = ms.features_df
        if len(df) == 0:
            return self._hold(ms, "no_data")
        row = df.iloc[-1]
        if ms.regime not in self.supported_regimes:
            return self._hold(ms, f"regime_{ms.regime}_not_supported")

        rsi = row.get("rsi_14")
        atr = row.get("atr_14")
        price = row["close"]
        if rsi is None or atr is None or atr != atr:
            return self._hold(ms, "features_unavailable")

        if rsi >= RSI_OVERBOUGHT:
            direction = "SELL"
        elif rsi <= RSI_OVERSOLD:
            direction = "BUY"
        else:
            return self._hold(ms, "rsi_not_extreme")

        if direction == "BUY":
            sl = price - self.atr_sl_mult * atr
            tp = price + self.atr_tp_mult * atr
        else:
            sl = price + self.atr_sl_mult * atr
            tp = price - self.atr_tp_mult * atr

        extremity = min(abs(rsi - 50) / 50, 1.0)
        return StrategySignal(
            strategy_id=self.strategy_id, strategy_version=self.strategy_version,
            symbol=ms.symbol, timeframe=ms.timeframe, direction=direction,
            confidence=round(0.3 + 0.5 * extremity, 3),
            entry_reference=float(price), stop_loss=float(sl), take_profit=float(tp),
            regime=ms.regime,
            reason_codes=[f"rsi_{rsi:.1f}_extreme", "range_regime_match"],
        )
