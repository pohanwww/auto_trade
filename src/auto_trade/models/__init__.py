"""Data models for auto trading system."""

from .account import Action, Balance, FuturePosition, Margin, Position
from .exit_reason import ExitReason
from .market import EMAData, EMAList, KBar, KBarList, MACDData, MACDList, Quote
from .order import (
    Deal,
    FuturesOrderRequest,
    FuturesOrderResult,
    FuturesTrade,
    OrderStatus,
)
from .strategy import StrategyInput, TradingSignal

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
    "TradingSignal",
    "StrategyInput",
    # Exit reason
    "ExitReason",
]
