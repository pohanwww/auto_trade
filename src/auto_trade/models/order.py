"""Futures order-related data models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from auto_trade.models.account import Action


@dataclass
class FuturesOrderRequest:
    """期貨下單請求模型"""

    symbol: str  # 商品代碼 (如: 'TXF')
    sub_symbol: str  # 子商品代碼 (如: 'TXF202301')
    action: Action  # Buy or Sell
    quantity: int  # 委託數量
    price: float | None = None  # 委託價格 (限價單必填，市價單可為None)
    price_type: str = "LMT"  # 'LMT': 限價, 'MKT': 市價, 'MKP': 範圍市價
    order_type: str = (
        "ROD"  # 'ROD': 當日有效, 'IOC': 立即成交否則取消, 'FOK': 全部成交否則取消
    )
    octype: str = "Auto"  # 'Auto': 自動, 'New': 新倉, 'Cover': 平倉, 'DayTrade': 當沖


@dataclass
class FuturesOrderResult:
    """期貨下單結果模型"""

    order_id: str  # 委託單ID
    symbol: str  # 商品代碼
    sub_symbol: str  # 子商品代碼
    action: Action  # 買賣別
    quantity: int  # 委託數量
    price: float | None  # 委託價格
    price_type: str  # 價格類型
    order_type: str  # 委託類別
    octype: str  # 委託類型
    status: str  # 委託狀態 (成功時為 API 返回的狀態，失敗時為 "Error")
    order_datetime: datetime  # 下單時間
    msg: str  # 訊息
    trade: Any | None = None  # Shioaji Trade 物件


@dataclass
class Deal:
    """成交資訊模型"""

    id: str  # 成交ID
    code: str  # 商品代碼
    direction: Action  # 買賣別
    quantity: int  # 成交數量
    price: float  # 成交價
    time: datetime  # 成交時間


@dataclass
class OrderStatus:
    """委託狀態模型"""

    id: str  # 關聯Order物件編碼
    status: str  # 委託狀態
    status_code: str  # 狀態碼
    order_datetime: datetime  # 委託時間
    order_quantity: int  # 委託數量
    modified_price: float | None = None  # 改價金額
    cancel_quantity: int = 0  # 取消委託數量
    deals: list[Deal] = None  # 成交資訊

    def __post_init__(self):
        if self.deals is None:
            self.deals = []


@dataclass
class FuturesTrade:
    """期貨交易模型"""

    order_id: str  # 委託單ID
    symbol: str  # 商品代碼
    sub_symbol: str  # 子商品代碼
    action: Action  # 買賣別
    quantity: int  # 委託數量
    price: float | None  # 委託價格
    price_type: str  # 價格類型
    order_type: str  # 委託類別
    octype: str  # 委託類型
    status: OrderStatus  # 委託狀態
    trade: Any | None = None  # Shioaji Trade 物件
