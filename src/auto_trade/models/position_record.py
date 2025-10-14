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


@dataclass
class PositionRecord:
    """持倉記錄"""

    symbol: str  # 商品代碼 (e.g., "MXF")
    sub_symbol: str  # 子商品代碼 (e.g., "MXF202511")
    direction: Action  # 方向 (Buy/Sell)
    quantity: int  # 數量
    entry_price: float  # 進場價格
    entry_time: datetime  # 進場時間
    stop_loss_price: float | None  # 停損價格
    timeframe: str = "30m"  # 時間尺度 (e.g., "30m", "5m")
    trailing_stop_active: bool = False  # 移動停損是否啟動
    sheets_row_number: int | None = None  # Google Sheets 中的行號

    def to_dict(self) -> dict:
        """轉換為字典"""
        return {
            "symbol": self.symbol,
            "sub_symbol": self.sub_symbol,
            "direction": self.direction.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "stop_loss_price": self.stop_loss_price,
            "timeframe": self.timeframe,
            "trailing_stop_active": self.trailing_stop_active,
            "sheets_row_number": self.sheets_row_number,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PositionRecord":
        """從字典創建"""
        return cls(
            symbol=data["symbol"],
            sub_symbol=data["sub_symbol"],
            direction=Action(data["direction"]),
            quantity=data["quantity"],
            entry_price=data["entry_price"],
            entry_time=datetime.fromisoformat(data["entry_time"]),
            stop_loss_price=data["stop_loss_price"],
            timeframe=data.get("timeframe", "30m"),  # 向後兼容，默認 30m
            trailing_stop_active=data.get("trailing_stop_active", False),
            sheets_row_number=data.get("sheets_row_number"),  # 向後兼容
        )
