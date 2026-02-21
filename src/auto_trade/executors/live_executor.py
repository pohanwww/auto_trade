"""Live Executor - 實盤下單執行器.

通過 Shioaji API 執行實際下單，等待成交回報。
"""

import time
from datetime import datetime, timedelta

from auto_trade.executors.base_executor import BaseExecutor, FillResult
from auto_trade.models.position import OrderAction
from auto_trade.services.order_service import OrderService


class LiveExecutor(BaseExecutor):
    """實盤下單執行器

    使用 Shioaji API 進行下單和成交確認。
    """

    def __init__(
        self,
        order_service: OrderService,
        timeout_minutes: int = 5,
    ):
        self.order_service = order_service
        self.timeout_minutes = timeout_minutes

    def execute(self, order_action: OrderAction) -> FillResult:
        """執行市價單並等待成交

        Args:
            order_action: 下單指令

        Returns:
            FillResult: 成交結果
        """
        try:
            octype = "Cover" if order_action.order_type == "Close" else "Auto"
            print(
                f"下市價單: {order_action.action.value} {order_action.order_type} x{order_action.quantity}"
            )

            result = self.order_service.place_order(
                symbol=order_action.symbol,
                sub_symbol=order_action.sub_symbol,
                action=order_action.action,
                quantity=order_action.quantity,
                price_type="MKT",
                octype=octype,
            )

            if result.status == "Error":
                print(f"下單失敗: {result.msg}")
                return FillResult(
                    success=False,
                    message=f"Order rejected: {result.msg}",
                )

            print(f"下單成功: {order_action.action.value} {order_action.order_type}")

            # 等待成交
            start_time = datetime.now()
            while datetime.now() - start_time < timedelta(minutes=self.timeout_minutes):
                trades = self.order_service.check_order_status(result.order_id)
                if trades:
                    status = trades[0].status.status
                    if status in ["Filled", "PartFilled", "Status.Filled"]:
                        current_trade = trades[0]
                        time.sleep(2)  # 等待系統更新

                        if current_trade.status.deals:
                            last_deal = current_trade.status.deals[-1]
                            fill_price = int(last_deal.price)
                            print(f"成交確認: {fill_price} @ {last_deal.time}")

                            return FillResult(
                                success=True,
                                fill_price=fill_price,
                                fill_time=last_deal.time,
                                fill_quantity=order_action.quantity,
                                order_id=result.order_id,
                            )
                        else:
                            return FillResult(
                                success=False,
                                message="No deal info in filled trade",
                            )

                    elif status in [
                        "Cancelled",
                        "Failed",
                        "Status.Cancelled",
                        "Status.Failed",
                    ]:
                        return FillResult(
                            success=False,
                            message=f"Order {status}",
                        )

                time.sleep(1)

            return FillResult(
                success=False,
                message=f"Timeout after {self.timeout_minutes} minutes",
            )

        except Exception as e:
            print(f"下單或等待成交失敗: {str(e)}")
            return FillResult(
                success=False,
                message=f"Exception: {str(e)}",
            )
