"""Base Executor - 下單執行器的抽象介面.

Executor 負責將 OrderAction 轉為實際的下單操作。
LiveExecutor 調用 Shioaji API，BacktestExecutor 模擬成交。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from auto_trade.models.position import OrderAction


@dataclass
class FillResult:
    """成交結果"""

    success: bool
    fill_price: int | None = None
    fill_time: datetime | None = None
    fill_quantity: int = 0
    order_id: str | None = None
    message: str = ""


class BaseExecutor(ABC):
    """下單執行器抽象基類"""

    @abstractmethod
    def execute(self, order_action: OrderAction) -> FillResult:
        """執行下單動作

        Args:
            order_action: 由 PositionManager 產生的下單指令

        Returns:
            FillResult: 成交結果
        """
        ...
