"""Position and PositionLeg models for the PositionManager.

Position 代表一個完整的交易部位，可以包含多個 PositionLeg。
每個 PositionLeg 有自己獨立的出場規則（停損、停利、移動停損）。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from auto_trade.models.account import Action
from auto_trade.models.position_record import ExitReason


class PositionStatus(Enum):
    """倉位狀態"""

    OPEN = "OPEN"  # 開倉中
    CLOSED = "CLOSED"  # 已平倉
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"  # 部分平倉


class LegType(Enum):
    """Leg 類型 - 每個 Leg 使用不同的出場策略"""

    TAKE_PROFIT = "TP"  # TP Leg：到達停利價時平倉
    TRAILING_STOP = "TS"  # TS Leg：使用移動停損保護獲利


@dataclass
class ExitRule:
    """出場規則 - 定義單一 Leg 的出場條件

    每個 PositionLeg 都有自己的 ExitRule，
    可以獨立設定停損、停利和移動停損。
    """

    # 停損設定
    stop_loss_price: int | None = None  # 固定停損價格

    # 停利設定（僅 TP Leg 使用）
    take_profit_price: int | None = None  # 固定停利價格

    # 移動停損設定
    trailing_stop_active: bool = False  # 是否啟動移動停損
    start_trailing_stop_price: int | None = None  # 啟動移動停損的價格
    trailing_stop_price: int | None = None  # 當前移動停損價格（動態更新）

    # 收緊移停（Staged Trailing Stop）
    # 當獲利達到 tighten_after_price 後，移停距離縮小為 tightened_trailing_stop_points
    tighten_after_price: int | None = None  # 觸發收緊的價格門檻
    tightened_trailing_stop_points: int | None = None  # 收緊後的移停距離
    is_tightened: bool = False  # 是否已進入收緊模式


@dataclass
class PositionLeg:
    """倉位 Leg - 代表部位的一個子部分

    一個 Position 可以有多個 Leg，每個 Leg 有不同的：
    - 數量
    - 出場類型（TP 或 TS）
    - 出場規則
    """

    leg_id: str  # 唯一識別碼
    leg_type: LegType  # Leg 類型
    quantity: int  # 該 Leg 的數量
    exit_rule: ExitRule  # 出場規則
    status: PositionStatus = PositionStatus.OPEN

    # 出場資訊
    exit_price: int | None = None
    exit_time: datetime | None = None
    exit_reason: ExitReason | None = None


@dataclass
class OrderAction:
    """下單動作 - PositionManager 產出給 Executor 的指令"""

    action: Action  # Buy / Sell
    symbol: str
    sub_symbol: str
    quantity: int
    order_type: str  # "Open" / "Close"
    reason: str = ""  # 下單原因描述
    leg_id: str | None = None  # 對應的 Leg ID（平倉時使用）
    metadata: dict = field(default_factory=dict)


@dataclass
class ManagedPosition:
    """受管理的倉位 - PositionManager 管理的核心資料結構

    代表一個完整的交易部位，包含：
    - 進場資訊
    - 多個 Leg（各自有獨立的出場規則）
    - 最高/最低價追蹤（用於移動停損）
    """

    position_id: str  # 唯一識別碼
    symbol: str
    sub_symbol: str
    direction: Action  # 交易方向 (Buy/Sell)
    total_quantity: int  # 總數量
    entry_price: int  # 進場價格
    entry_time: datetime  # 進場時間
    status: PositionStatus = PositionStatus.OPEN

    # Legs
    legs: list[PositionLeg] = field(default_factory=list)

    # 價格追蹤
    highest_price: int = 0  # 進場後最高價
    lowest_price: int = 999999  # 進場後最低價

    # MACD 快速停損狀態（方向無關：做多時追蹤死叉，做空時追蹤金叉）
    is_in_macd_adverse_cross: bool = False

    # 買回標記
    is_buy_back: bool = False

    # 策略傳入的額外資訊（如 ORB 的 override 參數）
    metadata: dict = field(default_factory=dict)

    # 附加資訊
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def open_quantity(self) -> int:
        """當前仍開倉的數量"""
        return sum(
            leg.quantity for leg in self.legs if leg.status == PositionStatus.OPEN
        )

    @property
    def open_legs(self) -> list[PositionLeg]:
        """取得所有仍開倉的 Legs"""
        return [leg for leg in self.legs if leg.status == PositionStatus.OPEN]

    @property
    def closed_legs(self) -> list[PositionLeg]:
        """取得所有已平倉的 Legs"""
        return [leg for leg in self.legs if leg.status == PositionStatus.CLOSED]

    def update_price_tracking(self, current_price: int) -> None:
        """更新最高/最低價追蹤"""
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price:
            self.lowest_price = current_price

    def close_leg(
        self,
        leg_id: str,
        exit_price: int,
        exit_time: datetime,
        exit_reason: ExitReason,
    ) -> None:
        """關閉指定的 Leg"""
        for leg in self.legs:
            if leg.leg_id == leg_id and leg.status == PositionStatus.OPEN:
                leg.status = PositionStatus.CLOSED
                leg.exit_price = exit_price
                leg.exit_time = exit_time
                leg.exit_reason = exit_reason
                break

        # 更新 Position 狀態
        if not self.open_legs:
            self.status = PositionStatus.CLOSED
        elif len(self.open_legs) < len(self.legs):
            self.status = PositionStatus.PARTIALLY_CLOSED
