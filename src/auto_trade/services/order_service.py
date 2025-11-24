"""Order service for managing futures trading operations."""

from datetime import datetime

import shioaji as sj

from auto_trade.models import (
    Action,
    Deal,
    FuturesOrderResult,
    FuturesTrade,
    OrderStatus,
)


class OrderService:
    """期貨下單服務類別"""

    def __init__(self, api_client):
        self.api_client = api_client

    def place_order(
        self,
        symbol: str,
        sub_symbol: str,
        action: Action,
        quantity: int,
        price: float | None = None,
        price_type: str = "LMT",
        order_type: str | None = None,
        octype: str = "Auto",
    ) -> FuturesOrderResult:
        """
        期貨下單功能

        Args:
            symbol: 商品代碼 (如: 'TXF')
            sub_symbol: 子商品代碼 (如: 'TXF202301')
            action: 買賣別 (Action.Buy 或 Action.Sell)
            quantity: 委託數量
            price: 委託價格 (限價單必填，市價單可為None)
            price_type: 價格類型 ('LMT': 限價, 'MKT': 市價, 'MKP': 範圍市價)
            order_type: 委託類別 (None: 自動選擇, 'ROD': 當日有效, 'IOC': 立即成交否則取消, 'FOK': 全部成交否則取消)
            octype: 委託類型 ('Auto': 自動, 'New': 新倉, 'Cover': 平倉, 'DayTrade': 當沖)

        Returns:
            下單結果
        """
        try:
            # 自動選擇委託類型
            if order_type is None:
                order_type = "IOC" if price_type == "MKT" else "ROD"

            # 取得期貨合約
            contract = self.api_client.Contracts.Futures[symbol][sub_symbol]

            # 建立委託單
            order = self.api_client.Order(
                action=getattr(sj.constant.Action, action.value),
                price=price or 0,  # 市價單時價格為0
                quantity=quantity,
                price_type=getattr(sj.constant.FuturesPriceType, price_type),
                order_type=getattr(sj.constant.OrderType, order_type),
                octype=getattr(sj.constant.FuturesOCType, octype),
                account=self.api_client.futopt_account,
            )

            # 執行下單
            trade = self.api_client.place_order(contract, order)

            # 更新委託狀態
            self.api_client.update_status(self.api_client.futopt_account)

            # place_order 如果失敗會拋出異常，沒拋異常就是成功
            return FuturesOrderResult(
                order_id=trade.order.id,
                symbol=symbol,
                sub_symbol=sub_symbol,
                action=action,
                quantity=quantity,
                price=price,
                price_type=price_type,
                order_type=order_type,
                octype=octype,
                status=trade.status.status,
                order_datetime=trade.status.order_datetime,
                msg=f"下單成功，委託編號: {trade.order.ordno}",
                trade=trade,
            )

        except Exception as e:
            return FuturesOrderResult(
                order_id="",
                symbol=symbol,
                sub_symbol=sub_symbol,
                action=action,
                quantity=quantity,
                price=price,
                price_type=price_type,
                order_type=order_type,
                octype=octype,
                status="Error",
                order_datetime=datetime.now(),
                msg=f"下單失敗: {str(e)}",
                trade=None,
            )

    def update_status(self, trade=None) -> bool:
        """
        更新委託單狀態

        Args:
            trade: 特定交易物件，如果為None則更新所有交易

        Returns:
            更新是否成功
        """
        try:
            if trade:
                # 更新特定交易狀態
                self.api_client.update_status(trade=trade)
            else:
                # 更新期貨帳戶所有交易狀態
                self.api_client.update_status(account=self.api_client.futopt_account)
            return True
        except Exception as e:
            print(f"更新委託單狀態失敗: {str(e)}")
            return False

    def list_trades(self) -> list[FuturesTrade]:
        """
        取得所有期貨委託單

        Returns:
            期貨委託單列表
        """
        try:
            # 先更新狀態
            self.update_status()

            # 取得所有交易
            trades = self.api_client.list_trades()

            futures_trades = []
            for trade in trades:
                # 只處理期貨交易
                if hasattr(trade.contract, "code") and trade.contract.code.startswith(
                    ("TXF", "EXF", "MXF")
                ):
                    # 轉換成交資訊
                    deals = []
                    if trade.status.deals:
                        for deal in trade.status.deals:
                            deals.append(
                                Deal(
                                    id=str(deal.seq),
                                    code=trade.contract.code,
                                    direction=Action.Buy
                                    if trade.order.action.value == "Buy"
                                    else Action.Sell,
                                    quantity=deal.quantity,
                                    price=deal.price,
                                    time=datetime.fromtimestamp(deal.ts),
                                )
                            )

                    # 建立委託狀態
                    order_status = OrderStatus(
                        id=trade.status.id,
                        status=str(trade.status.status),
                        status_code=trade.status.status_code,
                        order_datetime=trade.status.order_datetime,
                        order_quantity=trade.status.order_quantity,
                        modified_price=getattr(trade.status, "modified_price", None),
                        cancel_quantity=getattr(trade.status, "cancel_quantity", 0),
                        deals=deals,
                    )

                    # 建立期貨交易物件
                    futures_trade = FuturesTrade(
                        order_id=trade.order.id,
                        symbol=trade.contract.code[:3],  # 取前3位作為商品代碼
                        sub_symbol=trade.contract.code,  # 使用完整的合約代碼
                        action=Action.Buy
                        if trade.order.action.value == "Buy"
                        else Action.Sell,
                        quantity=trade.order.quantity,
                        price=trade.order.price,
                        price_type=str(trade.order.price_type),
                        order_type=str(trade.order.order_type),
                        octype=str(trade.order.octype),
                        status=order_status,
                        trade=trade,
                    )

                    futures_trades.append(futures_trade)

            return futures_trades

        except Exception as e:
            print(f"取得委託單列表失敗: {str(e)}")
            return []

    def get_trade_by_id(self, order_id: str) -> FuturesTrade | None:
        """
        根據委託單ID取得特定交易

        Args:
            order_id: 委託單ID

        Returns:
            期貨交易物件，如果找不到則返回None
        """
        try:
            trades = self.list_trades()
            for trade in trades:
                if trade.order_id == order_id:
                    return trade
            return None
        except Exception as e:
            print(f"取得特定交易失敗: {str(e)}")
            return None

    def check_order_status(
        self, order_id: str = None, symbol: str = None, sub_symbol: str = None
    ) -> list[FuturesTrade]:
        """
        檢查委託單狀態

        Args:
            order_id: 委託單ID (可選)
            symbol: 商品代碼 (可選)
            sub_symbol: 子商品代碼 (可選)

        Returns:
            符合條件的委託單列表
        """
        try:
            trades = self.list_trades()

            # 如果指定了order_id，直接返回該交易
            if order_id:
                trade = self.get_trade_by_id(order_id)
                return [trade] if trade else []

            # 根據symbol和sub_symbol篩選
            filtered_trades = []
            for trade in trades:
                if symbol and trade.symbol != symbol:
                    continue
                if sub_symbol and trade.sub_symbol != sub_symbol:
                    continue
                filtered_trades.append(trade)

            return filtered_trades

        except Exception as e:
            print(f"檢查委託單狀態失敗: {str(e)}")
            return []


if __name__ == "__main__":
    from auto_trade.core.client import create_api_client
    from auto_trade.core.config import Config

    config = Config()
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        simulation=False,
    )
    order_service = OrderService(api_client=api_client)
    trades = order_service.check_order_status(
        symbol="MXF",
        sub_symbol="MXF202512",
    )

    # trades = order_service.list_trades()
    print(trades)
