"""Strategy-related data models."""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from auto_trade.models.account import Action


@dataclass
class TradingSignal:
    """交易訊號模型"""

    action: Action  # Buy, Sell, Hold
    symbol: str
    price: float
    quantity: int = 1
    confidence: float = 1.0  # 訊號信心度 (0-1)
    reason: str = ""  # 訊號原因
    timestamp: datetime | None = None
    stop_loss_price: float | None = None  # 計算好的停損價格


@dataclass
class StrategyInput:
    """策略輸入資料模型"""

    symbol: str
    kbars: pd.DataFrame  # OHLCV資料
    current_price: float  # 即時價格
    timestamp: datetime
    stop_loss_points: int = 80  # 停損點數
