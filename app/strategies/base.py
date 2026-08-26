"""
Common strategy interface — blueprint section 10:

    Common interface: generate_signal(market_state)
    Output: strategy_id, signal (BUY/SELL/HOLD), confidence, entry_reference,
            stop_loss, take_profit, strategy_version, reason_codes

"Strategies propose trades; they do not send orders. Strategy code cannot
bypass the Risk Engine." — every strategy in this package only returns a
StrategySignal; nothing in this file (or any file under app/strategies/)
is allowed to import the gateway or execution engine.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class MarketState:
    """What a strategy is allowed to see. Deliberately narrow — a strategy
    should not need direct DB or gateway access."""
    symbol: str
    timeframe: str
    features_df: pd.DataFrame   # output of app.features.engine.build_features + add_regime
    regime: str
    regime_confidence: float
    account_equity: float


@dataclass
class StrategySignal:
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    direction: str              # BUY / SELL / HOLD
    confidence: float
    entry_reference: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    regime: Optional[str] = None
    reason_codes: list = field(default_factory=list)


class Strategy(ABC):
    strategy_id: str
    strategy_version: str
    family: str
    supported_regimes: tuple = ()

    @abstractmethod
    def generate_signal(self, market_state: MarketState) -> StrategySignal:
        ...

    def _hold(self, ms: MarketState, reason: str) -> StrategySignal:
        return StrategySignal(
            strategy_id=self.strategy_id, strategy_version=self.strategy_version,
            symbol=ms.symbol, timeframe=ms.timeframe, direction="HOLD",
            confidence=0.0, regime=ms.regime, reason_codes=[reason],
        )
