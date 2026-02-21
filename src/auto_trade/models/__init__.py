"""Data models for auto trading system."""

from .account import Action, Balance, FuturePosition, Margin, Position
from .backtest import (
    BacktestConfig,
    BacktestPosition,
    BacktestResult,
    BacktestTrade,
    PerformanceMetrics,
)
from .market import EMAData, EMAList, KBar, KBarList, MACDData, MACDList, Quote
from .order import (
    Deal,
    FuturesOrderRequest,
    FuturesOrderResult,
    FuturesTrade,
    OrderStatus,
)
from .position import (
    ExitRule,
    LegType,
    ManagedPosition,
    OrderAction,
    PositionLeg,
    PositionStatus,
)
from .position_record import ExitReason
from .strategy import SignalType, StrategySignal
from .trading_unit import TradingUnit

__all__ = [
    # Market models
    "KBar",
    "KBarList",
    "EMAData",
    "EMAList",
    "MACDData",
    "MACDList",
    "Quote",
    # Account models
    "Action",
    "Balance",
    "Position",
    "FuturePosition",
    "Margin",
    # Order models
    "FuturesOrderRequest",
    "FuturesOrderResult",
    "FuturesTrade",
    "OrderStatus",
    "Deal",
    # Strategy models
    "StrategySignal",
    "SignalType",
    # Position models
    "ManagedPosition",
    "PositionLeg",
    "PositionStatus",
    "LegType",
    "ExitRule",
    "OrderAction",
    # TradingUnit
    "TradingUnit",
    # Exit reason
    "ExitReason",
    # Backtest models
    "BacktestConfig",
    "BacktestPosition",
    "BacktestResult",
    "BacktestTrade",
    "PerformanceMetrics",
]
