"""Account-related data models."""

from dataclasses import dataclass
from enum import Enum


class Action(Enum):
    """交易方向"""

    Buy = "Buy"
    Sell = "Sell"
    Hold = "Hold"


@dataclass
class Balance:
    """帳戶餘額資料模型"""

    available_balance: float
    buying_power: float
    account_value: float


@dataclass
class Position:
    """持倉資料模型"""

    symbol: str
    quantity: int
    avg_price: float
    market_value: float
    unrealized_pnl: float


@dataclass
class FuturePosition:
    """期貨持倉資料模型"""

    id: int  # 部位代碼
    code: str  # 商品代碼
    direction: Action  # 交易方向 {Buy: 買, Sell: 賣}
    quantity: int  # 數量
    price: float  # 平均價格
    last_price: float  # 目前價格
    pnl: float  # 損益
    sub_symbol: str = ""  # 子商品代碼 (e.g., MXF202511)，手動設定


@dataclass
class Margin:
    """期貨保證金資料模型"""

    yesterday_balance: float  # 前日餘額
    today_balance: float  # 今日餘額
    deposit_withdrawal: float  # 存提
    fee: float  # 手續費
    tax: float  # 期交稅
    initial_margin: float  # 原始保證金
    maintenance_margin: float  # 維持保證金
    margin_call: float  # 追繳保證金
    risk_indicator: float  # 風險指標
    royalty_revenue_expenditure: float  # 權利金收入與支出
    equity: float  # 權益數
    equity_amount: float  # 權益總值
    option_openbuy_market_value: float  # 未沖銷買方選擇權市值
    option_opensell_market_value: float  # 未沖銷賣方選擇權市值
    option_open_position: float  # 參考未平倉選擇權損益
    option_settle_profitloss: float  # 參考選擇權平倉損益
    future_open_position: float  # 未沖銷期貨浮動損益
    today_future_open_position: float  # 參考當日未沖銷期貨浮動損益
    future_settle_profitloss: float  # 期貨平倉損益
    available_margin: float  # 可動用(出金)保證金
    plus_margin: float  # 依「加收保證金指標」所加收之保證金
    plus_margin_indicator: float  # 加收保證金指標
    security_collateral_amount: float  # 有價證券抵繳總額
    order_margin_premium: float  # 委託保證金及委託權利金
    collateral_amount: float  # 有價品額
