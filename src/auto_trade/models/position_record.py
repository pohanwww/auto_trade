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
    TIME_EXIT = "TE"  # 時間強制平倉（日內策略用）
    MOMENTUM_EXIT = "ME"  # 動能衰竭停利


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
    take_profit_price: int | None = None  # 獲利了結價格
    trailing_stop_active: bool = False  # 移動停損是否啟動
    highest_price: int | None = None  # 進場後最高價（用於重啟恢復移停）
    sheets_row_map: dict | None = None  # leg_id → Google Sheets 行號
    legs_info: dict | None = None  # leg_id → {entry_price, quantity}
    position_metadata: dict | None = None  # key_levels, trail_mode 等策略 metadata

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
            "take_profit_price": self.take_profit_price,
            "trailing_stop_active": self.trailing_stop_active,
            "highest_price": self.highest_price,
            "sheets_row_map": self.sheets_row_map,
            "legs_info": self.legs_info,
            "position_metadata": self.position_metadata,
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
            take_profit_price=(
                int(data["take_profit_price"])
                if data.get("take_profit_price")
                else None
            ),  # 向後兼容
            trailing_stop_active=data.get("trailing_stop_active", False),
            highest_price=(
                int(data["highest_price"]) if data.get("highest_price") else None
            ),
            sheets_row_map=data.get("sheets_row_map"),
            legs_info=data.get("legs_info"),
            position_metadata=data.get("position_metadata"),
        )
