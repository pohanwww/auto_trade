"""Backtest Executor - 回測模擬下單執行器.

在回測環境中模擬成交，假設市價單以當前價格成交。
"""

from datetime import datetime

from auto_trade.executors.base_executor import BaseExecutor, FillResult
from auto_trade.models.position import OrderAction


class BacktestExecutor(BaseExecutor):
    """回測模擬執行器

    假設所有市價單以指定價格即時成交。
    可以加入滑價 (slippage) 和手續費 (commission) 的模擬。
    """

    def __init__(
        self,
        slippage_points: int = 0,
        commission_per_contract: float = 0.0,
    ):
        self.slippage_points = slippage_points
        self.commission_per_contract = commission_per_contract
        self._simulated_price: int = 0
        self._simulated_time: datetime = datetime.now()

    def set_market_state(self, price: int, time: datetime) -> None:
        """設定當前市場狀態（由 BacktestEngine 呼叫）

        Args:
            price: 當前模擬價格
            time: 當前模擬時間
        """
        self._simulated_price = price
        self._simulated_time = time

    def execute(self, order_action: OrderAction) -> FillResult:
        """模擬成交

        Args:
            order_action: 下單指令

        Returns:
            FillResult: 模擬的成交結果
        """
        # 計算滑價
        from auto_trade.models.account import Action

        if order_action.action == Action.Buy:
            fill_price = self._simulated_price + self.slippage_points
        else:
            fill_price = self._simulated_price - self.slippage_points

        return FillResult(
            success=True,
            fill_price=fill_price,
            fill_time=self._simulated_time,
            fill_quantity=order_action.quantity,
            order_id=f"BT-{self._simulated_time.strftime('%H%M%S')}",
            message="Backtest fill",
        )
