"""Base strategy abstract interface.

所有策略都必須繼承此抽象類別，實現 evaluate 方法。
策略只負責「看盤勢、給信號」，不處理倉位管理或下單。
"""

from abc import ABC, abstractmethod

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
    ) -> StrategySignal:
        """評估當前市場狀況並產生信號

        Args:
            kbar_list: K線資料列表（包含歷史數據）
            current_price: 當前即時價格
            symbol: 商品代碼

        Returns:
            StrategySignal: 策略信號（純方向信號，不含倉位管理細節）
        """
        ...

    def on_position_closed(self) -> None:  # noqa: B027
        """平倉後回呼（可選覆寫）

        供需要在平倉後重設內部狀態的策略使用（如冷卻計數器）。
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
