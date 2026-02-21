"""Trading strategies package."""

from .base_strategy import BaseStrategy
from .bollinger_strategy import BollingerStrategy
from .macd_bidirectional import MACDBidirectionalStrategy
from .macd_golden_cross import MACDGoldenCrossStrategy
from .orb_strategy import ORBStrategy
from .scalp_strategy import ScalpStrategy

__all__ = [
    "BaseStrategy",
    "BollingerStrategy",
    "MACDBidirectionalStrategy",
    "MACDGoldenCrossStrategy",
    "ORBStrategy",
    "ScalpStrategy",
    "STRATEGY_TYPE_MAP",
    "create_strategy",
]

# 策略類型註冊表：strategy_type (YAML) → Strategy class
STRATEGY_TYPE_MAP: dict[str, type[BaseStrategy]] = {
    "macd_golden_cross": MACDGoldenCrossStrategy,
    "macd_bidirectional": MACDBidirectionalStrategy,
    "orb": ORBStrategy,
    "scalp": ScalpStrategy,
    "bollinger": BollingerStrategy,
}


def create_strategy(
    strategy_type: str,
    indicator_service,
    **kwargs,
) -> BaseStrategy:
    """根據 strategy_type 建立策略實例

    Args:
        strategy_type: YAML 中的策略類型字串
        indicator_service: 指標服務
        **kwargs: 傳遞給策略的額外參數（如 volume_percentile_threshold）

    Returns:
        BaseStrategy: 策略實例

    Raises:
        ValueError: 找不到對應的策略類型
    """
    strategy_cls = STRATEGY_TYPE_MAP.get(strategy_type)
    if strategy_cls is None:
        available = list(STRATEGY_TYPE_MAP.keys())
        raise ValueError(f"未知的策略類型: '{strategy_type}'，可用類型: {available}")
    return strategy_cls(indicator_service, **kwargs)
