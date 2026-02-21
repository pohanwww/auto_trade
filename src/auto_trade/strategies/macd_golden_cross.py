"""MACD Golden Cross Strategy.

當 MACD 線從下方穿越信號線（金叉）且 MACD 均值 < 35 時，產生做多信號。
"""

from datetime import datetime

from auto_trade.models.market import KBarList
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.strategies.base_strategy import BaseStrategy


class MACDGoldenCrossStrategy(BaseStrategy):
    """MACD 金叉策略

    進場條件：
    1. 有足夠的 K 線資料（至少 30 根）
    2. MACD 線和信號線的平均值 < macd_threshold
    3. 發生 MACD 金叉
    4. （可選）成交量百分位排名 >= volume_percentile_threshold

    此策略只負責產生進場信號，停損/停利由 PositionManager 管理。
    """

    def __init__(
        self,
        indicator_service: IndicatorService,
        macd_threshold: float = 35.0,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        volume_percentile_threshold: float = 0.0,
        volume_percentile_lookback: int = 100,
        **kwargs,  # 忽略其他策略（如 ORB）的專用參數
    ):
        super().__init__(indicator_service, name="MACD Golden Cross Strategy")
        self.macd_threshold = macd_threshold
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        # 量能過濾：0 表示不過濾，0.6 表示成交量須 >= 60th percentile
        self.volume_percentile_threshold = volume_percentile_threshold
        self.volume_percentile_lookback = volume_percentile_lookback

    def _check_volume_filter(self, kbar_list: KBarList) -> tuple[bool, float | None]:
        """檢查成交量是否通過過濾條件

        Returns:
            (passed, percentile): 是否通過, 百分位值
        """
        if self.volume_percentile_threshold <= 0:
            return True, None  # 未啟用過濾

        percentile = self.indicator_service.volume_percentile(
            kbar_list, self.volume_percentile_lookback
        )
        if percentile is None:
            return True, None  # 資料不足，不過濾

        return percentile >= self.volume_percentile_threshold, percentile

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
    ) -> StrategySignal:
        """評估 MACD 金叉條件

        Args:
            kbar_list: K線資料列表
            current_price: 當前即時價格
            symbol: 商品代碼

        Returns:
            StrategySignal: ENTRY_LONG 或 HOLD
        """
        now = datetime.now()

        # 資料不足
        if len(kbar_list) < 30:
            return StrategySignal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                price=current_price,
                reason="Insufficient data for MACD calculation",
                timestamp=now,
            )

        # 計算 MACD
        macd_list = self.indicator_service.calculate_macd(
            kbar_list, self.fast_period, self.slow_period, self.signal_period
        )

        # 取得當前 MACD 值
        latest_macd = macd_list.get_latest(1)
        current_macd = latest_macd[-1] if latest_macd else None

        # 日誌輸出
        if current_macd:
            print(f"latest_macd: {current_macd.macd_line:.1f}")
            print(f"latest_signal: {current_macd.signal_line:.1f}")

        # 檢查金叉
        is_golden_cross = self.indicator_service.check_golden_cross(macd_list)

        # MACD 金叉策略：MACD 均值 < threshold 且金叉時產生做多信號
        if (
            current_macd
            and (current_macd.macd_line + current_macd.signal_line) / 2
            < self.macd_threshold
            and is_golden_cross
        ):
            # 量能過濾
            vol_passed, vol_pct = self._check_volume_filter(kbar_list)
            if not vol_passed:
                vol_pct_str = f"{vol_pct:.0%}" if vol_pct is not None else "N/A"
                return StrategySignal(
                    signal_type=SignalType.HOLD,
                    symbol=symbol,
                    price=current_price,
                    reason=(
                        f"MACD Golden Cross but volume too low: "
                        f"percentile={vol_pct_str} < {self.volume_percentile_threshold:.0%}"
                    ),
                    timestamp=now,
                )

            vol_info = f", vol_pct={vol_pct:.0%}" if vol_pct is not None else ""
            return StrategySignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=symbol,
                price=current_price,
                confidence=0.8,
                reason=(
                    f"MACD Golden Cross: MACD({current_macd.macd_line:.2f}) > "
                    f"Signal({current_macd.signal_line:.2f}){vol_info}"
                ),
                timestamp=now,
                metadata={
                    "macd_line": current_macd.macd_line,
                    "signal_line": current_macd.signal_line,
                    "histogram": current_macd.histogram,
                    "volume_percentile": vol_pct,
                },
            )

        # 無信號
        if current_macd:
            reason = f"No signal: MACD({current_macd.macd_line:.2f}), Signal({current_macd.signal_line:.2f})"
        else:
            reason = "Insufficient MACD data"

        return StrategySignal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            price=current_price,
            reason=reason,
            timestamp=now,
        )
