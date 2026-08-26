"""
Strategy Registry & Selector — blueprint section 11.

  Registry fields: strategy_id, family, supported_symbols/timeframes,
  required_features, preferred_regimes, parameters, version, validation
  metrics.
  Selector inputs: current regime, regime-specific performance, volatility,
  spread, news risk, portfolio constraints. Output: ranked eligible
  strategies. "Do not rank only by total historical profit."

The registry entries below carry each strategy's REAL validation status.
trend_following_ema_v1's validation_metrics are the actual numbers from
Forex_Strategy_Backtest_Explained.pdf (out-of-sample Sharpe 0.05, profit
factor 1.03) — i.e. "candidate, does not currently show a durable edge".
The other two are "candidate, not yet backtested". None are "approved".
The selector uses this honestly: it does not pretend an unvalidated
strategy is safe just because it's registered and runnable in paper mode.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.strategies.base import Strategy, MarketState, StrategySignal
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.breakout import BreakoutStrategy


@dataclass
class RegistryEntry:
    strategy: Strategy
    supported_symbols: list
    supported_timeframes: list
    preferred_regimes: tuple
    status: str                       # candidate / approved / deprecated
    validation_metrics: dict = field(default_factory=dict)


class StrategyRegistry:
    def __init__(self):
        self._entries: dict[str, RegistryEntry] = {}

    def register(self, entry: RegistryEntry):
        self._entries[entry.strategy.strategy_version] = entry

    def get(self, strategy_version: str) -> Optional[RegistryEntry]:
        return self._entries.get(strategy_version)

    def all(self) -> list[RegistryEntry]:
        return list(self._entries.values())

    def eligible_for(self, symbol: str, timeframe: str, regime: str) -> list[RegistryEntry]:
        out = []
        for e in self._entries.values():
            if symbol not in e.supported_symbols:
                continue
            if timeframe not in e.supported_timeframes:
                continue
            if regime not in e.preferred_regimes:
                continue
            out.append(e)
        return out


def _score(entry: RegistryEntry, signal: StrategySignal, ms: MarketState) -> float:
    """Composite ranking score — NOT raw historical profit (section 11).

    Combines: the strategy's own signal confidence, the regime-detection
    confidence, and a validation-quality multiplier that deliberately
    PENALIZES strategies with weak/no out-of-sample validation instead of
    rewarding whichever backtested best in-sample.
    """
    metrics = entry.validation_metrics or {}
    oos_sharpe = metrics.get("out_of_sample_sharpe")
    if oos_sharpe is None:
        validation_factor = 0.3          # never validated -> heavily discounted
    else:
        validation_factor = max(0.05, min(1.0, oos_sharpe / 1.0))  # Sharpe 1.0 -> factor 1.0

    return signal.confidence * ms.regime_confidence * validation_factor


def select_signal(registry: StrategyRegistry, ms: MarketState) -> Optional[StrategySignal]:
    """Run every eligible strategy, rank the resulting non-HOLD signals, and
    return the top one (or None). This is what feeds the Signal Engine."""
    eligible = registry.eligible_for(ms.symbol, ms.timeframe, ms.regime)
    scored = []
    for entry in eligible:
        sig = entry.strategy.generate_signal(ms)
        if sig.direction == "HOLD":
            continue
        scored.append((_score(entry, sig, ms), sig))

    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def default_registry() -> StrategyRegistry:
    reg = StrategyRegistry()
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    timeframes = ["M5", "M15", "H1"]

    reg.register(RegistryEntry(
        strategy=TrendFollowingStrategy(),
        supported_symbols=symbols, supported_timeframes=timeframes,
        preferred_regimes=("TREND_UP", "TREND_DOWN"),
        status="candidate",
        validation_metrics={
            "source": "Forex_Strategy_Backtest_Explained.pdf",
            "in_sample_period": "1999-2015", "in_sample_sharpe": 0.46,
            "out_of_sample_period": "2016-2026", "out_of_sample_sharpe": 0.05,
            "out_of_sample_profit_factor": 1.03,
            "note": "Essentially breakeven out-of-sample. Do not promote to live without re-validation.",
        },
    ))
    reg.register(RegistryEntry(
        strategy=MeanReversionStrategy(),
        supported_symbols=symbols, supported_timeframes=timeframes,
        preferred_regimes=("RANGE",),
        status="candidate",
        validation_metrics={"note": "Not yet backtested. Paper-trade and validate before trusting."},
    ))
    reg.register(RegistryEntry(
        strategy=BreakoutStrategy(),
        supported_symbols=symbols, supported_timeframes=timeframes,
        preferred_regimes=("HIGH_VOLATILITY",),
        status="candidate",
        validation_metrics={"note": "Not yet backtested. Paper-trade and validate before trusting."},
    ))
    return reg
