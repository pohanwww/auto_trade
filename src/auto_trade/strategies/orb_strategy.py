"""Opening Range Breakout (ORB) Strategy - 開盤區間突破策略.

日內策略，只在日盤交易（08:45 ~ 13:45）。

=== 三模式進場（State Machine）===
1. Strong Breakout（強突破）：RVOL 高 + K 棒力道強 → 立即進場
2. Pullback Retest（回踩確認）：突破後等回踩 OR 關鍵位 → 確認站穩再進場
3. Sweep-then-Break（掃底突破）：先觸及反向關鍵支撐/壓力，再突破 → 立即進場

=== 狀態機（每方向獨立）===
  IDLE → 等待突破
  IDLE → STRONG_ENTRY（強突破 → 立即進場信號）
  IDLE → SWEEP_ENTRY（掃底後突破 → 立即進場信號）
  IDLE → WAITING_PULLBACK（弱突破 → 等回踩）
  WAITING_PULLBACK → TESTING_LEVEL（價格回到 OR 關鍵位附近）
  WAITING_PULLBACK → FAILED（超時 / 跌破 OR_Mid）
  TESTING_LEVEL → RETEST_ENTRY（反彈確認 → 進場信號）
  TESTING_LEVEL → FAILED（跌破 OR_Mid）

=== Sweep-then-Break 邏輯 ===
  做多：價格先下探 OR_Low / 昨收缺口 → 之後突破 OR_High → 進場
  做空：價格先上攻 OR_High / 昨收缺口 → 之後跌破 OR_Low → 進場

=== 基本流程 ===
1. 開盤區間計算：取開盤後前 N 根 K 棒
2. ADX 環境檢查（可選）
3. 追蹤掃底事件（每根 K 棒更新）
4. 偵測突破 → 分類為強突破、掃底突破、或弱突破
5. 強突破 / 掃底突破：立即進場
6. 弱突破：等回踩 → 確認站穩 → 進場
7. 出場由 PositionManager 管理（OR_Range based）
"""

from datetime import datetime, time, timedelta
from enum import Enum

from auto_trade.models.market import KBarList
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.strategies.base_strategy import BaseStrategy


class BreakoutState(Enum):
    """突破狀態機 - 每個方向（多/空）獨立追蹤"""

    IDLE = "IDLE"
    WAITING_PULLBACK = "WAITING_PULLBACK"
    TESTING_LEVEL = "TESTING_LEVEL"


class SessionOHLC:
    """某一時段的 OHLC 資料"""

    __slots__ = ("open", "high", "low", "close")

    def __init__(self, open_: int, high: int, low: int, close: int):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close

    def __repr__(self) -> str:
        return f"O={self.open} H={self.high} L={self.low} C={self.close}"


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout 策略 - 雙模式進場

    Attributes:
        or_bars: 計算開盤區間的 K 棒數量（預設 3 根 = 15 分鐘 @5m）
        or_start_time: 日盤開始時間
        entry_end_time: 最後可進場時間
        session_end_time: 日盤結束時間
        --- 強突破閾值 ---
        strong_rvol: RVOL 門檻（>= 此值視為強突破）
        strong_candle: K 棒力道門檻（做多 >= 此值 / 做空 <= 1-此值）
        --- 回踩確認參數 ---
        retest_tolerance_pct: 回踩容忍區間（OR_Range 的百分比）
        pullback_timeout_bars: 等待回踩的最大 K 棒數
        min_bounce_strength: 反彈 K 棒的最低力道
        --- 出場參數（OR_Range based）---
        tp_multiplier: 停利 = OR_Range × 此倍數
        ts_start_multiplier: 啟動移停 = OR_Range × 此倍數
        ts_distance_ratio: 移停距離 = OR_Range × 此比率
        --- 可選過濾 ---
        long_only: 只做多（不做空）
        use_vwap_filter / adx_threshold
    """

    def __init__(
        self,
        indicator_service: IndicatorService,
        or_bars: int = 3,
        or_start_time: str = "08:45",
        entry_end_time: str = "12:30",
        session_end_time: str = "13:45",
        # --- 強突破閾值 ---
        strong_rvol: float = 1.5,
        strong_candle: float = 0.7,
        # --- 回踩確認參數 ---
        retest_tolerance_pct: float = 0.3,
        pullback_timeout_bars: int = 12,
        min_bounce_strength: float = 0.55,
        # --- 出場參數 ---
        tp_multiplier: float = 2.0,
        ts_start_multiplier: float = 1.0,
        ts_distance_ratio: float = 0.5,
        # --- 可選過濾 ---
        long_only: bool = False,
        use_vwap_filter: bool = False,
        adx_threshold: float | None = None,
        adx_period: int = 14,
        # --- 前日 OHLC 過濾 ---
        use_prev_pressure_filter: bool = False,
        min_pressure_space_pct: float = 1.0,
        use_prev_direction_filter: bool = False,
        # --- 階梯式壓力線移停 ---
        use_key_level_trailing: bool = False,
        key_level_buffer: int = 10,
        key_level_min_profit_pct: float = 0.0,
        key_level_min_distance_pct: float = 0.0,
        # --- 壓力線停利 ---
        use_key_level_tp: bool = False,
        key_level_tp_min_pct: float = 0.5,
        # --- 最高壓力線停利（搭配 key_level_trailing 使用）---
        use_key_level_tp_max: bool = False,
        # --- 動能衰竭停利 ---
        use_momentum_exit: bool = False,
        momentum_min_profit_pct: float = 1.0,
        momentum_lookback: int = 5,
        momentum_weak_threshold: float = 0.45,
        momentum_min_weak_bars: int = 3,
        # --- 固定停利 + 壓力線取大 ---
        fixed_tp_points: int = 0,
        # --- 每日進場上限 ---
        max_entries_per_day: int = 1,
        # --- EMA 方向過濾 ---
        use_ema_direction: bool = False,
        ema_direction_period: int = 200,
        # --- Sweep-then-Break（掃底突破）---
        use_sweep_entry: bool = False,
        sweep_tolerance_pct: float = 0.1,
        # --- RVOL 計算 ---
        rvol_lookback: int = 20,
        **kwargs,
    ):
        super().__init__(indicator_service, name="ORB Strategy")
        self.or_bars = or_bars
        self.or_start_time = self._parse_time(or_start_time)
        self.entry_end_time = self._parse_time(entry_end_time)
        self.session_end_time = self._parse_time(session_end_time)

        # 強突破閾值
        self.strong_rvol = strong_rvol
        self.strong_candle = strong_candle

        # 回踩確認參數
        self.retest_tolerance_pct = retest_tolerance_pct
        self.pullback_timeout_bars = pullback_timeout_bars
        self.min_bounce_strength = min_bounce_strength

        # 出場參數
        self.tp_multiplier = tp_multiplier
        self.ts_start_multiplier = ts_start_multiplier
        self.ts_distance_ratio = ts_distance_ratio

        # 可選過濾
        self.long_only = long_only
        self.use_vwap_filter = use_vwap_filter
        self.adx_threshold = adx_threshold
        self.adx_period = adx_period

        # 前日 OHLC 過濾
        self.use_prev_pressure_filter = use_prev_pressure_filter
        self.min_pressure_space_pct = min_pressure_space_pct
        self.use_prev_direction_filter = use_prev_direction_filter

        # 階梯式壓力線移停
        self.use_key_level_trailing = use_key_level_trailing
        self.key_level_buffer = key_level_buffer
        self.key_level_min_profit_pct = key_level_min_profit_pct
        self.key_level_min_distance_pct = key_level_min_distance_pct

        # 壓力線停利
        self.use_key_level_tp = use_key_level_tp
        self.key_level_tp_min_pct = key_level_tp_min_pct
        self.use_key_level_tp_max = use_key_level_tp_max

        # 動能衰竭停利
        self.use_momentum_exit = use_momentum_exit
        self.momentum_min_profit_pct = momentum_min_profit_pct
        self.momentum_lookback = momentum_lookback
        self.momentum_weak_threshold = momentum_weak_threshold
        self.momentum_min_weak_bars = momentum_min_weak_bars

        # 固定停利（> 0 時啟用，與壓力線 TP 取 max）
        self.fixed_tp_points = fixed_tp_points
        # 每日進場上限（每方向分開計算）
        self.max_entries_per_day = max_entries_per_day

        # EMA 方向過濾
        self.use_ema_direction = use_ema_direction
        self.ema_direction_period = ema_direction_period

        # Sweep-then-Break
        self.use_sweep_entry = use_sweep_entry
        self.sweep_tolerance_pct = sweep_tolerance_pct

        self.rvol_lookback = rvol_lookback

        # === 每日狀態（每天重置）===
        self._current_date: datetime | None = None
        self._or_high: int | None = None
        self._or_low: int | None = None
        self._or_mid: int | None = None
        self._or_range: int | None = None
        self._or_calculated: bool = False
        self._long_trades_today: int = 0
        self._short_trades_today: int = 0

        # 狀態機（每方向獨立）
        self._long_state: BreakoutState = BreakoutState.IDLE
        self._short_state: BreakoutState = BreakoutState.IDLE
        self._long_bars_since_breakout: int = 0
        self._short_bars_since_breakout: int = 0
        self._long_breakout_price: int | None = None
        self._short_breakout_price: int | None = None

        # 前日日夜盤 OHLC
        self._prev_day: SessionOHLC | None = None
        self._prev_night: SessionOHLC | None = None

        # Sweep-then-Break 追蹤
        self._swept_low: bool = False
        self._swept_high: bool = False
        self._swept_low_level: str | None = None
        self._swept_high_level: str | None = None

        # 每日快取
        self._daily_adx: float | None = None
        self._daily_direction: str = "both"  # "long", "short", "both"

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """解析 HH:MM 格式的時間字串"""
        h, m = map(int, time_str.split(":"))
        return time(h, m)

    def _reset_daily_state(self) -> None:
        """重置每日狀態"""
        self._or_high = None
        self._or_low = None
        self._or_mid = None
        self._or_range = None
        self._or_calculated = False
        self._long_trades_today = 0
        self._short_trades_today = 0

        # 狀態機重置
        self._long_state = BreakoutState.IDLE
        self._short_state = BreakoutState.IDLE
        self._long_bars_since_breakout = 0
        self._short_bars_since_breakout = 0
        self._long_breakout_price = None
        self._short_breakout_price = None

        # Sweep 追蹤
        self._swept_low = False
        self._swept_high = False
        self._swept_low_level = None
        self._swept_high_level = None

        # 前日資料
        self._prev_day = None
        self._prev_night = None
        self._daily_adx = None
        self._daily_direction = "both"

    def _is_day_session(self, bar_time: datetime) -> bool:
        """判斷是否在日盤時段"""
        t = bar_time.time()
        return self.or_start_time <= t < self.session_end_time

    def _is_in_trading_window(self, bar_time: datetime) -> bool:
        """判斷是否在可進場的交易時段"""
        t = bar_time.time()
        return self._or_calculated and t <= self.entry_end_time

    # ──────────────────────────────────────────────
    # 開盤區間 & 前日 OHLC
    # ──────────────────────────────────────────────

    def _try_calculate_or(self, kbar_list: KBarList) -> bool:
        """嘗試計算開盤區間，同時計算 ADX 和前日 OHLC"""
        if self._current_date is None:
            return False

        today_day_kbars = []
        for kbar in kbar_list.kbars:
            if (
                kbar.time.date() == self._current_date.date()
                and kbar.time.time() >= self.or_start_time
                and kbar.time.time() < self.session_end_time
            ):
                today_day_kbars.append(kbar)

        if len(today_day_kbars) < self.or_bars:
            return False

        or_kbars = today_day_kbars[: self.or_bars]
        self._or_high = int(max(k.high for k in or_kbars))
        self._or_low = int(min(k.low for k in or_kbars))
        self._or_mid = (self._or_high + self._or_low) // 2
        self._or_range = self._or_high - self._or_low
        self._or_calculated = True

        # 前日日夜盤 OHLC
        self._calculate_previous_sessions(kbar_list)

        # ADX
        self._daily_adx = self.indicator_service.calculate_adx(
            kbar_list, self.adx_period
        )

        # EMA 方向過濾：開盤價 vs EMA
        if self.use_ema_direction:
            ema_list = self.indicator_service.calculate_ema(
                kbar_list, self.ema_direction_period
            )
            if len(ema_list.ema_data) > 0:
                ema_val = ema_list.ema_data[-1].ema_value
                open_price = or_kbars[0].open
                if open_price >= ema_val:
                    self._daily_direction = "long"
                else:
                    self._daily_direction = "short"

        # 印出資訊
        date_str = self._current_date.strftime("%Y-%m-%d")
        info = (
            f"  ORB [{date_str}]: "
            f"H={self._or_high} L={self._or_low} "
            f"Mid={self._or_mid} Range={self._or_range}"
        )
        if self._daily_adx is not None:
            info += f" | ADX={self._daily_adx:.1f}"
        if self.use_ema_direction:
            info += f" | EMA{self.ema_direction_period}→{self._daily_direction}"
        print(info)

        if self._prev_day:
            print(f"      Prev Day:   {self._prev_day}")
        if self._prev_night:
            print(f"      Prev Night: {self._prev_night}")

        return True

    def _calculate_previous_sessions(self, kbar_list: KBarList) -> None:
        """從歷史 K 棒中計算前日日盤和夜盤的 OHLC"""
        if self._current_date is None:
            return

        today = self._current_date.date()
        night_boundary = time(5, 0)

        day_sessions: dict = {}
        night_sessions: dict = {}

        for kbar in kbar_list.kbars:
            d = kbar.time.date()
            t = kbar.time.time()

            if self.or_start_time <= t < self.session_end_time and d < today:
                day_sessions.setdefault(d, []).append(kbar)
            elif t >= time(15, 0) and d < today:
                night_sessions.setdefault(d, []).append(kbar)
            elif t < night_boundary:
                ns_date = d - timedelta(days=1)
                if ns_date < today:
                    night_sessions.setdefault(ns_date, []).append(kbar)

        if day_sessions:
            latest = max(day_sessions.keys())
            kbars = sorted(day_sessions[latest], key=lambda k: k.time)
            self._prev_day = SessionOHLC(
                open_=int(kbars[0].open),
                high=int(max(k.high for k in kbars)),
                low=int(min(k.low for k in kbars)),
                close=int(kbars[-1].close),
            )

        if night_sessions:
            latest = max(night_sessions.keys())
            kbars = sorted(night_sessions[latest], key=lambda k: k.time)
            self._prev_night = SessionOHLC(
                open_=int(kbars[0].open),
                high=int(max(k.high for k in kbars)),
                low=int(min(k.low for k in kbars)),
                close=int(kbars[-1].close),
            )

    # ──────────────────────────────────────────────
    # Filters（僅保留 VWAP + ADX）
    # ──────────────────────────────────────────────

    def _check_adx_filter(self) -> tuple[bool, str]:
        """ADX 環境過濾：盤整環境不做突破"""
        if self.adx_threshold is None:
            return True, ""
        if self._daily_adx is None:
            return True, "ADX data insufficient"
        if self._daily_adx < self.adx_threshold:
            return False, (
                f"ADX={self._daily_adx:.1f} < {self.adx_threshold} "
                f"(ranging market, skip breakout)"
            )
        return True, f"ADX={self._daily_adx:.1f} OK"

    def _check_vwap_filter(
        self, kbar_list: KBarList, close: float, is_long: bool
    ) -> tuple[bool, str]:
        """VWAP 方向過濾：做多需價格在 VWAP 之上"""
        if not self.use_vwap_filter:
            return True, ""
        vwap = self.indicator_service.calculate_session_vwap(
            kbar_list, self.or_start_time, self.session_end_time
        )
        if vwap is None:
            return True, "VWAP data insufficient"
        if is_long and close <= vwap:
            return False, (
                f"VWAP filter: close({close:.0f}) <= VWAP({vwap:.0f}), "
                f"long rejected"
            )
        if not is_long and close >= vwap:
            return False, (
                f"VWAP filter: close({close:.0f}) >= VWAP({vwap:.0f}), "
                f"short rejected"
            )
        return True, f"VWAP={vwap:.0f} OK"

    # ──────────────────────────────────────────────
    # 前日 OHLC 過濾
    # ──────────────────────────────────────────────

    def _get_combined_prev_high(self) -> int | None:
        """取得前日日盤+夜盤的最高價"""
        highs = []
        if self._prev_day:
            highs.append(self._prev_day.high)
        if self._prev_night:
            highs.append(self._prev_night.high)
        return max(highs) if highs else None

    def _get_combined_prev_low(self) -> int | None:
        """取得前日日盤+夜盤的最低價"""
        lows = []
        if self._prev_day:
            lows.append(self._prev_day.low)
        if self._prev_night:
            lows.append(self._prev_night.low)
        return min(lows) if lows else None

    def _check_pressure_space_filter(
        self, is_long: bool
    ) -> tuple[bool, str]:
        """壓力空間過濾：突破方向上是否有足夠空間

        做多：OR_High 到前日 High 的距離 >= min_pressure_space_pct × OR_Range
              若 OR_High 已經超過前日 High，代表已經突破壓力位，直接通過
        做空：OR_Low 到前日 Low 的距離 >= min_pressure_space_pct × OR_Range
              若 OR_Low 已經低於前日 Low，代表已經突破支撐位，直接通過
        """
        if not self.use_prev_pressure_filter:
            return True, ""
        if self._or_range is None or self._or_range == 0:
            return True, ""

        prev_high = self._get_combined_prev_high()
        prev_low = self._get_combined_prev_low()

        min_space = self.min_pressure_space_pct * self._or_range

        if is_long:
            if prev_high is None:
                return True, "No prev high data"
            if self._or_high >= prev_high:
                return True, (
                    f"OR_High({self._or_high}) >= prev_High({prev_high}), "
                    f"already above resistance"
                )
            space = prev_high - self._or_high
            if space < min_space:
                return False, (
                    f"Pressure space: prev_High({prev_high}) - "
                    f"OR_High({self._or_high}) = {space} < "
                    f"{self.min_pressure_space_pct}x OR_Range({min_space:.0f})"
                )
            return True, f"Pressure space OK: {space} >= {min_space:.0f}"
        else:
            if prev_low is None:
                return True, "No prev low data"
            if self._or_low <= prev_low:
                return True, (
                    f"OR_Low({self._or_low}) <= prev_Low({prev_low}), "
                    f"already below support"
                )
            space = self._or_low - prev_low
            if space < min_space:
                return False, (
                    f"Support space: OR_Low({self._or_low}) - "
                    f"prev_Low({prev_low}) = {space} < "
                    f"{self.min_pressure_space_pct}x OR_Range({min_space:.0f})"
                )
            return True, f"Support space OK: {space} >= {min_space:.0f}"

    def _check_direction_bias_filter(
        self, is_long: bool
    ) -> tuple[bool, str]:
        """方向偏差過濾：今日開盤 vs 前日收盤

        做多：今日 OR 區間中點 > 前日收盤 → 跳空偏多，做多更可靠
        做空：今日 OR 區間中點 < 前日收盤 → 跳空偏空，做空更可靠

        使用前日日盤收盤做判斷（因為最接近今天開盤的參考）
        """
        if not self.use_prev_direction_filter:
            return True, ""
        if self._prev_day is None:
            return True, "No prev day data"
        if self._or_mid is None:
            return True, ""

        prev_close = self._prev_day.close

        if is_long:
            if self._or_mid <= prev_close:
                return False, (
                    f"Direction bias: OR_Mid({self._or_mid}) <= "
                    f"prev_close({prev_close}), bearish gap"
                )
            return True, (
                f"Direction bias OK: OR_Mid({self._or_mid}) > "
                f"prev_close({prev_close})"
            )
        else:
            if self._or_mid >= prev_close:
                return False, (
                    f"Direction bias: OR_Mid({self._or_mid}) >= "
                    f"prev_close({prev_close}), bullish gap"
                )
            return True, (
                f"Direction bias OK: OR_Mid({self._or_mid}) < "
                f"prev_close({prev_close})"
            )

    # ──────────────────────────────────────────────
    # Sweep-then-Break 追蹤
    # ──────────────────────────────────────────────

    def _update_sweep_tracking(self, kbar) -> None:
        """追蹤掃底/掃頂事件

        做多掃底：bar_low 觸及關鍵支撐（OR_Low、昨收缺口）
        做空掃頂：bar_high 觸及關鍵壓力（OR_High、昨收缺口）
        """
        if not self.use_sweep_entry or self._or_low is None:
            return

        bar_low = int(kbar.low)
        bar_high = int(kbar.high)
        or_range = self._or_range or 0
        tol = int(self.sweep_tolerance_pct * or_range)

        if not self._swept_low:
            sweep_targets: list[tuple[str, int]] = [
                ("OR_Low", self._or_low),
            ]
            if self._prev_day and self._prev_day.close < self._or_low:
                sweep_targets.append(("PrevDayClose", self._prev_day.close))
            if self._prev_night and self._prev_night.close < self._or_low:
                sweep_targets.append(("PrevNightClose", self._prev_night.close))

            for name, level in sweep_targets:
                if bar_low <= level + tol:
                    self._swept_low = True
                    self._swept_low_level = name
                    print(
                        f"  📊 Sweep low detected: "
                        f"bar_low({bar_low}) touched {name}({level})"
                    )
                    break

        if not self._swept_high:
            sweep_targets = [
                ("OR_High", self._or_high),
            ]
            if self._prev_day and self._prev_day.close > self._or_high:
                sweep_targets.append(("PrevDayClose", self._prev_day.close))
            if self._prev_night and self._prev_night.close > self._or_high:
                sweep_targets.append(("PrevNightClose", self._prev_night.close))

            for name, level in sweep_targets:
                if bar_high >= level - tol:
                    self._swept_high = True
                    self._swept_high_level = name
                    print(
                        f"  📊 Sweep high detected: "
                        f"bar_high({bar_high}) touched {name}({level})"
                    )
                    break

    # ──────────────────────────────────────────────
    # 突破分類 & 狀態機
    # ──────────────────────────────────────────────

    def _classify_breakout(
        self, kbar_list: KBarList, is_long: bool
    ) -> bool:
        """判斷突破是否為強突破

        強突破條件（兩者皆須滿足）：
        1. RVOL >= strong_rvol
        2. K 棒力道 >= strong_candle（做多）或 <= 1 - strong_candle（做空）

        Returns:
            True = 強突破（立即進場），False = 弱突破（等回踩）
        """
        latest_kbar = kbar_list.get_latest(1)[-1]

        # RVOL 檢查
        rvol = self.indicator_service.calculate_rvol(
            kbar_list, self.rvol_lookback
        )
        rvol_ok = rvol is not None and rvol >= self.strong_rvol

        # K 棒力道檢查
        strength = self.indicator_service.candle_strength(latest_kbar)
        if is_long:
            candle_ok = strength >= self.strong_candle
        else:
            candle_ok = (1.0 - strength) >= self.strong_candle

        return rvol_ok and candle_ok

    def _update_long_state(
        self, kbar_list: KBarList, close: int
    ) -> StrategySignal | None:
        """更新做多方向的狀態機

        Returns:
            StrategySignal if entry signal should be emitted, None otherwise
        """
        if self._long_trades_today >= self.max_entries_per_day or self._or_high is None:
            return None

        or_high = self._or_high
        or_mid = self._or_mid
        or_range = self._or_range
        tolerance = int(self.retest_tolerance_pct * or_range)
        latest_kbar = kbar_list.get_latest(1)[-1]
        bar_time = latest_kbar.time

        if self._long_state == BreakoutState.IDLE:
            # 偵測突破：close > OR_High
            if close > or_high:
                is_strong = self._classify_breakout(kbar_list, is_long=True)
                if is_strong:
                    # 強突破 → 通過 filters 後立即進場
                    reject = self._run_filters(kbar_list, close, is_long=True)
                    if reject:
                        return None
                    self._long_trades_today += 1
                    rvol = self.indicator_service.calculate_rvol(
                        kbar_list, self.rvol_lookback
                    )
                    strength = self.indicator_service.candle_strength(
                        latest_kbar
                    )
                    return StrategySignal(
                        signal_type=SignalType.ENTRY_LONG,
                        symbol=kbar_list.symbol,
                        price=float(close),
                        confidence=0.85,
                        reason=(
                            f"ORB Strong Long: close({close}) > "
                            f"OR_High({or_high}), "
                            f"RVOL={rvol:.2f} CS={strength:.2f}"
                        ),
                        timestamp=bar_time,
                        metadata=self._build_entry_metadata(
                            is_long=True, entry_type="strong"
                        ),
                    )
                elif self._swept_low:
                    # 掃底後突破 → 通過 filters 後立即進場
                    reject = self._run_filters(kbar_list, close, is_long=True)
                    if reject:
                        return None
                    self._long_trades_today += 1
                    strength = self.indicator_service.candle_strength(
                        latest_kbar
                    )
                    return StrategySignal(
                        signal_type=SignalType.ENTRY_LONG,
                        symbol=kbar_list.symbol,
                        price=float(close),
                        confidence=0.82,
                        reason=(
                            f"ORB Sweep Long: close({close}) > "
                            f"OR_High({or_high}), "
                            f"swept {self._swept_low_level}, "
                            f"CS={strength:.2f}"
                        ),
                        timestamp=bar_time,
                        metadata=self._build_entry_metadata(
                            is_long=True, entry_type="sweep"
                        ),
                    )
                else:
                    # 弱突破 → 等回踩
                    self._long_state = BreakoutState.WAITING_PULLBACK
                    self._long_bars_since_breakout = 0
                    self._long_breakout_price = close
                    print(
                        f"  📊 Weak Long Breakout @ {close}, "
                        f"waiting for pullback to OR_High({or_high})"
                    )

        elif self._long_state == BreakoutState.WAITING_PULLBACK:
            self._long_bars_since_breakout += 1

            # 失敗：超時
            if self._long_bars_since_breakout > self.pullback_timeout_bars:
                self._long_state = BreakoutState.IDLE
                print(
                    f"  ❌ Long pullback timeout after "
                    f"{self.pullback_timeout_bars} bars"
                )
                return None

            # 失敗：跌破 OR_Mid → 突破無效
            if close < or_mid:
                self._long_state = BreakoutState.IDLE
                print(
                    f"  ❌ Long breakout failed: "
                    f"close({close}) < OR_Mid({or_mid})"
                )
                return None

            # 回踩到位：price 進入 retest zone [OR_High - tol, OR_High + tol]
            if or_high - tolerance <= close <= or_high + tolerance:
                self._long_state = BreakoutState.TESTING_LEVEL
                print(
                    f"  📊 Long pullback to retest zone: "
                    f"close({close}) near OR_High({or_high}) "
                    f"tol=±{tolerance}"
                )

        elif self._long_state == BreakoutState.TESTING_LEVEL:
            self._long_bars_since_breakout += 1

            # 超時保護（仍計入總時間）
            if self._long_bars_since_breakout > self.pullback_timeout_bars:
                self._long_state = BreakoutState.IDLE
                print(
                    f"  ❌ Long retest timeout after "
                    f"{self.pullback_timeout_bars} bars"
                )
                return None

            # 失敗：跌破 OR_Mid
            if close < or_mid:
                self._long_state = BreakoutState.IDLE
                print(
                    f"  ❌ Long retest failed: "
                    f"close({close}) < OR_Mid({or_mid})"
                )
                return None

            # 確認反彈：close 回到 OR_High 之上 + K 棒力道確認
            strength = self.indicator_service.candle_strength(latest_kbar)
            print(
                f"  🔍 Long TESTING: close={close}, or_high={or_high}, "
                f"strength={strength:.3f} (need>={self.min_bounce_strength}), "
                f"bar={self._long_bars_since_breakout}"
            )
            if close > or_high and strength >= self.min_bounce_strength:
                # 通過 filters
                reject = self._run_filters(kbar_list, close, is_long=True)
                if reject:
                    return None
                self._long_trades_today += 1
                self._long_state = BreakoutState.IDLE
                return StrategySignal(
                    signal_type=SignalType.ENTRY_LONG,
                    symbol=kbar_list.symbol,
                    price=float(close),
                    confidence=0.8,
                    reason=(
                        f"ORB Retest Long: close({close}) bounced above "
                        f"OR_High({or_high}), "
                        f"strength={strength:.2f}, "
                        f"bars_waited={self._long_bars_since_breakout}"
                    ),
                    timestamp=bar_time,
                    metadata=self._build_entry_metadata(
                        is_long=True, entry_type="retest"
                    ),
                )

            # 仍在測試區間 → 繼續等待
            # 如果又跌回 retest zone 以下但還在 OR_Mid 以上，回到 WAITING
            if close < or_high - tolerance:
                self._long_state = BreakoutState.WAITING_PULLBACK

        return None

    def _update_short_state(
        self, kbar_list: KBarList, close: int
    ) -> StrategySignal | None:
        """更新做空方向的狀態機

        Returns:
            StrategySignal if entry signal should be emitted, None otherwise
        """
        if self._short_trades_today >= self.max_entries_per_day or self._or_low is None:
            return None

        or_low = self._or_low
        or_mid = self._or_mid
        or_range = self._or_range
        tolerance = int(self.retest_tolerance_pct * or_range)
        latest_kbar = kbar_list.get_latest(1)[-1]
        bar_time = latest_kbar.time

        if self._short_state == BreakoutState.IDLE:
            # 偵測突破：close < OR_Low
            if close < or_low:
                is_strong = self._classify_breakout(kbar_list, is_long=False)
                if is_strong:
                    reject = self._run_filters(
                        kbar_list, close, is_long=False
                    )
                    if reject:
                        return None
                    self._short_trades_today += 1
                    rvol = self.indicator_service.calculate_rvol(
                        kbar_list, self.rvol_lookback
                    )
                    strength = self.indicator_service.candle_strength(
                        latest_kbar
                    )
                    return StrategySignal(
                        signal_type=SignalType.ENTRY_SHORT,
                        symbol=kbar_list.symbol,
                        price=float(close),
                        confidence=0.85,
                        reason=(
                            f"ORB Strong Short: close({close}) < "
                            f"OR_Low({or_low}), "
                            f"RVOL={rvol:.2f} CS={1.0 - strength:.2f}"
                        ),
                        timestamp=bar_time,
                        metadata=self._build_entry_metadata(
                            is_long=False, entry_type="strong"
                        ),
                    )
                elif self._swept_high:
                    # 掃頂後跌破 → 通過 filters 後立即進場
                    reject = self._run_filters(
                        kbar_list, close, is_long=False
                    )
                    if reject:
                        return None
                    self._short_trades_today += 1
                    strength = self.indicator_service.candle_strength(
                        latest_kbar
                    )
                    return StrategySignal(
                        signal_type=SignalType.ENTRY_SHORT,
                        symbol=kbar_list.symbol,
                        price=float(close),
                        confidence=0.82,
                        reason=(
                            f"ORB Sweep Short: close({close}) < "
                            f"OR_Low({or_low}), "
                            f"swept {self._swept_high_level}, "
                            f"CS={1.0 - strength:.2f}"
                        ),
                        timestamp=bar_time,
                        metadata=self._build_entry_metadata(
                            is_long=False, entry_type="sweep"
                        ),
                    )
                else:
                    self._short_state = BreakoutState.WAITING_PULLBACK
                    self._short_bars_since_breakout = 0
                    self._short_breakout_price = close
                    print(
                        f"  📊 Weak Short Breakout @ {close}, "
                        f"waiting for pullback to OR_Low({or_low})"
                    )

        elif self._short_state == BreakoutState.WAITING_PULLBACK:
            self._short_bars_since_breakout += 1

            if self._short_bars_since_breakout > self.pullback_timeout_bars:
                self._short_state = BreakoutState.IDLE
                print(
                    f"  ❌ Short pullback timeout after "
                    f"{self.pullback_timeout_bars} bars"
                )
                return None

            if close > or_mid:
                self._short_state = BreakoutState.IDLE
                print(
                    f"  ❌ Short breakout failed: "
                    f"close({close}) > OR_Mid({or_mid})"
                )
                return None

            # 回踩到位：price 進入 retest zone [OR_Low - tol, OR_Low + tol]
            if or_low - tolerance <= close <= or_low + tolerance:
                self._short_state = BreakoutState.TESTING_LEVEL
                print(
                    f"  📊 Short pullback to retest zone: "
                    f"close({close}) near OR_Low({or_low}) "
                    f"tol=±{tolerance}"
                )

        elif self._short_state == BreakoutState.TESTING_LEVEL:
            self._short_bars_since_breakout += 1

            if self._short_bars_since_breakout > self.pullback_timeout_bars:
                self._short_state = BreakoutState.IDLE
                print(
                    f"  ❌ Short retest timeout after "
                    f"{self.pullback_timeout_bars} bars"
                )
                return None

            if close > or_mid:
                self._short_state = BreakoutState.IDLE
                print(
                    f"  ❌ Short retest failed: "
                    f"close({close}) > OR_Mid({or_mid})"
                )
                return None

            # 確認反彈：close 回到 OR_Low 之下 + K 棒力道確認
            strength = self.indicator_service.candle_strength(latest_kbar)
            bear_strength = 1.0 - strength
            print(
                f"  🔍 Short TESTING: close={close}, or_low={or_low}, "
                f"bear_strength={bear_strength:.3f} (need>={self.min_bounce_strength}), "
                f"bar={self._short_bars_since_breakout}"
            )
            if close < or_low and bear_strength >= self.min_bounce_strength:
                reject = self._run_filters(kbar_list, close, is_long=False)
                if reject:
                    return None
                self._short_trades_today += 1
                self._short_state = BreakoutState.IDLE
                return StrategySignal(
                    signal_type=SignalType.ENTRY_SHORT,
                    symbol=kbar_list.symbol,
                    price=float(close),
                    confidence=0.8,
                    reason=(
                        f"ORB Retest Short: close({close}) bounced below "
                        f"OR_Low({or_low}), "
                        f"strength={bear_strength:.2f}, "
                        f"bars_waited={self._short_bars_since_breakout}"
                    ),
                    timestamp=bar_time,
                    metadata=self._build_entry_metadata(
                        is_long=False, entry_type="retest"
                    ),
                )

            if close > or_low + tolerance:
                self._short_state = BreakoutState.WAITING_PULLBACK

        return None

    def _run_filters(
        self, kbar_list: KBarList, close: int, is_long: bool
    ) -> str | None:
        """執行進場過濾器

        Returns:
            None 如果全部通過，否則返回拒絕原因字串
        """
        # VWAP 方向過濾
        ok, reason = self._check_vwap_filter(kbar_list, float(close), is_long)
        if not ok:
            return reason

        # 前日壓力空間過濾
        ok, reason = self._check_pressure_space_filter(is_long)
        if not ok:
            return reason

        # 前日方向偏差過濾
        ok, reason = self._check_direction_bias_filter(is_long)
        if not ok:
            return reason

        return None

    # ──────────────────────────────────────────────
    # 主要評估邏輯
    # ──────────────────────────────────────────────

    def get_pending_state(self) -> dict | None:
        """回傳 ORB 待觸發的關鍵價位與狀態機狀態"""
        if self._or_high is None:
            return None
        return {
            "or_high": self._or_high,
            "or_low": self._or_low,
            "or_mid": self._or_mid,
            "or_range": self._or_range,
            "long_state": self._long_state.value,
            "short_state": self._short_state.value,
        }

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
    ) -> StrategySignal:
        """評估 ORB 策略 - 雙模式進場

        每根 K 棒進行以下流程：
        1. 日期檢查 → 重置每日狀態
        2. 日盤時段檢查
        3. 計算開盤區間
        4. ADX 環境過濾
        5. 更新多/空方向狀態機
        6. 如有進場信號 → 返回
        """
        if len(kbar_list) < 2:
            return self._hold(symbol, current_price, "Insufficient data")

        latest_kbar = kbar_list.get_latest(1)[-1]
        bar_time = latest_kbar.time

        # 1. 新的一天 → 重置狀態
        if (
            self._current_date is None
            or bar_time.date() != self._current_date.date()
        ):
            self._reset_daily_state()
            self._current_date = bar_time

        # 2. 不在日盤 → HOLD
        if not self._is_day_session(bar_time):
            return self._hold(symbol, current_price, "Outside day session")

        # 3. 尚未計算 OR → 嘗試計算
        if not self._or_calculated:
            self._try_calculate_or(kbar_list)
            return self._hold(
                symbol, current_price, "Calculating opening range"
            )

        # 4. OR Range 太小
        if self._or_range is not None and self._or_range < 10:
            return self._hold(
                symbol, current_price,
                f"OR Range too small ({self._or_range}pts)",
            )

        # 5. ADX 環境過濾
        adx_ok, adx_reason = self._check_adx_filter()
        if not adx_ok:
            return self._hold(symbol, current_price, adx_reason)

        # 6. 不在交易窗口 → HOLD（但仍繼續追蹤狀態機以便印 log）
        in_window = self._is_in_trading_window(bar_time)

        close = int(latest_kbar.close)

        # 7. 追蹤掃底/掃頂事件（每根 K 棒都更新，不受交易窗口限制）
        self._update_sweep_tracking(latest_kbar)

        # 8. 更新狀態機（多/空獨立）
        long_signal = None
        short_signal = None

        if in_window:
            allow_long = self._daily_direction in ("long", "both") or self.long_only
            allow_short = (
                self._daily_direction in ("short", "both")
                and not self.long_only
            )

            if allow_long:
                long_signal = self._update_long_state(kbar_list, close)
                if long_signal is not None:
                    return long_signal

            if allow_short:
                short_signal = self._update_short_state(kbar_list, close)
                if short_signal is not None:
                    return short_signal

        # 狀態摘要
        status_parts = [f"close={close}"]
        status_parts.append(f"OR=[{self._or_low}, {self._or_high}]")
        if self._swept_low:
            status_parts.append(f"SweptLow({self._swept_low_level})")
        if self._swept_high:
            status_parts.append(f"SweptHigh({self._swept_high_level})")
        if self._long_state != BreakoutState.IDLE:
            status_parts.append(
                f"L:{self._long_state.value}"
                f"({self._long_bars_since_breakout}bars)"
            )
        if self._short_state != BreakoutState.IDLE:
            status_parts.append(
                f"S:{self._short_state.value}"
                f"({self._short_bars_since_breakout}bars)"
            )
        if not in_window:
            status_parts.append("(outside trading window)")

        return self._hold(symbol, current_price, " | ".join(status_parts))

    # ──────────────────────────────────────────────
    # Metadata 建構
    # ──────────────────────────────────────────────

    def _build_entry_metadata(
        self, is_long: bool, entry_type: str = "strong"
    ) -> dict:
        """建立進場信號的 metadata（傳遞給 PositionManager）

        Args:
            is_long: 是否做多
            entry_type: "strong" 或 "retest"
        """
        or_range = self._or_range or 0

        tp_points = int(self.tp_multiplier * or_range)

        # 壓力線停利：用最近的壓力線距離作為 TP 目標
        if self.use_key_level_tp and or_range > 0:
            min_tp = int(self.key_level_tp_min_pct * or_range)
            kl_tp = self._compute_key_level_tp(is_long, min_tp)
            if kl_tp is not None:
                tp_points = kl_tp

        # 固定停利：fixed_tp_points > 0 時啟用
        # 與壓力線/倍率 TP 取 max（確保不低於固定下限）
        if self.fixed_tp_points > 0:
            tp_points = max(self.fixed_tp_points, tp_points)

        ts_start_points = int(self.ts_start_multiplier * or_range)
        ts_distance = int(self.ts_distance_ratio * or_range)

        meta: dict = {
            "entry_type": entry_type,
            "or_high": self._or_high,
            "or_low": self._or_low,
            "or_mid": self._or_mid,
            "or_range": or_range,
            "override_start_trailing_stop_points": ts_start_points,
            "override_trailing_stop_points": ts_distance,
            "override_stop_loss_price": self._or_mid,
        }

        # tp_points > 0 才覆寫，否則 fallback 到 PM 的 take_profit_points
        if tp_points > 0:
            meta["override_take_profit_points"] = tp_points

        if self._daily_adx is not None:
            meta["adx"] = round(self._daily_adx, 1)

        if self._prev_day:
            meta["prev_day_ohlc"] = {
                "open": self._prev_day.open,
                "high": self._prev_day.high,
                "low": self._prev_day.low,
                "close": self._prev_day.close,
            }
        if self._prev_night:
            meta["prev_night_ohlc"] = {
                "open": self._prev_night.open,
                "high": self._prev_night.high,
                "low": self._prev_night.low,
                "close": self._prev_night.close,
            }

        # 階梯式壓力線移停：收集關鍵價位
        if self.use_key_level_trailing:
            or_range = self._or_range or 0
            min_dist = int(self.key_level_min_distance_pct * or_range)

            if is_long:
                key_levels: list[int] = []
                if self._prev_day:
                    key_levels.append(self._prev_day.high)
                    key_levels.append(self._prev_day.close)
                if self._prev_night:
                    key_levels.append(self._prev_night.high)
                # 只保留高於 OR_High + 最小距離 的價位，升序排列
                or_high = self._or_high or 0
                threshold = or_high + min_dist
                key_levels = sorted(
                    {lv for lv in key_levels if lv > threshold}
                )
            else:
                key_levels = []
                if self._prev_day:
                    key_levels.append(self._prev_day.low)
                    key_levels.append(self._prev_day.close)
                if self._prev_night:
                    key_levels.append(self._prev_night.low)
                # 只保留低於 OR_Low - 最小距離 的價位，降序排列
                or_low = self._or_low or 999999
                threshold = or_low - min_dist
                key_levels = sorted(
                    {lv for lv in key_levels if lv < threshold}, reverse=True
                )
            if key_levels:
                meta["key_levels"] = key_levels
                meta["key_level_buffer"] = self.key_level_buffer
                # 最低獲利門檻（由 PM 在運行時檢查）
                if self.key_level_min_profit_pct > 0:
                    meta["key_level_min_profit"] = int(
                        self.key_level_min_profit_pct * or_range
                    )

                # 最高壓力線停利：TP 設在最遠的關鍵價位
                if self.use_key_level_tp_max:
                    if is_long:
                        # key_levels 升序，最後一個是最高
                        max_level = key_levels[-1]
                        entry_ref = self._or_high or 0
                        kl_tp_max = max_level - entry_ref
                    else:
                        # key_levels 降序，最後一個是最低
                        min_level = key_levels[-1]
                        entry_ref = self._or_low or 0
                        kl_tp_max = entry_ref - min_level
                    if kl_tp_max > 0:
                        current_tp = meta.get("override_take_profit_points", 0)
                        meta["override_take_profit_points"] = max(
                            current_tp, kl_tp_max
                        )

        # 動能衰竭停利參數
        if self.use_momentum_exit:
            meta["use_momentum_exit"] = True
            meta["momentum_min_profit"] = int(
                self.momentum_min_profit_pct * or_range
            )
            meta["momentum_lookback"] = self.momentum_lookback
            meta["momentum_weak_threshold"] = self.momentum_weak_threshold
            meta["momentum_min_weak_bars"] = self.momentum_min_weak_bars

        return meta

    def _compute_key_level_tp(
        self, is_long: bool, min_tp: int
    ) -> int | None:
        """計算壓力線停利距離

        找到最近的壓力/支撐線，計算距離作為 TP 目標。
        如果距離太近（< min_tp），跳到下一個。如果都不夠遠，回傳 None。
        """
        candidates: list[int] = []
        if self._prev_day:
            candidates.extend([
                self._prev_day.high,
                self._prev_day.close,
                self._prev_day.low,
            ])
        if self._prev_night:
            candidates.extend([
                self._prev_night.high,
                self._prev_night.close,
                self._prev_night.low,
            ])

        if is_long:
            or_high = self._or_high or 0
            # 找所有高於 OR_High 的價位（升序），取最近且 >= min_tp 的
            levels = sorted({lv for lv in candidates if lv > or_high})
            for lv in levels:
                dist = lv - or_high
                if dist >= min_tp:
                    return dist
        else:
            or_low = self._or_low or 999999
            # 找所有低於 OR_Low 的價位（降序），取最近且 >= min_tp 的
            levels = sorted(
                {lv for lv in candidates if lv < or_low}, reverse=True
            )
            for lv in levels:
                dist = or_low - lv
                if dist >= min_tp:
                    return dist

        return None

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _hold(
        self, symbol: str, price: float, reason: str
    ) -> StrategySignal:
        """產生 HOLD 信號"""
        return StrategySignal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            reason=reason,
        )

    def __repr__(self) -> str:
        parts = [f"or_bars={self.or_bars}"]
        parts.append(
            f"OR(tp={self.tp_multiplier}x "
            f"ts_start={self.ts_start_multiplier}x "
            f"ts_dist={self.ts_distance_ratio}x)"
        )
        parts.append(
            f"strong(RVOL>={self.strong_rvol} "
            f"CS>={self.strong_candle})"
        )
        parts.append(
            f"retest(tol={self.retest_tolerance_pct:.0%} "
            f"timeout={self.pullback_timeout_bars}bars "
            f"bounce>={self.min_bounce_strength})"
        )
        if self.long_only:
            parts.append("LongOnly")
        if self.use_vwap_filter:
            parts.append("VWAP")
        if self.adx_threshold is not None:
            parts.append(f"ADX>={self.adx_threshold}")
        if self.use_prev_pressure_filter:
            parts.append(f"Pressure>={self.min_pressure_space_pct}x")
        if self.use_prev_direction_filter:
            parts.append("DirBias")
        if self.use_key_level_trailing:
            kl_info = f"buf={self.key_level_buffer}"
            if self.key_level_min_profit_pct > 0:
                kl_info += f" minP={self.key_level_min_profit_pct}x"
            if self.key_level_min_distance_pct > 0:
                kl_info += f" minD={self.key_level_min_distance_pct}x"
            parts.append(f"KeyLvlTS({kl_info})")
        if self.use_key_level_tp:
            parts.append(f"KeyLvlTP(min={self.key_level_tp_min_pct}x)")
        if self.use_momentum_exit:
            parts.append(
                f"MomExit(P>={self.momentum_min_profit_pct}x "
                f"weak<{self.momentum_weak_threshold} "
                f"N={self.momentum_min_weak_bars})"
            )
        if self.fixed_tp_points > 0:
            parts.append(f"FixTP>={self.fixed_tp_points}")
        if self.use_sweep_entry:
            parts.append(f"Sweep(tol={self.sweep_tolerance_pct:.0%})")
        if self.max_entries_per_day > 1:
            parts.append(f"max{self.max_entries_per_day}x/day")
        return f"ORB({', '.join(parts)})"
