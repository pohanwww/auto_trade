"""Market service for managing market data operations."""

import threading
import time
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
        # 設置 quote callback
        self.api_client.quote.set_on_tick_fop_v1_callback(self._quote_callback)

        # key: (symbol, sub_symbol)
        # value: {
        #     "contract_code": str,         # 合約代碼（如 "MXFK5"）
        #     "latest_quote": tick,         # 最新報價（TickFOPv1 對象）
        #     "kbars_1m": KBarList,         # 1 分鐘 K 線數據
        #     "last_api_sync": datetime,    # 上次從 API 同步的時間
        #     "last_tick_update": datetime, # 上次從 tick 更新的時間
        #     "current_kbar": dict,         # 當前正在構建的 K 線
        #     "subscribed": bool            # 是否已訂閱
        # }
        self._symbol_cache: dict[tuple[str, str], dict] = {}

        # 合約代碼反向映射: contract_code -> (symbol, sub_symbol), 用於 callback 快速查找
        self._contract_mapping: dict[str, tuple[str, str]] = {}

        # Tick event: set() on every incoming tick so consumers can wake immediately
        self._tick_event = threading.Event()

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

    def _quote_callback(self, exchange, tick):
        """Quote callback - 儲存最新報價並更新 K 線緩存"""
        _ = exchange  # 參數由 API 提供但未使用
        contract_code = tick.code

        mapping = self._contract_mapping.get(contract_code)
        if mapping is None:
            # 滾動合約（如 MXFR1）訂閱後，tick 會以實際合約代碼送達（如 MXFG6）
            # 透過商品前綴自動匹配並註冊
            for _, (sym, sub_sym) in self._contract_mapping.items():
                if contract_code.startswith(sym):
                    self._contract_mapping[contract_code] = (sym, sub_sym)
                    mapping = (sym, sub_sym)
                    print(f"📌 自動映射合約: {contract_code} → {sym}/{sub_sym}")
                    break
            if mapping is None:
                return
        symbol, sub_symbol = mapping
        cache_key = (symbol, sub_symbol)
        if cache_key not in self._symbol_cache:
            return

        # 更新統一緩存中的報價
        self._symbol_cache[cache_key]["latest_quote"] = tick

        # 從 tick 更新 K 線緩存
        self._update_kbar_from_tick(tick)

        # Wake up any thread waiting in wait_for_tick()
        self._tick_event.set()

    # ── Tick event helpers ────────────────────────────────────

    def wait_for_tick(self, timeout: float | None = None) -> bool:
        """Block until the next tick arrives or *timeout* seconds elapse.

        Returns True if a tick woke us up, False on timeout.
        """
        triggered = self._tick_event.wait(timeout=timeout)
        self._tick_event.clear()
        return triggered

    def _update_kbar_from_tick(self, tick):
        """從 tick 數據實時更新 K 線緩存

        策略：
        1. 根據 tick.code 找到對應的 (symbol, sub_symbol)
        2. 將 tick.datetime 對齊到分鐘（去除秒和微秒）
        3. 檢查是否需要創建新 K 線或更新現有 K 線
        4. 更新 OHLC 和成交量
        """
        try:
            contract_code = tick.code

            # 檢查是否有映射
            if contract_code not in self._contract_mapping:
                return

            symbol, sub_symbol = self._contract_mapping[contract_code]
            cache_key = (symbol, sub_symbol)

            # 檢查緩存是否存在
            if cache_key not in self._symbol_cache:
                return

            cached_data = self._symbol_cache[cache_key]
            kbars_1m = cached_data["kbars_1m"]

            # 獲取 tick 價格和時間
            tick_price = tick.close
            tick_time = tick.datetime
            tick_total_volume = int(getattr(tick, "total_volume", 0) or 0)

            last_total_volume = cached_data.get("last_tick_total_volume")
            if last_total_volume is None:
                # First live tick is only used to establish cumulative-volume baseline.
                incremental_volume = 0
            elif tick_total_volume < last_total_volume:
                # Session reset / reconnect / contract volume reset.
                incremental_volume = 0
            else:
                incremental_volume = tick_total_volume - last_total_volume
            cached_data["last_tick_total_volume"] = tick_total_volume

            # 對齊到分鐘（去除秒和微秒）
            kbar_time = tick_time.replace(second=0, microsecond=0)

            # 檢查是否需要創建新 K 線
            if not kbars_1m.kbars or kbars_1m.kbars[-1].time < kbar_time:
                # 創建新的 1 分鐘 K 線
                new_kbar = KBar(
                    time=kbar_time,
                    open=tick_price,
                    high=tick_price,
                    low=tick_price,
                    close=tick_price,
                    volume=incremental_volume,
                )
                kbars_1m.kbars.append(new_kbar)
                # print(f"🆕 新 K 線: {kbar_time.strftime('%H:%M')} @ {tick_price}")
            else:
                # 更新現有 K 線（同一分鐘內的 tick）
                current_kbar = kbars_1m.kbars[-1]

                # 只在時間匹配時更新
                if current_kbar.time == kbar_time:
                    current_kbar.high = max(current_kbar.high, tick_price)
                    current_kbar.low = min(current_kbar.low, tick_price)
                    current_kbar.close = tick_price
                    current_kbar.volume += incremental_volume

            # 更新最後更新時間
            cached_data["last_tick_update"] = datetime.now()

        except Exception as e:
            # 靜默失敗，避免影響 quote callback
            print(f"⚠️  更新 K 線失敗: {e}")
            pass

    def sync_kbars_cache(self, symbol: str, sub_symbol: str, days: int = 1):
        """同步 K 線緩存（無條件執行）

        此方法會從 API 獲取指定天數的歷史數據並更新緩存。
        可用於：
        1. 首次訂閱時初始化（傳入 days=30）
        2. 手動觸發同步（傳入 days=1 或其他）

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            days: 獲取天數（默認 1 天）
        """
        cache_key = (symbol, sub_symbol)
        now = datetime.now()

        print(f"🔄 同步 K 線緩存: {symbol}/{sub_symbol} ({days} 天)")

        # 從 API 獲取歷史數據（失敗自動重試）
        max_retries = 3
        retry_delay = 10
        kbars_1m = None
        for attempt in range(1, max_retries + 1):
            kbars_1m = self.get_futures_historical_kbars(symbol, sub_symbol, days)
            if kbars_1m.kbars and len(kbars_1m.kbars) > 0:
                break
            print(f"⚠️  同步失敗：API 返回空數據 (第 {attempt}/{max_retries} 次)")
            if attempt < max_retries:
                print(f"⏳ {retry_delay} 秒後重試...")
                time.sleep(retry_delay)

        if not kbars_1m or not kbars_1m.kbars or len(kbars_1m.kbars) == 0:
            print("❌ 同步失敗：重試後仍無數據")
            return

        # 檢查緩存是否存在
        if cache_key not in self._symbol_cache:
            # 首次初始化
            self._symbol_cache[cache_key] = {
                "contract_code": None,  # 在 subscribe_symbol 中設置
                "latest_quote": None,
                "kbars_1m": kbars_1m,
                "last_api_sync": now,
                "last_tick_update": None,
                "last_tick_total_volume": None,
                "current_kbar": None,
                "subscribed": True,
            }
            print(f"✅ K線緩存初始化完成，共 {len(kbars_1m.kbars)} 根")
        else:
            # 更新現有緩存
            cached_data = self._symbol_cache[cache_key]
            existing_kbars = cached_data["kbars_1m"]

            if existing_kbars.kbars and len(existing_kbars.kbars) > 0:
                # 找到同步數據的最早時間
                sync_start_time = kbars_1m.kbars[0].time

                # 保留同步時間之前的歷史數據
                old_kbars = [
                    kb for kb in existing_kbars.kbars if kb.time < sync_start_time
                ]

                # 合併：舊數據 + 新數據
                merged_kbars = old_kbars + kbars_1m.kbars

                # 更新緩存
                existing_kbars.kbars = merged_kbars
                cached_data["last_api_sync"] = now
                cached_data["last_tick_total_volume"] = None

                # 清除 resample 緩存，強制從有 volume 的新數據重建
                for key in list(cached_data.keys()):
                    if key.startswith("_resample_"):
                        del cached_data[key]

                print(f"✅ 同步完成，當前共 {len(merged_kbars)} 根 K 線")
            else:
                # 緩存為空，直接使用新數據
                cached_data["kbars_1m"] = kbars_1m
                cached_data["last_api_sync"] = now
                cached_data["last_tick_total_volume"] = None
                print(f"✅ 同步完成，共 {len(kbars_1m.kbars)} 根 K 線")

    def subscribe_symbol(self, symbol: str, sub_symbol: str, init_days: int = 30):
        """訂閱商品並初始化 K 線緩存

        此方法會：
        1. 訂閱合約的 tick 數據流
        2. 建立 contract_code 映射
        3. 獲取歷史數據初始化 K 線緩存
        4. 之後 tick callback 會自動更新 K 線

        Args:
            symbol: 商品代碼 (如: MXF)
            sub_symbol: 子商品代碼 (如: MXF202511)
            init_days: 初始化緩存的天數（默認 30 天）
        """
        try:
            contract = self.api_client.Contracts.Futures[symbol][sub_symbol]
            contract_code = contract.code
            cache_key = (symbol, sub_symbol)

            # 檢查是否已訂閱
            if cache_key in self._symbol_cache and self._symbol_cache[cache_key].get(
                "subscribed"
            ):
                print(f"⚠️  {symbol}/{sub_symbol} 已經訂閱")
                return

            print(f"📡 訂閱合約: {symbol}/{sub_symbol} ({contract_code})")

            # 建立合約代碼映射（用於 tick callback）
            # 同時註冊 sub_symbol 和實際合約代碼（MXFR1 → MXFG6 等滾動合約場景）
            self._contract_mapping[contract_code] = (symbol, sub_symbol)
            if contract_code != sub_symbol:
                self._contract_mapping[sub_symbol] = (symbol, sub_symbol)

            # 訂閱合約
            self.api_client.quote.subscribe(
                contract,
                quote_type="tick",
                version="v1",
            )

            # 初始化 K 線緩存
            self.sync_kbars_cache(symbol, sub_symbol, days=init_days)

            # 設置 contract_code（在 sync_kbars_cache 之後）
            if cache_key in self._symbol_cache:
                self._symbol_cache[cache_key]["contract_code"] = contract_code

            # 等待數據流建立並收到第一筆報價
            print("⏳ 等待 tick 數據流建立...")
            start_time = datetime.now()
            while (datetime.now() - start_time).total_seconds() < 30:
                time.sleep(1)
                if (
                    cache_key in self._symbol_cache
                    and self._symbol_cache[cache_key].get("latest_quote") is not None
                ):
                    break
            else:
                # while 循環正常結束（超時）
                print("⚠️  等待超時，但訂閱已建立，tick 數據將陸續到達")

        except Exception as e:
            print(f"❌ 訂閱失敗: {type(e).__name__}: {e}")

    def get_realtime_quote(self, symbol: str, sub_symbol: str) -> Quote | None:
        """取得即時報價（從緩存讀取）

        注意：需要先調用 subscribe_symbol() 訂閱商品

        Args:
            symbol: 商品代碼 (如: MXF)
            sub_symbol: 子商品代碼 (如: MXF202511)

        Returns:
            Quote 對象或 None（如果尚未訂閱或無數據）
        """
        try:
            cache_key = (symbol, sub_symbol)

            # 從統一緩存讀取
            if cache_key not in self._symbol_cache:
                return None

            cached_data = self._symbol_cache[cache_key]
            tick = cached_data.get("latest_quote")

            if tick:
                return Quote(
                    symbol=tick.code,
                    price=int(tick.close),
                    volume=tick.total_volume,
                    bid_price=None,  # TickFOPv1 沒有 bid/ask 價格
                    ask_price=None,
                    timestamp=tick.datetime,
                )

            return None

        except Exception as e:
            print(f"⚠️  取得即時報價失敗: {type(e).__name__}: {e}")
            return None

    def get_futures_historical_kbars(
        self, symbol: str, sub_symbol: str, days: int = 30
    ) -> KBarList:
        """取得期貨歷史K線資料"""
        try:
            contract = self.api_client.Contracts.Futures[symbol][sub_symbol]
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)

            kbars = self.api_client.kbars(
                contract=contract,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
            )

            # 檢查是否有數據
            if not kbars.ts or len(kbars.ts) == 0:
                print(
                    f"⚠️  取得 {symbol}/{sub_symbol} 歷史K線資料為空 ({start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')})"
                )
                # 返回空的 KBarList
                return KBarList(kbars=[], symbol=symbol, timeframe="1m")

        except Exception as e:
            print(f"❌ 取得歷史K線失敗: {type(e).__name__}: {e}")
            return KBarList(kbars=[], symbol=symbol, timeframe="1m")

        return self._format_kbar_data(kbars, symbol, "1m")

    def get_futures_kbars_by_date_range(
        self,
        symbol: str,
        sub_symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "30m",
    ) -> KBarList:
        """取得指定日期範圍的期貨 K 線資料（回測專用）

        直接從 API 取得歷史 1 分鐘 K 線，然後重採樣到指定時間尺度。
        不需要事先 subscribe_symbol()。
        支持已到期合約的自動回退。

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            start_date: 開始日期
            end_date: 結束日期
            timeframe: 時間尺度 (如 "30m", "1h")

        Returns:
            KBarList: 指定時間尺度的 K 線資料
        """
        try:
            contract = self.api_client.Contracts.Futures[symbol][sub_symbol]
            if contract is None:
                print(f"❌ 無法解析合約: {symbol}/{sub_symbol}")
                return KBarList(kbars=[], symbol=symbol, timeframe=timeframe)

            print(
                f"📡 從 API 取得歷史數據: {symbol} ({contract.code}) "
                f"({start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')})"
            )

            kbars = self.api_client.kbars(
                contract=contract,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
            )

            # 檢查是否有數據
            if kbars is None or not kbars.ts or len(kbars.ts) == 0:
                print(
                    f"⚠️  取得 {symbol} 歷史K線資料為空 "
                    f"({start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')})"
                )
                return KBarList(kbars=[], symbol=symbol, timeframe=timeframe)

            # 格式化為 1 分鐘 K 線
            kbars_1m = self._format_kbar_data(kbars, symbol, "1m")
            print(f"✅ 取得 {len(kbars_1m.kbars)} 根 1 分鐘 K 線")

            # 如果需要的就是 1m，直接返回
            if timeframe == "1m":
                return kbars_1m

            # 重採樣到指定時間尺度
            resampled = self.resample_kbars(kbars_1m, timeframe)
            print(f"✅ 重採樣為 {timeframe}: {len(resampled.kbars)} 根 K 線")
            return resampled

        except Exception as e:
            print(f"❌ 取得歷史數據失敗: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            return KBarList(kbars=[], symbol=symbol, timeframe=timeframe)

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
        # 檢查是否有數據
        if not kbar_list.kbars or len(kbar_list.kbars) == 0:
            return KBarList(kbars=[], symbol=kbar_list.symbol, timeframe=timeframe)

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
            agg_dict = {
                "open": "first",  # 開盤價取第一個
                "high": "max",  # 最高價取最大值
                "low": "min",  # 最低價取最小值
                "close": "last",  # 收盤價取最後一個
            }
            if "volume" in morning_df.columns:
                agg_dict["volume"] = "sum"  # 成交量加總
            morning_resampled = (
                morning_df.resample(
                    pandas_freq, origin="08:45", closed="left", label="left"
                )
                .agg(agg_dict)
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
            agg_dict_eve = {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
            }
            if "volume" in evening_df.columns:
                agg_dict_eve["volume"] = "sum"
            evening_resampled = (
                evening_df.resample(
                    pandas_freq, origin="15:00", closed="left", label="left"
                )
                .agg(agg_dict_eve)
                .dropna()
            )
            if not evening_resampled.empty:
                resampled_dfs.append(evening_resampled)

        # 隔天00:00-05:00
        night_df = df[night_mask]
        if not night_df.empty:
            agg_dict_night = {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
            }
            if "volume" in night_df.columns:
                agg_dict_night["volume"] = "sum"
            night_resampled = (
                night_df.resample(
                    pandas_freq, origin="00:00", closed="left", label="left"
                )
                .agg(agg_dict_night)
                .dropna()
            )
            if not night_resampled.empty:
                resampled_dfs.append(night_resampled)
        # 合併所有時段的結果
        if resampled_dfs:
            resampled = pd.concat(resampled_dfs).sort_index()
        else:
            # 如果沒有符合時段的資料，返回空的DataFrame
            resampled = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        # 重置索引，將時間戳放回列中，並重命名為 'time'

        # resampled.reset_index(inplace=True)

        # 轉換回KBarList
        return KBarList.from_dataframe(resampled, kbar_list.symbol, timeframe)

    def get_futures_kbars_with_timeframe(
        self, symbol: str, sub_symbol: str, timeframe: str = "1m", days: int = 15
    ) -> KBarList:
        """
        取得指定時間尺度的期貨K線資料（從緩存讀取，零 API 調用）

        新策略（實時更新 + 備用校驗）：
        1. 首次訂閱時會初始化緩存（獲取 30 天歷史數據）- 1 次 API 調用
        2. 之後通過 tick callback 實時更新 K 線 - 零 API 調用
        3. 每 24 小時校驗一次（獲取 1 天數據對比）- 1 次 API 調用
        4. 本方法只從緩存讀取，不調用 API
        5. 需要不同時間尺度時，從 1m 重採樣

        流量消耗：
        - 首日：~3 MB (初始化 30 天)
        - 之後每日：~200 KB (校驗 1 天)
        - 總計：~3.2 MB/首日，~200 KB/後續每日

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            timeframe: 時間尺度
            days: 取得天數（默認 15 天，但緩存中有 30 天）

        Returns:
            指定時間尺度的K線資料列表
        """
        cache_key = (symbol, sub_symbol)

        # 檢查緩存是否存在
        if cache_key not in self._symbol_cache:
            print(f"⚠️  K線緩存尚未初始化: {symbol}/{sub_symbol}")
            print("💡 請先調用 subscribe_symbol() 來初始化緩存")
            # 返回空的 KBarList
            return KBarList(kbars=[], symbol=symbol, timeframe=timeframe)

        # 從緩存讀取 1 分鐘 K 線
        cached_data = self._symbol_cache[cache_key]
        kbars_1m = cached_data["kbars_1m"]

        # 檢查緩存是否有數據
        if not kbars_1m.kbars or len(kbars_1m.kbars) == 0:
            print(f"⚠️  K線緩存為空: {symbol}/{sub_symbol}")
            return KBarList(kbars=[], symbol=symbol, timeframe=timeframe)

        # 如果需要限制天數，裁剪數據（僅用於 1m 直接返回）
        if timeframe == "1m":
            if days < 30:
                cutoff_time = datetime.now() - timedelta(days=days)
                filtered_kbars = [kb for kb in kbars_1m.kbars if kb.time >= cutoff_time]
                return KBarList(
                    kbars=filtered_kbars, symbol=symbol, timeframe="1m"
                )
            return kbars_1m

        # 增量重採樣：基於 master kbars_1m（穩定、只增長），不用 filtered copy
        resample_cache_key = f"_resample_{timeframe}"
        resample_idx_key = f"_resample_idx_{timeframe}"

        cached_resampled: KBarList | None = cached_data.get(resample_cache_key)
        last_idx: int = cached_data.get(resample_idx_key, 0)

        tf_minutes = self._get_timeframe_minutes(timeframe)
        all_1m = kbars_1m.kbars

        if cached_resampled is None or last_idx > len(all_1m):
            cached_resampled = KBarList(
                kbars=[], symbol=symbol, timeframe=timeframe
            )
            cached_data[resample_cache_key] = cached_resampled
            last_idx = 0

        new_1m = all_1m[last_idx:]
        if new_1m:
            bars = cached_resampled.kbars
            for kb in new_1m:
                bucket = self._align_to_tf_bucket(kb.time, tf_minutes)

                if bars and bars[-1].time == bucket:
                    bar = bars[-1]
                    if kb.high > bar.high:
                        bar.high = kb.high
                    if kb.low < bar.low:
                        bar.low = kb.low
                    bar.close = kb.close
                    bar.volume += kb.volume
                else:
                    bars.append(
                        KBar(
                            time=bucket,
                            open=kb.open,
                            high=kb.high,
                            low=kb.low,
                            close=kb.close,
                            volume=kb.volume,
                        )
                    )
            cached_data[resample_idx_key] = len(all_1m)

        resampled_bars = cached_resampled.kbars

        # 刷新最後一根 5m bar：tick callback 會 in-place 更新 1m bar 的 OHLC，
        # 但增量 resample 只處理「新增」的 1m bar，不會看到同根 1m 的 tick 更新。
        # 這裡用最後一根 1m bar 的最新數據同步到對應的 5m bar，並重算該 5m bucket volume，
        # 避免只吃到該分鐘開頭的小量導致 5m baseline 被低估。
        if all_1m and resampled_bars:
            last_1m = all_1m[-1]
            last_bucket = self._align_to_tf_bucket(last_1m.time, tf_minutes)
            if resampled_bars[-1].time == last_bucket:
                bar = resampled_bars[-1]
                if last_1m.high > bar.high:
                    bar.high = last_1m.high
                if last_1m.low < bar.low:
                    bar.low = last_1m.low
                bar.close = last_1m.close
                # Recompute volume for current bucket from all 1m bars in that bucket.
                bar.volume = sum(
                    kb.volume
                    for kb in all_1m
                    if self._align_to_tf_bucket(kb.time, tf_minutes) == last_bucket
                )

        # 補充當前時段的合成 bar（成交量少時最新 1m 可能滯後）
        synthetic = None
        if resampled_bars and self.is_trading_time() and all_1m:
            last_1m = all_1m[-1]
            current_minute = datetime.now().replace(second=0, microsecond=0)
            if last_1m.time < current_minute:
                bucket = self._align_to_tf_bucket(current_minute, tf_minutes)
                if resampled_bars[-1].time != bucket:
                    synthetic = KBar(
                        time=bucket,
                        open=last_1m.close,
                        high=last_1m.close,
                        low=last_1m.close,
                        close=last_1m.close,
                    )

        # 篩選到請求的天數
        if days < 30:
            cutoff_time = datetime.now() - timedelta(days=days)
            result_bars = [b for b in resampled_bars if b.time >= cutoff_time]
        else:
            result_bars = list(resampled_bars)

        if synthetic:
            result_bars.append(synthetic)

        return KBarList(kbars=result_bars, symbol=symbol, timeframe=timeframe)

    @staticmethod
    def _align_to_tf_bucket(t: datetime, tf_minutes: int) -> datetime:
        """將 1m bar 時間對齊到所屬的 tf 分鐘 bucket 起始時間。

        台灣期貨三個時段各有不同的 origin：
          日盤  8:45-13:45  origin=8:45
          夜盤 15:00-23:59  origin=15:00
          凌晨  0:00-05:00  origin=0:00
        """
        total_min = t.hour * 60 + t.minute

        if 525 <= total_min < 825:      # 8:45 – 13:44
            origin = 525
        elif total_min >= 900:           # 15:00 – 23:59
            origin = 900
        else:                            # 0:00 – 05:00
            origin = 0

        offset = total_min - origin
        bucket_min = origin + (offset // tf_minutes) * tf_minutes
        return t.replace(
            hour=bucket_min // 60,
            minute=bucket_min % 60,
            second=0,
            microsecond=0,
        )

    def _get_timeframe_minutes(self, timeframe: str) -> int:
        """將 timeframe 字符串轉換為分鐘數"""
        timeframe_map = {
            "1m": 1,
            "2m": 2,
            "3m": 3,
            "5m": 5,
            "10m": 10,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "3h": 180,
            "4h": 240,
            "1d": 1440,
        }
        return timeframe_map.get(timeframe, 30)  # 預設 30 分鐘

    def clear_kbars_cache(
        self,
        symbol: str | None = None,
        sub_symbol: str | None = None,
    ):
        """清理 K 線緩存

        Args:
            symbol: 商品代碼（可選，不指定則清理全部）
            sub_symbol: 子商品代碼（可選）
        """
        if symbol is None:
            # 清理全部緩存
            count = len(self._symbol_cache)
            self._symbol_cache.clear()
            self._contract_mapping.clear()
            print(f"🗑️  已清理全部數據緩存 ({count} 項)")
        else:
            # 清理指定緩存
            keys_to_remove = [
                key
                for key in self._symbol_cache
                if (symbol is None or key[0] == symbol)
                and (sub_symbol is None or key[1] == sub_symbol)
            ]
            for key in keys_to_remove:
                # 同時清理反向映射
                cached_data = self._symbol_cache[key]
                contract_code = cached_data.get("contract_code")
                if contract_code and contract_code in self._contract_mapping:
                    del self._contract_mapping[contract_code]
                del self._symbol_cache[key]
            print(f"🗑️  已清理 {len(keys_to_remove)} 項數據緩存")

    def get_cache_stats(self) -> dict:
        """取得緩存統計信息"""
        now = datetime.now()
        stats = {
            "total_entries": len(self._symbol_cache),
            "total_mappings": len(self._contract_mapping),
            "entries": [],
        }

        for (symbol, sub_symbol), data in self._symbol_cache.items():
            kbars_1m = data["kbars_1m"]
            last_api_sync = data.get("last_api_sync")
            last_tick_update = data.get("last_tick_update")
            subscribed = data.get("subscribed", False)

            # 計算更新頻率
            tick_update_status = "從未更新"
            if last_tick_update:
                seconds_since_update = (now - last_tick_update).total_seconds()
                if seconds_since_update < 60:
                    tick_update_status = f"{int(seconds_since_update)} 秒前"
                elif seconds_since_update < 3600:
                    tick_update_status = f"{int(seconds_since_update / 60)} 分鐘前"
                else:
                    tick_update_status = f"{int(seconds_since_update / 3600)} 小時前"

            entry_info = {
                "symbol": symbol,
                "sub_symbol": sub_symbol,
                "timeframe": "1m (tick 實時更新)",
                "subscribed": subscribed,
                "kbar_count": len(kbars_1m.kbars) if kbars_1m.kbars else 0,
                "last_api_sync": last_api_sync.strftime("%Y-%m-%d %H:%M:%S")
                if last_api_sync
                else "N/A",
                "last_tick_update": tick_update_status,
                "latest_kbar_time": kbars_1m.kbars[-1].time.strftime("%Y-%m-%d %H:%M")
                if kbars_1m.kbars and len(kbars_1m.kbars) > 0
                else "N/A",
            }
            stats["entries"].append(entry_info)

        return stats

    def _format_kbar_data(
        self, kbars, symbol: str = "", timeframe: str = "1m"
    ) -> KBarList:
        """格式化K線資料"""
        kbar_list = []

        # Shioaji API 的 kbars 有 Volume 欄位
        volumes = getattr(kbars, "Volume", None) or [0] * len(kbars.ts)

        for ts, open, high, low, close, volume in zip(
            kbars.ts,
            kbars.Open,
            kbars.High,
            kbars.Low,
            kbars.Close,
            volumes,
            strict=False,
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
                    volume=int(volume) if volume is not None else 0,
                )
            )

        return KBarList(kbars=kbar_list, symbol=symbol, timeframe=timeframe)

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

    # 建立API客戶端
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        config.ca_cert_path,
        config.ca_password,
        simulation=config.simulation,
    )
    market_service = MarketService(api_client)
    symbol = "MXF"
    sub_symbol = "MXF202512"

    print("\n" + "=" * 60)
    print("📊 測試 MarketService 核心邏輯")
    print("=" * 60)
    print(f"⚙️ 當前設定: simulation={config.simulation}")
    print("策略: 訂閱商品 → Tick 實時更新 → 查詢緩存零API調用")
    print("=" * 60)

    # Step 1: 訂閱商品（自動初始化 30 天數據）
    print("\n[1/5] 訂閱商品並初始化緩存...")
    market_service.subscribe_symbol(symbol, sub_symbol, init_days=30)
    print("✅ 訂閱完成")

    # Step 2: 獲取即時報價
    print("\n[2/5] 獲取即時報價...")
    quote = market_service.get_realtime_quote(symbol, sub_symbol)
    if quote:
        print(f"✅ 當前價格: {quote.price}")
    else:
        print("⚠️  等待報價數據...")

    # Step 3: 等待 tick 更新
    print("\n[3/5] 等待 5 秒，觀察 Tick 實時更新...")
    time.sleep(5)

    # 再次獲取報價
    quote = market_service.get_realtime_quote(symbol, sub_symbol)
    if quote:
        print(f"✅ 更新後價格: {quote.price}")

    # Step 4: 從緩存獲取不同時間尺度的 K 線（零 API 調用）
    print("\n[4/5] 從緩存獲取不同時間尺度 K 線...")
    kbars_30m = market_service.get_futures_kbars_with_timeframe(
        symbol, sub_symbol, "30m", days=15
    )

    print(f"✅ 30分K: {len(kbars_30m.kbars)} 根")

    if len(kbars_30m.kbars) > 0:
        latest = kbars_30m.kbars[-1]
        print(
            f"   最新30分K: {latest.time.strftime('%Y-%m-%d %H:%M')} "
            f"O:{latest.open} H:{latest.high} L:{latest.low} C:{latest.close}"
        )

    api_client.logout()
    print("\n✅ 測試完成")
