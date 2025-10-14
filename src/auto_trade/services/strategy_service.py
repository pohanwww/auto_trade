"""Strategy service for trading strategy calculations."""

from datetime import datetime

import pandas as pd

from auto_trade.models import (
    Action,
    EMAData,
    EMAList,
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

    def generate_signal(self, input_data: StrategyInput) -> TradingSignal:
        """生成MACD金叉策略訊號"""
        if len(input_data.kbars) < 30:
            return TradingSignal(
                action=Action.Hold,
                symbol=input_data.symbol,
                price=input_data.current_price,
                reason="Insufficient data for MACD calculation",
            )

        # 計算MACD
        macd_list = self.calculate_macd(input_data.kbars)

        # 取得最新的MACD值
        latest_macd = macd_list.get_latest(3)  # 取得最新2個數據點
        if len(latest_macd) < 2:
            return TradingSignal(
                action=Action.Hold,
                symbol=input_data.symbol,
                price=input_data.current_price,
                reason="Insufficient MACD data",
            )

        current_macd = latest_macd[-2]
        previous_macd = latest_macd[-3]

        print(f"latest_macd: {latest_macd[-1].macd_line:.1f}")
        print(f"latest_signal: {latest_macd[-1].signal_line:.1f}")
        current_signal = current_macd.signal_line
        previous_signal = previous_macd.signal_line

        current_price = input_data.current_price

        # MACD金叉策略：MACD < 30 且金叉時買入
        if (
            (current_macd.macd_line + current_macd.signal_line) / 2 < 30
            and previous_macd.macd_line <= previous_signal
            and current_macd.macd_line > current_signal
        ):
            return TradingSignal(
                action=Action.Buy,
                symbol=input_data.symbol,
                price=current_price,
                confidence=0.8,
                reason=f"MACD Golden Cross: MACD({current_macd.macd_line:.2f}) > Signal({current_signal:.2f})",
                timestamp=datetime.now(),
            )

        return TradingSignal(
            action=Action.Hold,
            symbol=input_data.symbol,
            price=current_price,
            reason=f"No signal: MACD({current_macd.macd_line:.2f}), Signal({current_signal:.2f})",
            timestamp=datetime.now(),
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
    quote = market_service.get_futures_realtime_quote("TXF", "TXF202510")
    kbars_30m: KBarList = market_service.get_futures_kbars_with_timeframe(
        "TXF", "TXF202510", "30m", days=30
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
    breakpoint()
