"""Strategy-related data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SignalType(Enum):
    """信號類型"""

    ENTRY_LONG = "ENTRY_LONG"  # 做多進場
    ENTRY_SHORT = "ENTRY_SHORT"  # 做空進場
    EXIT = "EXIT"  # 出場
    HOLD = "HOLD"  # 持有/觀望


@dataclass
class StrategySignal:
    """策略信號 - BaseStrategy 產出的純信號

    只描述「方向和原因」，不包含倉位管理細節（如 stop_loss_price），
    那是 PositionManager 的責任。
    """

    signal_type: SignalType
    symbol: str
    price: float
    confidence: float = 1.0  # 信號信心度 (0-1)
    reason: str = ""
    timestamp: datetime | None = None
    metadata: dict = field(default_factory=dict)  # 策略特定的附加資訊
