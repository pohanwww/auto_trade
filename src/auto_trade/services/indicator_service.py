"""Indicator service for technical indicator calculations.

此服務只負責純粹的技術指標計算，不涉及任何交易邏輯或信號生成。
所有方法都是無副作用的純函數風格。
"""

from datetime import time

import pandas as pd

from auto_trade.models import (
    Action,
    EMAData,
    EMAList,
    KBar,
    KBarList,
    MACDData,
    MACDList,
)


class IndicatorService:
    """技術指標計算服務

    提供各種技術指標的計算功能：
    - EMA (指數移動平均線)
    - MACD (移動平均收斂/發散指標)
    - 金叉/死叉檢測
    - VWAP (成交量加權平均價)
    - ADX (平均方向指數)
    - ATR (平均真實範圍)
    - RVOL (相對成交量)
    - K棒型態識別
    """

    def __init__(self) -> None:
        self._ema_cache: dict[tuple[str, str, int], list[float]] = {}

    def _get_ema_array(self, kbar_list: KBarList, period: int) -> list[float]:
        """取得 EMA 原始 float 陣列（含增量快取）

        注意：最後一根 K 棒可能是正在形成的 bar（close 會持續變動），
        因此每次都重新計算最後一個 EMA 值，只快取 [:-1] 的部分。
        """
        cache_key = (kbar_list.symbol, kbar_list.timeframe, period)
        cached = self._ema_cache.get(cache_key)
        n = len(kbar_list)

        k = 2.0 / (period + 1)

        if cached is not None and len(cached) >= n:
            # 同樣的 bar 數量 — 重算最後一根（forming bar 的 close 可能已變動）
            if n > 1:
                cached[n - 1] = float(kbar_list[n - 1].close) * k + cached[n - 2] * (1 - k)
            else:
                cached[0] = float(kbar_list[0].close)
            return cached[:n]

        if cached is not None and len(cached) > 0:
            # 有新 bar 加入：先修正之前最後一根（它之前可能是 forming bar，現在已完成）
            prev_n = len(cached)
            if prev_n > 1:
                cached[prev_n - 1] = float(kbar_list[prev_n - 1].close) * k + cached[prev_n - 2] * (1 - k)
            else:
                cached[0] = float(kbar_list[0].close)
            start = prev_n
            arr = cached
        else:
            arr = [float(kbar_list[0].close)]
            start = 1

        for i in range(start, n):
            price = float(kbar_list[i].close)
            arr.append(price * k + arr[-1] * (1 - k))

        self._ema_cache[cache_key] = arr
        return arr

    def calculate_ema(self, kbar_list: KBarList, period: int) -> EMAList:
        """計算指數移動平均線 (EMA)

        Args:
            kbar_list: K線資料列表
            period: EMA 週期

        Returns:
            EMAList: 計算好的 EMA 資料列表
        """
        arr = self._get_ema_array(kbar_list, period)
        n = len(kbar_list)

        ema_data = [
            EMAData(time=kbar_list[i].time, ema_value=arr[i])
            for i in range(n)
        ]

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
        """計算 MACD 指標

        Args:
            kbar_list: K線資料列表
            fast_period: 快線 EMA 週期（預設 12）
            slow_period: 慢線 EMA 週期（預設 26）
            signal_period: 信號線 EMA 週期（預設 9）

        Returns:
            MACDList: 計算好的 MACD 資料列表
        """
        # 計算快線和慢線 EMA
        ema_fast = self.calculate_ema(kbar_list, fast_period)
        ema_slow = self.calculate_ema(kbar_list, slow_period)

        # 計算 MACD 線
        macd_line_values = []
        for i in range(len(kbar_list)):
            macd_value = ema_fast[i].ema_value - ema_slow[i].ema_value
            macd_line_values.append(macd_value)

        # 計算信號線 (MACD 線的 EMA)
        macd_series = pd.Series(macd_line_values)
        signal_line_values = macd_series.ewm(span=signal_period).mean()

        # 計算柱狀圖
        histogram_values = macd_series - signal_line_values

        # 建立 MACD 資料列表
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

        if not is_golden_cross:
            return False

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

        if not is_death_cross:
            return False

        if min_acceleration is None:
            return True

        # 計算加速度（趨勢變化率）
        previous_diff = previous.macd_line - previous.signal_line
        current_diff = current.macd_line - current.signal_line
        acceleration = current_diff - previous_diff

        return abs(acceleration) >= min_acceleration

    # ──────────────────────────────────────────────
    # VWAP (Volume Weighted Average Price)
    # ──────────────────────────────────────────────

    def calculate_session_vwap(
        self,
        kbar_list: KBarList,
        session_start: time,
        session_end: time,
    ) -> float | None:
        """計算當天日盤的 Session VWAP（每日重置）

        VWAP = Σ(TP × Volume) / Σ(Volume)
        TP = (High + Low + Close) / 3

        Args:
            kbar_list: K線資料列表
            session_start: 日盤開始時間（如 08:45）
            session_end: 日盤結束時間（如 13:45）

        Returns:
            float | None: VWAP 值，無資料返回 None
        """
        if not kbar_list or len(kbar_list) == 0:
            return None

        latest_date = kbar_list[-1].time.date()
        total_tp_vol = 0.0
        total_vol = 0.0

        for kbar in kbar_list.kbars:
            if (
                kbar.time.date() == latest_date
                and session_start <= kbar.time.time() < session_end
            ):
                tp = (kbar.high + kbar.low + kbar.close) / 3.0
                vol = float(kbar.volume) if kbar.volume > 0 else 1.0
                total_tp_vol += tp * vol
                total_vol += vol

        if total_vol == 0:
            return None

        return total_tp_vol / total_vol

    # ──────────────────────────────────────────────
    # ADX (Average Directional Index)
    # ──────────────────────────────────────────────

    def calculate_adx(
        self, kbar_list: KBarList, period: int = 14
    ) -> float | None:
        """計算 ADX（平均方向指數）

        ADX > 25：趨勢明確（適合做突破）
        ADX < 20：盤整環境（突破容易失敗）

        使用 Wilder 平滑法（alpha = 1/period）。

        Args:
            kbar_list: K線資料列表
            period: ADX 週期（預設 14）

        Returns:
            float | None: ADX 值 (0-100)，資料不足返回 None
        """
        if len(kbar_list) < period * 3:
            return None

        highs = pd.Series([float(k.high) for k in kbar_list.kbars])
        lows = pd.Series([float(k.low) for k in kbar_list.kbars])
        closes = pd.Series([float(k.close) for k in kbar_list.kbars])

        # True Range
        tr1 = highs - lows
        tr2 = (highs - closes.shift(1)).abs()
        tr3 = (lows - closes.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # +DM / -DM
        up_move = highs.diff()
        down_move = -lows.diff()
        plus_dm = pd.Series(0.0, index=highs.index)
        minus_dm = pd.Series(0.0, index=highs.index)

        mask_plus = (up_move > down_move) & (up_move > 0)
        mask_minus = (down_move > up_move) & (down_move > 0)
        plus_dm[mask_plus] = up_move[mask_plus]
        minus_dm[mask_minus] = down_move[mask_minus]

        # Wilder 平滑
        alpha = 1.0 / period
        atr = tr.ewm(alpha=alpha, min_periods=period).mean()
        smooth_plus = plus_dm.ewm(alpha=alpha, min_periods=period).mean()
        smooth_minus = minus_dm.ewm(alpha=alpha, min_periods=period).mean()

        # +DI / -DI
        plus_di = 100.0 * smooth_plus / atr
        minus_di = 100.0 * smooth_minus / atr

        # DX → ADX
        di_sum = plus_di + minus_di
        di_sum = di_sum.replace(0, float("nan"))
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        adx = dx.ewm(alpha=alpha, min_periods=period).mean()

        val = adx.iloc[-1]
        return float(val) if not pd.isna(val) else None

    # ──────────────────────────────────────────────
    # ATR (Average True Range)
    # ──────────────────────────────────────────────

    def calculate_atr(
        self, kbar_list: KBarList, period: int = 14
    ) -> float | None:
        """計算 ATR（平均真實範圍）

        衡量市場波動度，用於動態設定 SL/TP/TS 距離。

        Args:
            kbar_list: K線資料列表
            period: ATR 週期（預設 14）

        Returns:
            float | None: ATR 值（點數），資料不足返回 None
        """
        if len(kbar_list) < period + 1:
            return None

        highs = pd.Series([float(k.high) for k in kbar_list.kbars])
        lows = pd.Series([float(k.low) for k in kbar_list.kbars])
        closes = pd.Series([float(k.close) for k in kbar_list.kbars])

        tr1 = highs - lows
        tr2 = (highs - closes.shift(1)).abs()
        tr3 = (lows - closes.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        alpha = 1.0 / period
        atr = tr.ewm(alpha=alpha, min_periods=period).mean()

        val = atr.iloc[-1]
        return float(val) if not pd.isna(val) else None

    # ──────────────────────────────────────────────
    # RVOL (Relative Volume)
    # ──────────────────────────────────────────────

    def calculate_rvol(
        self, kbar_list: KBarList, lookback: int = 20
    ) -> float | None:
        """計算相對成交量 RVOL = 當前量 / 平均量

        RVOL > 1.3：放量突破，有效性高
        RVOL < 0.5：縮量，突破大概率假的

        使用最新已確認的 K 棒（[-1]）。

        Args:
            kbar_list: K線資料列表
            lookback: 計算平均量的回溯期數（預設 20）

        Returns:
            float | None: RVOL 比率，資料不足返回 None
        """
        if len(kbar_list) < lookback + 1:
            return None

        recent = kbar_list.get_latest(lookback + 1)
        current_vol = float(recent[-1].volume)
        if current_vol <= 0:
            return None

        hist_vols = [float(bar.volume) for bar in recent[:-1] if bar.volume > 0]
        if not hist_vols:
            return None

        avg_vol = sum(hist_vols) / len(hist_vols)
        if avg_vol <= 0:
            return None

        return current_vol / avg_vol

    # ──────────────────────────────────────────────
    # Volume Percentile (legacy)
    # ──────────────────────────────────────────────

    def volume_percentile(
        self, kbar_list: KBarList, lookback: int = 100
    ) -> float | None:
        """計算當前 K 棒成交量的百分位排名

        看「這根 bar 的成交量在最近 N 根中排第幾」。
        例如回傳 0.75 表示當前量大於 75% 的歷史 K 棒。

        使用 [-2] 確保檢查的是已確認的 K 線，而非正在形成的 K 線。

        Args:
            kbar_list: K 線資料列表（需包含 volume）
            lookback: 回看 K 棒數量（預設 100）

        Returns:
            float | None: 百分位排名 (0.0 ~ 1.0)，資料不足返回 None
        """
        if len(kbar_list) < lookback + 1:
            return None

        # 取最近 lookback+1 根（含當前已確認的那根）
        recent = kbar_list.get_latest(lookback + 1)
        # 已確認的最新 K 棒
        current_bar = recent[-2]
        current_volume = current_bar.volume

        if current_volume == 0:
            return None

        # 用 [-lookback-1:-2] 做為歷史比較基準（不含當前那根）
        history_volumes = [bar.volume for bar in recent[:-2] if bar.volume > 0]
        if not history_volumes:
            return None

        # 計算百分位：有多少比例的歷史量 < 當前量
        count_below = sum(1 for v in history_volumes if v < current_volume)
        percentile = count_below / len(history_volumes)
        return percentile

    # ──────────────────────────────────────────────
    # Bollinger Bands
    # ──────────────────────────────────────────────

    @staticmethod
    def calculate_bollinger_bands(
        kbar_list: KBarList,
        period: int = 20,
        num_std: float = 3.0,
    ) -> tuple[float, float, float] | None:
        """計算布林通道（回傳最新一根的值）

        Args:
            kbar_list: K 線資料
            period: 均線週期
            num_std: 標準差倍數

        Returns:
            (upper, middle, lower) 或 None（資料不足時）
        """
        if len(kbar_list) < period:
            return None

        closes = [float(k.close) for k in kbar_list.kbars[-period:]]
        middle = sum(closes) / period
        variance = sum((c - middle) ** 2 for c in closes) / period
        std = variance**0.5
        upper = middle + num_std * std
        lower = middle - num_std * std
        return upper, middle, lower

    # ──────────────────────────────────────────────
    # K 棒力道 (Candle Strength)
    # ──────────────────────────────────────────────

    @staticmethod
    def candle_strength(kbar: KBar) -> float:
        """計算 K 棒收盤位置強度

        回傳收盤價在 K 棒區間中的相對位置（0.0 ~ 1.0）。
        1.0 = 收在最高點（極度偏多），0.0 = 收在最低點（極度偏空）。

        做多確認：strength > 0.7（收在上方 30%）
        做空確認：strength < 0.3（收在下方 30%）

        Args:
            kbar: K 棒數據

        Returns:
            float: 0.0 ~ 1.0 的強度值
        """
        bar_range = float(kbar.high - kbar.low)
        if bar_range <= 0:
            return 0.5
        return (float(kbar.close) - float(kbar.low)) / bar_range

    # ──────────────────────────────────────────────
    # K 棒型態
    # ──────────────────────────────────────────────

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
            lower_shadow = min(kbar.open, kbar.close) - kbar.low
            if lower_shadow <= 50:
                return False

            if kbar.close <= kbar.open:
                return lower_shadow >= body_length * 2
            return lower_shadow >= body_length * 1.5

        elif direction == Action.Sell:
            upper_shadow = kbar.high - max(kbar.open, kbar.close)
            if upper_shadow <= 50:
                return False

            if kbar.close >= kbar.open:
                return upper_shadow >= body_length * 2
            return upper_shadow >= body_length * 1.5

        return False

    # ──────────────────────────────────────────────
    # MA Convergence Detection (均線糾纏)
    # ──────────────────────────────────────────────

    def detect_ma_convergence(
        self,
        kbar_list: KBarList,
        periods: list[int] | None = None,
        threshold_pct: float = 0.3,
        min_bars: int = 3,
        max_bars_after: int = 2,
        allow_entry_during_convergence: bool = False,
    ) -> dict:
        """檢測多條 EMA 是否處於糾纏（收斂）狀態

        糾纏定義：所有 EMA 之間的最大 spread < 價格的 threshold_pct%
        連續 min_bars 根 K 棒都滿足即為有效糾纏。

        一個糾纏區只產生一個進場機會：掃描糾纏區結束到當前 K 棒之間，
        若已存在完整突破信號（突破 + spread expanding），則當前不是第一進場點。

        Args:
            kbar_list: K 線資料列表
            periods: EMA 週期列表（預設 [5, 10, 20, 60]）
            threshold_pct: 最大 spread 占價格的百分比（預設 0.3%）
            min_bars: 最少持續幾根 K 棒算有效糾纏（預設 3）
            max_bars_after: 糾纏結束後最多幾根 K 棒內可觸發進場（預設 2）

        Returns:
            dict:
                converged: 最新 K 棒是否處於糾纏狀態
                converged_bars: 目前已連續糾纏的 K 棒數
                was_converged: 是否有有效糾纏（≥ min_bars）
                spread_pct: 最新一根的 spread 占價格百分比
                ema_values: 最新一根各 EMA 值 {period: value}
                breakout_long: 做多突破（時效內 + 第一進場點）
                breakout_short: 做空突破（時效內 + 第一進場點）
                spread_expanding: spread 正在擴張（相比前一根）
                convergence_ema_max: 糾纏區最高 EMA 值
                convergence_ema_min: 糾纏區最低 EMA 值
                bars_since_convergence: 距離上次有效糾纏的 K 棒數
        """
        if periods is None:
            periods = [5, 10, 20, 60]

        max_period = max(periods)
        if len(kbar_list) < max_period + min_bars + 5:
            return {
                "converged": False,
                "converged_bars": 0,
                "was_converged": False,
                "spread_pct": 0.0,
                "ema_values": {},
                "breakout_long": False,
                "breakout_short": False,
                "spread_expanding": False,
                "convergence_ema_max": 0.0,
                "convergence_ema_min": 0.0,
                "bars_since_convergence": 9999,
            }

        ema_arrays = {p: self._get_ema_array(kbar_list, p) for p in periods}

        n = len(kbar_list)
        lookback = min(n, 100)

        converged_count = 0
        prev_spread_pct = 0.0
        peak_converged_count = 0
        convergence_ema_max = 0.0
        convergence_ema_min = 0.0
        last_convergence_idx = -1

        # 跳過最後一根（可能為未完成的 forming bar），評估到 n-2
        eval_end = n - 1
        for i in range(n - lookback, eval_end):
            price = float(kbar_list[i].close)
            if price <= 0:
                converged_count = 0
                continue

            ema_vals = [ema_arrays[p][i] for p in periods]
            spread = max(ema_vals) - min(ema_vals)
            sp = (spread / price) * 100.0

            if sp < threshold_pct:
                converged_count += 1
            else:
                converged_count = 0

            if converged_count >= min_bars:
                last_convergence_idx = i

            if i < eval_end - 1:
                prev_spread_pct = sp
                if converged_count >= min_bars:
                    peak_converged_count = converged_count
                    convergence_ema_max = max(ema_vals)
                    convergence_ema_min = min(ema_vals)

        # 已確認的最新 K 棒（跳過 [-1] 未完成 bar，用 [-2]）
        eval_idx = n - 2
        latest_price = float(kbar_list[eval_idx].close)
        latest_ema_vals = {p: ema_arrays[p][eval_idx] for p in periods}
        all_ema = list(latest_ema_vals.values())
        latest_spread = max(all_ema) - min(all_ema)
        latest_spread_pct = (latest_spread / latest_price) * 100.0 if latest_price > 0 else 0.0

        is_converged = latest_spread_pct < threshold_pct
        was_converged = peak_converged_count >= min_bars

        bars_since = eval_idx - last_convergence_idx if last_convergence_idx >= 0 else 9999
        if allow_entry_during_convergence:
            in_window = was_converged and bars_since <= max_bars_after
        else:
            in_window = was_converged and bars_since >= 1 and bars_since <= max_bars_after

        breakout_long = (
            in_window
            and latest_price > max(all_ema)
        )
        breakout_short = (
            in_window
            and latest_price < min(all_ema)
        )
        spread_expanding = latest_spread_pct > prev_spread_pct

        return {
            "converged": is_converged,
            "converged_bars": converged_count,
            "was_converged": was_converged,
            "spread_pct": latest_spread_pct,
            "ema_values": latest_ema_vals,
            "breakout_long": breakout_long,
            "breakout_short": breakout_short,
            "spread_expanding": spread_expanding,
            "convergence_ema_max": convergence_ema_max,
            "convergence_ema_min": convergence_ema_min,
            "bars_since_convergence": bars_since,
        }

    # ──────────────────────────────────────────────
    # Swing High / Low Detection
    # ──────────────────────────────────────────────

    @staticmethod
    def find_swing_levels(
        kbar_list: KBarList,
        period: int = 5,
        lookback_bars: int = 200,
        dedup_tolerance: int = 20,
    ) -> tuple[list[int], list[int]]:
        """Detect swing high/low levels from K-bar data.

        A swing high at bar[i] means bar[i].high is the highest among
        the surrounding ``period`` bars on each side.  Swing low is the
        mirror for bar[i].low.

        Args:
            kbar_list: K-bar data (uses the last ``lookback_bars`` bars)
            period: number of bars on each side to compare
            lookback_bars: how many recent bars to scan
            dedup_tolerance: merge levels within this many points

        Returns:
            (swing_highs, swing_lows) -- each a sorted list of unique
            price levels (ascending).
        """
        bars = kbar_list.kbars[-lookback_bars:] if len(kbar_list) > lookback_bars else kbar_list.kbars
        n = len(bars)
        if n < period * 2 + 1:
            return [], []

        raw_highs: list[int] = []
        raw_lows: list[int] = []

        for i in range(period, n - period):
            h = int(bars[i].high)
            lo = int(bars[i].low)

            is_swing_high = all(
                h >= int(bars[j].high) for j in range(i - period, i)
            ) and all(
                h >= int(bars[j].high) for j in range(i + 1, i + period + 1)
            )
            if is_swing_high:
                raw_highs.append(h)

            is_swing_low = all(
                lo <= int(bars[j].low) for j in range(i - period, i)
            ) and all(
                lo <= int(bars[j].low) for j in range(i + 1, i + period + 1)
            )
            if is_swing_low:
                raw_lows.append(lo)

        def _dedup(levels: list[int], tol: int) -> list[int]:
            if not levels:
                return []
            sorted_levels = sorted(set(levels))
            result = [sorted_levels[0]]
            for lv in sorted_levels[1:]:
                if lv - result[-1] > tol:
                    result.append(lv)
            return result

        return _dedup(raw_highs, dedup_tolerance), _dedup(raw_lows, dedup_tolerance)
