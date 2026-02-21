"""Bollinger Band Mean-Reversion Strategy

布林通道均值回歸策略（日內高頻）。

=== 做多流程 ===
  IDLE → 價格觸及/跌破下軌
  TOUCH_LOWER → 出現止跌 K 棒（下影線長 or 收紅）
  REVERSAL_LONG → 下一根 K 棒突破前一根高點 → 進場做多
  停損：前低下方
  停利：中軌

=== 做空流程 ===
  IDLE → 價格觸及/突破上軌
  TOUCH_UPPER → 出現轉弱 K 棒（上影線長 or 收黑）
  REVERSAL_SHORT → 下一根 K 棒跌破前一根低點 → 進場做空
  停損：前高上方
  停利：中軌

=== 過濾 ===
  - 連續 N 根 K 棒貼著同一軌（強趨勢） → 不進場
"""

from __future__ import annotations

from datetime import time
from enum import Enum
from typing import TYPE_CHECKING

from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.strategies.base_strategy import BaseStrategy

if TYPE_CHECKING:
    from auto_trade.models.market import KBar, KBarList
    from auto_trade.services.indicator_service import IndicatorService


class _BBState(Enum):
    IDLE = "IDLE"
    TOUCH_LOWER = "TOUCH_LOWER"
    REVERSAL_LONG = "REVERSAL_LONG"
    TOUCH_UPPER = "TOUCH_UPPER"
    REVERSAL_SHORT = "REVERSAL_SHORT"


class BollingerStrategy(BaseStrategy):

    def __init__(
        self,
        indicator_service: IndicatorService,
        # --- 布林參數 ---
        bb_period: int = 20,
        bb_std: float = 3.0,
        # --- 交易時段 ---
        session_start_time: str = "09:05",
        entry_end_time: str = "13:00",
        session_end_time: str = "13:45",
        # --- 停利模式 ---
        tp_target: str = "middle",  # "middle", "opposite", "hybrid"
        tp_buffer: int = 5,
        # --- hybrid 模式：TS 腿的移停距離 ---
        hybrid_ts_trail_points: int = 30,
        # --- 停損 buffer ---
        sl_buffer: int = 10,
        # --- 趨勢過濾 ---
        trend_filter_bars: int = 4,
        # --- 方向 ---
        long_only: bool = False,
        short_only: bool = False,
        # --- 每日上限 ---
        max_entries_per_day: int = 99,
        # --- 冷卻 ---
        cooldown_bars: int = 2,
        **kwargs,  # noqa: ARG002
    ):
        super().__init__(indicator_service, name="Bollinger Strategy")

        self.bb_period = bb_period
        self.bb_std = bb_std

        self.session_start_time = self._parse_time(session_start_time)
        self.entry_end_time = self._parse_time(entry_end_time)
        self.session_end_time = self._parse_time(session_end_time)

        self.tp_target = tp_target
        self.tp_buffer = tp_buffer
        self.hybrid_ts_trail_points = hybrid_ts_trail_points
        self.sl_buffer = sl_buffer

        self.trend_filter_bars = trend_filter_bars
        self.long_only = long_only
        self.short_only = short_only
        self.max_entries_per_day = max_entries_per_day
        self.cooldown_bars = cooldown_bars

        # === 每日狀態 ===
        self._current_date = None
        self._state = _BBState.IDLE
        self._trades_today: int = 0
        self._bars_since_exit: int = 999

        # 狀態追蹤用
        self._reversal_bar: KBar | None = None
        self._recent_low: int = 0
        self._recent_high: int = 0

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
    ) -> StrategySignal:
        hold = StrategySignal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            price=current_price,
        )

        if len(kbar_list) < self.bb_period + 2:
            return hold

        latest = kbar_list.kbars[-1]
        bar_time = latest.time
        if bar_time is None:
            return hold

        # 日期切換 → 重置
        today = bar_time.date()
        if self._current_date is None or today != self._current_date:
            self._reset_daily_state()
            self._current_date = today

        t = bar_time.time()

        # 時段過濾（session_start_time == "00:00" 視為不限制）
        if self.session_start_time != time(0, 0):
            if t < self.session_start_time or t >= self.session_end_time:
                return hold
            if t >= self.entry_end_time:
                return hold

        # 每日上限
        if self._trades_today >= self.max_entries_per_day:
            return hold

        # 冷卻
        self._bars_since_exit += 1
        if self._bars_since_exit < self.cooldown_bars:
            return hold

        # === 計算布林通道 ===
        bb = self.indicator_service.calculate_bollinger_bands(
            kbar_list, self.bb_period, self.bb_std
        )
        if bb is None:
            return hold
        upper, middle, lower = bb

        # === 趨勢過濾：連續 N 根貼著同一軌 → 不交易 ===
        if self._is_hugging_band(kbar_list, upper, lower):
            if self._state != _BBState.IDLE:
                self._state = _BBState.IDLE
            return hold

        close = int(latest.close)
        prev = kbar_list.kbars[-2]

        # === 狀態機 ===
        signal = self._update_state(
            latest, prev, close, upper, middle, lower, symbol, current_price
        )
        if signal is not None:
            return signal

        return hold

    def on_position_closed(self) -> None:
        self._bars_since_exit = 0
        self._state = _BBState.IDLE

    # ──────────────────────────────────────────────
    # State Machine
    # ──────────────────────────────────────────────

    def _update_state(
        self,
        bar: KBar,
        prev: KBar,
        close: int,
        upper: float,
        middle: float,
        lower: float,
        symbol: str,
        current_price: float,
    ) -> StrategySignal | None:

        # ── IDLE: 偵測觸及軌道 ──
        if self._state == _BBState.IDLE:
            # 觸及下軌 → 準備做多
            if not self.short_only and close <= lower:
                self._state = _BBState.TOUCH_LOWER
                self._recent_low = int(bar.low)
                self._track_low(prev)
                print(
                    f"  📊 BB: 價格觸及下軌 close={close} <= lower={lower:.0f}"
                )
            # 觸及上軌 → 準備做空
            elif not self.long_only and close >= upper:
                self._state = _BBState.TOUCH_UPPER
                self._recent_high = int(bar.high)
                self._track_high(prev)
                print(
                    f"  📊 BB: 價格觸及上軌 close={close} >= upper={upper:.0f}"
                )
            return None

        # ── TOUCH_LOWER: 等待止跌 K 棒 ──
        if self._state == _BBState.TOUCH_LOWER:
            self._track_low(bar)
            if self._is_reversal_bullish(bar):
                self._state = _BBState.REVERSAL_LONG
                self._reversal_bar = bar
                print(
                    f"  📊 BB: 止跌K棒確認 "
                    f"(strength={self.indicator_service.candle_strength(bar):.2f})"
                )
            elif close > middle:
                self._state = _BBState.IDLE
            return None

        # ── REVERSAL_LONG: 等待突破前一根高點 → 做多 ──
        if self._state == _BBState.REVERSAL_LONG:
            if self._reversal_bar and close > int(self._reversal_bar.high):
                self._state = _BBState.IDLE
                self._trades_today += 1

                sl_price = self._recent_low - self.sl_buffer
                entry = int(current_price)
                mid_dist = int(middle) - self.tp_buffer - entry
                opp_dist = int(upper) - self.tp_buffer - entry

                if self.tp_target == "opposite":
                    tp_dist = opp_dist
                elif self.tp_target == "hybrid":
                    tp_dist = mid_dist
                else:
                    tp_dist = mid_dist

                sl_dist = entry - sl_price

                meta: dict = {
                    "override_stop_loss_price": sl_price,
                    "override_take_profit_points": max(tp_dist, 20),
                    "bb_upper": int(upper),
                    "bb_middle": int(middle),
                    "bb_lower": int(lower),
                }
                if self.tp_target == "hybrid":
                    meta["override_start_trailing_stop_points"] = max(mid_dist, 20)
                    meta["override_trailing_stop_points"] = self.hybrid_ts_trail_points

                tp_label = tp_dist if self.tp_target != "hybrid" else f"{mid_dist}(TP)/{opp_dist}(TS)"
                print(
                    f"🔔 BB 做多信號 @ {entry} | "
                    f"SL={sl_price} TP={tp_label} "
                    f"(risk={sl_dist})"
                )
                return StrategySignal(
                    signal_type=SignalType.ENTRY_LONG,
                    symbol=symbol,
                    price=current_price,
                    reason="BB reversal long",
                    metadata=meta,
                )
            # 如果又跌破下軌，重新等止跌
            if close <= lower:
                self._state = _BBState.TOUCH_LOWER
                self._track_low(bar)
            # 超時回 IDLE
            elif close > middle:
                self._state = _BBState.IDLE
            return None

        # ── TOUCH_UPPER: 等待轉弱 K 棒 ──
        if self._state == _BBState.TOUCH_UPPER:
            self._track_high(bar)
            if self._is_reversal_bearish(bar):
                self._state = _BBState.REVERSAL_SHORT
                self._reversal_bar = bar
                print(
                    f"  📊 BB: 轉弱K棒確認 "
                    f"(strength={self.indicator_service.candle_strength(bar):.2f})"
                )
            elif close < middle:
                self._state = _BBState.IDLE
            return None

        # ── REVERSAL_SHORT: 等待跌破前一根低點 → 做空 ──
        if self._state == _BBState.REVERSAL_SHORT:
            if self._reversal_bar and close < int(self._reversal_bar.low):
                self._state = _BBState.IDLE
                self._trades_today += 1

                sl_price = self._recent_high + self.sl_buffer
                entry = int(current_price)
                mid_dist = entry - (int(middle) + self.tp_buffer)
                opp_dist = entry - (int(lower) + self.tp_buffer)

                if self.tp_target == "opposite":
                    tp_dist = opp_dist
                elif self.tp_target == "hybrid":
                    tp_dist = mid_dist
                else:
                    tp_dist = mid_dist

                sl_dist = sl_price - entry

                meta: dict = {
                    "override_stop_loss_price": sl_price,
                    "override_take_profit_points": max(tp_dist, 20),
                    "bb_upper": int(upper),
                    "bb_middle": int(middle),
                    "bb_lower": int(lower),
                }
                if self.tp_target == "hybrid":
                    meta["override_start_trailing_stop_points"] = max(mid_dist, 20)
                    meta["override_trailing_stop_points"] = self.hybrid_ts_trail_points

                tp_label = tp_dist if self.tp_target != "hybrid" else f"{mid_dist}(TP)/{opp_dist}(TS)"
                print(
                    f"🔔 BB 做空信號 @ {entry} | "
                    f"SL={sl_price} TP={tp_label} "
                    f"(risk={sl_dist})"
                )
                return StrategySignal(
                    signal_type=SignalType.ENTRY_SHORT,
                    symbol=symbol,
                    price=current_price,
                    reason="BB reversal short",
                    metadata=meta,
                )
            # 如果又突破上軌，重新等轉弱
            if close >= upper:
                self._state = _BBState.TOUCH_UPPER
                self._track_high(bar)
            elif close < middle:
                self._state = _BBState.IDLE
            return None

        return None

    # ──────────────────────────────────────────────
    # Reversal Detection
    # ──────────────────────────────────────────────

    def _is_reversal_bullish(self, bar: KBar) -> bool:
        """止跌 K 棒：下影線明顯 或 收紅（close > open）"""
        body = abs(bar.close - bar.open)
        lower_shadow = min(bar.open, bar.close) - bar.low
        bar_range = bar.high - bar.low
        if bar_range <= 0:
            return False

        # 收紅 + 有一定實體
        if bar.close > bar.open and body > bar_range * 0.2:
            return True

        # 長下影線（下影線佔整根 40% 以上）
        return lower_shadow > bar_range * 0.4

    def _is_reversal_bearish(self, bar: KBar) -> bool:
        """轉弱 K 棒：上影線明顯 或 收黑（close < open）"""
        body = abs(bar.close - bar.open)
        upper_shadow = bar.high - max(bar.open, bar.close)
        bar_range = bar.high - bar.low
        if bar_range <= 0:
            return False

        # 收黑 + 有一定實體
        if bar.close < bar.open and body > bar_range * 0.2:
            return True

        # 長上影線（上影線佔整根 40% 以上）
        return upper_shadow > bar_range * 0.4

    # ──────────────────────────────────────────────
    # Trend Filter
    # ──────────────────────────────────────────────

    def _is_hugging_band(
        self, kbar_list: KBarList, upper: float, lower: float
    ) -> bool:
        """檢測是否連續 N 根 K 棒貼著同一軌（強趨勢不交易）"""
        n = self.trend_filter_bars
        if len(kbar_list) < n:
            return False

        recent = kbar_list.get_latest(n)

        # 連續貼上軌
        hugging_upper = all(float(bar.close) >= upper * 0.998 for bar in recent)
        # 連續貼下軌
        hugging_lower = all(float(bar.close) <= lower * 1.002 for bar in recent)

        return hugging_upper or hugging_lower

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _track_low(self, bar: KBar) -> None:
        self._recent_low = min(self._recent_low, int(bar.low))

    def _track_high(self, bar: KBar) -> None:
        self._recent_high = max(self._recent_high, int(bar.high))

    def _reset_daily_state(self) -> None:
        self._state = _BBState.IDLE
        self._trades_today = 0
        self._bars_since_exit = 999
        self._reversal_bar = None
        self._recent_low = 0
        self._recent_high = 0

    @staticmethod
    def _parse_time(t: str) -> time:
        parts = t.split(":")
        return time(int(parts[0]), int(parts[1]))

    def __repr__(self) -> str:
        parts = [f"BB({self.bb_period},{self.bb_std})"]
        tp_map = {"middle": "中軌", "opposite": "對面軌", "hybrid": "混合"}
        tp_label = tp_map.get(self.tp_target, self.tp_target)
        parts.append(f"TP→{tp_label}")
        if self.long_only:
            parts.append("LongOnly")
        if self.short_only:
            parts.append("ShortOnly")
        if self.max_entries_per_day < 99:
            parts.append(f"max{self.max_entries_per_day}x")
        return f"Bollinger({', '.join(parts)})"
