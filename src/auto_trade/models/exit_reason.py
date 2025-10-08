"""出場原因枚舉"""

from enum import Enum


class ExitReason(Enum):
    """出場原因"""

    TRAILING_STOP = "TrailingStop"  # 移動停損
    TAKE_PROFIT = "TakeProfit"  # 獲利了結
    STOP_LOSS = "StopLoss"  # 停損
    HOLD = "Hold"  # 持倉中
