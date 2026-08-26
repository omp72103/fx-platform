"""
Breakout strategy — Donchian-channel break during HIGH_VOLATILITY, betting
the move continues rather than snapping back.

UNVALIDATED — see the same note in mean_reversion.py. Backtest it honestly
(in-sample vs out-of-sample, per blueprint section 19) before it's anything
more than a paper-trading candidate.
"""
from app.strategies.base import Strategy, MarketState, StrategySignal


class BreakoutStrategy(Strategy):
    strategy_id = "breakout_donchian"
    strategy_version = "breakout_donchian_v1"
    family = "breakout"
    supported_regimes = ("HIGH_VOLATILITY",)

    def __init__(self, atr_sl_mult: float = 2.0, atr_tp_mult: float = 3.0):
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
        dc_high = row.get("donchian_high_20")
        dc_low = row.get("donchian_low_20")
        if atr is None or atr != atr or dc_high is None or dc_low is None:
            return self._hold(ms, "features_unavailable")

        if price >= dc_high:
            direction = "BUY"
        elif price <= dc_low:
            direction = "SELL"
        else:
            return self._hold(ms, "no_channel_break")

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
            reason_codes=["donchian_20_break", f"atr_{self.atr_sl_mult}x_stop"],
        )
