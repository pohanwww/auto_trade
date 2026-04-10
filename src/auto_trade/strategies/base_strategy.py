"""Base strategy abstract interface.

所有策略都必須繼承此抽象類別，實現 evaluate 方法。
策略只負責「看盤勢、給信號」，不處理倉位管理或下單。
"""

from abc import ABC, abstractmethod
from datetime import datetime

from auto_trade.models.market import KBarList
from auto_trade.models.strategy import StrategySignal
from auto_trade.services.indicator_service import IndicatorService


class BaseStrategy(ABC):
    """策略抽象基類

    職責：
    - 接收市場資料（K線）
    - 使用 IndicatorService 計算技術指標
    - 產生純信號（StrategySignal）

    不負責：
    - 倉位管理（PositionManager 的責任）
    - 下單執行（Executor 的責任）
    - 風險控制（RiskManager 的責任）
    """

    def __init__(self, indicator_service: IndicatorService, name: str = ""):
        self.indicator_service = indicator_service
        self.name = name or self.__class__.__name__

    @abstractmethod
    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
        bar_close: bool = True,
    ) -> StrategySignal:
        """評估當前市場狀況並產生信號

        Args:
            kbar_list: K線資料列表（包含歷史數據）
            current_price: 當前即時價格
            symbol: 商品代碼
            bar_close: True = 定時 bar 結束檢查; False = instant 觸發時 mid-bar 呼叫

        Returns:
            StrategySignal: 策略信號（純方向信號，不含倉位管理細節）
        """
        ...

    def get_pending_state(self) -> dict | None:  # noqa: B027
        """回傳策略目前等待中的關鍵狀態（供 dashboard 顯示）

        子類可覆寫此方法，回傳如待觸發價位等資訊。
        預設回傳 None（無額外狀態）。
        """
        return None

    def get_instant_targets(self) -> tuple[float | None, float | None]:  # noqa: B027
        """回傳 (long_trigger_price, short_trigger_price) 用於 instant 監控。

        Engine 只需比對 tick price 是否超過這兩個值。
        """
        return None, None

    def get_instant_trigger_prices(self) -> list[tuple[float, str]]:  # noqa: B027
        """Legacy: 回傳 instant breakout 的觸發價位列表。"""
        return []

    def on_position_closed(self, exit_price: int | None = None, bar_time: datetime | None = None) -> None:  # noqa: B027
        """平倉後回呼（可選覆寫）

        供需要在平倉後重設內部狀態的策略使用（如冷卻計數器）。
        exit_price: 平倉成交價，用於更新 prev_close 等狀態。
        bar_time: 回測用 — 模擬的 K 棒時間，None 表示使用 datetime.now()。
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
