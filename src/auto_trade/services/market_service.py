"""Market service for managing market data operations."""

from datetime import UTC, datetime, timedelta

import pandas as pd

from auto_trade.models import KBar, KBarList, Quote

# 支援的時間尺度對應表
TIMEFRAME_MAPPING = {
    "1m": "1min",  # 1分鐘
    "2m": "2min",  # 2分鐘
    "3m": "3min",  # 3分鐘
    "5m": "5min",  # 5分鐘
    "10m": "10min",  # 10分鐘
    "15m": "15min",  # 15分鐘
    "30m": "30min",  # 30分鐘
    "1h": "1h",  # 1小時
    "2h": "2h",  # 2小時
    "3h": "3h",  # 3小時
    "4h": "4h",  # 4小時
    "1d": "1D",  # 1天
    "1w": "1W",  # 1週
    "1month": "1M",  # 1月
}


class MarketService:
    """市場資料服務類別"""

    def __init__(self, api_client):
        self.api_client = api_client

    @staticmethod
    def is_trading_time():
        """檢查是否在交易時間"""
        now = datetime.now()
        if now.weekday() in [1, 2, 3, 4]:
            return (
                (now.strftime("%H:%M") < "13:45" and now.strftime("%H:%M") >= "08:45")
                or now.strftime("%H:%M") >= "15:00"
                or now.strftime("%H:%M") < "05:00"
            )
        elif now.weekday() == 0:
            return (
                now.strftime("%H:%M") < "13:45" and now.strftime("%H:%M") >= "08:45"
            ) or now.strftime("%H:%M") >= "15:00"
        elif now.weekday() == 5:
            return now.strftime("%H:%M") < "05:00"
        else:
            return False

    @staticmethod
    def convert_timestamp_to_datetime(ts: int, use_start_time: bool = True) -> datetime:
        """
        將納秒timestamp轉換為datetime

        Args:
            ts: 納秒timestamp
            use_start_time: 是否使用開始時間 (True) 或結束時間 (False)

        Returns:
            轉換後的datetime (UTC+0)
        """
        # 轉換為秒
        ts_seconds = ts / 1000000000

        # 轉換為UTC時間
        dt = datetime.fromtimestamp(ts_seconds, tz=UTC).replace(tzinfo=None)

        # 如果使用開始時間，需要減去1小時 (因為原始timestamp是結束時間)
        if use_start_time:
            dt = dt - timedelta(minutes=1)

        return dt

    def get_futures_realtime_quote(self, symbol: str, sub_symbol: str) -> Quote | None:
        """取得期貨即時報價"""
        try:
            contract = self.api_client.Contracts.Futures[symbol][sub_symbol]
            snapshot = self.api_client.snapshots([contract])
            if snapshot and len(snapshot) > 0:
                return self._format_quote_data(snapshot[0])
            return None
        except Exception:
            return None

    def get_futures_historical_kbars(
        self, symbol: str, sub_symbol: str, days: int = 30
    ) -> KBarList:
        """取得期貨歷史K線資料"""
        contract = self.api_client.Contracts.Futures[symbol][sub_symbol]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        kbars = self.api_client.kbars(
            contract=contract,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
        )
        if (
            self.convert_timestamp_to_datetime(
                kbars.ts[-1], use_start_time=True
            ).strftime("%H:%M")
            < datetime.now().strftime("%H:%M")
            and self.is_trading_time()
        ):
            kbars.ts.append(kbars.ts[-1] + 60000000000)
            kbars.Open.append(kbars.Open[-1])
            kbars.High.append(kbars.High[-1])
            kbars.Low.append(kbars.Low[-1])
            kbars.Close.append(kbars.Close[-1])

        return self._format_kbar_data(kbars, symbol, "1m")

    def resample_kbars(self, kbar_list: KBarList, timeframe: str) -> KBarList:
        """
        將1分鐘K線轉換為指定時間尺度的K線
        台灣市場時段 (UTC+0)：
        - 第一時段：8:45-13:45
        - 第二時段：15:00-隔天05:00

        注意：現在使用開始時間作為timestamp

        Args:
            kbar_list: K線資料列表
            timeframe: 目標時間尺度

        Returns:
            重採樣後的K線資料列表
        """
        if timeframe not in TIMEFRAME_MAPPING:
            raise ValueError(
                f"不支援的時間尺度: {timeframe}. 支援的尺度: {list(TIMEFRAME_MAPPING.keys())}"
            )

        # 如果已經是目標時間尺度，直接返回
        if timeframe == "1m":
            return kbar_list

        # 轉換為DataFrame進行重採樣
        df = kbar_list.to_dataframe()

        # 台灣市場時段定義 (UTC+0)
        # 第一時段：8:45-13:45
        # 第二時段：15:00-隔天05:00

        resampled_dfs = []
        pandas_freq = TIMEFRAME_MAPPING[timeframe]

        # 處理第一時段：8:45-13:45
        morning_mask = (df.index.time >= pd.to_datetime("08:45").time()) & (
            df.index.time <= pd.to_datetime("13:45").time()
        )
        morning_df = df[morning_mask]

        if not morning_df.empty:
            morning_resampled = (
                morning_df.resample(
                    pandas_freq, origin="08:45", closed="left", label="left"
                )
                .agg(
                    {
                        "open": "first",  # 開盤價取第一個
                        "high": "max",  # 最高價取最大值
                        "low": "min",  # 最低價取最小值
                        "close": "last",  # 收盤價取最後一個
                    }
                )
                .dropna()  # 移除空值行
            )
            if not morning_resampled.empty:
                resampled_dfs.append(morning_resampled)

        # 處理第二時段：15:00-隔天05:00
        # 分為當天15:00-23:59和隔天00:00-05:00兩部分
        evening_mask = df.index.time >= pd.to_datetime("15:00").time()
        night_mask = df.index.time <= pd.to_datetime("05:00").time()

        # 當天15:00-23:59
        evening_df = df[evening_mask & ~night_mask]
        if not evening_df.empty:
            evening_resampled = (
                evening_df.resample(
                    pandas_freq, origin="15:00", closed="left", label="left"
                )
                .agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                    }
                )
                .dropna()
            )
            if not evening_resampled.empty:
                resampled_dfs.append(evening_resampled)

        # 隔天00:00-05:00
        night_df = df[night_mask]
        if not night_df.empty:
            night_resampled = (
                night_df.resample(
                    pandas_freq, origin="00:00", closed="left", label="left"
                )
                .agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                    }
                )
                .dropna()
            )
            if not night_resampled.empty:
                resampled_dfs.append(night_resampled)
        # 合併所有時段的結果
        if resampled_dfs:
            resampled = pd.concat(resampled_dfs).sort_index()
        else:
            # 如果沒有符合時段的資料，返回空的DataFrame
            resampled = pd.DataFrame(columns=["open", "high", "low", "close"])
        # 重置索引，將時間戳放回列中，並重命名為 'time'

        # resampled.reset_index(inplace=True)

        # 轉換回KBarList
        return KBarList.from_dataframe(resampled, kbar_list.symbol, timeframe)

    def get_futures_kbars_with_timeframe(
        self, symbol: str, sub_symbol: str, timeframe: str = "1m", days: int = 30
    ) -> KBarList:
        """
        取得指定時間尺度的期貨K線資料

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            timeframe: 時間尺度
            days: 取得天數

        Returns:
            指定時間尺度的K線資料列表
        """
        # 先取得1分鐘K線
        kbars_1m = self.get_futures_historical_kbars(symbol, sub_symbol, days)
        # 轉換為指定時間尺度
        return self.resample_kbars(kbars_1m, timeframe)

    def _format_quote_data(self, snapshot) -> Quote:
        """格式化即時報價資料"""
        return Quote(
            symbol=snapshot.code,
            price=float(snapshot.close),
            volume=snapshot.total_volume,
            bid_price=float(snapshot.buy_price) if snapshot.buy_price else None,
            ask_price=float(snapshot.sell_price) if snapshot.sell_price else None,
            timestamp=self.convert_timestamp_to_datetime(
                snapshot.ts, use_start_time=False
            ),
        )

    def _format_kbar_data(
        self, kbars, symbol: str = "", timeframe: str = "1m"
    ) -> KBarList:
        """格式化K線資料"""
        kbar_list = []

        for ts, open, high, low, close in zip(
            kbars.ts, kbars.Open, kbars.High, kbars.Low, kbars.Close, strict=False
        ):
            if (
                ts is None
                or open is None
                or high is None
                or low is None
                or close is None
            ):
                continue
            kbar_list.append(
                KBar(
                    time=self.convert_timestamp_to_datetime(ts, use_start_time=True),
                    open=float(open),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                )
            )

        return KBarList(kbars=kbar_list, symbol=symbol, timeframe=timeframe)

    def _format_tick_data(self, ticks) -> pd.DataFrame:
        """格式化tick資料"""
        df = pd.DataFrame({**ticks})
        df["ts"] = pd.to_datetime(df["ts"])
        return df[["ts", "close", "volume", "bid_price", "ask_price"]]

    def list_all_futures_products(self) -> dict:
        """
        列出所有可用的期貨商品

        Returns:
            dict: 包含所有期貨商品的字典，格式為 {symbol: [sub_symbol1, sub_symbol2, ...]}
        """
        try:
            futures_products = {}

            # 遍歷所有期貨合約
            for symbol_obj in self.api_client.Contracts.Futures:
                # symbol_obj 可能是一個 StreamMultiContract 對象，需要獲取其代碼
                try:
                    if hasattr(symbol_obj, "code"):
                        symbol = symbol_obj.code
                    else:
                        symbol = str(symbol_obj)
                except Exception:
                    symbol = str(symbol_obj)

                sub_symbols = []

                # 獲取該商品的所有子合約
                try:
                    sub_contracts = self.api_client.Contracts.Futures[symbol]
                    if sub_contracts is None:
                        print(f"⚠️ {symbol} 的子合約為 None，跳過")
                        continue

                    for sub_symbol in sub_contracts:
                        try:
                            # 嘗試不同的方式獲取代碼
                            if hasattr(sub_symbol, "code"):
                                code = sub_symbol.code
                            elif hasattr(sub_symbol, "__str__"):
                                code = str(sub_symbol)
                            else:
                                code = repr(sub_symbol)

                            sub_symbols.append(code)
                        except Exception as e:
                            print(f"處理子合約時出錯: {e}, 合約: {sub_symbol}")
                            continue
                except Exception as e:
                    print(f"獲取 {symbol} 的子合約時出錯: {e}")
                    continue

                if sub_symbols:
                    # 確保所有元素都是字符串再排序
                    futures_products[symbol] = sorted(
                        [str(code) for code in sub_symbols]
                    )

            return futures_products

        except Exception as e:
            print(f"❌ 獲取期貨商品列表失敗: {e}")
            import traceback

            traceback.print_exc()
            return {}

    def get_futures_product_info(self, symbol: str) -> dict:
        """
        獲取特定期貨商品的詳細信息

        Args:
            symbol: 商品代碼 (例如: MXF, TXF)

        Returns:
            dict: 包含商品詳細信息的字典
        """
        try:
            # 檢查symbol是否存在於Futures中
            try:
                sub_contracts = self.api_client.Contracts.Futures[symbol]
                if sub_contracts is None:
                    print(f"❌ 商品 {symbol} 的子合約為 None")
                    return {}
            except Exception as e:
                print(f"❌ 無法找到商品 {symbol}: {e}")
                return {}

            product_info = {"symbol": symbol, "sub_symbols": [], "contracts": {}}

            # 獲取所有子合約信息
            for contract in sub_contracts:
                try:
                    # sub_symbol 是一個 StreamMultiContract 對象，需要獲取其 code 屬性
                    if hasattr(contract, "symbol"):
                        sub_symbol = contract.symbol
                    else:
                        sub_symbol = str(contract)

                    product_info["sub_symbols"].append(sub_symbol)
                    product_info["contracts"][sub_symbol] = {
                        "code": getattr(contract, "code", sub_symbol),
                        "name": getattr(contract, "name", ""),
                        "exchange": getattr(contract, "exchange", ""),
                        "delivery_month": getattr(contract, "delivery_month", ""),
                        "delivery_date": getattr(contract, "delivery_date", ""),
                    }
                except Exception as e:
                    print(f"處理子合約 {sub_symbol} 時出錯: {e}")
                    continue

            return product_info

        except Exception as e:
            print(f"❌ 獲取期貨商品信息失敗: {e}")
            import traceback

            traceback.print_exc()
            return {}


if __name__ == "__main__":
    from auto_trade.core.client import create_api_client
    from auto_trade.core.config import Config

    config = Config()

    api = create_api_client(
        config.api_key,
        config.secret_key,
        simulation=True,
    )
    market_service = MarketService(api)

    # print("📊 測試列出所有期貨商品:")
    # products = market_service.list_all_futures_products()
    # print(products)

    print("\n📈 測試獲取MXF商品信息:")
    mxf_info = market_service.get_futures_product_info("TXF")
    # print(mxf_info)
    for sub_symbol in mxf_info["sub_symbols"]:
        print(sub_symbol)
    for item in mxf_info["contracts"].items():
        print(item[0])
        print(item[1])

    # from auto_trade.core.client import create_api_client
    # from auto_trade.core.config import Config

    # config = Config()

    # api = create_api_client(
    #     config.api_key,
    #     config.secret_key,
    #     simulation=True,
    # )
    # market_service = MarketService(api)
    # kbars = market_service.get_futures_kbars_with_timeframe(
    #     symbol="MXF", sub_symbol="MXF202511", timeframe="30m"
    # )
    # print(kbars)
