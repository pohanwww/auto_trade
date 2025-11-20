"""持倉記錄模型"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from auto_trade.models.account import Action


class ExitReason(Enum):
    """出場原因"""

    TRAILING_STOP = "TS"  # 移動停損
    TAKE_PROFIT = "TP"  # 獲利了結
    STOP_LOSS = "SL"  # 停損
    HOLD = "Hold"  # 持倉中
    FAST_STOP = "FS"  # 快速停損


@dataclass
class PositionRecord:
    """持倉記錄"""

    symbol: str  # 商品代碼 (e.g., "MXF")
    sub_symbol: str  # 子商品代碼 (e.g., "MXF202511")
    direction: Action  # 方向 (Buy/Sell)
    entry_time: datetime  # 進場時間
    timeframe: str  # 時間尺度 (e.g., "30m", "5m")
    quantity: int  # 數量
    entry_price: int  # 進場價格
    stop_loss_price: int | None  # 停損價格
    start_trailing_stop_price: int | None = None  # 啟動移動停損的價格
    trailing_stop_active: bool = False  # 移動停損是否啟動
    sheets_row_number: int | None = None  # Google Sheets 中的行號
    is_buy_back: bool = False  # 是否為買回單

    def to_dict(self) -> dict:
        """轉換為字典"""
        return {
            "symbol": self.symbol,
            "sub_symbol": self.sub_symbol,
            "direction": self.direction.value,
            "entry_time": self.entry_time.isoformat(),
            "timeframe": self.timeframe,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "stop_loss_price": self.stop_loss_price,
            "start_trailing_stop_price": self.start_trailing_stop_price,
            "trailing_stop_active": self.trailing_stop_active,
            "sheets_row_number": self.sheets_row_number,
            "is_buy_back": self.is_buy_back,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PositionRecord":
        """從字典創建"""
        return cls(
            symbol=data["symbol"],
            sub_symbol=data["sub_symbol"],
            direction=Action(data["direction"]),
            entry_time=datetime.fromisoformat(data["entry_time"]),
            timeframe=data.get("timeframe", "30m"),  # 向後兼容，默認 30m
            quantity=data["quantity"],
            entry_price=int(data["entry_price"]),
            stop_loss_price=(
                int(data["stop_loss_price"]) if data["stop_loss_price"] else None
            ),
            start_trailing_stop_price=(
                int(data["start_trailing_stop_price"])
                if data.get("start_trailing_stop_price")
                else None
            ),  # 向後兼容
            trailing_stop_active=data.get("trailing_stop_active", False),
            sheets_row_number=data.get("sheets_row_number"),  # 向後兼容
            is_buy_back=data.get("is_buy_back", False),  # 向後兼容
        )


@dataclass
class BuybackState:
    """買回機制狀態"""

    symbol: str
    sub_symbol: str
    direction: Action
    check_time: datetime  # 預計檢查時間 (K棒結束前)
    monitoring_bar_time: datetime  # 監控的那根 K 棒開始時間
    exit_price: int  # 出場價格 (參考用)
    highest_price: int  # 出場前的最高價 (用於買回後設定啟動移停價)
    quantity: int = 1

    def to_dict(self) -> dict:
        """轉換為字典"""
        return {
            "symbol": self.symbol,
            "sub_symbol": self.sub_symbol,
            "direction": self.direction.value,
            "check_time": self.check_time.isoformat(),
            "monitoring_bar_time": self.monitoring_bar_time.isoformat(),
            "exit_price": self.exit_price,
            "highest_price": self.highest_price,
            "quantity": self.quantity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BuybackState":
        """從字典創建"""
        return cls(
            symbol=data["symbol"],
            sub_symbol=data["sub_symbol"],
            direction=Action(data["direction"]),
            check_time=datetime.fromisoformat(data["check_time"]),
            monitoring_bar_time=datetime.fromisoformat(data["monitoring_bar_time"]),
            exit_price=int(data["exit_price"]),
            highest_price=int(data.get("highest_price", 0)),  # 向後兼容
            quantity=data.get("quantity", 1),
        )
