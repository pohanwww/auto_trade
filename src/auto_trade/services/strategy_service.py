"""Strategy service for trading strategy calculations."""

from datetime import datetime

import pandas as pd

from auto_trade.models import (
    Action,
    EMAData,
    EMAList,
    KBar,
    KBarList,
    MACDData,
    MACDList,
    StrategyInput,
    TradingSignal,
)


class StrategyService:
    """交易策略服務類"""

    def __init__(self):
        self.name = "MACD Golden Cross Strategy"

    def calculate_ema(self, kbar_list: KBarList, period: int) -> EMAList:
        """計算指數移動平均線 (EMA)"""
        prices = pd.Series([kbar.close for kbar in kbar_list])
        ema_values = prices.ewm(span=period).mean()

        # 創建EMA EMAList
        ema_data = []
        for i, kbar in enumerate(kbar_list):
            ema_data.append(
                EMAData(
                    time=kbar.time,
                    ema_value=float(ema_values.iloc[i])
                    if not pd.isna(ema_values.iloc[i])
                    else 0.0,
                )
            )

        return EMAList(
            ema_data=ema_data,
            symbol=kbar_list.symbol,
            timeframe=kbar_list.timeframe,
            period=period,
        )

    def calculate_macd(
        self,
        kbar_list: KBarList,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> MACDList:
        """計算MACD指標"""
        # 計算快線和慢線EMA
        ema_fast = self.calculate_ema(kbar_list, fast_period)
        ema_slow = self.calculate_ema(kbar_list, slow_period)

        # 計算MACD線
        macd_line_values = []
        for i in range(len(kbar_list)):
            macd_value = ema_fast[i].ema_value - ema_slow[i].ema_value
            macd_line_values.append(macd_value)

        # 計算信號線 (MACD線的EMA)
        macd_series = pd.Series(macd_line_values)
        signal_line_values = macd_series.ewm(span=signal_period).mean()

        # 計算柱狀圖
        histogram_values = macd_series - signal_line_values

        # 創建MACD MACDList
        macd_data = []
        for i, kbar in enumerate(kbar_list):
            macd_data.append(
                MACDData(
                    time=kbar.time,
                    macd_line=float(macd_line_values[i])
                    if not pd.isna(macd_line_values[i])
                    else 0.0,
                    signal_line=float(signal_line_values.iloc[i])
                    if not pd.isna(signal_line_values.iloc[i])
                    else 0.0,
                    histogram=float(histogram_values.iloc[i])
                    if not pd.isna(histogram_values.iloc[i])
                    else 0.0,
                )
            )

        return MACDList(
            macd_data=macd_data,
            symbol=kbar_list.symbol,
            timeframe=kbar_list.timeframe,
        )

    def check_golden_cross(
        self, macd_list: MACDList, min_strength: float | None = None
    ) -> bool:
        """檢查是否發生 MACD 金叉（已確認）

        金叉定義：MACD 線從下方穿越信號線到上方
        使用 [-2] 和 [-3] 確保檢查的是已確認的K線，而非正在形成的K線

        Args:
            macd_list: MACD 數據列表
            min_strength: 最小金叉強度要求（可選）。強度定義為 abs(MACD - Signal)

        Returns:
            bool: True 如果發生金叉且符合強度要求，False 否則
        """
        if len(macd_list.macd_data) < 3:
            return False

        latest_macd = macd_list.get_latest(3)
        if len(latest_macd) < 3:
            return False

        current = latest_macd[-2]  # 已確認的最新K線
        previous = latest_macd[-3]  # 已確認的前一根K線

        # 金叉：前一根 MACD <= Signal，當前 MACD > Signal
        is_golden_cross = (
            previous.macd_line <= previous.signal_line
            and current.macd_line > current.signal_line
        )

        # 如果沒有發生金叉，直接返回 False
        if not is_golden_cross:
            return False

        # 如果沒有設置強度要求，直接返回 True
        if min_strength is None:
            return True

        # 檢查金叉強度
        strength = abs(current.macd_line - current.signal_line)
        return strength >= min_strength

    def check_death_cross(
        self, macd_list: MACDList, min_acceleration: float | None = None
    ) -> bool:
        """檢查是否發生 MACD 死叉（已確認）

        死叉定義：MACD 線從上方穿越信號線到下方
        使用 [-2] 和 [-3] 確保檢查的是已確認的K線，而非正在形成的K線

        Args:
            macd_list: MACD 數據列表
            min_acceleration: 最小死叉加速度要求（可選）。
                             加速度定義為：當前差距 - 前一根差距
                             其中差距 = MACD - Signal

        Returns:
            bool: True 如果發生死叉且符合加速度要求，False 否則
        """
        if len(macd_list.macd_data) < 3:
            return False

        latest_macd = macd_list.get_latest(3)
        if len(latest_macd) < 3:
            return False

        current = latest_macd[-2]  # 已確認的最新K線
        previous = latest_macd[-3]  # 已確認的前一根K線

        # 死叉：前一根 MACD >= Signal，當前 MACD < Signal
        is_death_cross = (
            previous.macd_line >= previous.signal_line
            and current.macd_line < current.signal_line
        )

        # 如果沒有發生死叉，直接返回 False
        if not is_death_cross:
            return False

        # 如果沒有設置加速度要求，直接返回 True
        if min_acceleration is None:
            return True

        # 計算加速度（趨勢變化率）
        previous_diff = previous.macd_line - previous.signal_line
        current_diff = current.macd_line - current.signal_line
        acceleration = current_diff - previous_diff

        return abs(acceleration) >= min_acceleration

    def check_hammer_kbar(self, kbar: KBar, direction: Action) -> bool:
        """檢查 K 棒型態是否為錘頭 (做多) 或 倒錘頭 (做空)

        Args:
            kbar: K 棒數據
            direction: 交易方向 (Buy: 檢查錘頭/長下影線, Sell: 檢查倒錘頭/長上影線)

        Returns:
            bool: True 如果符合型態，False 否則
        """
        body_length = abs(kbar.open - kbar.close)

        if direction == Action.Buy:
            # 多單買回條件：長下影線 (錘頭)
            lower_shadow = min(kbar.open, kbar.close) - kbar.low
            if lower_shadow >= body_length * 2:
                return True

        elif direction == Action.Sell:
            # 空單買回條件：長上影線 (倒錘頭/射擊之星)
            upper_shadow = kbar.high - max(kbar.open, kbar.close)
            if upper_shadow >= body_length * 2:
                return True

        return False

    def generate_signal(self, input_data: StrategyInput) -> TradingSignal:
        """生成MACD金叉策略訊號並計算停損價格"""
        if len(input_data.kbars) < 30:
            return TradingSignal(
                action=Action.Hold,
                symbol=input_data.symbol,
                price=input_data.current_price,
                reason="Insufficient data for MACD calculation",
                stop_loss_price=None,
            )

        # 計算前30根K線的最低點並設定停損價格
        try:
            lowest_price = min(kbar.low for kbar in input_data.kbars[-31:])
            stop_loss_price = lowest_price - input_data.stop_loss_points
        except Exception:
            stop_loss_price = (
                input_data.current_price - input_data.stop_loss_points
            )  # 預設值

        # 計算MACD
        macd_list = self.calculate_macd(input_data.kbars)

        # 取得當前 MACD 值（用於日誌和條件判斷）
        latest_macd = macd_list.get_latest(1)
        current_macd = latest_macd[-1] if latest_macd else None

        # 打印 MACD 日誌（如果有數據）
        if current_macd:
            print(f"latest_macd: {current_macd.macd_line:.1f}")
            print(f"latest_signal: {current_macd.signal_line:.1f}")

        current_price = input_data.current_price

        # 檢查金叉（內部已處理數據不足的情況）
        is_golden_cross = self.check_golden_cross(macd_list)

        # MACD金叉策略：MACD < 35 且金叉時買入
        if (
            current_macd
            and (current_macd.macd_line + current_macd.signal_line) / 2 < 35
            and is_golden_cross
        ):
            return TradingSignal(
                action=Action.Buy,
                symbol=input_data.symbol,
                price=current_price,
                confidence=0.8,
                reason=f"MACD Golden Cross: MACD({current_macd.macd_line:.2f}) > Signal({current_macd.signal_line:.2f})",
                timestamp=datetime.now(),
                stop_loss_price=stop_loss_price,
            )

        # 構建 Hold 原因說明
        if current_macd:
            reason = f"No signal: MACD({current_macd.macd_line:.2f}), Signal({current_macd.signal_line:.2f})"
        else:
            reason = "Insufficient MACD data"

        return TradingSignal(
            action=Action.Hold,
            symbol=input_data.symbol,
            price=current_price,
            reason=reason,
            timestamp=datetime.now(),
            stop_loss_price=stop_loss_price,
        )


if __name__ == "__main__":
    from auto_trade.core.client import create_api_client
    from auto_trade.core.config import Config
    from auto_trade.services.market_service import MarketService

    config = Config()

    # 建立API客戶端
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        simulation=True,
    )
    market_service = MarketService(api_client)
    # 訂閱商品
    market_service.subscribe_symbol("TXF", "TXF202510", init_days=30)
    quote = market_service.get_realtime_quote("TXF", "TXF202510")
    kbars_30m: KBarList = market_service.get_futures_kbars_with_timeframe(
        "TXF", "TXF202510", "30m", days=15
    )

    strategy = StrategyService()
    input_data = StrategyInput(
        symbol="TXF202510",
        kbars=kbars_30m,
        current_price=quote.price,
        timestamp=datetime.now(),
    )
    macd_list = strategy.calculate_macd(input_data.kbars)
    print(quote.price)
    print(kbars_30m[-1])
    print(macd_list[-1].macd_line)
    print(macd_list[-1].signal_line)
    print(macd_list[-1].histogram)
