"""TradingUnit model - 組合策略與倉位管理的配置物件.

TradingUnit 是一個純粹的配置/組合物件，不執行任何邏輯。
它定義了「使用哪個策略 + 使用什麼倉位管理配置」的組合。

範例用法：
    unit1 = TradingUnit(
        name="MACD 穩健",
        strategy=MACDGoldenCrossStrategy(indicator_service),
        pm_config=PositionManagerConfig(total_quantity=2, tp_leg_quantity=1, ts_leg_quantity=1),
    )
    unit2 = TradingUnit(
        name="MACD 激進",
        strategy=MACDGoldenCrossStrategy(indicator_service, macd_threshold=50),
        pm_config=PositionManagerConfig(total_quantity=5, tp_leg_quantity=2, ts_leg_quantity=3),
    )

    # 回測時可以測試不同組合
    engine.run([unit1, unit2])
"""

from dataclasses import dataclass

from auto_trade.services.position_manager import PositionManagerConfig
from auto_trade.strategies.base_strategy import BaseStrategy


@dataclass
class TradingUnit:
    """交易單元 - 組合一個策略和一套倉位管理配置

    這是一個純粹的配置物件，用於：
    1. 定義 TradingEngine / BacktestEngine 需要運行的組合
    2. 允許同一策略搭配不同倉位管理參數進行回測
    3. 允許多策略同時運行

    Attributes:
        name: 交易單元名稱（用於報告和日誌）
        strategy: 策略實例（繼承自 BaseStrategy）
        pm_config: 倉位管理器配置
        enabled: 是否啟用
    """

    name: str
    strategy: BaseStrategy
    pm_config: PositionManagerConfig
    enabled: bool = True

    def __repr__(self) -> str:
        return (
            f"TradingUnit(\n"
            f"  name='{self.name}',\n"
            f"  strategy={self.strategy},\n"
            f"  pm_config={self.pm_config},\n"
            f"  enabled={self.enabled}\n"
            f")"
        )
